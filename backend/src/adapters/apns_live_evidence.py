"""Live, privacy-bounded APNs provider-acceptance evidence harness.

This module deliberately has no replay or simulation mode.  Its CLI composes the
same PostgreSQL repositories, leased notification worker, and HTTP/2 APNs
provider used by the application.  Unit tests exercise the pure validation and
orchestration helpers with controlled adapters; those helpers never write or
claim live evidence.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import hmac
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import re
import ssl
import sys
from tempfile import NamedTemporaryFile
from time import monotonic
from typing import Any, Final, Literal, Protocol
from uuid import UUID

import certifi
import httpx
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.adapters.apns import (
    APNsConfigurationError,
    APNsNotificationProvider,
)
from vital_relay.adapters.postgres_dispatch import PostgresDispatchRepository
from vital_relay.adapters.postgres_notifications import (
    FernetDeviceTokenCipher,
    PostgresNotificationRepository,
)
from vital_relay.adapters.postgres_persona_sessions import (
    PostgresPersonaSessionRepository,
)
from vital_relay.adapters.static_routing import StaticVenueRoutingProvider
from vital_relay.application.dispatch_service import (
    DispatchService,
    ResponderAuthenticationError,
)
from vital_relay.application.notification_service import (
    NotificationAuthorizationError,
    NotificationService,
    NotificationWorker,
)
from vital_relay.application.persona_session_service import (
    PersonaAuthenticationError,
    PersonaSessionService,
)
from vital_relay.domain.dispatch import DispatchCoordinationView, InvitationStatus
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import IncidentState
from vital_relay.domain.notifications import (
    NotificationDeliveryStatus,
    NotificationErrorCode,
    NotificationProviderOutcome,
    NotificationProviderRequest,
    NotificationProviderResult,
    NotificationReceiptView,
    PushEnvironment,
    PushRegistrationRequest,
    PushRegistrationStatus,
    PushRegistrationView,
    ResponderInvitationNotificationPayload,
)
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256
from vital_relay.persistence.database import (
    DemoScopeUnavailableError,
    create_postgres_engine,
    create_session_factory,
    require_active_scope,
)
from vital_relay.persistence.models import (
    NotificationDeliveryAttemptRow,
    NotificationOutboxRow,
    ResponderInvitationRow,
    ResponderPushRegistrationRow,
)
from vital_relay.protocols.registry import FixedProtocolRegistry, ProtocolContentError


DATABASE_URL_ENV: Final = "VITAL_RELAY_DATABASE_URL"
SCOPE_ID_ENV: Final = "VITAL_RELAY_DEMO_SCOPE_ID"
APNS_ENABLED_ENV: Final = "VITAL_RELAY_APNS_ENABLED"
APNS_TEAM_ID_ENV: Final = "VITAL_RELAY_APNS_TEAM_ID"
APNS_KEY_ID_ENV: Final = "VITAL_RELAY_APNS_KEY_ID"
APNS_TOPIC_ENV: Final = "VITAL_RELAY_APNS_TOPIC"
APNS_PRIVATE_KEY_PATH_ENV: Final = "VITAL_RELAY_APNS_PRIVATE_KEY_PATH"
APNS_ENVIRONMENT_ENV: Final = "VITAL_RELAY_APNS_ENVIRONMENT"
APNS_TIMEOUT_ENV: Final = "VITAL_RELAY_APNS_TIMEOUT_SECONDS"
NOTIFICATION_ALLOWLIST_ENV: Final = (
    "VITAL_RELAY_NOTIFICATION_RESPONDER_ALLOWLIST"
)
NOTIFICATION_ENCRYPTION_KEY_ENV: Final = (
    "VITAL_RELAY_NOTIFICATION_TOKEN_ENCRYPTION_KEY"
)
SESSION_ACCESS_TOKEN_ENV: Final = "VITAL_RELAY_LIVE_APNS_SESSION_ACCESS_TOKEN"
DEVICE_TOKEN_ENV: Final = "VITAL_RELAY_LIVE_APNS_DEVICE_TOKEN"
RESPONDER_RADIUS_ENV: Final = "VITAL_RELAY_RESPONDER_RADIUS_M"
RESPONDER_STALE_ENV: Final = "VITAL_RELAY_RESPONDER_STALE_SECONDS"
EXPECTED_CERTIFI_SHA256_ENV: Final = (
    "VITAL_RELAY_LIVE_APNS_EXPECTED_CERTIFI_SHA256"
)
ATTESTATION_ISSUER_ENV: Final = "VITAL_RELAY_LIVE_APNS_ATTESTATION_ISSUER"
ATTESTATION_KEY_ID_ENV: Final = "VITAL_RELAY_LIVE_APNS_ATTESTATION_KEY_ID"
ATTESTATION_HMAC_KEY_ENV: Final = (
    "VITAL_RELAY_LIVE_APNS_ATTESTATION_HMAC_KEY"
)

_PROXY_OVERRIDE_ENVIRONMENTS: Final = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_CUSTOM_CA_OVERRIDE_ENVIRONMENTS: Final = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "ssl_cert_file",
    "ssl_cert_dir",
    "requests_ca_bundle",
    "curl_ca_bundle",
)
_NO_PROXY_ENVIRONMENTS: Final = ("NO_PROXY", "no_proxy")

DEFAULT_APNS_TIMEOUT_SECONDS: Final = 10.0
DEFAULT_RESPONDER_RADIUS_M: Final = 1_000
DEFAULT_RESPONDER_STALE_SECONDS: Final = 120
NOTIFICATION_BATCH_SIZE: Final = 1
NOTIFICATION_LEASE_SECONDS: Final = 30
NOTIFICATION_MAX_RETRY_SECONDS: Final = 900
DIRECT_TRANSPORT_MAX_CONNECTIONS: Final = 1
DIRECT_TRANSPORT_MAX_KEEPALIVE_CONNECTIONS: Final = 1
DIRECT_TRANSPORT_KEEPALIVE_EXPIRY_SECONDS: Final = 30.0
_LIVE_EVIDENCE_DOMAIN: Final = (
    "vital-relay.live-evidence.apns-provider-acceptance.hmac-sha256.v1"
)
_LIVE_EVIDENCE_DOMAIN_BYTES: Final = (
    b"vital-relay\0live-evidence\0apns-provider-acceptance\0"
    b"hmac-sha256\0v1\0"
)
_LIVE_EVIDENCE_ALGORITHM: Final = "HMAC-SHA256"
_LIVE_COMPOSITION: Final = "concrete_postgres_worker_apns_v1"
_TEST_ONLY_COMPOSITION: Final = "controlled_adapters_test_only_v1"
_HEX_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_ATTESTATION_ISSUER_PATTERN: Final = re.compile(
    r"[a-z0-9]+(?:[.-][a-z0-9]+)*"
)
_ATTESTATION_KEY_ID_PATTERN: Final = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?"
)
_APNS_TOPIC_PATTERN: Final = re.compile(
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)
_PROJECT_ROOT: Final = Path(__file__).resolve().parents[4]
_IDENTIFIER_DOMAIN: Final = b"vital-relay-live-apns-evidence-id-v1\0"
_FORBIDDEN_KEY_FRAGMENTS: Final = (
    "authorization",
    "coordinate",
    "credential",
    "database_url",
    "destination_url",
    "device_token",
    "health",
    "jwt",
    "location",
    "payload",
    "private_key",
    "provider_body",
    "reasoning",
    "session_token",
    "token",
    "url",
)


class LiveEvidenceError(RuntimeError):
    """A bounded failure safe to render without including exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LiveEvidenceConfiguration:
    """Validated live inputs; secret-bearing fields are excluded from repr."""

    database_url: str = field(repr=False)
    apns_enabled: bool
    non_production_confirmed: bool
    scope_id: UUID
    incident_id: UUID
    confirmed_installation_id: UUID
    confirmed_responder_id: UUID
    output_dir: Path
    team_id: str
    key_id: str
    topic: str
    private_key_path: Path = field(repr=False)
    environment: PushEnvironment
    responder_allowlist: frozenset[UUID]
    token_encryption_key: str = field(repr=False)
    session_access_token: str = field(repr=False)
    device_token: str = field(repr=False)
    timeout_seconds: float
    responder_radius_m: int
    responder_stale_seconds: int
    expected_certifi_sha256: str
    attestation_issuer: str
    attestation_key_id: str
    attestation_hmac_key: bytes = field(repr=False)


@dataclass(frozen=True)
class _ReviewedNetworkTrust:
    """Exact public CA bytes and direct-transport policy used for one run."""

    ca_bundle_pem: str = field(repr=False)
    expected_ca_bundle_sha256: str
    actual_ca_bundle_sha256: str
    ca_source: str
    ca_source_version: str
    httpx_version: str
    httpcore_version: str
    h2_version: str
    hpack_version: str
    hyperframe_version: str
    cryptography_version: str
    sqlalchemy_version: str
    psycopg_version: str
    pydantic_version: str
    geoalchemy2_version: str
    tls_backend: str

    def digest_material(self) -> dict[str, Any]:
        return {
            "transport": "httpx.HTTPTransport",
            "transport_environment": "disabled",
            "proxy": "disabled",
            "no_proxy_policy": (
                "ignored_by_direct_transport_and_cannot_override_proxy_rejection"
            ),
            "no_proxy_environment_names": list(_NO_PROXY_ENVIRONMENTS),
            "http1": False,
            "http2": True,
            "retries": 0,
            "hostname_verification": True,
            "certificate_verification": "required",
            "minimum_tls_version": "TLSv1.2",
            "max_connections": DIRECT_TRANSPORT_MAX_CONNECTIONS,
            "max_keepalive_connections": (
                DIRECT_TRANSPORT_MAX_KEEPALIVE_CONNECTIONS
            ),
            "keepalive_expiry_seconds": (
                DIRECT_TRANSPORT_KEEPALIVE_EXPIRY_SECONDS
            ),
            "ca_source": self.ca_source,
            "ca_source_version": self.ca_source_version,
            "expected_ca_bundle_sha256": self.expected_ca_bundle_sha256,
            "actual_ca_bundle_sha256": self.actual_ca_bundle_sha256,
            "ca_pin_policy": "operator_reviewed_exact_bytes_required",
            "httpx_version": self.httpx_version,
            "httpcore_version": self.httpcore_version,
            "h2_version": self.h2_version,
            "hpack_version": self.hpack_version,
            "hyperframe_version": self.hyperframe_version,
            "cryptography_version": self.cryptography_version,
            "sqlalchemy_version": self.sqlalchemy_version,
            "psycopg_version": self.psycopg_version,
            "pydantic_version": self.pydantic_version,
            "geoalchemy2_version": self.geoalchemy2_version,
            "tls_backend": self.tls_backend,
        }


@dataclass(frozen=True)
class _ReadyPathState:
    notification_id: UUID
    invitation_id: UUID
    invitation_created_at: datetime
    outbox_created_at: datetime


@dataclass(frozen=True)
class _PersistedPathState:
    notification_id: UUID
    invitation_id: UUID
    incident_id: UUID
    responder_id: UUID
    registration_id: UUID
    installation_id: UUID
    registration_responder_id: UUID
    registration_environment: PushEnvironment
    registration_device_token_matches: bool
    registration_authorized_at: datetime
    registration_updated_at: datetime
    registration_revoked_at: datetime | None
    registration_status: PushRegistrationStatus
    registration_simulated: bool
    invitation_simulated: bool
    outbox_simulated: bool
    attempt_simulated: bool
    outbox_channel: str
    outbox_template: str
    outbox_payload: ResponderInvitationNotificationPayload
    invitation_incident_id: UUID
    invitation_responder_id: UUID
    invitation_rank: int
    invitation_status: InvitationStatus
    invitation_created_at: datetime
    invitation_responded_at: datetime | None
    attempt_number: int
    attempt_outcome: NotificationProviderOutcome
    attempt_error_code: NotificationErrorCode | None
    provider_message_id: UUID | None
    requested_at: datetime
    responded_at: datetime
    outbox_status: NotificationDeliveryStatus
    outbox_attempt_count: int
    outbox_provider_message_id: UUID | None
    outbox_last_error_code: NotificationErrorCode | None
    outbox_next_attempt_at: datetime
    outbox_lease_cleared: bool
    outbox_created_at: datetime
    outbox_updated_at: datetime
    outbox_finalized_at: datetime | None
    registration_count: int
    invitation_count: int
    outbox_count: int
    attempt_count: int


@dataclass(frozen=True)
class _ObservedRun:
    principal: PersonaPrincipal
    registration: PushRegistrationView
    first_coordination: DispatchCoordinationView
    ready: _ReadyPathState
    receipt: NotificationReceiptView
    persisted: _PersistedPathState
    first_worker_processed: int
    second_worker_processed: int | None
    provider_send_count: int
    provider_result: NotificationProviderResult | None
    duplicate_result: str


@dataclass(frozen=True)
class _ControlledTestObservation:
    observed: _ObservedRun
    evidence_mode: Literal["test_only"] = "test_only"
    test_only: Literal[True] = True
    authenticated: Literal[False] = False
    composition: Literal["controlled_adapters_test_only_v1"] = (
        _TEST_ONLY_COMPOSITION
    )


@dataclass(frozen=True)
class _ProviderRequestAuthorization:
    notification_id: UUID
    invitation_id: UUID
    incident_id: UUID
    responder_id: UUID
    device_token: str = field(repr=False)
    topic: str
    environment: PushEnvironment
    outbox_created_at: datetime


@dataclass(frozen=True)
class _ConcreteLiveComposition:
    clock: _SystemClock
    persona_repository: PostgresPersonaSessionRepository
    persona_service: PersonaSessionService
    token_cipher: FernetDeviceTokenCipher
    notification_repository: PostgresNotificationRepository
    notification_service: NotificationService
    routing_provider: StaticVenueRoutingProvider
    protocol_registry: FixedProtocolRegistry
    dispatch_repository: PostgresDispatchRepository
    dispatch_service: DispatchService
    provider: APNsNotificationProvider
    provider_observer: _ObservedAPNsProvider
    worker: NotificationWorker
    reader: _PostgresEvidenceReader


@dataclass(frozen=True)
class LiveEvidenceArtifact:
    path: Path
    sha256: str
    provider_accepted: bool


@dataclass(frozen=True)
class _CompletedLiveEvidence:
    observed: _ObservedRun
    path: Path
    sha256: str


class _NotificationPath(Protocol):
    def register(
        self,
        responder_id: UUID,
        installation_id: UUID,
        registration: PushRegistrationRequest,
        *,
        responder_token: str | None = None,
        authenticated_responder_id: UUID | None = None,
    ) -> PushRegistrationView: ...

    def get_receipt(
        self,
        responder_id: UUID,
        invitation_id: UUID,
        *,
        responder_token: str | None = None,
        authenticated_responder_id: UUID | None = None,
    ) -> NotificationReceiptView: ...


class _DispatchPath(Protocol):
    def get_coordination(self, incident_id: UUID) -> DispatchCoordinationView: ...

    def coordinate(self, incident_id: UUID) -> DispatchCoordinationView: ...


class _WorkerPath(Protocol):
    def process_due(self) -> int: ...


class _EvidenceReader(Protocol):
    def assert_pristine(self, incident_id: UUID) -> None: ...

    def assert_registration(
        self,
        registration: PushRegistrationView,
        *,
        device_token: str,
    ) -> None: ...

    def assert_ready(
        self,
        *,
        invitation_id: UUID,
        responder_id: UUID,
        incident_id: UUID,
    ) -> _ReadyPathState: ...

    def load_persisted(
        self,
        *,
        notification_id: UUID,
        invitation_id: UUID,
        installation_id: UUID,
        device_token: str,
    ) -> _PersistedPathState: ...


class _ObservedAPNsProvider:
    """Transparent single-destination observer around the real APNs adapter."""

    def __init__(self, delegate: APNsNotificationProvider) -> None:
        self._delegate = delegate
        self.send_count = 0
        self.delegate_send_count = 0
        self.request: NotificationProviderRequest | None = None
        self.result: NotificationProviderResult | None = None
        self.validation_error: str | None = None
        self._authorization: _ProviderRequestAuthorization | None = None

    def authorize(self, authorization: _ProviderRequestAuthorization) -> None:
        if self._authorization is not None:
            raise LiveEvidenceError("provider_request_authorization_reused")
        self._authorization = authorization

    def send(self, request: NotificationProviderRequest) -> NotificationProviderResult:
        self.send_count += 1
        self.request = request
        try:
            if self._authorization is None:
                raise LiveEvidenceError("provider_request_not_authorized")
            _validate_provider_request(request, self._authorization)
        except LiveEvidenceError as exc:
            self.validation_error = exc.code
            result = NotificationProviderResult(
                outcome=NotificationProviderOutcome.UNKNOWN,
                error_code=NotificationErrorCode.PROVIDER_RESPONSE_INVALID,
            )
            self.result = result
            return result
        self.delegate_send_count += 1
        result = self._delegate.send(request)
        self.result = result
        return result


class _PostgresEvidenceReader:
    """Read only the durable, privacy-bounded state needed for evidence."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
    ) -> None:
        self._sessions = sessions
        self._scope_id = scope_id

    def assert_pristine(self, incident_id: UUID) -> None:
        with self._sessions() as session:
            require_active_scope(session, self._scope_id)
            if self._count(session, ResponderPushRegistrationRow) != 0:
                raise LiveEvidenceError("scope_has_existing_push_registration")
            if self._count(session, NotificationOutboxRow) != 0:
                raise LiveEvidenceError("scope_has_existing_notification_outbox")
            if self._count(session, NotificationDeliveryAttemptRow) != 0:
                raise LiveEvidenceError("scope_has_existing_notification_attempt")
            invitations = self._count(session, ResponderInvitationRow)
            if int(invitations or 0) != 0:
                raise LiveEvidenceError("scope_has_existing_invitation")

    def assert_registration(
        self,
        registration: PushRegistrationView,
        *,
        device_token: str,
    ) -> None:
        with self._sessions() as session:
            rows = session.scalars(
                select(ResponderPushRegistrationRow).where(
                    ResponderPushRegistrationRow.scope_id == self._scope_id
                )
            ).all()
            if len(rows) != 1:
                raise LiveEvidenceError("registration_persistence_mismatch")
            row = rows[0]
            if (
                row.registration_id != registration.registration_id
                or row.installation_id != registration.installation_id
                or row.responder_id != registration.responder_id
                or row.platform != "apns"
                or row.status != "active"
                or row.environment != PushEnvironment.SANDBOX.value
                or not row.device_token_ciphertext
                or not hmac.compare_digest(
                    row.device_token_sha256,
                    sha256(device_token.encode("ascii")).hexdigest(),
                )
                or _utc(row.authorized_at) != registration.updated_at
                or _utc(row.updated_at) != registration.updated_at
                or row.revoked_at is not None
                or row.simulated
            ):
                raise LiveEvidenceError("registration_persistence_mismatch")

    def assert_ready(
        self,
        *,
        invitation_id: UUID,
        responder_id: UUID,
        incident_id: UUID,
    ) -> _ReadyPathState:
        with self._sessions() as session:
            invitations = session.scalars(
                select(ResponderInvitationRow).where(
                    ResponderInvitationRow.scope_id == self._scope_id,
                    ResponderInvitationRow.incident_id == incident_id,
                )
            ).all()
            outboxes = session.scalars(
                select(NotificationOutboxRow).where(
                    NotificationOutboxRow.scope_id == self._scope_id
                )
            ).all()
            attempt_count = self._count(session, NotificationDeliveryAttemptRow)
            if len(invitations) != 1 or len(outboxes) != 1 or attempt_count != 0:
                raise LiveEvidenceError("atomic_invitation_outbox_mismatch")
            invitation = invitations[0]
            outbox = outboxes[0]
            try:
                payload = ResponderInvitationNotificationPayload.model_validate(
                    outbox.payload
                )
            except ValidationError as exc:
                raise LiveEvidenceError("atomic_invitation_outbox_mismatch") from exc
            if (
                invitation.invitation_id != invitation_id
                or invitation.responder_id != responder_id
                or invitation.incident_id != incident_id
                or invitation.rank != 1
                or invitation.status != "pending"
                or invitation.responded_at is not None
                or invitation.simulated
                or outbox.invitation_id != invitation_id
                or outbox.incident_id != incident_id
                or outbox.responder_id != responder_id
                or outbox.channel != "apns"
                or outbox.template != "responder_invitation_v1"
                or outbox.status != NotificationDeliveryStatus.PENDING.value
                or outbox.attempt_count != 0
                or outbox.provider_message_id is not None
                or outbox.last_error_code is not None
                or outbox.lease_token is not None
                or outbox.lease_until is not None
                or _utc(outbox.next_attempt_at) != _utc(outbox.created_at)
                or payload.invitation_id != invitation_id
                or payload.incident_id != incident_id
                or _utc(invitation.created_at) != _utc(outbox.created_at)
                or outbox.simulated
            ):
                raise LiveEvidenceError("atomic_invitation_outbox_mismatch")
            return _ReadyPathState(
                notification_id=outbox.notification_id,
                invitation_id=invitation.invitation_id,
                invitation_created_at=_utc(invitation.created_at),
                outbox_created_at=_utc(outbox.created_at),
            )

    def load_persisted(
        self,
        *,
        notification_id: UUID,
        invitation_id: UUID,
        installation_id: UUID,
        device_token: str,
    ) -> _PersistedPathState:
        with self._sessions() as session:
            registrations = session.scalars(
                select(ResponderPushRegistrationRow).where(
                    ResponderPushRegistrationRow.scope_id == self._scope_id,
                    ResponderPushRegistrationRow.installation_id == installation_id,
                )
            ).all()
            invitations = session.scalars(
                select(ResponderInvitationRow).where(
                    ResponderInvitationRow.scope_id == self._scope_id,
                    ResponderInvitationRow.invitation_id == invitation_id,
                )
            ).all()
            outboxes = session.scalars(
                select(NotificationOutboxRow).where(
                    NotificationOutboxRow.scope_id == self._scope_id,
                    NotificationOutboxRow.notification_id == notification_id,
                )
            ).all()
            attempts = session.scalars(
                select(NotificationDeliveryAttemptRow)
                .where(
                    NotificationDeliveryAttemptRow.scope_id == self._scope_id,
                    NotificationDeliveryAttemptRow.notification_id == notification_id,
                )
                .order_by(NotificationDeliveryAttemptRow.attempt_number)
            ).all()
            counts = (
                self._count(session, ResponderPushRegistrationRow),
                self._count(session, ResponderInvitationRow),
                self._count(session, NotificationOutboxRow),
                self._count(session, NotificationDeliveryAttemptRow),
            )
            if (
                len(registrations) != 1
                or len(invitations) != 1
                or len(outboxes) != 1
                or len(attempts) != 1
                or counts != (1, 1, 1, 1)
            ):
                raise LiveEvidenceError("durable_notification_state_mismatch")
            registration = registrations[0]
            invitation = invitations[0]
            outbox = outboxes[0]
            attempt = attempts[0]
            if (
                invitation.invitation_id != outbox.invitation_id
                or outbox.notification_id != attempt.notification_id
                or attempt.invitation_id != invitation.invitation_id
            ):
                raise LiveEvidenceError("durable_notification_binding_mismatch")
            try:
                outbox_payload = (
                    ResponderInvitationNotificationPayload.model_validate(
                        outbox.payload
                    )
                )
            except ValidationError as exc:
                raise LiveEvidenceError(
                    "durable_notification_binding_mismatch"
                ) from exc
            return _PersistedPathState(
                notification_id=outbox.notification_id,
                invitation_id=outbox.invitation_id,
                incident_id=outbox.incident_id,
                responder_id=outbox.responder_id,
                registration_id=registration.registration_id,
                installation_id=registration.installation_id,
                registration_responder_id=registration.responder_id,
                registration_environment=PushEnvironment(
                    registration.environment
                ),
                registration_device_token_matches=(
                    bool(registration.device_token_ciphertext)
                    and hmac.compare_digest(
                        registration.device_token_sha256,
                        sha256(device_token.encode("ascii")).hexdigest(),
                    )
                ),
                registration_authorized_at=_utc(registration.authorized_at),
                registration_updated_at=_utc(registration.updated_at),
                registration_revoked_at=(
                    _utc(registration.revoked_at)
                    if registration.revoked_at is not None
                    else None
                ),
                registration_status=PushRegistrationStatus(registration.status),
                registration_simulated=registration.simulated,
                invitation_simulated=invitation.simulated,
                outbox_simulated=outbox.simulated,
                attempt_simulated=attempt.simulated,
                outbox_channel=outbox.channel,
                outbox_template=outbox.template,
                outbox_payload=outbox_payload,
                invitation_incident_id=invitation.incident_id,
                invitation_responder_id=invitation.responder_id,
                invitation_rank=invitation.rank,
                invitation_status=InvitationStatus(invitation.status),
                invitation_created_at=_utc(invitation.created_at),
                invitation_responded_at=(
                    _utc(invitation.responded_at)
                    if invitation.responded_at is not None
                    else None
                ),
                attempt_number=attempt.attempt_number,
                attempt_outcome=NotificationProviderOutcome(attempt.outcome),
                attempt_error_code=(
                    NotificationErrorCode(attempt.error_code)
                    if attempt.error_code is not None
                    else None
                ),
                provider_message_id=attempt.provider_message_id,
                requested_at=_utc(attempt.requested_at),
                responded_at=_utc(attempt.responded_at),
                outbox_status=NotificationDeliveryStatus(outbox.status),
                outbox_attempt_count=outbox.attempt_count,
                outbox_provider_message_id=outbox.provider_message_id,
                outbox_last_error_code=(
                    NotificationErrorCode(outbox.last_error_code)
                    if outbox.last_error_code is not None
                    else None
                ),
                outbox_next_attempt_at=_utc(outbox.next_attempt_at),
                outbox_lease_cleared=(
                    outbox.lease_token is None and outbox.lease_until is None
                ),
                outbox_created_at=_utc(outbox.created_at),
                outbox_updated_at=_utc(outbox.updated_at),
                outbox_finalized_at=(
                    _utc(outbox.finalized_at)
                    if outbox.finalized_at is not None
                    else None
                ),
                registration_count=counts[0],
                invitation_count=counts[1],
                outbox_count=counts[2],
                attempt_count=counts[3],
            )

    def _count(self, session: Session, model: Any) -> int:
        value = session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.scope_id == self._scope_id)
        )
        return int(value or 0)


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _drive_notification_path(
    *,
    config: LiveEvidenceConfiguration,
    principal: PersonaPrincipal,
    notification_service: _NotificationPath,
    dispatch_service: _DispatchPath,
    worker: _WorkerPath,
    reader: _EvidenceReader,
    provider_observer: Any,
    live_observer: _ObservedAPNsProvider | None,
) -> _ObservedRun:
    """Drive the path without granting signing or live-writer authority."""

    _validate_principal(config, principal)
    reader.assert_pristine(config.incident_id)
    preview = dispatch_service.get_coordination(config.incident_id)
    if preview.state is not IncidentState.ESCALATING:
        raise LiveEvidenceError("incident_not_escalating")
    if preview.invitations:
        raise LiveEvidenceError("incident_has_existing_invitation")
    if (
        not preview.candidates
        or preview.candidates[0].responder_id != principal.responder_id
    ):
        raise LiveEvidenceError("confirmed_responder_not_first_eligible")

    registration = notification_service.register(
        principal.responder_id,
        principal.installation_id,
        PushRegistrationRequest(
            schema_version=SCHEMA_VERSION,
            platform="apns",
            device_token=config.device_token,
        ),
        authenticated_responder_id=principal.responder_id,
    )
    if (
        registration.schema_version != SCHEMA_VERSION
        or registration.installation_id != config.confirmed_installation_id
        or registration.responder_id != config.confirmed_responder_id
        or registration.platform != "apns"
        or registration.status is not PushRegistrationStatus.ACTIVE
        or registration.environment is not PushEnvironment.SANDBOX
    ):
        raise LiveEvidenceError("registration_product_binding_mismatch")
    reader.assert_registration(registration, device_token=config.device_token)

    first = dispatch_service.coordinate(config.incident_id)
    second = dispatch_service.coordinate(config.incident_id)
    if (
        len(first.invitations) != 1
        or first.invitations != second.invitations
        or first.invitations[0].responder.responder_id != principal.responder_id
    ):
        raise LiveEvidenceError("invitation_duplicate_suppression_failed")
    invitation = first.invitations[0]
    ready = reader.assert_ready(
        invitation_id=invitation.invitation_id,
        responder_id=principal.responder_id,
        incident_id=config.incident_id,
    )
    if (
        invitation.schema_version != SCHEMA_VERSION
        or invitation.invitation_id != ready.invitation_id
        or invitation.incident_id != config.incident_id
        or invitation.sequence != 1
        or invitation.status.value != "pending"
        or invitation.responded_at is not None
        or invitation.decision_id is not None
        or _utc(invitation.invited_at) != ready.invitation_created_at
    ):
        raise LiveEvidenceError("invitation_product_binding_mismatch")
    authorization = _ProviderRequestAuthorization(
        notification_id=ready.notification_id,
        invitation_id=ready.invitation_id,
        incident_id=config.incident_id,
        responder_id=principal.responder_id,
        device_token=config.device_token,
        topic=config.topic,
        environment=PushEnvironment.SANDBOX,
        outbox_created_at=ready.outbox_created_at,
    )
    if live_observer is not None:
        live_observer.authorize(authorization)

    first_processed = worker.process_due()
    if (
        first_processed != 1
        or provider_observer.send_count != 1
        or (
            live_observer is not None
            and (
                live_observer.validation_error is not None
                or live_observer.delegate_send_count != 1
            )
        )
    ):
        raise LiveEvidenceError("leased_worker_did_not_submit_exactly_once")
    request = provider_observer.request
    if request is None:
        raise LiveEvidenceError("leased_worker_request_binding_mismatch")
    _validate_provider_request(request, authorization)

    receipt = notification_service.get_receipt(
        principal.responder_id,
        invitation.invitation_id,
        authenticated_responder_id=principal.responder_id,
    )
    first_persisted = reader.load_persisted(
        notification_id=ready.notification_id,
        invitation_id=ready.invitation_id,
        installation_id=principal.installation_id,
        device_token=config.device_token,
    )
    _validate_persisted_result(
        config=config,
        principal=principal,
        registration=registration,
        ready=ready,
        provider_request=request,
        receipt=receipt,
        persisted=first_persisted,
        provider_result=provider_observer.result,
    )

    second_processed: int | None = None
    duplicate_result = "retry_deferred_after_transient_failure"
    persisted = first_persisted
    if receipt.status is not NotificationDeliveryStatus.PENDING:
        second_processed = worker.process_due()
        persisted = reader.load_persisted(
            notification_id=ready.notification_id,
            invitation_id=ready.invitation_id,
            installation_id=principal.installation_id,
            device_token=config.device_token,
        )
        if (
            second_processed != 0
            or provider_observer.send_count != 1
            or persisted.attempt_count != 1
            or persisted.outbox_attempt_count != 1
        ):
            raise LiveEvidenceError("terminal_duplicate_suppression_failed")
        _validate_persisted_result(
            config=config,
            principal=principal,
            registration=registration,
            ready=ready,
            provider_request=request,
            receipt=receipt,
            persisted=persisted,
            provider_result=provider_observer.result,
        )
        duplicate_result = "single_outbox_terminal_not_reclaimed"

    return _ObservedRun(
        principal=principal,
        registration=registration,
        first_coordination=first,
        ready=ready,
        receipt=receipt,
        persisted=persisted,
        first_worker_processed=first_processed,
        second_worker_processed=second_processed,
        provider_send_count=provider_observer.send_count,
        provider_result=provider_observer.result,
        duplicate_result=duplicate_result,
    )


def _orchestrate_test_only(
    *,
    config: LiveEvidenceConfiguration,
    principal: PersonaPrincipal,
    notification_service: _NotificationPath,
    dispatch_service: _DispatchPath,
    worker: _WorkerPath,
    reader: _EvidenceReader,
    provider_observer: Any,
) -> _ControlledTestObservation:
    """Run controlled adapters with an immutable unsigned/test-only result."""

    return _ControlledTestObservation(
        observed=_drive_notification_path(
            config=config,
            principal=principal,
            notification_service=notification_service,
            dispatch_service=dispatch_service,
            worker=worker,
            reader=reader,
            provider_observer=provider_observer,
            live_observer=None,
        )
    )


def _run_authenticated_live_product_path(
    *,
    config: LiveEvidenceConfiguration,
    principal: PersonaPrincipal,
    composition: _ConcreteLiveComposition,
    adapter_sha256: str,
    configuration_sha256: str,
    source_manifest: Mapping[str, Mapping[str, str]],
    network_trust: _ReviewedNetworkTrust,
) -> _CompletedLiveEvidence:
    """Seal concrete orchestration, attestation, and writing in one path."""

    _validate_concrete_live_composition(composition, config)
    if (
        _adapter_sha256(source_manifest) != adapter_sha256
        or _configuration_sha256(
            config,
            network_trust=network_trust,
            adapter_sha256=adapter_sha256,
        )
        != configuration_sha256
        or network_trust.expected_ca_bundle_sha256
        != config.expected_certifi_sha256
        or network_trust.actual_ca_bundle_sha256
        != config.expected_certifi_sha256
    ):
        raise LiveEvidenceError("live_evidence_provenance_invalid")
    started_at = _utc(composition.clock.now())
    started_monotonic = monotonic()
    observed = _drive_notification_path(
        config=config,
        principal=principal,
        notification_service=composition.notification_service,
        dispatch_service=composition.dispatch_service,
        worker=composition.worker,
        reader=composition.reader,
        provider_observer=composition.provider_observer,
        live_observer=composition.provider_observer,
    )
    completed_at = _utc(composition.clock.now())
    elapsed_ms = max(0, round((monotonic() - started_monotonic) * 1_000))
    evidence_payload = _build_evidence_payload_body(
        config=config,
        observed=observed,
        adapter_sha256=adapter_sha256,
        configuration_sha256=configuration_sha256,
        started_at=started_at,
        completed_at=completed_at,
        elapsed_ms=elapsed_ms,
        evidence_kind="vital_relay_live_apns_provider_acceptance",
        claim="apns_provider_acceptance_only",
        provider_claim_allowed=True,
        execution={
            "mode": "live",
            "test_only": False,
            "composition": _LIVE_COMPOSITION,
        },
        provenance={
            "source_manifest": {
                key: dict(value) for key, value in source_manifest.items()
            },
            "network_trust": network_trust.digest_material(),
        },
    )
    evidence_bytes = _privacy_checked_bytes(
        evidence_payload,
        _artifact_forbidden_values(config, observed),
    )
    evidence_sha256 = sha256(evidence_bytes).hexdigest()
    unsigned = {
        "schema_version": 1,
        "domain": _LIVE_EVIDENCE_DOMAIN,
        "algorithm": _LIVE_EVIDENCE_ALGORITHM,
        "issuer": config.attestation_issuer,
        "key_id": config.attestation_key_id,
        "evidence_sha256": evidence_sha256,
        "evidence": evidence_payload,
    }
    encoded = canonical_json_bytes(
        {
            **unsigned,
            "signature_sha256": _attestation_signature(
                unsigned,
                config.attestation_hmac_key,
            ),
        }
    )
    envelope = _verify_live_evidence(
        encoded,
        key=config.attestation_hmac_key,
        expected_issuer=config.attestation_issuer,
        expected_key_id=config.attestation_key_id,
    )
    if envelope["evidence_sha256"] != evidence_sha256:
        raise LiveEvidenceError("live_evidence_attestation_invalid")
    _privacy_checked_bytes(envelope, _secret_values(config))
    path, digest = _write_content_addressed_bytes(config.output_dir, encoded)
    return _CompletedLiveEvidence(
        observed=observed,
        path=path,
        sha256=digest,
    )


def run_live_evidence(
    config: LiveEvidenceConfiguration,
) -> LiveEvidenceArtifact:
    """Run real PostgreSQL/APNs dependencies and write one addressed artifact."""

    _reject_environment_transport_overrides(os.environ)
    _validate_live_configuration(config)
    _validate_output_dir_writable(config.output_dir)
    network_trust = _reviewed_network_trust(
        config.expected_certifi_sha256
    )
    source_manifest = _adapter_source_manifest()
    adapter_sha256 = _adapter_sha256(source_manifest)
    configuration_sha256 = _configuration_sha256(
        config,
        network_trust=network_trust,
        adapter_sha256=adapter_sha256,
    )
    active_clock = _SystemClock()
    engine = None
    provider = None
    try:
        engine = create_postgres_engine(config.database_url)
        sessions = create_session_factory(engine)
        persona_repository = PostgresPersonaSessionRepository(
            engine,
            sessions,
            config.scope_id,
        )
        persona_service = PersonaSessionService(
            persona_repository,
            active_clock,
        )
        principal = persona_service.authenticate_access(
            access_token=config.session_access_token
        )
        _validate_principal(config, principal)

        # Validate every local APNs prerequisite before registration mutates the
        # dedicated evidence scope. Network acceptance itself can only be tested
        # by the product worker after the durable outbox exists.
        token_cipher = FernetDeviceTokenCipher(config.token_encryption_key)
        provider = _create_apns_provider(config, network_trust)
        PushRegistrationRequest(
            schema_version=SCHEMA_VERSION,
            platform="apns",
            device_token=config.device_token,
        )
        protocol_registry = FixedProtocolRegistry()
        protocol_registry.validate_all()

        notification_repository = PostgresNotificationRepository(
            sessions,
            config.scope_id,
            responder_allowlist=config.responder_allowlist,
            environment=config.environment,
            topic=config.topic,
            token_cipher=token_cipher,
        )
        notification_service = NotificationService(
            notification_repository,
            active_clock,
        )
        routing_provider = StaticVenueRoutingProvider()
        dispatch_repository = PostgresDispatchRepository(
            engine,
            sessions,
            config.scope_id,
            routing_provider,
            protocol_registry,
            notification_enqueuer=notification_repository.enqueue_invitation,
        )
        dispatch_service = DispatchService(
            dispatch_repository,
            active_clock,
            responder_radius_m=config.responder_radius_m,
            responder_stale_seconds=config.responder_stale_seconds,
        )
        observer = _ObservedAPNsProvider(provider)
        worker = NotificationWorker(
            notification_repository,
            observer,
            active_clock,
            batch_size=NOTIFICATION_BATCH_SIZE,
            lease_seconds=NOTIFICATION_LEASE_SECONDS,
            max_retry_seconds=NOTIFICATION_MAX_RETRY_SECONDS,
        )
        reader = _PostgresEvidenceReader(sessions, config.scope_id)
        completed = _run_authenticated_live_product_path(
            config=config,
            principal=principal,
            composition=_ConcreteLiveComposition(
                clock=active_clock,
                persona_repository=persona_repository,
                persona_service=persona_service,
                token_cipher=token_cipher,
                notification_repository=notification_repository,
                notification_service=notification_service,
                routing_provider=routing_provider,
                protocol_registry=protocol_registry,
                dispatch_repository=dispatch_repository,
                dispatch_service=dispatch_service,
                provider=provider,
                provider_observer=observer,
                worker=worker,
                reader=reader,
            ),
            adapter_sha256=adapter_sha256,
            configuration_sha256=configuration_sha256,
            source_manifest=source_manifest,
            network_trust=network_trust,
        )
        return LiveEvidenceArtifact(
            path=completed.path,
            sha256=completed.sha256,
            provider_accepted=(
                completed.observed.receipt.status
                is NotificationDeliveryStatus.PROVIDER_ACCEPTED
            ),
        )
    except LiveEvidenceError:
        raise
    except PersonaAuthenticationError as exc:
        raise LiveEvidenceError("session_prerequisite_unavailable") from exc
    except (ResponderAuthenticationError, NotificationAuthorizationError) as exc:
        raise LiveEvidenceError(
            "responder_authorization_prerequisite_unavailable"
        ) from exc
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        raise LiveEvidenceError("postgres_prerequisite_unavailable") from exc
    except (APNsConfigurationError, ValidationError, ValueError) as exc:
        raise LiveEvidenceError("apns_configuration_prerequisite_invalid") from exc
    except ProtocolContentError as exc:
        raise LiveEvidenceError("protocol_content_prerequisite_invalid") from exc
    finally:
        if provider is not None:
            provider.close()
        if engine is not None:
            engine.dispose()


def _validate_principal(
    config: LiveEvidenceConfiguration,
    principal: PersonaPrincipal,
) -> None:
    if principal.scope_id != config.scope_id:
        raise LiveEvidenceError("session_scope_confirmation_mismatch")
    if (
        principal.persona is not Persona.RESPONDER
        or principal.responder_id is None
        or principal.responder_id != config.confirmed_responder_id
    ):
        raise LiveEvidenceError("session_responder_confirmation_mismatch")
    if principal.installation_id != config.confirmed_installation_id:
        raise LiveEvidenceError("session_installation_confirmation_mismatch")
    if principal.responder_id not in config.responder_allowlist:
        raise LiveEvidenceError("confirmed_responder_not_apns_allowlisted")


def _validate_provider_request(
    request: NotificationProviderRequest,
    authorization: _ProviderRequestAuthorization,
) -> None:
    try:
        token_matches = hmac.compare_digest(
            request.device_token.get_secret_value(),
            authorization.device_token,
        )
        topic_matches = hmac.compare_digest(request.topic, authorization.topic)
        requested_at = _utc(request.requested_at)
        payload = request.payload
        mismatch = (
            not token_matches
            or not topic_matches
            or request.notification_id != authorization.notification_id
            or request.invitation_id != authorization.invitation_id
            or request.incident_id != authorization.incident_id
            or request.responder_id != authorization.responder_id
            or request.attempt_number != 1
            or request.environment is not PushEnvironment.SANDBOX
            or request.environment is not authorization.environment
            or payload.schema_version != SCHEMA_VERSION
            or payload.kind != "responder_invitation"
            or payload.invitation_id != authorization.invitation_id
            or payload.incident_id != authorization.incident_id
            or requested_at < _utc(authorization.outbox_created_at)
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise LiveEvidenceError("leased_worker_request_binding_mismatch") from exc
    if mismatch:
        raise LiveEvidenceError("leased_worker_request_binding_mismatch")


def _validate_concrete_live_composition(
    composition: _ConcreteLiveComposition,
    config: LiveEvidenceConfiguration,
) -> None:
    exact_types = (
        (composition.clock, _SystemClock),
        (composition.persona_repository, PostgresPersonaSessionRepository),
        (composition.persona_service, PersonaSessionService),
        (composition.token_cipher, FernetDeviceTokenCipher),
        (composition.notification_repository, PostgresNotificationRepository),
        (composition.notification_service, NotificationService),
        (composition.routing_provider, StaticVenueRoutingProvider),
        (composition.protocol_registry, FixedProtocolRegistry),
        (composition.dispatch_repository, PostgresDispatchRepository),
        (composition.dispatch_service, DispatchService),
        (composition.provider, APNsNotificationProvider),
        (composition.provider_observer, _ObservedAPNsProvider),
        (composition.worker, NotificationWorker),
        (composition.reader, _PostgresEvidenceReader),
    )
    enqueuer = composition.dispatch_repository._notification_enqueuer
    if (
        any(type(value) is not expected for value, expected in exact_types)
        or composition.persona_service._repository
        is not composition.persona_repository
        or composition.persona_service._clock is not composition.clock
        or composition.notification_service._repository
        is not composition.notification_repository
        or composition.notification_service._clock is not composition.clock
        or composition.notification_repository._token_cipher
        is not composition.token_cipher
        or composition.notification_repository._responder_allowlist
        != config.responder_allowlist
        or composition.dispatch_service._repository
        is not composition.dispatch_repository
        or composition.dispatch_service._clock is not composition.clock
        or composition.dispatch_service._radius_m != config.responder_radius_m
        or composition.dispatch_service._stale_window.total_seconds()
        != config.responder_stale_seconds
        or composition.dispatch_repository._routing_provider
        is not composition.routing_provider
        or composition.dispatch_repository._protocol_registry
        is not composition.protocol_registry
        or getattr(enqueuer, "__self__", None)
        is not composition.notification_repository
        or getattr(enqueuer, "__func__", None)
        is not PostgresNotificationRepository.enqueue_invitation
        or composition.provider_observer._delegate is not composition.provider
        or type(composition.provider._client._transport) is not httpx.HTTPTransport
        or composition.worker._repository
        is not composition.notification_repository
        or composition.worker._provider is not composition.provider_observer
        or composition.worker._clock is not composition.clock
        or composition.worker._batch_size != NOTIFICATION_BATCH_SIZE
        or composition.worker._lease_window.total_seconds()
        != NOTIFICATION_LEASE_SECONDS
        or composition.worker._max_retry_seconds
        != NOTIFICATION_MAX_RETRY_SECONDS
        or composition.notification_repository.scope_id != config.scope_id
        or composition.notification_repository._environment
        is not PushEnvironment.SANDBOX
        or composition.notification_repository._topic != config.topic
        or composition.dispatch_repository.scope_id != config.scope_id
        or composition.persona_repository.scope_id != config.scope_id
        or composition.reader._scope_id != config.scope_id
        or composition.persona_repository._engine
        is not composition.dispatch_repository._engine
        or composition.persona_repository._sessions
        is not composition.notification_repository._sessions
        or composition.dispatch_repository._sessions
        is not composition.notification_repository._sessions
        or composition.reader._sessions
        is not composition.notification_repository._sessions
    ):
        raise LiveEvidenceError("concrete_live_composition_validation_failed")


def _validate_persisted_result(
    *,
    config: LiveEvidenceConfiguration,
    principal: PersonaPrincipal,
    registration: PushRegistrationView,
    ready: _ReadyPathState,
    provider_request: NotificationProviderRequest,
    receipt: NotificationReceiptView,
    persisted: _PersistedPathState,
    provider_result: NotificationProviderResult | None,
) -> None:
    if any(
        (
            persisted.registration_simulated,
            persisted.invitation_simulated,
            persisted.outbox_simulated,
            persisted.attempt_simulated,
        )
    ):
        raise LiveEvidenceError("simulated_state_cannot_be_live_evidence")
    if (
        provider_result is None
        or persisted.notification_id != provider_request.notification_id
        or persisted.invitation_id != provider_request.invitation_id
        or persisted.incident_id != provider_request.incident_id
        or persisted.responder_id != provider_request.responder_id
        or persisted.registration_id != registration.registration_id
        or persisted.installation_id != principal.installation_id
        or persisted.registration_responder_id != principal.responder_id
        or persisted.registration_environment is not PushEnvironment.SANDBOX
        or not persisted.registration_device_token_matches
        or persisted.registration_authorized_at != registration.updated_at
        or persisted.invitation_created_at != ready.invitation_created_at
        or persisted.outbox_channel != "apns"
        or persisted.outbox_template != "responder_invitation_v1"
        or persisted.outbox_payload != provider_request.payload
        or persisted.invitation_incident_id != provider_request.incident_id
        or persisted.invitation_responder_id != provider_request.responder_id
        or persisted.invitation_rank != 1
        or persisted.invitation_status is not InvitationStatus.PENDING
        or persisted.invitation_responded_at is not None
        or not persisted.outbox_lease_cleared
        or persisted.attempt_number != 1
        or persisted.attempt_count != 1
        or persisted.outbox_attempt_count != 1
        or persisted.requested_at != provider_request.requested_at
        or persisted.responded_at < persisted.requested_at
        or receipt.schema_version != SCHEMA_VERSION
        or receipt.notification_id != provider_request.notification_id
        or receipt.invitation_id != provider_request.invitation_id
        or receipt.incident_id != config.incident_id
        or receipt.responder_id != config.confirmed_responder_id
        or receipt.channel != "apns"
        or receipt.template != "responder_invitation_v1"
        or receipt.attempt_count != persisted.outbox_attempt_count
        or receipt.provider_message_id != persisted.outbox_provider_message_id
        or receipt.last_error_code != persisted.outbox_last_error_code
        or persisted.outbox_status is not receipt.status
        or persisted.outbox_created_at != ready.outbox_created_at
        or persisted.outbox_created_at != receipt.created_at
        or persisted.outbox_updated_at != receipt.updated_at
        or persisted.outbox_finalized_at != receipt.finalized_at
        or persisted.outbox_updated_at != persisted.responded_at
    ):
        raise LiveEvidenceError("persisted_receipt_validation_failed")
    if (
        persisted.attempt_outcome is not provider_result.outcome
        or persisted.attempt_error_code != provider_result.error_code
        or persisted.provider_message_id != provider_result.provider_message_id
    ):
        raise LiveEvidenceError("provider_attempt_persistence_mismatch")
    invalid_destination_codes = {
        NotificationErrorCode.BAD_DEVICE_TOKEN,
        NotificationErrorCode.DEVICE_TOKEN_NOT_FOR_TOPIC,
        NotificationErrorCode.DEVICE_TOKEN_UNREGISTERED,
    }
    if persisted.registration_status is PushRegistrationStatus.ACTIVE:
        if (
            persisted.registration_updated_at != registration.updated_at
            or persisted.registration_revoked_at is not None
        ):
            raise LiveEvidenceError("persisted_receipt_validation_failed")
    elif (
        persisted.registration_status is not PushRegistrationStatus.REVOKED
        or persisted.attempt_error_code not in invalid_destination_codes
        or persisted.registration_updated_at != persisted.responded_at
        or persisted.registration_revoked_at != persisted.responded_at
    ):
        raise LiveEvidenceError("persisted_receipt_validation_failed")
    if persisted.attempt_outcome is NotificationProviderOutcome.PROVIDER_ACCEPTED:
        if (
            receipt.status is not NotificationDeliveryStatus.PROVIDER_ACCEPTED
            or persisted.provider_message_id != persisted.notification_id
            or persisted.outbox_provider_message_id != persisted.notification_id
            or persisted.attempt_error_code is not None
            or persisted.outbox_last_error_code is not None
            or persisted.outbox_next_attempt_at != persisted.outbox_created_at
        ):
            raise LiveEvidenceError("apns_id_correlation_failed")
    else:
        if (
            persisted.provider_message_id is not None
            or persisted.attempt_error_code is None
            or persisted.outbox_provider_message_id is not None
        ):
            raise LiveEvidenceError("provider_failure_not_bounded")
        if persisted.attempt_outcome is NotificationProviderOutcome.TRANSIENT_FAILURE:
            if (
                receipt.status is not NotificationDeliveryStatus.PENDING
                or persisted.outbox_last_error_code is not None
                or persisted.outbox_finalized_at is not None
                or persisted.outbox_next_attempt_at <= persisted.responded_at
            ):
                raise LiveEvidenceError("provider_failure_not_bounded")
        elif (
            persisted.outbox_last_error_code != persisted.attempt_error_code
            or persisted.outbox_finalized_at != persisted.responded_at
            or persisted.outbox_next_attempt_at != persisted.outbox_created_at
        ):
            raise LiveEvidenceError("provider_failure_not_bounded")


def _build_test_only_evidence_payload(
    *,
    config: LiveEvidenceConfiguration,
    controlled: _ControlledTestObservation,
    adapter_sha256: str,
    configuration_sha256: str,
    started_at: datetime,
    completed_at: datetime,
    elapsed_ms: int,
) -> dict[str, Any]:
    return _build_evidence_payload_body(
        config=config,
        observed=controlled.observed,
        adapter_sha256=adapter_sha256,
        configuration_sha256=configuration_sha256,
        started_at=started_at,
        completed_at=completed_at,
        elapsed_ms=elapsed_ms,
        evidence_kind="vital_relay_apns_controlled_test_observation",
        claim="none",
        provider_claim_allowed=False,
        execution={
            "mode": controlled.evidence_mode,
            "test_only": controlled.test_only,
            "composition": controlled.composition,
            "authenticated": controlled.authenticated,
        },
        provenance={"attestation": "unsigned_test_only"},
    )


def _build_evidence_payload_body(
    *,
    config: LiveEvidenceConfiguration,
    observed: _ObservedRun,
    adapter_sha256: str,
    configuration_sha256: str,
    started_at: datetime,
    completed_at: datetime,
    elapsed_ms: int,
    evidence_kind: str,
    claim: str,
    provider_claim_allowed: bool,
    execution: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    persisted = observed.persisted
    _validate_evidence_timing(
        observed=observed,
        started_at=started_at,
        completed_at=completed_at,
    )
    provider_accepted = (
        provider_claim_allowed
        and (
            persisted.attempt_outcome
            is NotificationProviderOutcome.PROVIDER_ACCEPTED
        )
        and observed.receipt.status is NotificationDeliveryStatus.PROVIDER_ACCEPTED
    )
    provider_id = persisted.provider_message_id
    return {
        "schema_version": 1,
        "evidence_kind": evidence_kind,
        "claim": claim,
        "provider_acceptance_observed": provider_accepted,
        "signed_device_display_open_verified": False,
        "apple_evidence_correlation_required": True,
        "environment": PushEnvironment.SANDBOX.value,
        "operator_confirmation": "non_production_installation_and_recipient",
        "execution": dict(execution),
        "identifiers": {
            "scope_id_sha256": _identifier_sha256(config.scope_id),
            "incident_id_sha256": _identifier_sha256(config.incident_id),
            "responder_id_sha256": _identifier_sha256(
                config.confirmed_responder_id
            ),
            "installation_id_sha256": _identifier_sha256(
                config.confirmed_installation_id
            ),
            "registration_id_sha256": _identifier_sha256(
                observed.registration.registration_id
            ),
            "invitation_id_sha256": _identifier_sha256(
                persisted.invitation_id
            ),
            "notification_id_sha256": _identifier_sha256(
                persisted.notification_id
            ),
            "apns_id_sha256": (
                _identifier_sha256(provider_id) if provider_id is not None else None
            ),
            "apns_id_correlated": (
                provider_id is not None and provider_id == persisted.notification_id
            ),
        },
        "durable_path": {
            "registration_created_status": observed.registration.status.value,
            "registration_persisted_status": persisted.registration_status.value,
            "invitation_rank": persisted.invitation_rank,
            "invitation_status": persisted.invitation_status.value,
            "invitation_responded": (
                persisted.invitation_responded_at is not None
            ),
            "attempt_number": persisted.attempt_number,
            "provider_outcome": persisted.attempt_outcome.value,
            "provider_error_code": (
                persisted.attempt_error_code.value
                if persisted.attempt_error_code is not None
                else None
            ),
            "receipt_status": observed.receipt.status.value,
            "receipt_attempt_count": observed.receipt.attempt_count,
            "receipt_finalized": observed.receipt.finalized_at is not None,
            "all_persisted_rows_unsimulated": not any(
                (
                    persisted.registration_simulated,
                    persisted.invitation_simulated,
                    persisted.outbox_simulated,
                    persisted.attempt_simulated,
                )
            ),
            "row_counts": {
                "registrations": persisted.registration_count,
                "invitations": persisted.invitation_count,
                "outboxes": persisted.outbox_count,
                "attempts": persisted.attempt_count,
            },
        },
        "duplicate_suppression": {
            "invitation_replay_same_id": True,
            "first_worker_processed": observed.first_worker_processed,
            "second_worker_processed": observed.second_worker_processed,
            "provider_send_count": observed.provider_send_count,
            "result": observed.duplicate_result,
        },
        "integrity": {
            "adapter_sha256": adapter_sha256,
            "configuration_sha256": configuration_sha256,
            **dict(provenance),
        },
        "timing": {
            "started_at": _timestamp(started_at),
            "registration_updated_at": _timestamp(
                observed.registration.updated_at
            ),
            "invitation_created_at": _timestamp(
                observed.ready.invitation_created_at
            ),
            "outbox_created_at": _timestamp(observed.ready.outbox_created_at),
            "provider_requested_at": _timestamp(persisted.requested_at),
            "provider_responded_at": _timestamp(persisted.responded_at),
            "provider_latency_ms": max(
                0,
                round(
                    (persisted.responded_at - persisted.requested_at).total_seconds()
                    * 1_000
                ),
            ),
            "receipt_finalized_at": (
                _timestamp(observed.receipt.finalized_at)
                if observed.receipt.finalized_at is not None
                else None
            ),
            "completed_at": _timestamp(completed_at),
            "elapsed_ms": elapsed_ms,
        },
    }


def _validate_evidence_timing(
    *,
    observed: _ObservedRun,
    started_at: datetime,
    completed_at: datetime,
) -> None:
    persisted = observed.persisted
    ordered = (
        _utc(started_at),
        _utc(observed.registration.updated_at),
        _utc(observed.ready.invitation_created_at),
        _utc(persisted.requested_at),
        _utc(persisted.responded_at),
        _utc(completed_at),
    )
    if any(later < earlier for earlier, later in zip(ordered, ordered[1:])):
        raise LiveEvidenceError("evidence_timing_order_invalid")
    if (
        observed.ready.invitation_created_at != observed.ready.outbox_created_at
        or observed.ready.outbox_created_at != persisted.outbox_created_at
    ):
        raise LiveEvidenceError("atomic_outbox_timing_mismatch")


def _adapter_source_manifest() -> dict[str, dict[str, str]]:
    executed_sources = {
        "harness": (__name__, Path(__file__)),
        "provider_adapter": _module_source(APNsNotificationProvider),
        "destination_cipher_adapter": _module_source(FernetDeviceTokenCipher),
        "notification_repository_adapter": _module_source(
            PostgresNotificationRepository
        ),
        "notification_service": _module_source(NotificationService),
        "leased_notification_worker": _module_source(NotificationWorker),
        "dispatch_service": _module_source(DispatchService),
        "dispatch_repository_adapter": _module_source(
            PostgresDispatchRepository
        ),
        "dispatch_model_fingerprint": _module_source_name(
            "vital_relay.adapters.fingerprints"
        ),
        "dispatch_integrity_adapter": _module_source_name(
            "vital_relay.adapters.postgres_health"
        ),
        "dispatch_routing_adapter": _module_source(StaticVenueRoutingProvider),
        "persona_session_service": _module_source(PersonaSessionService),
        "persona_session_repository_adapter": _module_source(
            PostgresPersonaSessionRepository
        ),
        "notification_domain_schema": _module_source(
            NotificationProviderRequest
        ),
        "dispatch_domain_schema": _module_source(DispatchCoordinationView),
        "incident_domain_schema": _module_source(IncidentState),
        "persona_session_domain_schema": _module_source(PersonaPrincipal),
        "schema_version_domain": _module_source_name(
            "vital_relay.domain.health"
        ),
        "protocol_domain_schema": _module_source_name(
            "vital_relay.domain.protocols"
        ),
        "persistence_database": _module_source(create_postgres_engine),
        "persistence_models": _module_source(NotificationOutboxRow),
        "canonical_hashing": _module_source(canonical_json_bytes),
        "fixed_protocol_registry": _module_source(FixedProtocolRegistry),
    }
    manifest = {
        role: {
            "module": module_name,
            "source_sha256": sha256(source.read_bytes()).hexdigest(),
        }
        for role, (module_name, source) in executed_sources.items()
    }
    protocol_root = Path(_module_source(FixedProtocolRegistry)[1]).parent / "content"
    for source in sorted(protocol_root.glob("*.json")):
        manifest[f"fixed_protocol_content:{source.name}"] = {
            "module": f"vital_relay.protocols.content.{source.name}",
            "source_sha256": sha256(source.read_bytes()).hexdigest(),
        }
    return manifest


def _module_source(value: Any) -> tuple[str, Path]:
    module_name = value.__module__
    source = Path(sys.modules[module_name].__file__ or "")
    return module_name, source


def _module_source_name(module_name: str) -> tuple[str, Path]:
    return module_name, Path(sys.modules[module_name].__file__ or "")


def _adapter_sha256(
    manifest: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    return canonical_sha256(
        {
            "schema_version": 1,
            "executed_sources": (
                dict(manifest) if manifest is not None else _adapter_source_manifest()
            ),
        }
    )


def _configuration_sha256(
    config: LiveEvidenceConfiguration,
    *,
    network_trust: _ReviewedNetworkTrust,
    adapter_sha256: str,
) -> str:
    return canonical_sha256(
        {
            "adapter_sha256": adapter_sha256,
            "apns_enabled": config.apns_enabled,
            "non_production_confirmed": config.non_production_confirmed,
            "environment": config.environment.value,
            "scope_id_sha256": _identifier_sha256(config.scope_id),
            "incident_id_sha256": _identifier_sha256(config.incident_id),
            "confirmed_responder_id_sha256": _identifier_sha256(
                config.confirmed_responder_id
            ),
            "confirmed_installation_id_sha256": _identifier_sha256(
                config.confirmed_installation_id
            ),
            "team_id_sha256": _text_sha256(config.team_id),
            "key_id_sha256": _text_sha256(config.key_id),
            "topic_sha256": _text_sha256(config.topic),
            "allowlist_id_sha256": sorted(
                _identifier_sha256(item) for item in config.responder_allowlist
            ),
            "timeout_seconds": config.timeout_seconds,
            "responder_radius_m": config.responder_radius_m,
            "responder_stale_seconds": config.responder_stale_seconds,
            "worker_batch_size": NOTIFICATION_BATCH_SIZE,
            "worker_lease_seconds": NOTIFICATION_LEASE_SECONDS,
            "worker_max_retry_seconds": NOTIFICATION_MAX_RETRY_SECONDS,
            "attestation": {
                "domain": _LIVE_EVIDENCE_DOMAIN,
                "algorithm": _LIVE_EVIDENCE_ALGORITHM,
                "issuer": config.attestation_issuer,
                "key_id": config.attestation_key_id,
                "composition": _LIVE_COMPOSITION,
            },
            "network_trust": network_trust.digest_material(),
        }
    )


def _reviewed_network_trust(
    expected_ca_bundle_sha256: str,
) -> _ReviewedNetworkTrust:
    _validate_sha256_pin(expected_ca_bundle_sha256)
    try:
        ca_bundle = Path(certifi.where()).read_bytes()
        actual_ca_bundle_sha256 = sha256(ca_bundle).hexdigest()
        if not hmac.compare_digest(
            actual_ca_bundle_sha256,
            expected_ca_bundle_sha256,
        ):
            raise LiveEvidenceError("reviewed_ca_bundle_sha256_mismatch")
        ca_bundle_pem = ca_bundle.decode("ascii")
        certifi_version = version("certifi")
        httpx_version = version("httpx")
        httpcore_version = version("httpcore")
        h2_version = version("h2")
        hpack_version = version("hpack")
        hyperframe_version = version("hyperframe")
        cryptography_version = version("cryptography")
        sqlalchemy_version = version("SQLAlchemy")
        psycopg_version = version("psycopg")
        pydantic_version = version("pydantic")
        geoalchemy2_version = version("GeoAlchemy2")
    except LiveEvidenceError:
        raise
    except (OSError, UnicodeError, PackageNotFoundError) as exc:
        raise LiveEvidenceError("reviewed_ca_trust_source_unavailable") from exc
    if ca_bundle_pem.count("-----BEGIN CERTIFICATE-----") < 1:
        raise LiveEvidenceError("reviewed_ca_trust_source_invalid")
    return _ReviewedNetworkTrust(
        ca_bundle_pem=ca_bundle_pem,
        expected_ca_bundle_sha256=expected_ca_bundle_sha256,
        actual_ca_bundle_sha256=actual_ca_bundle_sha256,
        ca_source="certifi_mozilla_ca_bundle",
        ca_source_version=certifi_version,
        httpx_version=httpx_version,
        httpcore_version=httpcore_version,
        h2_version=h2_version,
        hpack_version=hpack_version,
        hyperframe_version=hyperframe_version,
        cryptography_version=cryptography_version,
        sqlalchemy_version=sqlalchemy_version,
        psycopg_version=psycopg_version,
        pydantic_version=pydantic_version,
        geoalchemy2_version=geoalchemy2_version,
        tls_backend=ssl.OPENSSL_VERSION,
    )


def _direct_apns_transport(
    network_trust: _ReviewedNetworkTrust,
) -> httpx.HTTPTransport:
    try:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_context.check_hostname = True
        tls_context.verify_mode = ssl.CERT_REQUIRED
        # Load the exact public CA bytes whose digest is bound above. This avoids
        # both environment-selected trust and a path-content time-of-check race.
        tls_context.load_verify_locations(cadata=network_trust.ca_bundle_pem)
        return httpx.HTTPTransport(
            verify=tls_context,
            trust_env=False,
            http1=False,
            http2=True,
            limits=httpx.Limits(
                max_connections=DIRECT_TRANSPORT_MAX_CONNECTIONS,
                max_keepalive_connections=(
                    DIRECT_TRANSPORT_MAX_KEEPALIVE_CONNECTIONS
                ),
                keepalive_expiry=DIRECT_TRANSPORT_KEEPALIVE_EXPIRY_SECONDS,
            ),
            proxy=None,
            retries=0,
        )
    except (ImportError, OSError, ValueError) as exc:
        raise LiveEvidenceError("direct_apns_transport_unavailable") from exc


def _create_apns_provider(
    config: LiveEvidenceConfiguration,
    network_trust: _ReviewedNetworkTrust,
) -> APNsNotificationProvider:
    transport = _direct_apns_transport(network_trust)
    try:
        return APNsNotificationProvider.from_key_file(
            team_id=config.team_id,
            key_id=config.key_id,
            private_key_path=config.private_key_path,
            timeout_seconds=config.timeout_seconds,
            transport=transport,
        )
    except Exception:
        transport.close()
        raise


def _validate_live_configuration(config: LiveEvidenceConfiguration) -> None:
    if type(config) is not LiveEvidenceConfiguration:
        raise LiveEvidenceError("live_evidence_configuration_invalid")
    if config.apns_enabled is not True:
        raise LiveEvidenceError("apns_must_be_explicitly_enabled")
    if config.non_production_confirmed is not True:
        raise LiveEvidenceError("non_production_confirmation_required")
    if config.environment is not PushEnvironment.SANDBOX:
        raise LiveEvidenceError("production_apns_evidence_forbidden")
    if (
        not isinstance(config.scope_id, UUID)
        or not isinstance(config.incident_id, UUID)
        or not isinstance(config.confirmed_installation_id, UUID)
        or not isinstance(config.confirmed_responder_id, UUID)
        or type(config.responder_allowlist) is not frozenset
        or not config.responder_allowlist
        or any(not isinstance(item, UUID) for item in config.responder_allowlist)
        or config.confirmed_responder_id not in config.responder_allowlist
    ):
        raise LiveEvidenceError("live_evidence_identity_configuration_invalid")
    output_dir = config.output_dir
    if (
        not isinstance(output_dir, Path)
        or not output_dir.is_absolute()
        or not output_dir.is_dir()
    ):
        raise LiveEvidenceError("evidence_output_dir_unavailable")
    resolved_output = output_dir.resolve()
    if (
        output_dir != resolved_output
        or resolved_output == _PROJECT_ROOT
        or resolved_output.is_relative_to(_PROJECT_ROOT)
    ):
        raise LiveEvidenceError("evidence_output_dir_must_be_outside_repository")
    if (
        not _strict_nonempty(config.database_url)
        or not _strict_nonempty(config.team_id)
        or not _strict_nonempty(config.key_id)
        or not _strict_nonempty(config.topic)
        or len(config.topic) > 255
        or _APNS_TOPIC_PATTERN.fullmatch(config.topic) is None
        or not isinstance(config.private_key_path, Path)
        or not config.private_key_path.is_absolute()
        or not _strict_nonempty(config.token_encryption_key)
        or not _strict_nonempty(config.session_access_token)
        or not _strict_nonempty(config.device_token)
    ):
        raise LiveEvidenceError("live_evidence_configuration_invalid")
    if (
        isinstance(config.timeout_seconds, bool)
        or not isinstance(config.timeout_seconds, (int, float))
        or not math.isfinite(config.timeout_seconds)
        or config.timeout_seconds <= 0
        or type(config.responder_radius_m) is not int
        or not 1 <= config.responder_radius_m <= 2_000
        or type(config.responder_stale_seconds) is not int
        or config.responder_stale_seconds <= 0
    ):
        raise LiveEvidenceError("live_evidence_numeric_configuration_invalid")
    _validate_sha256_pin(config.expected_certifi_sha256)
    if (
        _ATTESTATION_ISSUER_PATTERN.fullmatch(config.attestation_issuer) is None
        or not 3 <= len(config.attestation_issuer) <= 128
        or _ATTESTATION_KEY_ID_PATTERN.fullmatch(config.attestation_key_id) is None
        or not 1 <= len(config.attestation_key_id) <= 64
        or type(config.attestation_hmac_key) is not bytes
        or len(config.attestation_hmac_key) != 32
    ):
        raise LiveEvidenceError("live_evidence_attestation_configuration_invalid")
    try:
        PushRegistrationRequest(
            schema_version=SCHEMA_VERSION,
            platform="apns",
            device_token=config.device_token,
        )
    except ValidationError as exc:
        raise LiveEvidenceError("apns_configuration_prerequisite_invalid") from exc


def _strict_nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _validate_sha256_pin(value: str) -> None:
    if not isinstance(value, str) or _HEX_SHA256_PATTERN.fullmatch(value) is None:
        raise LiveEvidenceError("expected_certifi_sha256_invalid")


def _reject_environment_transport_overrides(
    environ: Mapping[str, str],
) -> None:
    configured = tuple(
        name
        for name in (
            *_PROXY_OVERRIDE_ENVIRONMENTS,
            *_CUSTOM_CA_OVERRIDE_ENVIRONMENTS,
        )
        if name in environ
    )
    if configured:
        # NO_PROXY never makes a configured proxy acceptable. With no proxy
        # variable, NO_PROXY/no_proxy is inert because trust_env is disabled.
        raise LiveEvidenceError("environment_transport_override_forbidden")


def _privacy_checked_bytes(
    payload: Mapping[str, Any],
    secret_values: Sequence[str],
) -> bytes:
    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = str(key).lower()
                if any(fragment in normalized for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                    raise LiveEvidenceError("evidence_privacy_boundary_violation")
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(payload)
    encoded = canonical_json_bytes(payload)
    for secret in secret_values:
        if secret and secret.encode("utf-8") in encoded:
            raise LiveEvidenceError("evidence_secret_redaction_failed")
    return encoded


def _attestation_signature(
    unsigned: Mapping[str, Any],
    key: bytes,
) -> str:
    return hmac.new(
        key,
        _LIVE_EVIDENCE_DOMAIN_BYTES + canonical_json_bytes(unsigned),
        "sha256",
    ).hexdigest()


def _verify_live_evidence(
    encoded: bytes,
    *,
    key: bytes,
    expected_issuer: str,
    expected_key_id: str,
) -> Mapping[str, Any]:
    try:
        if (
            type(encoded) is not bytes
            or type(key) is not bytes
            or len(key) != 32
            or not isinstance(expected_issuer, str)
            or _ATTESTATION_ISSUER_PATTERN.fullmatch(expected_issuer) is None
            or not 3 <= len(expected_issuer) <= 128
            or not isinstance(expected_key_id, str)
            or _ATTESTATION_KEY_ID_PATTERN.fullmatch(expected_key_id) is None
            or not 1 <= len(expected_key_id) <= 64
        ):
            raise ValueError
        envelope = _encoded_json_mapping(encoded)
        if encoded != canonical_json_bytes(envelope):
            raise ValueError
        if set(envelope) != {
            "schema_version",
            "domain",
            "algorithm",
            "issuer",
            "key_id",
            "evidence_sha256",
            "evidence",
            "signature_sha256",
        }:
            raise ValueError
        evidence = envelope["evidence"]
        if not isinstance(evidence, Mapping):
            raise ValueError
        execution = evidence.get("execution")
        if (
            envelope["schema_version"] != 1
            or envelope["domain"] != _LIVE_EVIDENCE_DOMAIN
            or envelope["algorithm"] != _LIVE_EVIDENCE_ALGORITHM
            or envelope["issuer"] != expected_issuer
            or envelope["key_id"] != expected_key_id
            or not isinstance(execution, Mapping)
            or execution.get("mode") != "live"
            or execution.get("test_only") is not False
            or execution.get("composition") != _LIVE_COMPOSITION
            or evidence.get("evidence_kind")
            != "vital_relay_live_apns_provider_acceptance"
            or evidence.get("claim") != "apns_provider_acceptance_only"
            or evidence.get("signed_device_display_open_verified") is not False
            or evidence.get("apple_evidence_correlation_required") is not True
        ):
            raise ValueError
        evidence_digest = envelope["evidence_sha256"]
        signature = envelope["signature_sha256"]
        if (
            not isinstance(evidence_digest, str)
            or _HEX_SHA256_PATTERN.fullmatch(evidence_digest) is None
            or evidence_digest
            != sha256(canonical_json_bytes(evidence)).hexdigest()
            or not isinstance(signature, str)
            or _HEX_SHA256_PATTERN.fullmatch(signature) is None
        ):
            raise ValueError
        unsigned = {
            key_name: value
            for key_name, value in envelope.items()
            if key_name != "signature_sha256"
        }
        expected_signature = _attestation_signature(unsigned, key)
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise LiveEvidenceError("live_evidence_attestation_invalid") from exc
    return envelope


def _encoded_json_mapping(encoded: bytes) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    decoded = json.loads(
        encoded.decode("utf-8"),
        object_pairs_hook=unique_object,
    )
    if not isinstance(decoded, Mapping):
        raise ValueError("JSON root must be an object")
    return decoded


def _write_content_addressed_bytes(
    output_dir: Path,
    payload: bytes,
) -> tuple[Path, str]:
    digest = sha256(payload).hexdigest()
    parent = output_dir / digest[:2]
    target = parent / f"{digest[2:]}.json"
    parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise LiveEvidenceError("evidence_content_address_collision")
        return target, digest

    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=parent,
            prefix=".live-apns-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            os.link(temporary_path, target)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise LiveEvidenceError("evidence_content_address_collision")
        return target, digest
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_output_dir_writable(output_dir: Path) -> None:
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=output_dir,
            prefix=".live-apns-preflight-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(b"preflight")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
    except OSError as exc:
        raise LiveEvidenceError("evidence_output_dir_not_writable") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _load_configuration(
    args: argparse.Namespace,
    environ: Mapping[str, str],
) -> LiveEvidenceConfiguration:
    _reject_environment_transport_overrides(environ)
    if not args.confirm_non_production:
        raise LiveEvidenceError("non_production_confirmation_required")
    apns_enabled = _boolean(
        _required(environ, APNS_ENABLED_ENV),
        APNS_ENABLED_ENV,
    )
    if not apns_enabled:
        raise LiveEvidenceError("apns_must_be_explicitly_enabled")
    try:
        environment = PushEnvironment(
            _required(environ, APNS_ENVIRONMENT_ENV).lower()
        )
    except ValueError as exc:
        raise LiveEvidenceError("apns_environment_invalid") from exc
    if environment is not PushEnvironment.SANDBOX:
        raise LiveEvidenceError("production_apns_evidence_forbidden")

    output_dir = args.output_dir.expanduser()
    if not output_dir.is_absolute():
        raise LiveEvidenceError("evidence_output_dir_must_be_absolute")
    output_dir = output_dir.resolve()
    if output_dir == _PROJECT_ROOT or output_dir.is_relative_to(_PROJECT_ROOT):
        raise LiveEvidenceError("evidence_output_dir_must_be_outside_repository")
    if not output_dir.is_dir():
        raise LiveEvidenceError("evidence_output_dir_unavailable")

    allowlist = _uuid_list(
        _required(environ, NOTIFICATION_ALLOWLIST_ENV),
        NOTIFICATION_ALLOWLIST_ENV,
    )
    timeout_seconds = _positive_float(
        environ.get(APNS_TIMEOUT_ENV),
        default=DEFAULT_APNS_TIMEOUT_SECONDS,
        name=APNS_TIMEOUT_ENV,
    )
    radius_m = _positive_int(
        environ.get(RESPONDER_RADIUS_ENV),
        default=DEFAULT_RESPONDER_RADIUS_M,
        name=RESPONDER_RADIUS_ENV,
    )
    if radius_m > 2_000:
        raise LiveEvidenceError("responder_radius_invalid")
    stale_seconds = _positive_int(
        environ.get(RESPONDER_STALE_ENV),
        default=DEFAULT_RESPONDER_STALE_SECONDS,
        name=RESPONDER_STALE_ENV,
    )
    try:
        scope_id = UUID(_required(environ, SCOPE_ID_ENV))
    except ValueError as exc:
        raise LiveEvidenceError("scope_id_invalid") from exc
    config = LiveEvidenceConfiguration(
        database_url=_required(environ, DATABASE_URL_ENV),
        apns_enabled=apns_enabled,
        non_production_confirmed=args.confirm_non_production,
        scope_id=scope_id,
        incident_id=args.incident_id,
        confirmed_installation_id=args.confirm_installation_id,
        confirmed_responder_id=args.confirm_responder_id,
        output_dir=output_dir,
        team_id=_required(environ, APNS_TEAM_ID_ENV),
        key_id=_required(environ, APNS_KEY_ID_ENV),
        topic=_required(environ, APNS_TOPIC_ENV),
        private_key_path=Path(_required(environ, APNS_PRIVATE_KEY_PATH_ENV)),
        environment=environment,
        responder_allowlist=allowlist,
        token_encryption_key=_required(environ, NOTIFICATION_ENCRYPTION_KEY_ENV),
        session_access_token=_required(environ, SESSION_ACCESS_TOKEN_ENV),
        device_token=_required(environ, DEVICE_TOKEN_ENV),
        timeout_seconds=timeout_seconds,
        responder_radius_m=radius_m,
        responder_stale_seconds=stale_seconds,
        expected_certifi_sha256=_strict_required(
            environ,
            EXPECTED_CERTIFI_SHA256_ENV,
        ),
        attestation_issuer=_strict_required(
            environ,
            ATTESTATION_ISSUER_ENV,
        ),
        attestation_key_id=_strict_required(
            environ,
            ATTESTATION_KEY_ID_ENV,
        ),
        attestation_hmac_key=_decode_attestation_key(
            _strict_required(environ, ATTESTATION_HMAC_KEY_ENV)
        ),
    )
    _validate_live_configuration(config)
    return config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture live sandbox APNs provider-acceptance evidence through the "
            "durable Vital Relay product path."
        )
    )
    parser.add_argument("--incident-id", required=True, type=UUID)
    parser.add_argument("--confirm-installation-id", required=True, type=UUID)
    parser.add_argument("--confirm-responder-id", required=True, type=UUID)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--confirm-non-production",
        required=True,
        action="store_true",
        help="Confirm the installation and recipient are non-production test assets.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = _load_configuration(args, os.environ)
        artifact = run_live_evidence(config)
    except LiveEvidenceError as exc:
        _emit({"status": "failed", "error_code": exc.code}, stream=sys.stderr)
        return 2
    except OSError:
        _emit(
            {"status": "failed", "error_code": "external_io_prerequisite_unavailable"},
            stream=sys.stderr,
        )
        return 2
    except Exception:
        # Never render arbitrary exception text: database drivers and HTTP clients
        # can embed credentials or destination paths in their messages.
        _emit(
            {"status": "failed", "error_code": "live_product_path_failed"},
            stream=sys.stderr,
        )
        return 2

    _emit(
        {
            "status": (
                "provider_accepted" if artifact.provider_accepted else "not_accepted"
            ),
            "artifact_sha256": artifact.sha256,
            "artifact_path": str(artifact.path),
        },
        stream=sys.stdout,
    )
    return 0 if artifact.provider_accepted else 3


def _emit(payload: Mapping[str, Any], *, stream: Any) -> None:
    stream.write(canonical_json_bytes(payload).decode("utf-8") + "\n")


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise LiveEvidenceError(f"missing_{name.lower()}")
    return value


def _strict_required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if value is None or not value or value != value.strip():
        raise LiveEvidenceError(f"missing_or_invalid_{name.lower()}")
    return value


def _decode_attestation_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(
            (value + "=").encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise LiveEvidenceError(
            "live_evidence_attestation_key_invalid"
        ) from exc
    if len(decoded) != 32 or not hmac.compare_digest(value, canonical):
        raise LiveEvidenceError("live_evidence_attestation_key_invalid")
    return decoded


def _boolean(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise LiveEvidenceError(f"invalid_{name.lower()}")


def _uuid_list(value: str, name: str) -> frozenset[UUID]:
    result: set[UUID] = set()
    try:
        for item in value.split(","):
            normalized = item.strip()
            if not normalized:
                raise ValueError
            result.add(UUID(normalized))
    except ValueError as exc:
        raise LiveEvidenceError(f"invalid_{name.lower()}") from exc
    if not result:
        raise LiveEvidenceError(f"invalid_{name.lower()}")
    return frozenset(result)


def _positive_float(value: str | None, *, default: float, name: str) -> float:
    try:
        parsed = default if value is None else float(value)
    except ValueError as exc:
        raise LiveEvidenceError(f"invalid_{name.lower()}") from exc
    if not parsed > 0 or parsed in {float("inf"), float("-inf")}:
        raise LiveEvidenceError(f"invalid_{name.lower()}")
    return parsed


def _positive_int(value: str | None, *, default: int, name: str) -> int:
    try:
        parsed = default if value is None else int(value)
    except ValueError as exc:
        raise LiveEvidenceError(f"invalid_{name.lower()}") from exc
    if parsed <= 0:
        raise LiveEvidenceError(f"invalid_{name.lower()}")
    return parsed


def _identifier_sha256(value: UUID) -> str:
    return sha256(_IDENTIFIER_DOMAIN + str(value).encode("ascii")).hexdigest()


def _text_sha256(value: str) -> str:
    return sha256(
        b"vital-relay-live-apns-evidence-config-v1\0" + value.encode("utf-8")
    ).hexdigest()


def _secret_values(config: LiveEvidenceConfiguration) -> tuple[str, ...]:
    return (
        config.database_url,
        config.session_access_token,
        config.device_token,
        config.token_encryption_key,
        str(config.private_key_path),
        config.team_id,
        config.key_id,
        config.topic,
        base64.urlsafe_b64encode(config.attestation_hmac_key)
        .decode("ascii")
        .rstrip("="),
    )


def _artifact_forbidden_values(
    config: LiveEvidenceConfiguration,
    observed: _ObservedRun,
) -> tuple[str, ...]:
    provider_id = observed.persisted.provider_message_id
    raw_identifiers = (
        config.scope_id,
        config.incident_id,
        config.confirmed_responder_id,
        config.confirmed_installation_id,
        observed.principal.session_id,
        observed.principal.account_id,
        observed.registration.registration_id,
        observed.persisted.invitation_id,
        observed.persisted.notification_id,
    )
    return (
        *_secret_values(config),
        *(str(item) for item in raw_identifiers),
        *((str(provider_id),) if provider_id is not None else ()),
    )


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LiveEvidenceError("evidence_timestamp_not_timezone_aware")
    return value.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
