"""Host-authored terminal evidence for sandboxed coordination-agent runs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from vital_relay.agent.contracts import (
    AgentConclusion,
    AgentFailureCode,
    AgentRunResult,
    AgentRunStatus,
    AgentToolTrace,
    ToolEffect,
    ToolTraceEvidenceSource,
    ToolTraceStatus,
)
from vital_relay.agent.tool_identity import mutation_operation_id
from vital_relay.application.tool_proxy import (
    ToolProxyAuditRecord,
    ToolProxyAuditStatus,
)


_CREDENTIAL_KEY_PARTS = (
    "api_key",
    "access_token",
    "authorization",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
)
_AUTHORIZATION_VALUE_RE = re.compile(
    r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"
)
_CAPABILITY_VALUE_RE = re.compile(
    r"\bv[0-9]+\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{32,}\b"
)
_JWT_VALUE_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}\b"
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|authorization|credential|password|"
    r"private[_-]?key|refresh[_-]?token|secret)\s*[:=]\s*\S+"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
HOST_READ_ACTION_SUMMARY = (
    "The coordination agent completed authorized incident checks."
)
HOST_MUTATION_ACTION_SUMMARY = (
    "The coordination agent completed an authorized coordination update."
)


@dataclass(frozen=True, slots=True)
class AgentRunEvidenceContext:
    """Trusted durable identity used to select authenticated proxy evidence."""

    scope_id: str
    run_id: UUID
    incident_id: UUID
    state_version: int
    policy_sha256: str


def reconcile_agent_result(
    result: AgentRunResult,
    records: Sequence[ToolProxyAuditRecord],
    context: AgentRunEvidenceContext,
    *,
    exact_secret: str = "",
) -> AgentRunResult:
    """Replace sandbox trace bodies with correlated append-only host evidence."""

    try:
        host_trace = host_audit_trace(records, context)
    except Exception:
        return _manualize(result, (), AgentFailureCode.RUNNER_ERROR)
    if result_contains_credential_material(result, exact_secret=exact_secret):
        return _manualize(result, host_trace, AgentFailureCode.RUNNER_ERROR)
    if not runtime_trace_matches_audit(
        result.tool_trace,
        host_trace,
        run_id=context.run_id,
    ):
        return _manualize(result, host_trace, AgentFailureCode.RUNNER_ERROR)

    statuses = {trace.status for trace in host_trace}
    if result.status is AgentRunStatus.COMPLETED:
        if not host_trace or ToolTraceStatus.COMPLETED not in statuses:
            return _manualize(
                result,
                host_trace,
                AgentFailureCode.INVALID_MODEL_OUTPUT,
            )
        if ToolTraceStatus.DENIED in statuses:
            return _manualize(result, host_trace, AgentFailureCode.TOOL_DENIED)
        if ToolTraceStatus.FAILED in statuses:
            return _manualize(result, host_trace, AgentFailureCode.TOOL_FAILED)
    update: dict[str, object] = {"tool_trace": host_trace}
    if result.status is AgentRunStatus.COMPLETED:
        # Model-authored prose is neither persisted nor exposed. The host
        # renders one reviewed operational summary solely from authenticated
        # proxy effects; this is an output boundary, not a coordination plan.
        summary = (
            HOST_MUTATION_ACTION_SUMMARY
            if any(trace.effect is ToolEffect.MUTATE for trace in host_trace)
            else HOST_READ_ACTION_SUMMARY
        )
        update["conclusion"] = AgentConclusion(action_summary=summary)
    return AgentRunResult.model_validate(
        result.model_copy(update=update).model_dump()
    )


def host_audit_trace(
    records: Sequence[ToolProxyAuditRecord],
    context: AgentRunEvidenceContext,
) -> tuple[AgentToolTrace, ...]:
    """Collapse authenticated proxy rows into body-free call evidence."""

    seen_audit_ids: set[UUID] = set()
    grouped: dict[tuple[UUID, str, str], list[ToolProxyAuditRecord]] = {}
    for record in records:
        if record.audit_id in seen_audit_ids:
            raise ValueError("duplicate tool proxy audit ID")
        seen_audit_ids.add(record.audit_id)
        if not _matches_granted_authority(record, context):
            raise ValueError("tool proxy audit has mismatched granted authority")
        identity = (
            record.invocation_id,
            record.tool_name,
            record.request_sha256,
        )
        grouped.setdefault(identity, []).append(record)

    traces: list[AgentToolTrace] = []
    for records_for_invocation in grouped.values():
        starts = sorted(
            (
                record
                for record in records_for_invocation
                if record.status is ToolProxyAuditStatus.STARTED
            ),
            key=_audit_sort_key,
        )
        fresh_terminals = sorted(
            (
                record
                for record in records_for_invocation
                if record.status
                in {ToolProxyAuditStatus.COMPLETED, ToolProxyAuditStatus.FAILED}
            ),
            key=_audit_sort_key,
        )
        paired = min(len(starts), len(fresh_terminals))
        for index in range(paired):
            traces.append(_terminal_trace(fresh_terminals[index], starts[index]))
        for terminal in fresh_terminals[paired:]:
            traces.append(_terminal_trace(terminal, None))
        for record in records_for_invocation:
            if record.status in {
                ToolProxyAuditStatus.REPLAYED,
                ToolProxyAuditStatus.DENIED,
            }:
                traces.append(_terminal_trace(record, None))
        for started in starts[paired:]:
            _validate_started(started)
            traces.append(
                AgentToolTrace(
                    tool_call_id=started.audit_id,
                    tool_name=started.tool_name,
                    arguments={},
                    status=ToolTraceStatus.FAILED,
                    started_at=started.occurred_at,
                    finished_at=started.occurred_at,
                    error_code="audit_incomplete",
                    evidence_source=ToolTraceEvidenceSource.HOST_PROXY_AUDIT,
                    request_sha256=started.request_sha256,
                    proxy_invocation_id=started.invocation_id,
                    effect=started.effect,
                )
            )
    return tuple(sorted(traces, key=_trace_sort_key))


def runtime_trace_matches_audit(
    runtime_trace: Sequence[AgentToolTrace],
    audit_trace: Sequence[AgentToolTrace],
    *,
    run_id: UUID,
) -> bool:
    """Correlate bodies to host hashes without trusting sandbox ordering or IDs."""

    if len(runtime_trace) != len(audit_trace):
        return False
    unmatched = list(audit_trace)
    for runtime in runtime_trace:
        if runtime.evidence_source is not ToolTraceEvidenceSource.RUNTIME:
            return False
        request_sha256 = sha256_json(runtime.arguments)
        result_sha256 = (
            sha256_json(runtime.result)
            if runtime.status is ToolTraceStatus.COMPLETED
            else None
        )
        matched_index = next(
            (
                index
                for index, audited in enumerate(unmatched)
                if _trace_matches(
                    runtime,
                    audited,
                    run_id=run_id,
                    request_sha256=request_sha256,
                    result_sha256=result_sha256,
                )
            ),
            None,
        )
        if matched_index is None:
            return False
        unmatched.pop(matched_index)
    return not unmatched


def result_contains_credential_material(
    result: AgentRunResult,
    *,
    exact_secret: str = "",
) -> bool:
    return _contains_credential_material(
        result.model_dump(mode="json"),
        exact_secret=exact_secret,
    )


def sha256_json(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _matches_granted_authority(
    record: ToolProxyAuditRecord,
    context: AgentRunEvidenceContext,
) -> bool:
    return (
        record.granted_scope_id == context.scope_id
        and record.granted_run_id == context.run_id
        and record.granted_incident_id == context.incident_id
        and record.granted_state_version == context.state_version
        and record.granted_policy_sha256 == context.policy_sha256
        and record.effect is not None
    )


def _terminal_trace(
    record: ToolProxyAuditRecord,
    started: ToolProxyAuditRecord | None,
) -> AgentToolTrace:
    if started is not None:
        _validate_started(started)
        if started.effect is not record.effect:
            raise ValueError("tool proxy audit effect changed")
    if record.status in {
        ToolProxyAuditStatus.COMPLETED,
        ToolProxyAuditStatus.REPLAYED,
    }:
        if record.result_sha256 is None or record.error_code is not None:
            raise ValueError("invalid completed tool proxy evidence")
        status = ToolTraceStatus.COMPLETED
        error_code = None
    elif record.status in {
        ToolProxyAuditStatus.DENIED,
        ToolProxyAuditStatus.FAILED,
    }:
        if record.result_sha256 is not None or record.error_code is None:
            raise ValueError("invalid failed tool proxy evidence")
        status = (
            ToolTraceStatus.DENIED
            if record.status is ToolProxyAuditStatus.DENIED
            else ToolTraceStatus.FAILED
        )
        error_code = record.error_code.value
    else:
        raise ValueError("started evidence requires a terminal record")
    start_time = started.occurred_at if started is not None else record.occurred_at
    finish_time = max(start_time, record.occurred_at)
    return AgentToolTrace(
        tool_call_id=record.audit_id,
        tool_name=record.tool_name,
        arguments={},
        status=status,
        started_at=start_time,
        finished_at=finish_time,
        error_code=error_code,
        evidence_source=ToolTraceEvidenceSource.HOST_PROXY_AUDIT,
        request_sha256=record.request_sha256,
        result_sha256=record.result_sha256,
        proxy_invocation_id=record.invocation_id,
        effect=record.effect,
    )


def _validate_started(record: ToolProxyAuditRecord) -> None:
    if (
        record.effect is not ToolEffect.MUTATE
        or record.result_sha256 is not None
        or record.error_code is not None
    ):
        raise ValueError("invalid started tool proxy evidence")


def _trace_matches(
    runtime: AgentToolTrace,
    audited: AgentToolTrace,
    *,
    run_id: UUID,
    request_sha256: str,
    result_sha256: str | None,
) -> bool:
    if (
        audited.evidence_source is not ToolTraceEvidenceSource.HOST_PROXY_AUDIT
        or runtime.tool_name != audited.tool_name
        or runtime.status is not audited.status
        or request_sha256 != audited.request_sha256
        or result_sha256 != audited.result_sha256
        or audited.effect is None
        or audited.proxy_invocation_id is None
    ):
        return False
    expected_invocation_id = (
        runtime.tool_call_id
        if audited.effect is ToolEffect.READ
        else mutation_operation_id(run_id, runtime.tool_name, runtime.arguments)
    )
    return expected_invocation_id == audited.proxy_invocation_id


def _manualize(
    result: AgentRunResult,
    trace: tuple[AgentToolTrace, ...],
    failure_code: AgentFailureCode,
) -> AgentRunResult:
    finished_at = max(
        (item.finished_at for item in trace),
        default=result.finished_at,
    )
    return AgentRunResult(
        schema_version=result.schema_version,
        run_id=result.run_id,
        incident_id=result.incident_id,
        policy=result.policy,
        model_id=result.model_id,
        sandbox=result.sandbox,
        status=AgentRunStatus.MANUAL_REQUIRED,
        started_at=result.started_at,
        finished_at=max(result.finished_at, finished_at),
        tool_trace=trace,
        failure_code=failure_code,
    )


def _contains_credential_material(value: object, *, exact_secret: str) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(part in normalized for part in _CREDENTIAL_KEY_PARTS):
                return True
            if _contains_credential_material(nested, exact_secret=exact_secret):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(
            _contains_credential_material(item, exact_secret=exact_secret)
            for item in value
        )
    if not isinstance(value, str):
        return False
    return bool(
        (exact_secret and exact_secret in value)
        or _AUTHORIZATION_VALUE_RE.search(value)
        or _CAPABILITY_VALUE_RE.search(value)
        or _JWT_VALUE_RE.search(value)
        or _CREDENTIAL_ASSIGNMENT_RE.search(value)
        or _PRIVATE_KEY_RE.search(value)
    )


def _audit_sort_key(record: ToolProxyAuditRecord) -> tuple[datetime, str]:
    return record.occurred_at, str(record.audit_id)


def _trace_sort_key(trace: AgentToolTrace) -> tuple[datetime, datetime, str]:
    return trace.started_at, trace.finished_at, str(trace.tool_call_id)
