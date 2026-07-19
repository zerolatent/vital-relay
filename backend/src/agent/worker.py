"""Single-run sandbox worker for the live coordination agent.

The worker is intentionally a stdin/stdout protocol rather than a daemon.  It
does not own capability signing, policy activation, incident credentials, or
persistence.  NemoClaw/OpenShell or Docker owns process and egress isolation.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from vital_relay.agent.contracts import SandboxKind
from vital_relay.agent.deep_agent import DeepAgentRunner
from vital_relay.agent.http_tools import HttpToolProxyClient
from vital_relay.agent.sandbox_wire import (
    MAX_SANDBOX_REQUEST_BYTES,
    MAX_SANDBOX_RESULT_BYTES,
    SandboxWorkerEnvelope,
)
from vital_relay.evolution.ace.selection import verify_selected_context


def main(argv: Sequence[str] | None = None) -> int:
    """Execute exactly one sealed run; emit no diagnostics across the wire."""

    if argv:
        return 2
    try:
        raw = sys.stdin.buffer.read(MAX_SANDBOX_REQUEST_BYTES + 1)
        envelope = SandboxWorkerEnvelope.from_wire_bytes(raw)
        selected_context = verify_selected_context(
            envelope.selected_context,
            envelope.request,
            available_tools=envelope.invocation.allowed_tools,
            model_id=envelope.vllm.model,
        )
        # The host sealed one concrete sandbox kind into the envelope. Select
        # only its reviewed transport; never probe or retry the other runtime.
        if envelope.sandbox is SandboxKind.NEMOCLAW:
            proxy_client_context = HttpToolProxyClient.nemoclaw(
                envelope.tool_proxy_endpoint
            )
        elif envelope.sandbox is SandboxKind.DOCKER:
            proxy_client_context = HttpToolProxyClient.docker(
                envelope.tool_proxy_endpoint
            )
        else:  # The envelope validator already rejects in-process execution.
            return 1
        with proxy_client_context as proxy_client:
            result = DeepAgentRunner(
                envelope.vllm.to_settings(),
                sandbox=envelope.sandbox,
            ).run(
                envelope.request,
                proxy_client.gateway(),
                policy_snapshot=envelope.policy_snapshot,
                invocation_context=envelope.invocation.to_context(),
                selected_context=selected_context,
            )
        output = result.model_dump_json().encode("utf-8")
        if len(output) > MAX_SANDBOX_RESULT_BYTES:
            return 3
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
        return 0
    except BaseException:
        # Raw exceptions can contain provider payloads, proxy URLs, or secrets.
        # The host converts a non-zero exit into a bounded manual-required result.
        return 1


if __name__ == "__main__":  # pragma: no cover - module CLI edge
    raise SystemExit(main())
