"""Integrity-checked fixed first-aid protocol content."""

from vital_relay.protocols.registry import (
    FixedProtocolRegistry,
    ProtocolContentError,
    ProtocolIntegrityError,
    ProtocolMappingError,
)

__all__ = [
    "FixedProtocolRegistry",
    "ProtocolContentError",
    "ProtocolIntegrityError",
    "ProtocolMappingError",
]
