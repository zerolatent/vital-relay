"""Responder-authenticated registration and notification receipt boundary."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from vital_relay.api.persona_auth import (
    RESPONDER_TOKEN_HEADER,
    authenticate_responder_access,
)
from vital_relay.application.dispatch_service import ResponderAuthenticationError
from vital_relay.application.notification_service import (
    NotificationAuthorizationError,
    NotificationNotFoundError,
    NotificationService,
)
from vital_relay.domain.notifications import (
    NotificationReceiptView,
    PushRegistrationRequest,
    PushRegistrationView,
)
from vital_relay.domain.persona_sessions import PersonaPrincipal
from vital_relay.persistence.database import DemoScopeUnavailableError


class _RedactedNotificationValidationRoute(APIRoute):
    """Keep write-only device tokens out of FastAPI validation responses."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def redacted_route_handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    content={
                        "detail": {"code": "invalid_notification_request"}
                    },
                )

        return redacted_route_handler


router = APIRouter(
    tags=["notifications"],
    route_class=_RedactedNotificationValidationRoute,
)
ResponderToken = Annotated[
    str | None,
    Header(alias=RESPONDER_TOKEN_HEADER),
]


@router.put(
    "/v1/responders/{responder_id}/push-registrations/{installation_id}",
    response_model=PushRegistrationView,
)
def register_responder_push(
    responder_id: UUID,
    installation_id: UUID,
    registration: PushRegistrationRequest,
    request: Request,
    responder_token: ResponderToken = None,
) -> PushRegistrationView:
    service = _configured_notification_service(request)
    principal = authenticate_responder_access(
        request,
        responder_token,
        responder_id=responder_id,
    )
    _authorize_installation(principal, installation_id)
    try:
        return service.register(
            responder_id,
            installation_id,
            registration,
            responder_token=(responder_token if principal is None else None),
            authenticated_responder_id=(
                principal.responder_id if principal is not None else None
            ),
        )
    except ResponderAuthenticationError:
        _raise_responder_authentication()
    except NotificationAuthorizationError:
        _raise_notification_authorization()
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.delete(
    "/v1/responders/{responder_id}/push-registrations/{installation_id}",
    response_model=PushRegistrationView,
)
def revoke_responder_push(
    responder_id: UUID,
    installation_id: UUID,
    request: Request,
    responder_token: ResponderToken = None,
) -> PushRegistrationView:
    service = _configured_notification_service(request)
    principal = authenticate_responder_access(
        request,
        responder_token,
        responder_id=responder_id,
    )
    _authorize_installation(principal, installation_id)
    try:
        return service.revoke(
            responder_id,
            installation_id,
            responder_token=(responder_token if principal is None else None),
            authenticated_responder_id=(
                principal.responder_id if principal is not None else None
            ),
        )
    except ResponderAuthenticationError:
        _raise_responder_authentication()
    except NotificationAuthorizationError:
        _raise_notification_authorization()
    except NotificationNotFoundError as exc:
        _raise_not_found(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.get(
    "/v1/responders/{responder_id}/invitations/{invitation_id}/notification",
    response_model=NotificationReceiptView,
)
def get_responder_notification_receipt(
    responder_id: UUID,
    invitation_id: UUID,
    request: Request,
    responder_token: ResponderToken = None,
) -> NotificationReceiptView:
    service = _configured_notification_service(request)
    principal = authenticate_responder_access(
        request,
        responder_token,
        responder_id=responder_id,
    )
    try:
        return service.get_receipt(
            responder_id,
            invitation_id,
            responder_token=(responder_token if principal is None else None),
            authenticated_responder_id=(
                principal.responder_id if principal is not None else None
            ),
        )
    except ResponderAuthenticationError:
        _raise_responder_authentication()
    except NotificationAuthorizationError:
        _raise_notification_authorization()
    except NotificationNotFoundError as exc:
        _raise_not_found(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


def _configured_notification_service(request: Request) -> NotificationService:
    service = getattr(request.app.state, "notification_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "notification_persistence_unavailable"},
        )
    return service


def _raise_responder_authentication() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_responder_token"},
    )


def _raise_notification_authorization() -> None:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": NotificationAuthorizationError.code},
    )


def _raise_not_found(exc: NotificationNotFoundError) -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=exc.as_detail(),
    ) from exc


def _authorize_installation(
    principal: PersonaPrincipal | None,
    installation_id: UUID,
) -> None:
    if principal is None:
        return
    if principal.installation_id != installation_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "persona_not_authorized"},
        )


def _raise_persistence_unavailable(
    exc: DemoScopeUnavailableError | SQLAlchemyError,
) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "notification_persistence_unavailable"},
    ) from exc
