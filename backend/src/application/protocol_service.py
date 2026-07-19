"""Application boundary for immutable first-aid protocol presentations."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from vital_relay.domain.protocols import ProtocolPresentationView


class ProtocolRepository(Protocol):
    """Read persisted protocol presentations through bounded audiences."""

    def get_for_command(self, incident_id: UUID) -> ProtocolPresentationView | None:
        """Return the accepted dispatch presentation to the command client."""

    def get_for_responder(
        self,
        incident_id: UUID,
        responder_id: UUID,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
    ) -> ProtocolPresentationView | None:
        """Return a presentation only to its assigned, active responder."""


class ProtocolNotFoundError(Exception):
    """No fixed presentation exists for the requested accepted dispatch."""

    code = "protocol_presentation_not_found"

    def __init__(self, incident_id: UUID) -> None:
        self.incident_id = incident_id
        super().__init__(self.code)

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "identifier": str(self.incident_id)}


class ProtocolAuthenticationError(Exception):
    """A responder credential does not authorize the presentation."""


class ProtocolService:
    """Expose one durable fixed presentation without selecting new content."""

    def __init__(self, repository: ProtocolRepository) -> None:
        self._repository = repository

    def get_for_command(self, incident_id: UUID) -> ProtocolPresentationView:
        presentation = self._repository.get_for_command(incident_id)
        if presentation is None:
            raise ProtocolNotFoundError(incident_id)
        return presentation

    def get_for_responder(
        self,
        incident_id: UUID,
        responder_id: UUID,
        *,
        responder_token: str | None = None,
        authenticated_responder_id: UUID | None = None,
    ) -> ProtocolPresentationView:
        presentation = self._repository.get_for_responder(
            incident_id,
            responder_id,
            responder_token=responder_token,
            authenticated_responder_id=authenticated_responder_id,
        )
        if presentation is None:
            raise ProtocolNotFoundError(incident_id)
        return presentation
