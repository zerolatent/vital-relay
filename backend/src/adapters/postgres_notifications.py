"""PostgreSQL registration and durable outbox adapter for responder APNs."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from secrets import compare_digest
from typing import Final
from uuid import UUID, uuid4, uuid5

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr, ValidationError
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.application.dispatch_service import ResponderAuthenticationError
from vital_relay.application.notification_service import (
    DeviceTokenCipher,
    NotificationAuthorizationError,
    NotificationNotFoundError,
)
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.dispatch import InvitationStatus
from vital_relay.domain.incidents import IncidentState
from vital_relay.domain.notifications import (
    NotificationErrorCode,
    NotificationDeliveryStatus,
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
from vital_relay.persistence.database import require_active_scope
from vital_relay.persistence.models import (
    NotificationDeliveryAttemptRow,
    NotificationOutboxRow,
    IncidentRow,
    ResponderInvitationRow,
    ResponderPushRegistrationRow,
    ResponderRow,
)


_ID_NAMESPACE: Final = UUID("fabd65cd-f0b6-48c0-8c70-3a854f1c9540")
_CHANNEL: Final = "apns"
_TEMPLATE: Final = "responder_invitation_v1"
_INVALID_DESTINATION_CODES: Final = frozenset(
    {
        NotificationErrorCode.BAD_DEVICE_TOKEN,
        NotificationErrorCode.DEVICE_TOKEN_NOT_FOR_TOPIC,
        NotificationErrorCode.DEVICE_TOKEN_UNREGISTERED,
    }
)


class DeviceTokenCipherError(RuntimeError):
    """Token ciphertext cannot be safely encrypted or decrypted."""


class FernetDeviceTokenCipher(DeviceTokenCipher):
    """Authenticated encryption for APNs tokens stored in PostgreSQL."""

    def __init__(self, key: SecretStr | str) -> None:
        raw_key = key.get_secret_value() if isinstance(key, SecretStr) else key
        try:
            self._fernet = Fernet(raw_key.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise ValueError("device token encryption key is invalid") from exc

    def encrypt(self, token: str) -> bytes:
        try:
            return self._fernet.encrypt(token.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise DeviceTokenCipherError("device token encryption failed") from exc

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("ascii")
        except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
            raise DeviceTokenCipherError("device token decryption failed") from exc


class PostgresNotificationRepository:
    """Scope-bound registration, receipt, and leased-outbox repository."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
        *,
        responder_allowlist: frozenset[UUID],
        environment: PushEnvironment,
        topic: str,
        token_cipher: DeviceTokenCipher,
    ) -> None:
        normalized_topic = topic.strip()
        if not normalized_topic or len(normalized_topic) > 255:
            raise ValueError("APNs topic must contain 1 to 255 characters")
        self._sessions = sessions
        self.scope_id = scope_id
        self._responder_allowlist = responder_allowlist
        self._environment = environment
        self._topic = normalized_topic
        self._token_cipher = token_cipher

    def register(
        self,
        responder_id: UUID,
        installation_id: UUID,
        registration: PushRegistrationRequest,
        *,
        responder_token: str | None,
        authorized_at: datetime,
        authenticated_responder_id: UUID | None = None,
    ) -> PushRegistrationView:
        occurred_at = _utc(authorized_at)
        plaintext_token = registration.device_token.get_secret_value()
        token_hash = sha256(plaintext_token.encode("ascii")).hexdigest()
        ciphertext = self._token_cipher.encrypt(plaintext_token)

        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            responder = self._authenticate_and_authorize(
                session,
                responder_id=responder_id,
                responder_token=responder_token,
                authenticated_responder_id=authenticated_responder_id,
            )
            effective_at = max(occurred_at, responder.updated_at)
            existing = session.scalar(
                select(ResponderPushRegistrationRow)
                .where(
                    ResponderPushRegistrationRow.scope_id == self.scope_id,
                    ResponderPushRegistrationRow.installation_id == installation_id,
                )
                .with_for_update()
            )
            if existing is not None and existing.responder_id != responder_id:
                raise NotificationAuthorizationError

            destination_owner = session.scalar(
                select(ResponderPushRegistrationRow)
                .where(
                    ResponderPushRegistrationRow.scope_id == self.scope_id,
                    ResponderPushRegistrationRow.environment
                    == self._environment.value,
                    ResponderPushRegistrationRow.device_token_sha256 == token_hash,
                    ResponderPushRegistrationRow.status
                    == PushRegistrationStatus.ACTIVE.value,
                )
                .with_for_update()
            )
            if (
                destination_owner is not None
                and destination_owner.responder_id != responder_id
            ):
                raise NotificationAuthorizationError

            if (
                existing is not None
                and existing.status == PushRegistrationStatus.ACTIVE.value
                and existing.environment == self._environment.value
                and existing.device_token_sha256 == token_hash
                and existing.authorized_at >= responder.updated_at
            ):
                return _registration_view(existing)

            active_rows = session.scalars(
                select(ResponderPushRegistrationRow)
                .where(
                    ResponderPushRegistrationRow.scope_id == self.scope_id,
                    ResponderPushRegistrationRow.responder_id == responder_id,
                    ResponderPushRegistrationRow.status
                    == PushRegistrationStatus.ACTIVE.value,
                )
                .with_for_update()
            ).all()
            for active in active_rows:
                if (
                    existing is not None
                    and active.registration_id == existing.registration_id
                ):
                    continue
                _revoke_registration(active, effective_at)
            # Flush revocations before an INSERT/UPDATE encounters either partial
            # uniqueness constraint for the new active destination.
            session.flush()

            if existing is None:
                existing = ResponderPushRegistrationRow(
                    scope_id=self.scope_id,
                    registration_id=_stable_id(
                        self.scope_id,
                        "push-registration",
                        installation_id,
                    ),
                    installation_id=installation_id,
                    responder_id=responder_id,
                    platform=_CHANNEL,
                    environment=self._environment.value,
                    device_token_ciphertext=ciphertext,
                    device_token_sha256=token_hash,
                    status=PushRegistrationStatus.ACTIVE.value,
                    authorized_at=effective_at,
                    updated_at=effective_at,
                    revoked_at=None,
                    simulated=False,
                )
                session.add(existing)
            else:
                existing.platform = _CHANNEL
                existing.environment = self._environment.value
                existing.device_token_ciphertext = ciphertext
                existing.device_token_sha256 = token_hash
                existing.status = PushRegistrationStatus.ACTIVE.value
                existing.authorized_at = effective_at
                existing.updated_at = effective_at
                existing.revoked_at = None
            session.flush()
            return _registration_view(existing)

    def revoke(
        self,
        responder_id: UUID,
        installation_id: UUID,
        *,
        responder_token: str | None,
        revoked_at: datetime,
        authenticated_responder_id: UUID | None = None,
    ) -> PushRegistrationView:
        occurred_at = _utc(revoked_at)
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            self._authenticate_and_authorize(
                session,
                responder_id=responder_id,
                responder_token=responder_token,
                authenticated_responder_id=authenticated_responder_id,
            )
            registration = session.scalar(
                select(ResponderPushRegistrationRow)
                .where(
                    ResponderPushRegistrationRow.scope_id == self.scope_id,
                    ResponderPushRegistrationRow.installation_id == installation_id,
                    ResponderPushRegistrationRow.responder_id == responder_id,
                )
                .with_for_update()
            )
            if registration is None:
                raise NotificationNotFoundError(
                    code="push_registration_not_found",
                    identifier=str(installation_id),
                )
            if registration.status == PushRegistrationStatus.ACTIVE.value:
                _revoke_registration(registration, occurred_at)
                session.flush()
            return _registration_view(registration)

    def get_receipt(
        self,
        responder_id: UUID,
        invitation_id: UUID,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None = None,
    ) -> NotificationReceiptView | None:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            self._authenticate_and_authorize(
                session,
                responder_id=responder_id,
                responder_token=responder_token,
                authenticated_responder_id=authenticated_responder_id,
            )
            row = session.scalar(
                select(NotificationOutboxRow).where(
                    NotificationOutboxRow.scope_id == self.scope_id,
                    NotificationOutboxRow.invitation_id == invitation_id,
                    NotificationOutboxRow.responder_id == responder_id,
                )
            )
            return _receipt_view(row) if row is not None else None

    def enqueue_invitation(
        self,
        session: Session,
        invitation: ResponderInvitationRow,
        occurred_at: datetime,
    ) -> None:
        """Create the logical outbox row inside the invitation transaction."""

        if invitation.scope_id != self.scope_id:
            raise ValueError("notification enqueuer scope does not match invitation")
        created_at = _utc(occurred_at)
        notification_id = _stable_id(
            self.scope_id,
            "responder-invitation-notification",
            invitation.invitation_id,
        )
        payload = ResponderInvitationNotificationPayload(
            schema_version=SCHEMA_VERSION,
            kind="responder_invitation",
            incident_id=invitation.incident_id,
            invitation_id=invitation.invitation_id,
        )
        existing = session.get(
            NotificationOutboxRow,
            (self.scope_id, notification_id),
        )
        if existing is not None:
            if (
                existing.invitation_id != invitation.invitation_id
                or existing.incident_id != invitation.incident_id
                or existing.responder_id != invitation.responder_id
                or existing.channel != _CHANNEL
                or existing.template != _TEMPLATE
                or existing.payload != payload.model_dump(mode="json")
            ):
                raise ValueError("notification ID conflicts with stored invitation")
            return
        session.add(
            NotificationOutboxRow(
                scope_id=self.scope_id,
                notification_id=notification_id,
                invitation_id=invitation.invitation_id,
                incident_id=invitation.incident_id,
                responder_id=invitation.responder_id,
                channel=_CHANNEL,
                template=_TEMPLATE,
                status=NotificationDeliveryStatus.PENDING.value,
                attempt_count=0,
                payload=payload.model_dump(mode="json"),
                next_attempt_at=created_at,
                lease_token=None,
                lease_until=None,
                provider_message_id=None,
                last_error_code=None,
                created_at=created_at,
                updated_at=created_at,
                finalized_at=None,
                simulated=False,
            )
        )

    def claim_due(
        self,
        *,
        as_of: datetime,
        lease_until: datetime,
        batch_size: int,
    ) -> tuple[NotificationProviderRequest, ...]:
        claimed_at = _utc(as_of)
        normalized_lease_until = _utc(lease_until)
        if normalized_lease_until <= claimed_at:
            raise ValueError("notification lease_until must follow as_of")
        if batch_size <= 0:
            raise ValueError("notification batch_size must be positive")

        requests: list[NotificationProviderRequest] = []
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            expired_rows = session.scalars(
                select(NotificationOutboxRow)
                .where(
                    NotificationOutboxRow.scope_id == self.scope_id,
                    NotificationOutboxRow.status
                    == NotificationDeliveryStatus.PENDING.value,
                    NotificationOutboxRow.lease_token.is_not(None),
                    NotificationOutboxRow.lease_until <= claimed_at,
                )
                .order_by(
                    NotificationOutboxRow.lease_until,
                    NotificationOutboxRow.notification_id,
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            ).all()
            for row in expired_rows:
                # A crashed worker may have submitted to APNs before it could
                # settle. Retrying this lease could display a duplicate alert,
                # so preserve the ambiguity as a terminal audited outcome.
                session.add(
                    NotificationDeliveryAttemptRow(
                        scope_id=self.scope_id,
                        attempt_id=_stable_id(
                            self.scope_id,
                            "notification-attempt",
                            f"{row.notification_id}:{row.attempt_count}",
                        ),
                        notification_id=row.notification_id,
                        invitation_id=row.invitation_id,
                        attempt_number=row.attempt_count,
                        outcome=NotificationProviderOutcome.UNKNOWN.value,
                        provider_message_id=None,
                        error_code=(
                            NotificationErrorCode.PROVIDER_OUTCOME_UNKNOWN.value
                        ),
                        requested_at=row.updated_at,
                        responded_at=max(claimed_at, row.updated_at),
                        simulated=False,
                    )
                )
                row.status = NotificationDeliveryStatus.UNKNOWN.value
                row.updated_at = max(claimed_at, row.updated_at)
                row.finalized_at = row.updated_at
                row.last_error_code = (
                    NotificationErrorCode.PROVIDER_OUTCOME_UNKNOWN.value
                )
                row.lease_token = None
                row.lease_until = None

            rows = session.scalars(
                select(NotificationOutboxRow)
                .where(
                    NotificationOutboxRow.scope_id == self.scope_id,
                    NotificationOutboxRow.status
                    == NotificationDeliveryStatus.PENDING.value,
                    NotificationOutboxRow.next_attempt_at <= claimed_at,
                    NotificationOutboxRow.lease_token.is_(None),
                )
                .order_by(
                    NotificationOutboxRow.next_attempt_at,
                    NotificationOutboxRow.created_at,
                    NotificationOutboxRow.notification_id,
                )
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                invitation = session.get(
                    ResponderInvitationRow,
                    (self.scope_id, row.invitation_id),
                )
                incident = session.get(
                    IncidentRow,
                    (self.scope_id, row.incident_id),
                )
                if (
                    invitation is None
                    or invitation.status != InvitationStatus.PENDING.value
                ):
                    _finalize_unavailable(
                        row,
                        finalized_at=claimed_at,
                        error_code=NotificationErrorCode.INVITATION_NOT_PENDING,
                    )
                    continue
                if (
                    incident is None
                    or incident.current_state != IncidentState.ESCALATING.value
                ):
                    _finalize_unavailable(
                        row,
                        finalized_at=claimed_at,
                        error_code=NotificationErrorCode.INCIDENT_NOT_ESCALATING,
                    )
                    continue
                if row.responder_id not in self._responder_allowlist:
                    _finalize_unavailable(
                        row,
                        finalized_at=claimed_at,
                        error_code=(
                            NotificationErrorCode.RESPONDER_NOT_NOTIFICATION_ALLOWLISTED
                        ),
                    )
                    continue
                registration = session.scalar(
                    select(ResponderPushRegistrationRow)
                    .join(
                        ResponderRow,
                        and_(
                            ResponderRow.scope_id
                            == ResponderPushRegistrationRow.scope_id,
                            ResponderRow.responder_id
                            == ResponderPushRegistrationRow.responder_id,
                        ),
                    )
                    .where(
                        ResponderPushRegistrationRow.scope_id == self.scope_id,
                        ResponderPushRegistrationRow.responder_id == row.responder_id,
                        ResponderPushRegistrationRow.status
                        == PushRegistrationStatus.ACTIVE.value,
                        ResponderPushRegistrationRow.environment
                        == self._environment.value,
                        ResponderRow.status == "active",
                        ResponderPushRegistrationRow.authorized_at
                        >= ResponderRow.updated_at,
                    )
                    .with_for_update()
                )
                if registration is None:
                    _finalize_unavailable(
                        row,
                        finalized_at=claimed_at,
                        error_code=(
                            NotificationErrorCode.ACTIVE_PUSH_REGISTRATION_UNAVAILABLE
                        ),
                    )
                    continue
                try:
                    token = self._token_cipher.decrypt(
                        registration.device_token_ciphertext
                    )
                    # Re-apply the write contract after decryption so corrupted or
                    # wrongly keyed ciphertext can never reach a provider URL.
                    validated_token = PushRegistrationRequest(
                        schema_version=SCHEMA_VERSION,
                        platform="apns",
                        device_token=token,
                    ).device_token
                except (DeviceTokenCipherError, ValidationError):
                    _finalize_unavailable(
                        row,
                        finalized_at=claimed_at,
                        error_code=NotificationErrorCode.DEVICE_TOKEN_UNREADABLE,
                    )
                    continue

                payload = ResponderInvitationNotificationPayload.model_validate(
                    row.payload
                )
                row.attempt_count += 1
                row.lease_token = uuid4()
                row.lease_until = normalized_lease_until
                row.updated_at = claimed_at
                requests.append(
                    NotificationProviderRequest(
                        notification_id=row.notification_id,
                        invitation_id=row.invitation_id,
                        incident_id=row.incident_id,
                        responder_id=row.responder_id,
                        attempt_number=row.attempt_count,
                        requested_at=claimed_at,
                        device_token=validated_token,
                        environment=self._environment,
                        topic=self._topic,
                        payload=payload,
                    )
                )
            session.flush()
        return tuple(requests)

    def settle(
        self,
        request: NotificationProviderRequest,
        result: NotificationProviderResult,
        *,
        responded_at: datetime,
        retry_at: datetime | None,
    ) -> NotificationReceiptView | None:
        response_time = max(_utc(responded_at), request.requested_at)
        normalized_retry_at = _utc(retry_at) if retry_at is not None else None
        if (
            result.outcome is NotificationProviderOutcome.PROVIDER_ACCEPTED
            and result.provider_message_id != request.notification_id
        ):
            result = NotificationProviderResult(
                outcome=NotificationProviderOutcome.UNKNOWN,
                error_code=NotificationErrorCode.PROVIDER_RESPONSE_INVALID,
            )
        transient = result.outcome is NotificationProviderOutcome.TRANSIENT_FAILURE
        if transient:
            if normalized_retry_at is None or normalized_retry_at <= response_time:
                raise ValueError("transient provider result requires a future retry_at")
        elif normalized_retry_at is not None:
            raise ValueError("terminal provider result cannot include retry_at")

        with self._sessions.begin() as session:
            row = session.scalar(
                select(NotificationOutboxRow)
                .where(
                    NotificationOutboxRow.scope_id == self.scope_id,
                    NotificationOutboxRow.notification_id
                    == request.notification_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if (
                row.status != NotificationDeliveryStatus.PENDING.value
                or row.lease_token is None
                or row.attempt_count != request.attempt_number
            ):
                return None
            if (
                row.invitation_id != request.invitation_id
                or row.incident_id != request.incident_id
                or row.responder_id != request.responder_id
            ):
                return None

            session.add(
                NotificationDeliveryAttemptRow(
                    scope_id=self.scope_id,
                    attempt_id=_stable_id(
                        self.scope_id,
                        "notification-attempt",
                        f"{row.notification_id}:{request.attempt_number}",
                    ),
                    notification_id=row.notification_id,
                    invitation_id=row.invitation_id,
                    attempt_number=request.attempt_number,
                    outcome=result.outcome.value,
                    provider_message_id=result.provider_message_id,
                    error_code=(
                        result.error_code.value
                        if result.error_code is not None
                        else None
                    ),
                    requested_at=request.requested_at,
                    responded_at=response_time,
                    simulated=False,
                )
            )
            row.updated_at = response_time
            row.lease_token = None
            row.lease_until = None
            if transient:
                row.next_attempt_at = normalized_retry_at
            elif result.outcome is NotificationProviderOutcome.PROVIDER_ACCEPTED:
                row.status = NotificationDeliveryStatus.PROVIDER_ACCEPTED.value
                row.provider_message_id = result.provider_message_id
                row.finalized_at = response_time
            elif result.outcome is NotificationProviderOutcome.PERMANENT_FAILURE:
                row.status = NotificationDeliveryStatus.PERMANENT_FAILED.value
                row.last_error_code = (
                    result.error_code.value
                    if result.error_code is not None
                    else None
                )
                row.finalized_at = response_time
                if result.error_code in _INVALID_DESTINATION_CODES:
                    registration = session.scalar(
                        select(ResponderPushRegistrationRow)
                        .where(
                            ResponderPushRegistrationRow.scope_id == self.scope_id,
                            ResponderPushRegistrationRow.responder_id
                            == row.responder_id,
                            ResponderPushRegistrationRow.status
                            == PushRegistrationStatus.ACTIVE.value,
                        )
                        .with_for_update()
                    )
                    if registration is not None:
                        _revoke_registration(registration, response_time)
            else:
                row.status = NotificationDeliveryStatus.UNKNOWN.value
                row.last_error_code = (
                    result.error_code.value
                    if result.error_code is not None
                    else None
                )
                row.finalized_at = response_time
            session.flush()
            return _receipt_view(row)

    def _authenticate_and_authorize(
        self,
        session: Session,
        *,
        responder_id: UUID,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
    ) -> ResponderRow:
        responder = session.get(ResponderRow, (self.scope_id, responder_id))
        if responder is None or responder.status != "active":
            raise ResponderAuthenticationError
        if authenticated_responder_id is not None:
            if (
                authenticated_responder_id != responder_id
                or responder_token is not None
            ):
                raise ResponderAuthenticationError
        else:
            if responder_token is None:
                raise ResponderAuthenticationError
            presented_hash = sha256(responder_token.encode("utf-8")).hexdigest()
            if not compare_digest(presented_hash, responder.access_token_hash):
                raise ResponderAuthenticationError
        if responder_id not in self._responder_allowlist:
            raise NotificationAuthorizationError
        return responder


def _registration_view(row: ResponderPushRegistrationRow) -> PushRegistrationView:
    return PushRegistrationView(
        schema_version=SCHEMA_VERSION,
        registration_id=row.registration_id,
        installation_id=row.installation_id,
        responder_id=row.responder_id,
        platform="apns",
        status=PushRegistrationStatus(row.status),
        environment=PushEnvironment(row.environment),
        updated_at=row.updated_at,
    )


def _receipt_view(row: NotificationOutboxRow) -> NotificationReceiptView:
    return NotificationReceiptView(
        schema_version=SCHEMA_VERSION,
        notification_id=row.notification_id,
        invitation_id=row.invitation_id,
        incident_id=row.incident_id,
        responder_id=row.responder_id,
        channel="apns",
        template="responder_invitation_v1",
        status=NotificationDeliveryStatus(row.status),
        attempt_count=row.attempt_count,
        provider_message_id=row.provider_message_id,
        last_error_code=row.last_error_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finalized_at=row.finalized_at,
    )


def _revoke_registration(
    row: ResponderPushRegistrationRow,
    revoked_at: datetime,
) -> None:
    effective_at = max(_utc(revoked_at), row.authorized_at, row.updated_at)
    row.status = PushRegistrationStatus.REVOKED.value
    row.updated_at = effective_at
    row.revoked_at = effective_at


def _finalize_unavailable(
    row: NotificationOutboxRow,
    *,
    finalized_at: datetime,
    error_code: NotificationErrorCode,
) -> None:
    row.status = NotificationDeliveryStatus.UNAVAILABLE.value
    row.updated_at = finalized_at
    row.finalized_at = finalized_at
    row.provider_message_id = None
    row.last_error_code = error_code.value
    row.lease_token = None
    row.lease_until = None


def _stable_id(scope_id: UUID, namespace: str, identifier: object) -> UUID:
    return uuid5(_ID_NAMESPACE, f"{scope_id}:{namespace}:{identifier}")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("notification timestamps must be timezone-aware")
    return value.astimezone(UTC)
