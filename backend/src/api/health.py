"""Health ingestion HTTP boundary."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from vital_relay.api.persona_auth import (
    DEVICE_TOKEN_HEADER,
    authenticate_device_access,
)

from vital_relay.application.health_context import (
    HealthCapabilityIngestionService,
    HealthSnapshotService,
)
from vital_relay.application.health_ingestion import (
    HealthIngestionService,
    IdempotencyConflictError,
)
from vital_relay.domain.health import (
    HealthMetricBatch,
    HealthMetricBatchResult,
    IngestionStatus,
)
from vital_relay.domain.health_context import (
    HealthCapabilityBatch,
    HealthCapabilityBatchResult,
    HealthSnapshotCreateRequest,
    HealthSnapshotView,
)
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal

router = APIRouter(prefix="/v1/health", tags=["health"])
DeviceToken = Annotated[
    str | None,
    Header(alias=DEVICE_TOKEN_HEADER),
]


@router.post(
    "/metrics:batch",
    response_model=HealthMetricBatchResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": HealthMetricBatchResult,
            "description": "Exact idempotent replay; no metrics were written again.",
        },
        status.HTTP_409_CONFLICT: {
            "description": "A batch or metric ID was reused with different content.",
        },
    },
)
def ingest_health_metrics(
    batch: HealthMetricBatch,
    request: Request,
    response: Response,
    device_token: DeviceToken = None,
) -> HealthMetricBatchResult:
    principal = _health_principal(
        request,
        device_token,
        allowed_personas={Persona.COMMUNITY},
    )
    _authorize_health_write(
        principal,
        user_id=batch.user_id,
        device_id=batch.device_id,
    )
    service: HealthIngestionService = request.app.state.health_ingestion_service
    try:
        result = service.ingest(batch)
        if result.status is IngestionStatus.ALREADY_PROCESSED:
            response.status_code = status.HTTP_200_OK
        return result
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "identifier": exc.identifier},
        ) from exc


@router.post(
    "/capabilities:batch",
    response_model=HealthCapabilityBatchResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": HealthCapabilityBatchResult,
            "description": "Exact idempotent replay; no capabilities were written again.",
        },
        status.HTTP_409_CONFLICT: {
            "description": "A capability batch or item ID has conflicting content.",
        },
    },
)
def ingest_health_capabilities(
    batch: HealthCapabilityBatch,
    request: Request,
    response: Response,
    device_token: DeviceToken = None,
) -> HealthCapabilityBatchResult:
    principal = _health_principal(
        request,
        device_token,
        allowed_personas={Persona.COMMUNITY},
    )
    _authorize_health_write(
        principal,
        user_id=batch.user_id,
        device_id=batch.device_id,
    )
    service: HealthCapabilityIngestionService = (
        request.app.state.health_capability_ingestion_service
    )
    try:
        result = service.ingest(batch)
        if result.status is IngestionStatus.ALREADY_PROCESSED:
            response.status_code = status.HTTP_200_OK
        return result
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "identifier": exc.identifier},
        ) from exc


@router.post(
    "/snapshots",
    response_model=HealthSnapshotView,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": HealthSnapshotView,
            "description": "Exact snapshot request replay; the stored snapshot is returned.",
        },
        status.HTTP_409_CONFLICT: {
            "description": "The snapshot ID was reused for a different request.",
        },
    },
)
def create_health_snapshot(
    create_request: HealthSnapshotCreateRequest,
    request: Request,
    response: Response,
    device_token: DeviceToken = None,
) -> HealthSnapshotView:
    principal = _health_principal(
        request,
        device_token,
        allowed_personas={Persona.COMMUNITY},
    )
    _authorize_health_write(
        principal,
        user_id=create_request.user_id,
        device_id=None,
    )
    service: HealthSnapshotService = request.app.state.health_snapshot_service
    try:
        outcome = service.create(create_request)
        if not outcome.created:
            response.status_code = status.HTTP_200_OK
        return service.to_view(outcome.snapshot)
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "identifier": exc.identifier},
        ) from exc


@router.get(
    "/snapshots/{snapshot_id}",
    response_model=HealthSnapshotView,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Snapshot not found."}},
)
def get_health_snapshot(
    snapshot_id: UUID,
    request: Request,
    device_token: DeviceToken = None,
) -> HealthSnapshotView:
    principal = _health_principal(
        request,
        device_token,
        allowed_personas={Persona.COMMUNITY, Persona.COMMAND},
    )
    service: HealthSnapshotService = request.app.state.health_snapshot_service
    snapshot = service.get_view(snapshot_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "snapshot_not_found", "identifier": str(snapshot_id)},
        )
    if (
        principal is not None
        and principal.persona is Persona.COMMUNITY
        and principal.user_id != snapshot.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "snapshot_not_found", "identifier": str(snapshot_id)},
        )
    return snapshot


def _health_principal(
    request: Request,
    device_token: str | None,
    *,
    allowed_personas: set[Persona],
) -> PersonaPrincipal | None:
    # In-memory repositories are an isolated development/test boundary with no
    # durable account/session store. PostgreSQL product mode always authenticates.
    if getattr(request.app.state, "persona_session_service", None) is None:
        return None
    if (
        getattr(request.app.state, "legacy_persona_auth", False)
        and device_token is None
    ):
        return None
    return authenticate_device_access(
        request,
        device_token,
        allowed_personas=allowed_personas,
    )


def _authorize_health_write(
    principal: PersonaPrincipal | None,
    *,
    user_id: str,
    device_id: str | None,
) -> None:
    if principal is None:
        return
    if (
        principal.persona is not Persona.COMMUNITY
        or principal.user_id != user_id
        or (
            device_id is not None
            and str(principal.installation_id) != device_id.lower()
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "persona_not_authorized"},
        )
