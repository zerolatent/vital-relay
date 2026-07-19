"""Real HTTP/2 Apple Push Notification service provider adapter.

The adapter deliberately returns only bounded result codes.  It never logs or
returns a device token, provider response body, responder credential, or APNs
authorization token.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from vital_relay.domain.notifications import (
    NotificationErrorCode,
    NotificationProviderOutcome,
    NotificationProviderRequest,
    NotificationProviderResult,
    PushEnvironment,
)


APNS_SANDBOX_HOST: Final = "api.sandbox.push.apple.com"
APNS_PRODUCTION_HOST: Final = "api.push.apple.com"
APNS_MAX_PAYLOAD_BYTES: Final = 4_096
_JWT_REFRESH_SECONDS: Final = 50 * 60
_IDENTIFIER_PATTERN: Final = re.compile(r"[A-Z0-9]{10}")
_TOPIC_PATTERN: Final = re.compile(
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)
_DEVICE_TOKEN_PATTERN: Final = re.compile(r"[0-9a-f]{32,512}")
_APNS_LOG_URL_PATTERN: Final = re.compile(
    r"(https://api(?:\.sandbox)?\.push\.apple\.com/3/device/)"
    r"[0-9a-fA-F]{32,512}"
)


class _APNsURLRedactionFilter(logging.Filter):
    """Redact only APNs destination paths while preserving other HTTPX logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_apns_url(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_log_argument(item) for item in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: _redact_log_argument(value)
                for key, value in record.args.items()
            }
        return True


_APNS_URL_REDACTION_FILTER: Final = _APNsURLRedactionFilter()


class APNsConfigurationError(ValueError):
    """The provider credential or server-owned APNs setting is invalid."""


class APNsNotificationProvider:
    """Submit privacy-minimal responder alerts to APNs over HTTP/2."""

    def __init__(
        self,
        *,
        team_id: str,
        key_id: str,
        private_key_pem: bytes,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        epoch_seconds: Callable[[], float] = time.time,
    ) -> None:
        self._team_id = _validated_provider_identifier(team_id, "team_id")
        self._key_id = _validated_provider_identifier(key_id, "key_id")
        if timeout_seconds <= 0:
            raise APNsConfigurationError("timeout_seconds must be positive")
        try:
            private_key = serialization.load_pem_private_key(
                private_key_pem,
                password=None,
            )
        except (TypeError, ValueError) as exc:
            raise APNsConfigurationError("APNs signing key is invalid") from exc
        if not isinstance(private_key, ec.EllipticCurvePrivateKey) or not isinstance(
            private_key.curve,
            ec.SECP256R1,
        ):
            raise APNsConfigurationError("APNs signing key must be an EC P-256 key")

        self._private_key = private_key
        self._epoch_seconds = epoch_seconds
        self._jwt_lock = threading.Lock()
        self._cached_jwt: str | None = None
        self._cached_jwt_issued_at: int | None = None
        self._client = httpx.Client(
            http2=True,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )
        # HTTPX logs full request URLs at INFO. APNs requires the device token in
        # the path, so install a narrowly targeted filter rather than disabling
        # unrelated HTTP client logs for the process.
        logging.getLogger("httpx").addFilter(_APNS_URL_REDACTION_FILTER)

    @classmethod
    def from_key_file(
        cls,
        *,
        team_id: str,
        key_id: str,
        private_key_path: str | Path,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        epoch_seconds: Callable[[], float] = time.time,
    ) -> APNsNotificationProvider:
        try:
            private_key_pem = Path(private_key_path).read_bytes()
        except OSError as exc:
            raise APNsConfigurationError("APNs signing key is unavailable") from exc
        return cls(
            team_id=team_id,
            key_id=key_id,
            private_key_pem=private_key_pem,
            timeout_seconds=timeout_seconds,
            transport=transport,
            epoch_seconds=epoch_seconds,
        )

    def send(self, request: NotificationProviderRequest) -> NotificationProviderResult:
        topic = request.topic.strip()
        if not _TOPIC_PATTERN.fullmatch(topic):
            return _permanent(NotificationErrorCode.INVALID_APNS_TOPIC)

        device_token = request.device_token.get_secret_value().strip().lower()
        if (
            len(device_token) % 2 != 0
            or _DEVICE_TOKEN_PATTERN.fullmatch(device_token) is None
        ):
            return _permanent(NotificationErrorCode.INVALID_DEVICE_TOKEN)

        payload = _notification_payload(request)
        encoded_payload = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded_payload) > APNS_MAX_PAYLOAD_BYTES:
            return _permanent(NotificationErrorCode.PAYLOAD_TOO_LARGE)

        host = (
            APNS_SANDBOX_HOST
            if request.environment is PushEnvironment.SANDBOX
            else APNS_PRODUCTION_HOST
        )
        headers = {
            "authorization": f"bearer {self._authorization_token()}",
            "apns-topic": topic,
            "apns-push-type": "alert",
            "apns-priority": "10",
            # Do not retain an emergency invitation for later stale delivery.
            "apns-expiration": "0",
            # Both identifiers are stable across an explicit safe retry.
            "apns-id": str(request.notification_id),
            "apns-collapse-id": str(request.invitation_id),
            "content-type": "application/json",
        }

        url = f"https://{host}/3/device/{device_token}"
        response = self._post(url, encoded_payload, headers)
        if isinstance(response, NotificationProviderResult):
            return response

        reason = _apns_reason(response)
        if response.status_code == 403 and reason == "ExpiredProviderToken":
            # APNs explicitly rejected this request, so one submission with a
            # freshly generated provider token cannot duplicate a visible alert.
            self._invalidate_authorization_token()
            headers["authorization"] = f"bearer {self._authorization_token()}"
            response = self._post(url, encoded_payload, headers)
            if isinstance(response, NotificationProviderResult):
                return response
            reason = _apns_reason(response)

        if response.status_code == 200:
            provider_message_id = _matching_apns_id(
                response.headers.get("apns-id"),
                expected=request.notification_id,
            )
            if provider_message_id is None:
                return _unknown(NotificationErrorCode.PROVIDER_RESPONSE_INVALID)
            return NotificationProviderResult(
                outcome=NotificationProviderOutcome.PROVIDER_ACCEPTED,
                provider_message_id=provider_message_id,
            )

        if response.status_code == 429:
            return _transient(NotificationErrorCode.PROVIDER_RATE_LIMITED)
        if response.status_code >= 500:
            return _transient(NotificationErrorCode.PROVIDER_DELAYED_RETRY)
        if reason == "IdleTimeout":
            return _transient(NotificationErrorCode.PROVIDER_UNAVAILABLE)
        if response.status_code == 410 or reason == "Unregistered":
            return _permanent(NotificationErrorCode.DEVICE_TOKEN_UNREGISTERED)
        return _permanent(_bounded_rejection_code(reason))

    def close(self) -> None:
        self._client.close()

    def _post(
        self,
        url: str,
        encoded_payload: bytes,
        headers: dict[str, str],
    ) -> httpx.Response | NotificationProviderResult:
        try:
            return self._client.post(
                url,
                content=encoded_payload,
                headers=headers,
            )
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            # These failures happen before a response can be accepted and are
            # safe for the durable worker's bounded retry policy.
            return _transient(NotificationErrorCode.PROVIDER_UNAVAILABLE)
        except httpx.TimeoutException:
            # A write/read timeout can occur after APNs accepted the request.
            # Retrying would risk a duplicate visible alert.
            return _unknown(NotificationErrorCode.PROVIDER_OUTCOME_UNKNOWN)
        except httpx.TransportError:
            return _unknown(NotificationErrorCode.PROVIDER_OUTCOME_UNKNOWN)

    def _invalidate_authorization_token(self) -> None:
        with self._jwt_lock:
            self._cached_jwt = None
            self._cached_jwt_issued_at = None

    def _authorization_token(self) -> str:
        now = int(self._epoch_seconds())
        with self._jwt_lock:
            if (
                self._cached_jwt is not None
                and self._cached_jwt_issued_at is not None
                and 0 <= now - self._cached_jwt_issued_at < _JWT_REFRESH_SECONDS
            ):
                return self._cached_jwt

            encoded_header = _base64url(
                json.dumps(
                    {"alg": "ES256", "kid": self._key_id},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
            encoded_claims = _base64url(
                json.dumps(
                    {"iat": now, "iss": self._team_id},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
            signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
            der_signature = self._private_key.sign(
                signing_input,
                ec.ECDSA(hashes.SHA256()),
            )
            r, s = decode_dss_signature(der_signature)
            raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
            token = f"{encoded_header}.{encoded_claims}.{_base64url(raw_signature)}"
            self._cached_jwt = token
            self._cached_jwt_issued_at = now
            return token


def _notification_payload(request: NotificationProviderRequest) -> dict[str, object]:
    return {
        "aps": {
            "alert": {
                "title": "Vital Relay responder request",
                "body": "Open Vital Relay to review a responder invitation.",
            },
            "category": "RESPONDER_INVITATION",
            "sound": "default",
            "thread-id": str(request.incident_id),
        },
        "vital_relay": request.payload.model_dump(mode="json"),
    }


def _validated_provider_identifier(value: str, name: str) -> str:
    normalized = value.strip().upper()
    if _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        raise APNsConfigurationError(f"{name} must be a 10-character Apple identifier")
    return normalized


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _apns_reason(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    reason = body.get("reason")
    return reason if isinstance(reason, str) else None


def _bounded_rejection_code(reason: str | None) -> NotificationErrorCode:
    return {
        "BadDeviceToken": NotificationErrorCode.BAD_DEVICE_TOKEN,
        "DeviceTokenNotForTopic": (
            NotificationErrorCode.DEVICE_TOKEN_NOT_FOR_TOPIC
        ),
        "BadTopic": NotificationErrorCode.BAD_APNS_TOPIC,
        "MissingTopic": NotificationErrorCode.MISSING_APNS_TOPIC,
        "PayloadEmpty": NotificationErrorCode.PAYLOAD_EMPTY,
        "PayloadTooLarge": NotificationErrorCode.PAYLOAD_TOO_LARGE,
        "ExpiredProviderToken": (
            NotificationErrorCode.PROVIDER_AUTHENTICATION_FAILED
        ),
        "InvalidProviderToken": (
            NotificationErrorCode.PROVIDER_AUTHENTICATION_FAILED
        ),
        "MissingProviderToken": (
            NotificationErrorCode.PROVIDER_AUTHENTICATION_FAILED
        ),
    }.get(reason, NotificationErrorCode.PROVIDER_REJECTED)


def _matching_apns_id(value: str | None, *, expected: UUID) -> UUID | None:
    if value is None:
        return None
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError):
        return None
    if parsed != expected or value.lower() != str(parsed):
        return None
    return parsed


def _transient(code: NotificationErrorCode) -> NotificationProviderResult:
    return NotificationProviderResult(
        outcome=NotificationProviderOutcome.TRANSIENT_FAILURE,
        error_code=code,
    )


def _permanent(code: NotificationErrorCode) -> NotificationProviderResult:
    return NotificationProviderResult(
        outcome=NotificationProviderOutcome.PERMANENT_FAILURE,
        error_code=code,
    )


def _unknown(code: NotificationErrorCode) -> NotificationProviderResult:
    return NotificationProviderResult(
        outcome=NotificationProviderOutcome.UNKNOWN,
        error_code=code,
    )


def _redact_log_argument(value: object) -> object:
    text = str(value)
    return _redact_apns_url(text) if _APNS_LOG_URL_PATTERN.search(text) else value


def _redact_apns_url(value: str) -> str:
    return _APNS_LOG_URL_PATTERN.sub(r"\1<redacted>", value)
