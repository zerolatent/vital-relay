"""Framework-neutral agent runner boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from vital_relay.agent.contracts import AgentRunRequest, AgentRunResult
from vital_relay.agent.capability_runtime import ToolInvocationContext
from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.agent.tools import BoundedToolGateway
from vital_relay.evolution.ace.contracts import SelectedContext


class AgentRunner(Protocol):
    """Execute one bounded coordination request without choosing a fallback."""

    def run(
        self,
        request: AgentRunRequest,
        tools: BoundedToolGateway,
        *,
        policy_snapshot: CoordinationPolicySnapshot,
        invocation_context: ToolInvocationContext,
        selected_context: SelectedContext,
    ) -> AgentRunResult:
        """Return a normalized result or an explicit manual-required outcome."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
