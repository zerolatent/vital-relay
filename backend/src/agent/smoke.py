"""Synthetic in-process typed-tool smoke run for vLLM and Deep Agents."""

from __future__ import annotations

import argparse
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Sequence
from uuid import UUID

from pydantic import BaseModel

from vital_relay.agent.capabilities import (
    ToolCapabilityAuthority,
    ToolInvocationContext,
)

from vital_relay.agent.contracts import (
    AgentIncidentSummary,
    AgentRunRequest,
    AgentRunStatus,
    SandboxKind,
    VLLMSettings,
)
from vital_relay.agent.deep_agent import DeepAgentRunner
from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.agent.tool_contracts import (
    GET_INCIDENT,
    AgentIncidentToolView,
    IncidentBoundToolInput,
)
from vital_relay.agent.tools import (
    BoundedToolGateway,
    ToolBinding,
    ToolInvocationContext,
)
from vital_relay.config import build_generator_context_selector
from vital_relay.evolution.ace import OperationalTool


RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
INCIDENT_ID = UUID("22222222-2222-4222-8222-222222222222")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a synthetic in-process Vital Relay Deep Agent tool-call probe"
        ),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8001/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument(
        "--sandbox",
        choices=[entry.value for entry in SandboxKind],
        default=SandboxKind.IN_PROCESS.value,
    )
    args = parser.parse_args(argv)

    if SandboxKind(args.sandbox) is not SandboxKind.IN_PROCESS:
        parser.error(
            "synthetic smoke supports only in_process; use the authenticated "
            "live-evidence exporter for reviewed sandbox evidence"
        )

    settings = VLLMSettings(
        base_url=args.base_url,
        model=args.model,
        api_key=os.environ.get("VITAL_RELAY_VLLM_API_KEY", "local-vllm"),
    )
    policy_snapshot = _policy_snapshot()
    request = _request(policy_snapshot)
    context_selector = build_generator_context_selector(
        settings,
        revision=args.revision,
        artifact_sha256=args.artifact_sha256,
    )
    selected_context = context_selector.select(
        request,
        available_tools=(OperationalTool.GET_INCIDENT,),
    )
    gateway = BoundedToolGateway(
        (
            ToolBinding(
                name=GET_INCIDENT,
                description=(
                    "Read the authoritative state of the synthetic incident. "
                    "Call exactly once before concluding."
                ),
                input_model=IncidentBoundToolInput,
                handler=_get_incident,
            ),
        )
    )
    sandbox = SandboxKind.IN_PROCESS
    invocation_context = _invocation_context(policy_snapshot)
    result = DeepAgentRunner(
        settings,
        sandbox=sandbox,
    ).run(
        request,
        gateway,
        policy_snapshot=policy_snapshot,
        invocation_context=invocation_context,
        selected_context=selected_context,
    )
    print(result.model_dump_json(indent=2))  # noqa: T201 - explicit smoke CLI
    if result.status is not AgentRunStatus.COMPLETED:
        return 1
    if len(result.tool_trace) != 1:
        return 2
    return 0


def _request(policy_snapshot: CoordinationPolicySnapshot) -> AgentRunRequest:
    now = datetime.now(UTC)
    return AgentRunRequest(
        schema_version=1,
        run_id=RUN_ID,
        objective="coordinate_emergency_response",
        requested_at=now,
        incident=AgentIncidentSummary(
            schema_version=1,
            incident_id=INCIDENT_ID,
            kind="fall",
            state="escalating",
            state_version=3,
            opened_at=now,
            responder_search_active=False,
            accepted_responder_present=False,
            fixed_protocol_available=False,
        ),
        policy=policy_snapshot.reference,
    )


def _get_incident(
    arguments: BaseModel,
    context: ToolInvocationContext,
):
    parsed = IncidentBoundToolInput.model_validate(arguments)
    if (
        parsed.incident_id != context.incident_id
        or parsed.expected_state_version != context.state_version
    ):
        raise ValueError("synthetic incident binding mismatch")
    now = datetime.now(UTC)
    return AgentIncidentToolView(
        schema_version=1,
        incident_id=context.incident_id,
        kind="fall",
        state="escalating",
        state_version=context.state_version,
        opened_at=now,
        updated_at=now,
    ).model_dump(mode="json")


def _policy_snapshot() -> CoordinationPolicySnapshot:
    return CoordinationPolicySnapshot.model_validate(
        {
            "schema_version": 1,
            "policy_id": "smoke",
            "version": "0.1.0",
            "objective": "coordinate_emergency_response",
            "strategy": {
                "mission": "bounded_coordination_progress",
                "principles": ["authoritative_result_feedback"],
                "human_review_conditions": ["tool_denied_failed_or_stale"],
            },
            "tool_budget": {
                "max_total_calls": 1,
                "max_mutating_calls": 0,
                "tools": [
                    {"name": GET_INCIDENT, "effect": "read", "max_calls": 1}
                ],
            },
        }
    )


def _invocation_context(
    policy_snapshot: CoordinationPolicySnapshot,
) -> ToolInvocationContext:
    now = datetime.now(UTC)
    # Synthetic-only host orchestration; this command cannot claim a process sandbox.
    return ToolCapabilityAuthority(secrets.token_bytes(32)).issue(
        run_id=RUN_ID,
        scope_id="synthetic-smoke",
        incident_id=INCIDENT_ID,
        state_version=3,
        policy_sha256=policy_snapshot.sha256,
        allowed_tools=(GET_INCIDENT,),
        issued_at=now,
        lifetime=timedelta(minutes=5),
    )


if __name__ == "__main__":
    raise SystemExit(main())
