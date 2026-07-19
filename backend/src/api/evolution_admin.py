"""Bearer-only command API for the durable offline evolution control plane."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, status
from sqlalchemy.exc import SQLAlchemyError

from vital_relay.api.persona_auth import authenticate_bearer, require_persona
from vital_relay.application.evolution_promotion import (
    ActiveEvolutionVersion,
    ArchiveEvolutionReleaseCommand,
    CandidateVersionDetail,
    CandidateVersionSummary,
    EvolutionConflict,
    EvolutionIntegrityError,
    EvolutionPointerNotFound,
    EvolutionPromotionError,
    EvolutionPromotionService,
    EvolutionTransitionCommand,
    EvolutionTransitionResult,
    EvolutionVersionNotFound,
    SHA256_PATTERN,
)
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.persistence.database import DemoScopeUnavailableError


router = APIRouter(prefix="/v1/admin/evolution", tags=["evolution admin"])

Authorization = Annotated[str | None, Header(alias="Authorization")]
VersionSHA256 = Annotated[
    str,
    Path(min_length=64, max_length=64, pattern=SHA256_PATTERN),
]


@router.post(
    "/archive",
    response_model=CandidateVersionDetail,
    status_code=status.HTTP_201_CREATED,
)
def archive_release(
    command: ArchiveEvolutionReleaseCommand,
    request: Request,
    authorization: Authorization = None,
) -> CandidateVersionDetail:
    principal = _authenticate_command(request, authorization)
    try:
        return _configured_service(request).archive_from_source(command, principal)
    except Exception as exc:
        _raise_evolution_error(exc)


@router.get(
    "/archive",
    response_model=tuple[CandidateVersionSummary, ...],
)
def list_archive(
    request: Request,
    authorization: Authorization = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> tuple[CandidateVersionSummary, ...]:
    principal = _authenticate_command(request, authorization)
    try:
        return _configured_service(request).list_versions(principal, limit=limit)
    except Exception as exc:
        _raise_evolution_error(exc)


@router.get(
    "/archive/{version_sha256}",
    response_model=CandidateVersionDetail,
)
def get_archived_version(
    version_sha256: VersionSHA256,
    request: Request,
    authorization: Authorization = None,
) -> CandidateVersionDetail:
    principal = _authenticate_command(request, authorization)
    try:
        return _configured_service(request).get_version(version_sha256, principal)
    except Exception as exc:
        _raise_evolution_error(exc)


@router.get(
    "/agents/{version_sha256}",
    response_model=CandidateVersionDetail,
    include_in_schema=False,
)
def get_archived_agent_alias(
    version_sha256: VersionSHA256,
    request: Request,
    authorization: Authorization = None,
) -> CandidateVersionDetail:
    """Compatibility route for the documented command-dashboard noun."""

    return get_archived_version(version_sha256, request, authorization)


@router.get("/active", response_model=ActiveEvolutionVersion)
def get_active_version(
    request: Request,
    authorization: Authorization = None,
) -> ActiveEvolutionVersion:
    principal = _authenticate_command(request, authorization)
    try:
        return _configured_service(request).get_active(principal)
    except Exception as exc:
        _raise_evolution_error(exc)


@router.post("/promote", response_model=EvolutionTransitionResult)
def promote_version(
    command: EvolutionTransitionCommand,
    request: Request,
    authorization: Authorization = None,
) -> EvolutionTransitionResult:
    principal = _authenticate_command(request, authorization)
    try:
        return _configured_service(request).promote(command, principal)
    except Exception as exc:
        _raise_evolution_error(exc)


@router.post(
    "/archive/{version_sha256}/promote",
    response_model=EvolutionTransitionResult,
    include_in_schema=False,
)
def promote_archived_version(
    version_sha256: VersionSHA256,
    command: EvolutionTransitionCommand,
    request: Request,
    authorization: Authorization = None,
) -> EvolutionTransitionResult:
    if command.target_version_sha256 != version_sha256:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "promotion_path_target_mismatch"},
        )
    return promote_version(command, request, authorization)


@router.post("/rollback", response_model=EvolutionTransitionResult)
def rollback_version(
    command: EvolutionTransitionCommand,
    request: Request,
    authorization: Authorization = None,
) -> EvolutionTransitionResult:
    principal = _authenticate_command(request, authorization)
    try:
        return _configured_service(request).rollback(command, principal)
    except Exception as exc:
        _raise_evolution_error(exc)


def _authenticate_command(
    request: Request,
    authorization: str | None,
) -> PersonaPrincipal:
    # This endpoint deliberately has no device-header or legacy-token path.
    principal = authenticate_bearer(request, authorization)
    require_persona(principal, {Persona.COMMAND})
    return principal


def _configured_service(request: Request) -> EvolutionPromotionService:
    service = getattr(request.app.state, "evolution_promotion_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "evolution_control_plane_unavailable"},
        )
    return service


def _raise_evolution_error(exc: Exception) -> NoReturn:
    if isinstance(exc, (EvolutionVersionNotFound, EvolutionPointerNotFound)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.as_detail(),
        ) from exc
    if isinstance(exc, EvolutionConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.as_detail(),
        ) from exc
    if isinstance(exc, EvolutionIntegrityError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.as_detail(),
        ) from exc
    if isinstance(exc, EvolutionPromotionError):
        if exc.code == "persona_not_authorized":
            error_status = status.HTTP_403_FORBIDDEN
        elif exc.code == "evolution_release_source_unavailable":
            error_status = status.HTTP_503_SERVICE_UNAVAILABLE
        else:
            error_status = status.HTTP_409_CONFLICT
        raise HTTPException(
            status_code=error_status,
            detail=exc.as_detail(),
        ) from exc
    if isinstance(exc, (DemoScopeUnavailableError, SQLAlchemyError)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "evolution_control_plane_unavailable"},
        ) from exc
    raise exc
