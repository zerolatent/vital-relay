"""Application boundary for allowlisted responder push notifications."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from vital_relay.application.health_ingestion import Clock
from vital_relay.domain.notifications import (
    NotificationErrorCode,
    NotificationProviderOutcome,
    NotificationProviderRequest,
    NotificationProviderResult,
    NotificationReceiptView,
    PushRegistrationRequest,
    PushRegistrationView,
)


DEFAULT_NOTIFICATION_BATCH_SIZE = 20
DEFAULT_NOTIFICATION_LEASE_SECONDS = 30
DEFAULT_NOTIFICATION_MAX_RETRY_SECONDS = 900


class DeviceTokenCipher(Protocol):
    """Encrypt APNs destination tokens before they enter durable storage."""

    def encrypt(self, token: str) -> bytes:
        """Return authenticated ciphertext without retaining plaintext."""

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt only while constructing one provider submission."""


class NotificationProvider(Protocol):
    """One bounded external provider; implementations must not raise raw secrets."""

    def send(self, request: NotificationProviderRequest) -> NotificationProviderResult:
        """Submit one request and return a privacy-safe normalized outcome."""


class NotificationRepository(Protocol):
    """Registration, receipt, and leased-outbox persistence boundary."""

    def register(
        self,
        responder_id: UUID,
        installation_id: UUID,
        registration: PushRegistrationRequest,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
        authorized_at: datetime,
    ) -> PushRegistrationView:
        """Create, rotate, or reactivate one authenticated registration."""

    def revoke(
        self,
        responder_id: UUID,
        installation_id: UUID,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
        revoked_at: datetime,
    ) -> PushRegistrationView:
        """Idempotently withdraw one authenticated device registration."""

    def get_receipt(
        self,
        responder_id: UUID,
        invitation_id: UUID,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
    ) -> NotificationReceiptView | None:
        """Load only the authenticated responder's invitation receipt."""

    def claim_due(
        self,
        *,
        as_of: datetime,
        lease_until: datetime,
        batch_size: int,
    ) -> tuple[NotificationProviderRequest, ...]:
        """Lease due logical notifications and resolve active destinations."""

    def settle(
        self,
        request: NotificationProviderRequest,
        result: NotificationProviderResult,
        *,
        responded_at: datetime,
        retry_at: datetime | None,
    ) -> NotificationReceiptView | None:
        """Append an attempt and conditionally settle the matching live lease."""


class NotificationAuthorizationError(Exception):
    """A responder is authenticated but not explicitly push-allowlisted."""

    code = "responder_not_notification_allowlisted"


class NotificationNotFoundError(Exception):
    """A responder-scoped registration or notification receipt is absent."""

    def __init__(self, *, code: str, identifier: str) -> None:
        self.code = code
        self.identifier = identifier
        super().__init__(code)

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "identifier": self.identifier}


class NotificationService:
    """Apply authoritative time to responder registration and receipt reads."""

    def __init__(self, repository: NotificationRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    def register(
        self,
        responder_id: UUID,
        installation_id: UUID,
        registration: PushRegistrationRequest,
        *,
        responder_token: str | None = None,
        authenticated_responder_id: UUID | None = None,
    ) -> PushRegistrationView:
        return self._repository.register(
            responder_id,
            installation_id,
            registration,
            responder_token=responder_token,
            authenticated_responder_id=authenticated_responder_id,
            authorized_at=_utc(self._clock.now()),
        )

    def revoke(
        self,
        responder_id: UUID,
        installation_id: UUID,
        *,
        responder_token: str | None = None,
        authenticated_responder_id: UUID | None = None,
    ) -> PushRegistrationView:
        return self._repository.revoke(
            responder_id,
            installation_id,
            responder_token=responder_token,
            authenticated_responder_id=authenticated_responder_id,
            revoked_at=_utc(self._clock.now()),
        )

    def get_receipt(
        self,
        responder_id: UUID,
        invitation_id: UUID,
        *,
        responder_token: str | None = None,
        authenticated_responder_id: UUID | None = None,
    ) -> NotificationReceiptView:
        receipt = self._repository.get_receipt(
            responder_id,
            invitation_id,
            responder_token=responder_token,
            authenticated_responder_id=authenticated_responder_id,
        )
        if receipt is None:
            raise NotificationNotFoundError(
                code="notification_receipt_not_found",
                identifier=str(invitation_id),
            )
        return receipt


class NotificationWorker:
    """Lease, submit, and settle durable notifications in bounded batches."""

    def __init__(
        self,
        repository: NotificationRepository,
        provider: NotificationProvider,
        clock: Clock,
        *,
        batch_size: int = DEFAULT_NOTIFICATION_BATCH_SIZE,
        lease_seconds: int = DEFAULT_NOTIFICATION_LEASE_SECONDS,
        max_retry_seconds: int = DEFAULT_NOTIFICATION_MAX_RETRY_SECONDS,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("notification batch_size must be positive")
        if lease_seconds <= 0:
            raise ValueError("notification lease_seconds must be positive")
        if max_retry_seconds < 900:
            raise ValueError("notification max_retry_seconds must be at least 900")
        self._repository = repository
        self._provider = provider
        self._clock = clock
        self._batch_size = batch_size
        self._lease_window = timedelta(seconds=lease_seconds)
        self._max_retry_seconds = max_retry_seconds

    def process_due(self) -> int:
        claimed_at = _utc(self._clock.now())
        requests = self._repository.claim_due(
            as_of=claimed_at,
            lease_until=claimed_at + self._lease_window,
            batch_size=self._batch_size,
        )
        processed = 0
        for request in requests:
            try:
                result = self._provider.send(request)
            except Exception:
                # The provider boundary is required to normalize its failures.  A
                # surprise exception is conservatively ambiguous: retrying could
                # display a second alert if the request reached APNs.
                result = NotificationProviderResult(
                    outcome=NotificationProviderOutcome.UNKNOWN,
                    error_code=NotificationErrorCode.PROVIDER_OUTCOME_UNKNOWN,
                )
            if (
                result.outcome
                is NotificationProviderOutcome.PROVIDER_ACCEPTED
                and result.provider_message_id != request.notification_id
            ):
                # Provider results are untrusted adapter output. A mismatched
                # identifier is ambiguous and must never enter a durable receipt.
                result = NotificationProviderResult(
                    outcome=NotificationProviderOutcome.UNKNOWN,
                    error_code=NotificationErrorCode.PROVIDER_RESPONSE_INVALID,
                )
            responded_at = _utc(self._clock.now())
            retry_at = None
            if result.outcome is NotificationProviderOutcome.TRANSIENT_FAILURE:
                retry_seconds = (
                    900
                    if result.error_code
                    is NotificationErrorCode.PROVIDER_DELAYED_RETRY
                    else min(
                        2 ** min(request.attempt_number, 20),
                        self._max_retry_seconds,
                    )
                )
                retry_at = responded_at + timedelta(seconds=retry_seconds)
            self._repository.settle(
                request,
                result,
                responded_at=responded_at,
                retry_at=retry_at,
            )
            processed += 1
        return processed


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("notification clock must return a timezone-aware timestamp")
    return value.astimezone(UTC)
