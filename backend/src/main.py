"""FastAPI application composition for the Vital Relay backend."""

from __future__ import annotations

import asyncio
import base64
import binascii
import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, status
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from vital_relay.adapters.in_memory_health import InMemoryHealthMetricRepository
from vital_relay.adapters.in_memory_health_context import (
    InMemoryHealthCapabilityRepository,
    InMemoryHealthSnapshotRepository,
)
from vital_relay.adapters.apns import APNsNotificationProvider
from vital_relay.adapters.composite_routing import LiveFirstRoutingProvider
from vital_relay.adapters.live_routing import (
    DEFAULT_MAPBOX_DIRECTIONS_BASE_URL,
    MapboxDirectionsRoutingProvider,
    validate_mapbox_directions_base_url,
)
from vital_relay.adapters.postgres_agent_control import (
    PostgresActivePolicyAuthorization,
    PostgresAgentRunRepository,
    PostgresAppendOnlyToolAudit,
    PostgresDurableIdempotencyExecutor,
)
from vital_relay.adapters.postgres_dispatch import PostgresDispatchRepository
from vital_relay.adapters.postgres_health import (
    PostgresHealthCapabilityRepository,
    PostgresHealthMetricRepository,
    PostgresHealthSnapshotRepository,
    PostgresHealthSnapshotUnitOfWork,
)
from vital_relay.adapters.postgres_incidents import PostgresIncidentRepository
from vital_relay.adapters.postgres_notifications import (
    FernetDeviceTokenCipher,
    PostgresNotificationRepository,
)
from vital_relay.adapters.postgres_persona_sessions import (
    PostgresPersonaSessionRepository,
    provision_persona_account,
)
from vital_relay.adapters.postgres_protocols import PostgresProtocolRepository
from vital_relay.api.dispatch import router as dispatch_router
from vital_relay.api.agent_runs import router as agent_run_router
from vital_relay.api.agent_tools import router as agent_tool_router
from vital_relay.api.health import router as health_router
from vital_relay.api.incidents import router as incident_router
from vital_relay.api.notifications import router as notification_router
from vital_relay.api.persona_auth import valid_opaque_token
from vital_relay.api.persona_sessions import router as persona_session_router
from vital_relay.api.protocols import router as protocol_router
from vital_relay.application.dispatch_service import (
    DEFAULT_RESPONDER_RADIUS_M,
    DEFAULT_RESPONDER_STALE_SECONDS,
    DispatchService,
)
from vital_relay.agent.capabilities import ToolCapabilityAuthority
from vital_relay.agent.contracts import SandboxKind, VLLMSettings
from vital_relay.agent.policy import load_pinned_policy_snapshot
from vital_relay.agent.sandbox import (
    DOCKER_INFERENCE_BASE_URL,
    DOCKER_REVIEWED_COMPOSE_FILE,
    DOCKER_TOOL_PROXY_ENDPOINT,
    NEMOCLAW_INFERENCE_BASE_URL,
    NEMOCLAW_MANAGED_INFERENCE_API_KEY,
    NEMOCLAW_SANDBOX_NAME_PATTERN,
    ProcessSandboxAgentRunner,
    ProcessSandboxSelection,
    SandboxCleanupEvidence,
    SandboxStartupEvidence,
)
from vital_relay.application.agent_service import (
    AgentRunService,
    StaticActivePolicySnapshotProvider,
)
from vital_relay.application.agent_tool_execution import AgentToolExecutionPool
from vital_relay.application.health_context import (
    FreshnessPolicy,
    HealthCapabilityIngestionService,
    HealthCapabilityRepository,
    HealthSnapshotRepository,
    HealthSnapshotService,
    HealthSnapshotUnitOfWork,
)
from vital_relay.application.health_ingestion import (
    Clock,
    HealthIngestionService,
    HealthMetricRepository,
)
from vital_relay.application.incident_service import (
    DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    IncidentService,
)
from vital_relay.application.notification_service import (
    NotificationService,
    NotificationWorker,
)
from vital_relay.application.persona_session_service import PersonaSessionService
from vital_relay.application.protocol_service import ProtocolService
from vital_relay.application.tool_proxy import (
    InternalAgentToolProxy,
    initial_tool_proxy_bindings,
)
from vital_relay.config import (
    AGENT_MODEL_ARTIFACT_SHA256_ENV,
    AGENT_MODEL_REVISION_ENV,
    generator_context_selector_from_environment,
)
from vital_relay.persistence.database import (
    DemoScopeUnavailableError,
    create_postgres_engine,
    create_session_factory,
    require_active_scope,
)
from vital_relay.domain.notifications import PushEnvironment
from vital_relay.domain.persona_sessions import Persona
from vital_relay.evolution.ace import GeneratorContextSelector
from vital_relay.protocols.registry import (
    FixedProtocolRegistry,
    ProtocolContentError,
)


DEVICE_TOKEN_ENV = "VITAL_RELAY_DEVICE_TOKEN"
VERIFICATION_TIMEOUT_ENV = "VITAL_RELAY_VERIFICATION_TIMEOUT_SECONDS"
DEADLINE_POLL_ENV = "VITAL_RELAY_DEADLINE_POLL_SECONDS"
RESPONDER_RADIUS_ENV = "VITAL_RELAY_RESPONDER_RADIUS_M"
RESPONDER_STALE_ENV = "VITAL_RELAY_RESPONDER_STALE_SECONDS"
MAPBOX_ACCESS_TOKEN_ENV = "VITAL_RELAY_MAPBOX_ACCESS_TOKEN"
MAPBOX_DIRECTIONS_BASE_URL_ENV = (
    "VITAL_RELAY_MAPBOX_DIRECTIONS_BASE_URL"
)
ROUTING_TIMEOUT_ENV = "VITAL_RELAY_ROUTING_TIMEOUT_SECONDS"
APNS_ENABLED_ENV = "VITAL_RELAY_APNS_ENABLED"
APNS_TEAM_ID_ENV = "VITAL_RELAY_APNS_TEAM_ID"
APNS_KEY_ID_ENV = "VITAL_RELAY_APNS_KEY_ID"
APNS_TOPIC_ENV = "VITAL_RELAY_APNS_TOPIC"
APNS_PRIVATE_KEY_PATH_ENV = "VITAL_RELAY_APNS_PRIVATE_KEY_PATH"
APNS_ENVIRONMENT_ENV = "VITAL_RELAY_APNS_ENVIRONMENT"
APNS_TIMEOUT_ENV = "VITAL_RELAY_APNS_TIMEOUT_SECONDS"
NOTIFICATION_ALLOWLIST_ENV = "VITAL_RELAY_NOTIFICATION_RESPONDER_ALLOWLIST"
NOTIFICATION_ENCRYPTION_KEY_ENV = (
    "VITAL_RELAY_NOTIFICATION_TOKEN_ENCRYPTION_KEY"
)
NOTIFICATION_POLL_ENV = "VITAL_RELAY_NOTIFICATION_POLL_SECONDS"
AGENT_ENABLED_ENV = "VITAL_RELAY_AGENT_ENABLED"
AGENT_SANDBOX_ENV = "VITAL_RELAY_AGENT_SANDBOX"
AGENT_SANDBOX_NAME_ENV = "VITAL_RELAY_AGENT_SANDBOX_NAME"
AGENT_SIGNING_KEY_ENV = "VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY"
AGENT_TOOL_PROXY_ENDPOINT_ENV = "VITAL_RELAY_AGENT_TOOL_PROXY_ENDPOINT"
AGENT_VLLM_BASE_URL_ENV = "VITAL_RELAY_VLLM_BASE_URL"
AGENT_VLLM_MODEL_ENV = "VITAL_RELAY_VLLM_MODEL"
AGENT_DOCKER_VLLM_API_KEY_ENV = "VITAL_RELAY_DOCKER_VLLM_API_KEY"
AGENT_TIMEOUT_ENV = "VITAL_RELAY_AGENT_TIMEOUT_SECONDS"
AGENT_POLICY_PATH_ENV = "VITAL_RELAY_AGENT_POLICY_PATH"
AGENT_POLICY_DIGEST_PATH_ENV = "VITAL_RELAY_AGENT_POLICY_DIGEST_PATH"
NEMOCLAW_TOOL_PROXY_ENDPOINT = (
    "https://vital-relay.internal:8443/internal/v1/agent/tools/invoke"
)
DEFAULT_DEADLINE_POLL_SECONDS = 1.0
DEFAULT_NOTIFICATION_POLL_SECONDS = 1.0
DEFAULT_APNS_TIMEOUT_SECONDS = 10.0
DEFAULT_ROUTING_TIMEOUT_SECONDS = 3.0
DEFAULT_AGENT_TIMEOUT_SECONDS = 90.0
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AGENT_POLICY_PATH = (
    PROJECT_ROOT / "agents/policies/baseline/coordination_policy.yaml"
)
DEFAULT_AGENT_POLICY_DIGEST_PATH = (
    PROJECT_ROOT / "agents/policies/baseline/coordination_policy.sha256"
)


@dataclass(frozen=True)
class APNsRuntimeConfiguration:
    team_id: str
    key_id: str
    topic: str
    private_key_path: Path
    environment: PushEnvironment
    responder_allowlist: frozenset[UUID]
    token_encryption_key: SecretStr
    timeout_seconds: float
    poll_seconds: float


@dataclass(frozen=True)
class RoutingRuntimeConfiguration:
    """Server-owned live-routing settings with a redacted provider token."""

    access_token: SecretStr = field(repr=False)
    base_url: str
    timeout_seconds: float


@dataclass(frozen=True)
class AgentRuntimeConfiguration:
    """Reviewed host settings for exactly one process sandbox boundary."""

    selection: ProcessSandboxSelection
    signing_key: bytes = field(repr=False)
    tool_proxy_endpoint: str
    vllm: VLLMSettings
    timeout_seconds: float
    policy_path: Path
    policy_digest_path: Path
    context_selector: GeneratorContextSelector


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def create_app(
    repository: HealthMetricRepository | None = None,
    capability_repository: HealthCapabilityRepository | None = None,
    snapshot_repository: HealthSnapshotRepository | None = None,
    clock: Clock | None = None,
    freshness_policy: FreshnessPolicy | None = None,
    snapshot_unit_of_work: HealthSnapshotUnitOfWork | None = None,
    database_url: str | None = None,
    demo_scope_id: UUID | None = None,
    device_token: str | None = None,
    verification_timeout_seconds: int | None = None,
    deadline_poll_seconds: float | None = None,
    responder_radius_m: int | None = None,
    responder_stale_seconds: int | None = None,
    apns_enabled: bool | None = None,
    agent_enabled: bool | None = None,
    legacy_persona_auth: bool = False,
) -> FastAPI:
    database_engine: Engine | None = None
    active_incident_service: IncidentService | None = None
    active_dispatch_service: DispatchService | None = None
    active_protocol_service: ProtocolService | None = None
    active_notification_service: NotificationService | None = None
    active_notification_worker: NotificationWorker | None = None
    active_notification_provider: APNsNotificationProvider | None = None
    active_persona_session_service: PersonaSessionService | None = None
    active_agent_run_service: AgentRunService | None = None
    active_internal_agent_tool_proxy: InternalAgentToolProxy | None = None
    active_agent_tool_execution_pool: AgentToolExecutionPool | None = None
    active_agent_runner: ProcessSandboxAgentRunner | None = None
    agent_startup_evidence: SandboxStartupEvidence | None = None
    active_routing_provider: LiveFirstRoutingProvider | None = None
    protocol_registry = FixedProtocolRegistry()
    protocol_registry.validate_all()
    configured_deadline_poll_seconds = DEFAULT_DEADLINE_POLL_SECONDS
    configured_notification_poll_seconds = DEFAULT_NOTIFICATION_POLL_SECONDS

    @asynccontextmanager
    async def lifespan(active_app: FastAPI) -> AsyncIterator[None]:
        deadline_worker_stop = asyncio.Event()
        deadline_worker_task: asyncio.Task[None] | None = None
        notification_worker_stop = asyncio.Event()
        notification_worker_task: asyncio.Task[None] | None = None
        lifecycle_failure: BaseException | None = None
        try:
            if active_incident_service is not None:
                await _process_deadline_batch(
                    active_app,
                    active_incident_service,
                )
                deadline_worker_task = asyncio.create_task(
                    _run_deadline_worker(
                        active_app,
                        active_incident_service,
                        stop=deadline_worker_stop,
                        poll_seconds=configured_deadline_poll_seconds,
                    )
                )
                deadline_worker_task.set_name(
                    "vital-relay-incident-deadlines"
                )
            if active_notification_worker is not None:
                await _process_notification_batch(
                    active_app,
                    active_notification_worker,
                )
                notification_worker_task = asyncio.create_task(
                    _run_notification_worker(
                        active_app,
                        active_notification_worker,
                        stop=notification_worker_stop,
                        poll_seconds=configured_notification_poll_seconds,
                    )
                )
                notification_worker_task.set_name(
                    "vital-relay-notification-outbox"
                )
            yield
        except BaseException as exc:
            lifecycle_failure = exc
            raise
        finally:
            await _cleanup_application_lifespan(
                app=active_app,
                failure=lifecycle_failure,
                deadline_worker_stop=deadline_worker_stop,
                deadline_worker_task=deadline_worker_task,
                notification_worker_stop=notification_worker_stop,
                notification_worker_task=notification_worker_task,
                notification_provider=active_notification_provider,
                agent_runner=active_agent_runner,
                agent_tool_execution_pool=(
                    active_agent_tool_execution_pool
                ),
                routing_provider=active_routing_provider,
                engine=database_engine,
            )

    app = FastAPI(
        title="Vital Relay API",
        version="0.9.0",
        description="Agentic emergency-response coordination demo",
        lifespan=lifespan,
    )

    configured_database_url = database_url or os.environ.get(
        "VITAL_RELAY_DATABASE_URL"
    )
    configured_agent_enabled = _boolean_setting(
        explicit=agent_enabled,
        environment_name=AGENT_ENABLED_ENV,
        default=False,
    )
    if configured_agent_enabled and not configured_database_url:
        raise ValueError(
            f"{AGENT_ENABLED_ENV}=true requires VITAL_RELAY_DATABASE_URL"
        )
    agent_configuration = (
        _agent_runtime_configuration() if configured_agent_enabled else None
    )
    configured_device_token = (
        device_token
        if device_token is not None
        else os.environ.get(DEVICE_TOKEN_ENV)
    )
    if (
        configured_database_url
        and not legacy_persona_auth
        and configured_device_token is not None
        and not valid_opaque_token(configured_device_token)
    ):
        raise ValueError(
            f"{DEVICE_TOKEN_ENV} must be a 43-256 character URL-safe "
            "command enrollment bootstrap"
        )
    configured_apns_enabled = _boolean_setting(
        explicit=apns_enabled,
        environment_name=APNS_ENABLED_ENV,
        default=False,
    )
    apns_configuration = (
        _apns_runtime_configuration()
        if configured_apns_enabled
        else None
    )
    routing_configuration = _routing_runtime_configuration()
    if apns_configuration is not None and not configured_database_url:
        raise ValueError(
            f"{APNS_ENABLED_ENV}=true requires VITAL_RELAY_DATABASE_URL"
        )
    injected_repositories = (
        repository,
        capability_repository,
        snapshot_repository,
    )
    if configured_database_url and any(
        item is not None for item in injected_repositories
    ):
        raise ValueError(
            "database configuration cannot be mixed with injected repositories"
        )

    database_sessions = None
    active_database_scope_id = None
    provisioned_command_account = None
    active_clock = clock if clock is not None else SystemClock()
    if configured_database_url:
        scope_id = demo_scope_id or _scope_id_from_environment()
        if legacy_persona_auth and (
            not configured_device_token or not configured_device_token.strip()
        ):
            raise ValueError(
                f"{DEVICE_TOKEN_ENV} is required when legacy persona auth is enabled"
            )
        configured_verification_timeout_seconds = _positive_int_setting(
            explicit=verification_timeout_seconds,
            environment_name=VERIFICATION_TIMEOUT_ENV,
            default=DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
        )
        configured_deadline_poll_seconds = _positive_float_setting(
            explicit=deadline_poll_seconds,
            environment_name=DEADLINE_POLL_ENV,
            default=DEFAULT_DEADLINE_POLL_SECONDS,
        )
        configured_responder_radius_m = _positive_int_setting(
            explicit=responder_radius_m,
            environment_name=RESPONDER_RADIUS_ENV,
            default=DEFAULT_RESPONDER_RADIUS_M,
        )
        configured_responder_stale_seconds = _positive_int_setting(
            explicit=responder_stale_seconds,
            environment_name=RESPONDER_STALE_ENV,
            default=DEFAULT_RESPONDER_STALE_SECONDS,
        )
        candidate_engine = create_postgres_engine(configured_database_url)
        try:
            sessions = create_session_factory(candidate_engine)
            with sessions() as session:
                require_active_scope(session, scope_id)
            health_repository = PostgresHealthMetricRepository(sessions, scope_id)
            health_capability_repository = PostgresHealthCapabilityRepository(
                sessions,
                scope_id,
            )
            health_snapshot_repository = PostgresHealthSnapshotRepository(
                sessions,
                scope_id,
            )
            active_snapshot_unit_of_work = PostgresHealthSnapshotUnitOfWork(
                candidate_engine,
                sessions,
                scope_id,
            )
            incident_repository = PostgresIncidentRepository(
                candidate_engine,
                sessions,
                scope_id,
            )
            persona_session_repository = PostgresPersonaSessionRepository(
                candidate_engine,
                sessions,
                scope_id,
            )
            active_persona_session_service = PersonaSessionService(
                persona_session_repository,
                active_clock,
            )
            if configured_device_token and configured_device_token.strip():
                provisioned_command_account = provision_persona_account(
                    sessions,
                    scope_id=scope_id,
                    persona=Persona.COMMAND,
                    display_name="Incident command",
                    provisioned_at=active_clock.now(),
                    enrollment_token=configured_device_token,
                )
            notification_repository = None
            if apns_configuration is not None:
                token_cipher = FernetDeviceTokenCipher(
                    apns_configuration.token_encryption_key
                )
                notification_repository = PostgresNotificationRepository(
                    sessions,
                    scope_id,
                    responder_allowlist=(
                        apns_configuration.responder_allowlist
                    ),
                    environment=apns_configuration.environment,
                    topic=apns_configuration.topic,
                    token_cipher=token_cipher,
                )
                active_notification_provider = (
                    APNsNotificationProvider.from_key_file(
                        team_id=apns_configuration.team_id,
                        key_id=apns_configuration.key_id,
                        private_key_path=(
                            apns_configuration.private_key_path
                        ),
                        timeout_seconds=apns_configuration.timeout_seconds,
                    )
                )
                active_notification_service = NotificationService(
                    notification_repository,
                    active_clock,
                )
                active_notification_worker = NotificationWorker(
                    notification_repository,
                    active_notification_provider,
                    active_clock,
                )
                configured_notification_poll_seconds = (
                    apns_configuration.poll_seconds
                )
            active_routing_provider = _create_routing_provider(
                routing_configuration
            )
            dispatch_repository = PostgresDispatchRepository(
                candidate_engine,
                sessions,
                scope_id,
                active_routing_provider,
                protocol_registry,
                notification_enqueuer=(
                    notification_repository.enqueue_invitation
                    if notification_repository is not None
                    else None
                ),
            )
            protocol_repository = PostgresProtocolRepository(
                sessions,
                scope_id,
                protocol_registry,
            )
            active_incident_service = IncidentService(
                incident_repository,
                active_clock,
                verification_timeout_seconds=(
                    configured_verification_timeout_seconds
                ),
            )
            active_dispatch_service = DispatchService(
                dispatch_repository,
                active_clock,
                responder_radius_m=configured_responder_radius_m,
                responder_stale_seconds=configured_responder_stale_seconds,
            )
            active_protocol_service = ProtocolService(protocol_repository)
            if agent_configuration is not None:
                policy_snapshot = load_pinned_policy_snapshot(
                    agent_configuration.policy_path,
                    agent_configuration.policy_digest_path,
                )
                policy_authorization = PostgresActivePolicyAuthorization(
                    sessions,
                    scope_id,
                )
                active_policy = policy_authorization.get_active()
                if active_policy is None:
                    if provisioned_command_account is None:
                        raise ValueError(
                            f"first {AGENT_ENABLED_ENV}=true startup requires "
                            f"{DEVICE_TOKEN_ENV} to activate the pinned policy"
                        )
                    active_policy = policy_authorization.initialize(
                        policy_snapshot.reference,
                        activated_at=active_clock.now(),
                        activated_by_account_id=(
                            provisioned_command_account.account.account_id
                        ),
                    )
                if active_policy.policy != policy_snapshot.reference:
                    raise ValueError(
                        "configured agent policy does not match the active "
                        "scope policy"
                    )

                capability_authority = ToolCapabilityAuthority(
                    agent_configuration.signing_key
                )
                tool_audit = PostgresAppendOnlyToolAudit(sessions, scope_id)
                active_internal_agent_tool_proxy = InternalAgentToolProxy(
                    capability_authority=capability_authority,
                    scope_id=str(scope_id),
                    policy_authorization=policy_authorization,
                    incident_port=active_incident_service,
                    bindings=initial_tool_proxy_bindings(
                        incident_port=active_incident_service,
                        dispatch_port=active_dispatch_service,
                        protocol_port=active_protocol_service,
                    ),
                    audit_sink=tool_audit,
                    idempotency=PostgresDurableIdempotencyExecutor(
                        sessions,
                        scope_id,
                    ),
                    clock=active_clock,
                    identifier_factory=uuid4,
                )
                active_agent_tool_execution_pool = AgentToolExecutionPool()
                active_agent_runner = ProcessSandboxAgentRunner.selected(
                    agent_configuration.selection,
                    settings=agent_configuration.vllm,
                    model_artifact_sha256=(
                        agent_configuration.context_selector.model_identity
                        .artifact_sha256
                        if agent_configuration.selection.sandbox
                        is SandboxKind.DOCKER
                        else None
                    ),
                    tool_proxy_endpoint=(
                        agent_configuration.tool_proxy_endpoint
                    ),
                    timeout_seconds=agent_configuration.timeout_seconds,
                    clock=active_clock,
                )
                agent_startup_evidence = (
                    active_agent_runner.validate_startup()
                )
                active_agent_run_service = AgentRunService(
                    scope_id=scope_id,
                    incident_service=active_incident_service,
                    protocol_service=active_protocol_service,
                    repository=PostgresAgentRunRepository(sessions, scope_id),
                    policy_provider=StaticActivePolicySnapshotProvider(
                        policy_snapshot,
                        policy_authorization,
                    ),
                    capability_authority=capability_authority,
                    runner=active_agent_runner,
                    tools=active_internal_agent_tool_proxy.gateway(),
                    model_id=active_agent_runner.model_id,
                    sandbox=active_agent_runner.sandbox,
                    clock=active_clock,
                    context_selector=agent_configuration.context_selector,
                )
        except BaseException as construction_error:
            _cleanup_failed_database_composition(
                failure=construction_error,
                routing_provider=active_routing_provider,
                agent_runner=active_agent_runner,
                agent_tool_execution_pool=(
                    active_agent_tool_execution_pool
                ),
                notification_provider=active_notification_provider,
                engine=candidate_engine,
            )
            active_routing_provider = None
            active_agent_runner = None
            active_agent_tool_execution_pool = None
            active_notification_provider = None
            raise
        database_engine = candidate_engine
        database_sessions = sessions
        active_database_scope_id = scope_id
        app.state.database_engine = database_engine
        app.state.demo_scope_id = scope_id
        app.state.storage_backend = "postgresql"
    else:
        health_repository = repository or InMemoryHealthMetricRepository()
        health_capability_repository = (
            capability_repository or InMemoryHealthCapabilityRepository()
        )
        health_snapshot_repository = (
            snapshot_repository or InMemoryHealthSnapshotRepository()
        )
        active_snapshot_unit_of_work = snapshot_unit_of_work
        app.state.storage_backend = "in_memory"

    app.state.device_token = (
        configured_device_token
        if active_incident_service is not None and legacy_persona_auth
        else None
    )
    app.state.legacy_persona_auth = legacy_persona_auth
    app.state.persona_session_service = active_persona_session_service
    app.state.incident_service = active_incident_service
    app.state.dispatch_service = active_dispatch_service
    app.state.protocol_service = active_protocol_service
    app.state.notification_service = active_notification_service
    app.state.agent_run_service = active_agent_run_service
    app.state.internal_agent_tool_proxy = active_internal_agent_tool_proxy
    app.state.agent_tool_execution_pool = active_agent_tool_execution_pool
    app.state.agent_sandbox_startup_evidence = (
        agent_startup_evidence
        if active_agent_run_service is not None
        else None
    )
    app.state.agent_sandbox_cleanup_evidence = None
    app.state.agent_sandbox_cleanup_history = ()
    app.state.agent_sandbox_cleanup_retry = None
    app.state.agent_runtime = (
        active_agent_runner.sandbox.value
        if active_agent_runner is not None
        else "disabled"
    )
    app.state.routing_runtime = (
        "mapbox_directions"
        if active_dispatch_service is not None
        and routing_configuration is not None
        else (
            "static_fallback"
            if active_dispatch_service is not None
            else "disabled"
        )
    )
    app.state.protocol_registry = protocol_registry
    app.state.incident_worker_error = None
    app.state.notification_worker_error = None
    app.state.notification_delivery = (
        "apns" if active_notification_worker is not None else "disabled"
    )
    app.state.health_metric_repository = health_repository
    app.state.health_capability_repository = health_capability_repository
    app.state.health_snapshot_repository = health_snapshot_repository
    app.state.health_ingestion_service = HealthIngestionService(
        health_repository,
        active_clock,
    )
    app.state.health_capability_ingestion_service = HealthCapabilityIngestionService(
        health_capability_repository,
        active_clock,
    )
    app.state.health_snapshot_service = HealthSnapshotService(
        metric_repository=health_repository,
        capability_repository=health_capability_repository,
        snapshot_repository=health_snapshot_repository,
        clock=active_clock,
        freshness_policy=freshness_policy,
        unit_of_work=active_snapshot_unit_of_work,
    )
    app.include_router(health_router)
    app.include_router(incident_router)
    app.include_router(dispatch_router)
    app.include_router(protocol_router)
    app.include_router(notification_router)
    app.include_router(persona_session_router)
    app.include_router(agent_run_router)
    app.include_router(agent_tool_router)

    @app.get("/healthz", tags=["readiness"])
    def healthcheck() -> dict[str, str]:
        if app.state.incident_worker_error is not None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "postgres_health_unavailable"},
            )
        if app.state.notification_worker_error is not None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "notification_delivery_unavailable"},
            )
        try:
            protocol_registry.validate_all()
        except ProtocolContentError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "protocol_content_unavailable"},
            ) from exc
        if database_sessions is not None and active_database_scope_id is not None:
            try:
                with database_sessions() as session:
                    require_active_scope(session, active_database_scope_id)
                    session.execute(text("SELECT PostGIS_Version()"))
            except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"code": "postgres_health_unavailable"},
                ) from exc
        return {
            "status": "ok",
            "slice": "persona_sessions",
        }

    return app


async def _run_deadline_worker(
    app: FastAPI,
    service: IncidentService,
    *,
    stop: asyncio.Event,
    poll_seconds: float,
) -> None:
    """Process persisted deadlines until shutdown, surviving transient failures."""

    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            await _process_deadline_batch(app, service)


async def _process_deadline_batch(
    app: FastAPI,
    service: IncidentService,
) -> None:
    try:
        await asyncio.to_thread(service.process_due)
    except Exception as exc:
        app.state.incident_worker_error = exc
    else:
        app.state.incident_worker_error = None


async def _run_notification_worker(
    app: FastAPI,
    worker: NotificationWorker,
    *,
    stop: asyncio.Event,
    poll_seconds: float,
) -> None:
    """Drain durable notification intents until orderly application shutdown."""

    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            await _process_notification_batch(app, worker)


async def _process_notification_batch(
    app: FastAPI,
    worker: NotificationWorker,
) -> None:
    try:
        await asyncio.to_thread(worker.process_due)
    except Exception as exc:
        app.state.notification_worker_error = exc
    else:
        app.state.notification_worker_error = None


async def _cleanup_application_lifespan(
    *,
    app: FastAPI,
    failure: BaseException | None,
    deadline_worker_stop: asyncio.Event,
    deadline_worker_task: asyncio.Task[None] | None,
    notification_worker_stop: asyncio.Event,
    notification_worker_task: asyncio.Task[None] | None,
    notification_provider: APNsNotificationProvider | None,
    agent_runner: ProcessSandboxAgentRunner | None,
    agent_tool_execution_pool: AgentToolExecutionPool | None,
    routing_provider: LiveFirstRoutingProvider | None,
    engine: Engine | None,
) -> None:
    """Stop partial workers and close all resources without masking a failure."""

    cleanup_failures: list[tuple[str, BaseException]] = []
    workers = (
        (
            "deadline worker",
            deadline_worker_stop,
            deadline_worker_task,
        ),
        (
            "notification worker",
            notification_worker_stop,
            notification_worker_task,
        ),
    )
    cancelled_for_failure: set[asyncio.Task[None]] = set()
    for _label, stop, task in workers:
        if task is None:
            continue
        stop.set()
        if failure is not None and not task.done():
            task.cancel()
            cancelled_for_failure.add(task)
    for label, _stop, task in workers:
        if task is None:
            continue
        try:
            await task
        except BaseException as cleanup_error:
            if (
                task in cancelled_for_failure
                and isinstance(cleanup_error, asyncio.CancelledError)
            ):
                continue
            cleanup_failures.append((label, cleanup_error))

    synchronous_resources = (
        ("notification provider", notification_provider, "close"),
        ("routing provider", routing_provider, "close"),
        ("database engine", engine, "dispose"),
    )
    if agent_tool_execution_pool is not None:
        try:
            await asyncio.to_thread(agent_tool_execution_pool.close)
        except BaseException as cleanup_error:
            cleanup_failures.append(
                ("agent tool execution pool", cleanup_error)
            )
    if agent_runner is not None:
        try:
            evidence = await asyncio.to_thread(agent_runner.close)
            _store_agent_cleanup_evidence(
                app=app,
                runner=agent_runner,
                retain_for_retry=bool(evidence.unresolved_checks),
            )
            if evidence.unresolved_checks:
                cleanup_failures.append(
                    (
                        "agent runner",
                        RuntimeError("agent sandbox cleanup is incomplete"),
                    )
                )
        except BaseException as cleanup_error:
            _store_agent_cleanup_evidence(
                app=app,
                runner=agent_runner,
                retain_for_retry=True,
            )
            cleanup_failures.append(("agent runner", cleanup_error))
    for label, resource, operation in synchronous_resources:
        if resource is None:
            continue
        try:
            getattr(resource, operation)()
        except BaseException as cleanup_error:
            cleanup_failures.append((label, cleanup_error))

    if failure is not None:
        for label, cleanup_error in cleanup_failures:
            failure.add_note(
                f"{label} cleanup also failed "
                f"({type(cleanup_error).__name__})"
            )
        return
    if cleanup_failures:
        label, primary_cleanup_error = cleanup_failures[0]
        for secondary_label, secondary_error in cleanup_failures[1:]:
            primary_cleanup_error.add_note(
                f"{secondary_label} cleanup also failed "
                f"({type(secondary_error).__name__})"
            )
        primary_cleanup_error.add_note(f"cleanup source: {label}")
        raise primary_cleanup_error


def _routing_runtime_configuration() -> RoutingRuntimeConfiguration | None:
    """Parse optional live routing without treating empty values as absent."""

    raw_access_token = os.environ.get(MAPBOX_ACCESS_TOKEN_ENV)
    if raw_access_token is None:
        partial_settings = (
            MAPBOX_DIRECTIONS_BASE_URL_ENV,
            ROUTING_TIMEOUT_ENV,
        )
        if any(name in os.environ for name in partial_settings):
            raise ValueError(
                f"{MAPBOX_ACCESS_TOKEN_ENV} is required when live-routing "
                "settings are present"
            )
        return None

    access_token = raw_access_token.strip()
    if (
        not access_token
        or access_token != raw_access_token
        or len(access_token) > 2_048
        or any(character.isspace() for character in access_token)
    ):
        raise ValueError(f"{MAPBOX_ACCESS_TOKEN_ENV} is invalid")

    if MAPBOX_DIRECTIONS_BASE_URL_ENV in os.environ:
        raw_base_url = os.environ[MAPBOX_DIRECTIONS_BASE_URL_ENV]
        base_url = raw_base_url.strip()
        if not base_url or base_url != raw_base_url:
            raise ValueError(
                f"{MAPBOX_DIRECTIONS_BASE_URL_ENV} must be a clean HTTPS URL"
            )
    else:
        base_url = DEFAULT_MAPBOX_DIRECTIONS_BASE_URL
    try:
        base_url = validate_mapbox_directions_base_url(base_url)
    except ValueError as exc:
        raise ValueError(
            f"{MAPBOX_DIRECTIONS_BASE_URL_ENV} must be a clean HTTPS URL"
        ) from exc

    timeout_seconds = _positive_float_setting(
        explicit=None,
        environment_name=ROUTING_TIMEOUT_ENV,
        default=DEFAULT_ROUTING_TIMEOUT_SECONDS,
    )
    if not 0.05 <= timeout_seconds <= 10.0:
        raise ValueError(
            f"{ROUTING_TIMEOUT_ENV} must be between 0.05 and 10 seconds"
        )

    return RoutingRuntimeConfiguration(
        access_token=SecretStr(access_token),
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )


def _create_routing_provider(
    configuration: RoutingRuntimeConfiguration | None,
) -> LiveFirstRoutingProvider:
    """Construct the composite while retaining any raw-client ownership."""

    live_provider = (
        MapboxDirectionsRoutingProvider(
            access_token=configuration.access_token.get_secret_value(),
            base_url=configuration.base_url,
            timeout_seconds=configuration.timeout_seconds,
        )
        if configuration is not None
        else None
    )
    try:
        return LiveFirstRoutingProvider(live_provider)
    except BaseException as construction_error:
        if live_provider is not None:
            try:
                live_provider.close()
            except BaseException as cleanup_error:
                construction_error.add_note(
                    "raw routing provider cleanup also failed "
                    f"({type(cleanup_error).__name__})"
                )
        raise


def _cleanup_failed_database_composition(
    *,
    failure: BaseException,
    routing_provider: LiveFirstRoutingProvider | None,
    agent_runner: ProcessSandboxAgentRunner | None,
    agent_tool_execution_pool: AgentToolExecutionPool | None,
    notification_provider: APNsNotificationProvider | None,
    engine: Engine,
) -> None:
    """Release every constructed resource while preserving the startup error."""

    resources = (
        ("agent tool execution pool", agent_tool_execution_pool),
        ("agent runner", agent_runner),
        ("notification provider", notification_provider),
        ("routing provider", routing_provider),
        ("database engine", engine),
    )
    for label, resource in resources:
        if resource is None:
            continue
        close = getattr(resource, "dispose" if resource is engine else "close")
        try:
            result = close()
            if (
                resource is agent_runner
                and isinstance(result, SandboxCleanupEvidence)
                and result.unresolved_checks
            ):
                _retain_agent_cleanup_retry_on_failure(
                    failure=failure,
                    runner=agent_runner,
                )
                raise RuntimeError("agent sandbox cleanup is incomplete")
        except BaseException as cleanup_error:
            if resource is agent_runner:
                _retain_agent_cleanup_retry_on_failure(
                    failure=failure,
                    runner=agent_runner,
                )
            failure.add_note(
                f"{label} cleanup also failed "
                f"({type(cleanup_error).__name__})"
            )


def _store_agent_cleanup_evidence(
    *,
    app: FastAPI,
    runner: ProcessSandboxAgentRunner,
    retain_for_retry: bool,
) -> None:
    """Publish only host-authored, non-secret sandbox cleanup evidence."""

    app.state.agent_sandbox_cleanup_evidence = runner.last_cleanup_evidence
    app.state.agent_sandbox_cleanup_history = runner.cleanup_history
    if retain_for_retry:
        app.state.agent_sandbox_cleanup_retry = lambda: (
            _retry_agent_sandbox_cleanup(app=app, runner=runner)
        )
    else:
        app.state.agent_sandbox_cleanup_retry = None


def _retry_agent_sandbox_cleanup(
    *,
    app: FastAPI,
    runner: ProcessSandboxAgentRunner,
) -> SandboxCleanupEvidence:
    """Retry cleanup on the same retained runner and refresh host evidence."""

    try:
        evidence = runner.close()
    except BaseException:
        _store_agent_cleanup_evidence(
            app=app,
            runner=runner,
            retain_for_retry=True,
        )
        raise
    _store_agent_cleanup_evidence(
        app=app,
        runner=runner,
        retain_for_retry=bool(evidence.unresolved_checks),
    )
    return evidence


def _retain_agent_cleanup_retry_on_failure(
    *,
    failure: BaseException,
    runner: ProcessSandboxAgentRunner,
) -> None:
    """Keep exact Docker custody reachable when composition cannot return app."""

    setattr(failure, "agent_sandbox_cleanup_retry", runner.close)


def _agent_runtime_configuration() -> AgentRuntimeConfiguration:
    """Parse one explicit process sandbox without probing or fallback."""

    def required(name: str) -> str:
        return _required_environment_setting(
            name,
            when_enabled=AGENT_ENABLED_ENV,
        )

    raw_sandbox = required(AGENT_SANDBOX_ENV)
    try:
        sandbox = SandboxKind(raw_sandbox)
    except ValueError as exc:
        raise ValueError(
            f"{AGENT_SANDBOX_ENV} must be nemoclaw or docker"
        ) from exc
    if sandbox not in {SandboxKind.NEMOCLAW, SandboxKind.DOCKER}:
        raise ValueError(f"{AGENT_SANDBOX_ENV} must be nemoclaw or docker")

    raw_signing_key = required(AGENT_SIGNING_KEY_ENV)
    if "=" in raw_signing_key:
        raise ValueError(
            f"{AGENT_SIGNING_KEY_ENV} must be unpadded base64url"
        )
    try:
        padding = "=" * (-len(raw_signing_key) % 4)
        signing_key = base64.b64decode(
            raw_signing_key + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise ValueError(
            f"{AGENT_SIGNING_KEY_ENV} must be unpadded base64url"
        ) from exc
    if not 32 <= len(signing_key) <= 64:
        raise ValueError(
            f"{AGENT_SIGNING_KEY_ENV} must decode to 32-64 bytes"
        )

    timeout_seconds = _positive_float_setting(
        explicit=None,
        environment_name=AGENT_TIMEOUT_ENV,
        default=DEFAULT_AGENT_TIMEOUT_SECONDS,
    )
    if not 10.0 <= timeout_seconds <= 300.0:
        raise ValueError(
            f"{AGENT_TIMEOUT_ENV} must be between 10 and 300 seconds"
        )
    model = required(AGENT_VLLM_MODEL_ENV)
    if sandbox is SandboxKind.NEMOCLAW:
        _reject_environment_settings(
            selected=sandbox,
            names=(AGENT_DOCKER_VLLM_API_KEY_ENV,),
        )
        sandbox_name = required(AGENT_SANDBOX_NAME_ENV)
        if NEMOCLAW_SANDBOX_NAME_PATTERN.fullmatch(sandbox_name) is None:
            raise ValueError(f"{AGENT_SANDBOX_NAME_ENV} is invalid")
        tool_proxy_endpoint = required(AGENT_TOOL_PROXY_ENDPOINT_ENV)
        if tool_proxy_endpoint != NEMOCLAW_TOOL_PROXY_ENDPOINT:
            raise ValueError(
                f"{AGENT_TOOL_PROXY_ENDPOINT_ENV} must use the reviewed "
                "HTTPS sandbox route"
            )
        vllm_base_url = required(AGENT_VLLM_BASE_URL_ENV)
        if vllm_base_url != NEMOCLAW_INFERENCE_BASE_URL:
            raise ValueError(
                f"{AGENT_VLLM_BASE_URL_ENV} must use the managed NemoClaw "
                "route"
            )
        selection = ProcessSandboxSelection(
            SandboxKind.NEMOCLAW,
            nemoclaw_sandbox_name=sandbox_name,
        )
        inference_api_key = NEMOCLAW_MANAGED_INFERENCE_API_KEY
    else:
        _reject_environment_settings(
            selected=sandbox,
            names=(
                AGENT_SANDBOX_NAME_ENV,
                AGENT_TOOL_PROXY_ENDPOINT_ENV,
                AGENT_VLLM_BASE_URL_ENV,
            ),
        )
        selection = ProcessSandboxSelection(
            SandboxKind.DOCKER,
            docker_compose_file=DOCKER_REVIEWED_COMPOSE_FILE,
        )
        tool_proxy_endpoint = DOCKER_TOOL_PROXY_ENDPOINT
        vllm_base_url = DOCKER_INFERENCE_BASE_URL
        inference_api_key = required(AGENT_DOCKER_VLLM_API_KEY_ENV)

    vllm = VLLMSettings(
        base_url=vllm_base_url,
        model=model,
        api_key=SecretStr(inference_api_key),
        timeout_seconds=max(1.0, timeout_seconds - 5.0),
        max_retries=0,
        temperature=0.0,
    )
    context_selector = generator_context_selector_from_environment(
        vllm,
        provider=(
            "ollama" if sandbox is SandboxKind.DOCKER else "vllm"
        ),
    )
    policy_path = Path(
        os.environ.get(AGENT_POLICY_PATH_ENV, str(DEFAULT_AGENT_POLICY_PATH))
    )
    policy_digest_path = Path(
        os.environ.get(
            AGENT_POLICY_DIGEST_PATH_ENV,
            str(DEFAULT_AGENT_POLICY_DIGEST_PATH),
        )
    )
    if not policy_path.is_absolute() or not policy_digest_path.is_absolute():
        raise ValueError(
            f"{AGENT_POLICY_PATH_ENV} and {AGENT_POLICY_DIGEST_PATH_ENV} "
            "must be absolute paths"
        )
    return AgentRuntimeConfiguration(
        selection=selection,
        signing_key=signing_key,
        tool_proxy_endpoint=tool_proxy_endpoint,
        vllm=vllm,
        timeout_seconds=timeout_seconds,
        policy_path=policy_path,
        policy_digest_path=policy_digest_path,
        context_selector=context_selector,
    )


def _reject_environment_settings(
    *,
    selected: SandboxKind,
    names: tuple[str, ...],
) -> None:
    """Reject even blank settings belonging to the unselected runtime."""

    configured = tuple(name for name in names if name in os.environ)
    if configured:
        raise ValueError(
            f"{', '.join(configured)} cannot be set when "
            f"{AGENT_SANDBOX_ENV}={selected.value}"
        )


def _apns_runtime_configuration() -> APNsRuntimeConfiguration:
    raw_environment = _required_environment_setting(APNS_ENVIRONMENT_ENV).lower()
    try:
        environment = PushEnvironment(raw_environment)
    except ValueError as exc:
        raise ValueError(
            f"{APNS_ENVIRONMENT_ENV} must be sandbox or production"
        ) from exc

    raw_allowlist = _required_environment_setting(NOTIFICATION_ALLOWLIST_ENV)
    responder_ids: set[UUID] = set()
    for raw_identifier in raw_allowlist.split(","):
        value = raw_identifier.strip()
        if not value:
            raise ValueError(
                f"{NOTIFICATION_ALLOWLIST_ENV} must be a comma-separated UUID list"
            )
        try:
            responder_ids.add(UUID(value))
        except ValueError as exc:
            raise ValueError(
                f"{NOTIFICATION_ALLOWLIST_ENV} must contain only UUIDs"
            ) from exc
    if not responder_ids:
        raise ValueError(f"{NOTIFICATION_ALLOWLIST_ENV} cannot be empty")

    topic = _required_environment_setting(APNS_TOPIC_ENV)
    if len(topic) > 255:
        raise ValueError(f"{APNS_TOPIC_ENV} must contain at most 255 characters")

    return APNsRuntimeConfiguration(
        team_id=_required_environment_setting(APNS_TEAM_ID_ENV),
        key_id=_required_environment_setting(APNS_KEY_ID_ENV),
        topic=topic,
        private_key_path=Path(
            _required_environment_setting(APNS_PRIVATE_KEY_PATH_ENV)
        ),
        environment=environment,
        responder_allowlist=frozenset(responder_ids),
        token_encryption_key=SecretStr(
            _required_environment_setting(NOTIFICATION_ENCRYPTION_KEY_ENV)
        ),
        timeout_seconds=_positive_float_setting(
            explicit=None,
            environment_name=APNS_TIMEOUT_ENV,
            default=DEFAULT_APNS_TIMEOUT_SECONDS,
        ),
        poll_seconds=_positive_float_setting(
            explicit=None,
            environment_name=NOTIFICATION_POLL_ENV,
            default=DEFAULT_NOTIFICATION_POLL_SECONDS,
        ),
    )


def _required_environment_setting(
    environment_name: str,
    *,
    when_enabled: str = APNS_ENABLED_ENV,
) -> str:
    value = os.environ.get(environment_name, "").strip()
    if not value:
        raise ValueError(
            f"{environment_name} is required when {when_enabled}=true"
        )
    return value


def _boolean_setting(
    *,
    explicit: bool | None,
    environment_name: str,
    default: bool,
) -> bool:
    if explicit is not None:
        return explicit
    raw_value = os.environ.get(environment_name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{environment_name} must be true or false")


def _positive_int_setting(
    *,
    explicit: int | None,
    environment_name: str,
    default: int,
) -> int:
    raw_value: object = (
        explicit if explicit is not None else os.environ.get(environment_name)
    )
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{environment_name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{environment_name} must be a positive integer")
    return value


def _positive_float_setting(
    *,
    explicit: float | None,
    environment_name: str,
    default: float,
) -> float:
    raw_value: object = (
        explicit if explicit is not None else os.environ.get(environment_name)
    )
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{environment_name} must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{environment_name} must be a positive number")
    return value


def _scope_id_from_environment() -> UUID:
    raw_scope_id = os.environ.get("VITAL_RELAY_DEMO_SCOPE_ID")
    if not raw_scope_id:
        raise ValueError(
            "VITAL_RELAY_DEMO_SCOPE_ID is required when PostgreSQL is configured"
        )
    try:
        return UUID(raw_scope_id)
    except ValueError as exc:
        raise ValueError("VITAL_RELAY_DEMO_SCOPE_ID must be a UUID") from exc


def run() -> None:
    import uvicorn

    uvicorn.run(
        "vital_relay.main:create_app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        factory=True,
    )


if __name__ == "__main__":
    run()
