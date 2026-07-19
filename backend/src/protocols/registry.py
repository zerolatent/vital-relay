"""Fail-closed loader for locally packaged, fixed first-aid protocols."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from vital_relay.domain.incidents import IncidentKind
from vital_relay.domain.protocols import FixedFirstAidProtocol


class ProtocolContentError(RuntimeError):
    """Base error for unavailable, unmapped, or invalid fixed content."""


class ProtocolMappingError(ProtocolContentError):
    """No registered fixed protocol matches the requested emergency identity."""


class ProtocolIntegrityError(ProtocolContentError):
    """Registered content is missing, modified, or contract-invalid."""


@dataclass(frozen=True, slots=True)
class _ProtocolRegistration:
    incident_kind: IncidentKind
    protocol_id: str
    version: str
    resource_name: str
    content_sha256: str


_CATALOG = MappingProxyType(
    {
        ("fall-response", "1.0.0"): _ProtocolRegistration(
            incident_kind=IncidentKind.FALL,
            protocol_id="fall-response",
            version="1.0.0",
            resource_name="fall-response-v1.json",
            content_sha256=(
                "ab3958b01c17d83e7ef8f1a33898ccd9"
                "a45833589c3652eca20adb3a757afcad"
            ),
        ),
        ("manual-sos-response", "1.0.0"): _ProtocolRegistration(
            incident_kind=IncidentKind.MANUAL_SOS,
            protocol_id="manual-sos-response",
            version="1.0.0",
            resource_name="manual-sos-v1.json",
            content_sha256=(
                "547eeb0045bb60f8a2da8d6f775a9a9e"
                "da188934800fea062fb50726b1db31ee"
            ),
        ),
    }
)

_ACTIVE_PROTOCOLS = MappingProxyType(
    {
        IncidentKind.FALL: ("fall-response", "1.0.0"),
        IncidentKind.MANUAL_SOS: ("manual-sos-response", "1.0.0"),
    }
)


class FixedProtocolRegistry:
    """Select and verify fixed protocol files without caching their contents.

    Expected SHA-256 digests are pinned in this Python module, separate from the
    packaged JSON. Every public read goes back to disk (or the installed package
    resource), recomputes the digest over the exact bytes, and validates the
    result against the public contract.
    """

    def __init__(self, content_root: Path | None = None) -> None:
        self._content_root: Traversable = (
            content_root
            if content_root is not None
            else resources.files("vital_relay.protocols").joinpath("content")
        )

    def validate_all(self) -> tuple[FixedFirstAidProtocol, ...]:
        """Verify the append-only catalog and every active kind mapping."""

        loaded = {
            identity: self._load(_CATALOG[identity])
            for identity in sorted(_CATALOG)
        }
        for incident_kind in IncidentKind:
            identity = _ACTIVE_PROTOCOLS.get(incident_kind)
            if identity is None or identity not in loaded:
                raise ProtocolMappingError(
                    f"no active fixed protocol is registered for {incident_kind.value}"
                )
            if loaded[identity].emergency_kind is not incident_kind:
                raise ProtocolIntegrityError(
                    f"active protocol mapping does not match {incident_kind.value}"
                )
        return tuple(loaded[identity] for identity in sorted(loaded))

    def select(self, incident_kind: IncidentKind) -> FixedFirstAidProtocol:
        """Return the only fixed protocol mapped to ``incident_kind``."""

        try:
            identity = _ACTIVE_PROTOCOLS[incident_kind]
            registration = _CATALOG[identity]
        except (KeyError, TypeError) as error:
            raise ProtocolMappingError(
                f"no fixed protocol is registered for incident kind {incident_kind!r}"
            ) from error
        if registration.incident_kind is not incident_kind:
            raise ProtocolIntegrityError(
                f"active protocol mapping does not match {incident_kind.value}"
            )
        return self._load(registration)

    def load_exact(
        self,
        protocol_id: str,
        version: str,
        content_sha256: str,
    ) -> FixedFirstAidProtocol:
        """Reload an exact protocol identity stored with a presentation."""

        registration = _CATALOG.get((protocol_id, version))
        if registration is None:
            raise ProtocolMappingError(
                f"no fixed protocol is registered as {protocol_id!r} {version!r}"
            )
        if content_sha256 != registration.content_sha256:
            raise ProtocolIntegrityError(
                "stored protocol digest does not match the registered version"
            )
        return self._load(registration)

    def _load(self, registration: _ProtocolRegistration) -> FixedFirstAidProtocol:
        resource = self._content_root.joinpath(registration.resource_name)
        try:
            raw_content = resource.read_bytes()
        except (FileNotFoundError, IsADirectoryError, OSError) as error:
            raise ProtocolIntegrityError(
                f"fixed protocol resource {registration.resource_name!r} is unavailable"
            ) from error

        actual_digest = hashlib.sha256(raw_content).hexdigest()
        if actual_digest != registration.content_sha256:
            raise ProtocolIntegrityError(
                f"fixed protocol resource {registration.resource_name!r} failed integrity validation"
            )

        payload = self._decode_payload(raw_content, registration.resource_name)
        if "content_sha256" in payload:
            raise ProtocolIntegrityError(
                "fixed protocol content cannot declare its own integrity digest"
            )

        try:
            protocol = FixedFirstAidProtocol.model_validate(
                {**payload, "content_sha256": actual_digest}
            )
        except ValidationError as error:
            raise ProtocolIntegrityError(
                f"fixed protocol resource {registration.resource_name!r} violates its contract"
            ) from error

        if (
            protocol.protocol_id != registration.protocol_id
            or protocol.version != registration.version
            or protocol.emergency_kind is not registration.incident_kind
        ):
            raise ProtocolIntegrityError(
                f"fixed protocol resource {registration.resource_name!r} does not match its registration"
            )
        return protocol

    @staticmethod
    def _decode_payload(raw_content: bytes, resource_name: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtocolIntegrityError(
                f"fixed protocol resource {resource_name!r} is not valid UTF-8 JSON"
            ) from error
        if not isinstance(payload, dict):
            raise ProtocolIntegrityError(
                f"fixed protocol resource {resource_name!r} must contain a JSON object"
            )
        return payload
