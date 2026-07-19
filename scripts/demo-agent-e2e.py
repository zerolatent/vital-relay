#!/usr/bin/env python3
"""Trigger and verify the real Vital Relay local-demo coordination loop.

This operator tool talks only to the public product API. It does not import the
backend, write the database, invoke a replay fixture, or manufacture a successful
agent result. A successful ``verify`` run proves a fresh durable agent execution,
responder invitation and acceptance, fixed-protocol presentation, closure, and
revocation of responder-only exact incident data.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import re
import sys
import time
from typing import Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4


SCHEMA_VERSION = 1
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_INCIDENT_LATITUDE = 41.8781
DEFAULT_INCIDENT_LONGITUDE = -87.6298
MAX_RESPONSE_BYTES = 64 * 1024
MAX_DIAGNOSTIC_CHARACTERS = 700
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,256}$")
TERMINAL_AGENT_STATUSES = frozenset({"completed", "manual_required"})
EXACT_RESPONDER_FIELDS = frozenset(
    {
        "wearer_location",
        "health_snapshot_id",
        "route",
        "protocol",
        "latitude",
        "longitude",
        "coordinate",
    }
)


class DemoFailure(RuntimeError):
    """An operator-safe failure that never includes credentials or exact data."""

    def __init__(self, message: str, *, diagnostic: str | None = None) -> None:
        self.message = message
        self.diagnostic = diagnostic
        super().__init__(message)


class _NoRedirects(HTTPRedirectHandler):
    """Keep bearer credentials on the exact configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: Any


@dataclass(frozen=True)
class PersonaSession:
    persona: str
    session_id: str
    installation_id: str
    account_id: str
    access_token: str
    refresh_token: str | None
    user_id: str | None
    responder_id: str | None


@dataclass(frozen=True)
class TriggerResult:
    incident: dict[str, Any]
    run: dict[str, Any]


class ApiClient:
    """Tiny JSON client with bounded bodies and redirects disabled."""

    def __init__(self, base_url: str, *, timeout_seconds: float) -> None:
        self.base_url = _validated_base_url(base_url)
        if not 0 < timeout_seconds <= 600:
            raise DemoFailure("request timeout must be between 0 and 600 seconds")
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(_NoRedirects())

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ApiResponse:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise DemoFailure("internal demo client path is not normalized")
        request_headers = {
            "Accept": "application/json",
            **(headers or {}),
        }
        data = None
        if payload is not None:
            data = json.dumps(
                payload,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
            status = int(response.status)
            raw = _bounded_read(response)
        except HTTPError as exc:
            status = int(exc.code)
            raw = _bounded_read(exc)
        except (OSError, TimeoutError, URLError) as exc:
            raise DemoFailure(
                f"{method} {path} could not reach the configured API",
                diagnostic=type(exc).__name__,
            ) from exc
        if not raw:
            return ApiResponse(status=status, body=None)
        try:
            return ApiResponse(status=status, body=json.loads(raw))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DemoFailure(
                f"{method} {path} returned invalid JSON",
                diagnostic=f"http_status={status}",
            ) from exc


def _bounded_read(response: Any) -> bytes:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DemoFailure("API response exceeded the 64 KiB demo-client limit")
    return raw


def _validated_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlparse(normalized)
    if (
        value != value.strip()
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DemoFailure("base URL must be a credential-free absolute HTTP(S) URL")
    return normalized


def _safe_diagnostic(body: Any) -> str:
    """Return only allowlisted operational fields from an API body."""

    source = body if isinstance(body, dict) else {}
    detail = source.get("detail")
    if isinstance(detail, dict):
        source = detail
    allowlisted: dict[str, Any] = {}
    for key in (
        "code",
        "status",
        "failure_code",
        "current_state",
        "current_state_version",
        "schema_version",
    ):
        value = source.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if key in source:
                allowlisted[key] = value
    rendered = json.dumps(allowlisted, sort_keys=True, separators=(",", ":"))
    return rendered[:MAX_DIAGNOSTIC_CHARACTERS]


def _expect(
    response: ApiResponse,
    statuses: set[int],
    *,
    operation: str,
) -> Any:
    if response.status not in statuses:
        raise DemoFailure(
            f"{operation} returned HTTP {response.status}",
            diagnostic=_safe_diagnostic(response.body),
        )
    return response.body


def _object(value: Any, *, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DemoFailure(f"{operation} returned a non-object JSON body")
    return value


def _list(value: Any, *, operation: str) -> list[Any]:
    if not isinstance(value, list):
        raise DemoFailure(f"{operation} returned a non-array JSON field")
    return value


def _required_string(
    value: Any,
    *,
    field: str,
    operation: str,
) -> str:
    if not isinstance(value, str) or not value:
        raise DemoFailure(f"{operation} omitted required field {field}")
    return value


def _opaque_token(value: str | None, *, label: str) -> str:
    if value is None or TOKEN_PATTERN.fullmatch(value) is None:
        raise DemoFailure(f"{label} must be a 43-256 character URL-safe token")
    return value


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_opaque_token(token, label='access token')}"}


def _device(token: str) -> dict[str, str]:
    return {
        "X-Vital-Relay-Device-Token": _opaque_token(
            token,
            label="device access token",
        )
    }


def _responder(token: str) -> dict[str, str]:
    return {
        "X-Vital-Relay-Responder-Token": _opaque_token(
            token,
            label="responder access token",
        )
    }


def enroll(
    client: ApiClient,
    *,
    enrollment_token: str,
    installation_id: UUID,
    expected_persona: str,
) -> PersonaSession:
    response = client.request(
        "POST",
        "/v1/persona-sessions",
        headers={
            "X-Vital-Relay-Enrollment-Token": _opaque_token(
                enrollment_token,
                label=f"{expected_persona} enrollment token",
            )
        },
        payload={
            "schema_version": SCHEMA_VERSION,
            "installation_id": str(installation_id),
        },
    )
    body = _object(
        _expect(response, {201}, operation=f"enroll {expected_persona} persona"),
        operation=f"enroll {expected_persona} persona",
    )
    account = _object(
        body.get("account"),
        operation=f"enroll {expected_persona} persona account",
    )
    if account.get("persona") != expected_persona:
        raise DemoFailure(
            f"enrollment token belongs to {account.get('persona')!r}, "
            f"not {expected_persona!r}"
        )
    if body.get("installation_id") != str(installation_id):
        raise DemoFailure("persona receipt installation binding does not match")
    access_token = _opaque_token(
        body.get("access_token"),
        label=f"{expected_persona} access token receipt",
    )
    refresh_token = _opaque_token(
        body.get("refresh_token"),
        label=f"{expected_persona} refresh token receipt",
    )
    session = PersonaSession(
        persona=expected_persona,
        session_id=_required_string(
            body.get("session_id"),
            field="session_id",
            operation="persona enrollment",
        ),
        installation_id=str(installation_id),
        account_id=_required_string(
            account.get("account_id"),
            field="account.account_id",
            operation="persona enrollment",
        ),
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=(account.get("user_id") if isinstance(account.get("user_id"), str) else None),
        responder_id=(
            account.get("responder_id")
            if isinstance(account.get("responder_id"), str)
            else None
        ),
    )
    _assert_current_session(client, session)
    return session


def existing_command_session(client: ApiClient, access_token: str) -> PersonaSession:
    token = _opaque_token(access_token, label="command access token")
    response = client.request(
        "GET",
        "/v1/persona-sessions/current",
        headers=_bearer(token),
    )
    body = _object(
        _expect(response, {200}, operation="authenticate command session"),
        operation="authenticate command session",
    )
    account = _object(body.get("account"), operation="current persona account")
    if account.get("persona") != "command":
        raise DemoFailure("provided access token is not a command persona session")
    return PersonaSession(
        persona="command",
        session_id=_required_string(
            body.get("session_id"), field="session_id", operation="current session"
        ),
        installation_id=_required_string(
            body.get("installation_id"),
            field="installation_id",
            operation="current session",
        ),
        account_id=_required_string(
            account.get("account_id"),
            field="account.account_id",
            operation="current session",
        ),
        access_token=token,
        refresh_token=None,
        user_id=None,
        responder_id=None,
    )


def _assert_current_session(client: ApiClient, session: PersonaSession) -> None:
    body = _object(
        _expect(
            client.request(
                "GET",
                "/v1/persona-sessions/current",
                headers=_bearer(session.access_token),
            ),
            {200},
            operation=f"authenticate {session.persona} session",
        ),
        operation=f"authenticate {session.persona} session",
    )
    account = _object(body.get("account"), operation="current persona account")
    if (
        body.get("session_id") != session.session_id
        or body.get("installation_id") != session.installation_id
        or account.get("account_id") != session.account_id
        or account.get("persona") != session.persona
    ):
        raise DemoFailure("current persona session does not match its enrollment receipt")
    if "access_token" in body or "refresh_token" in body:
        raise DemoFailure("current persona endpoint re-disclosed credential material")


def discover_command_incident(
    client: ApiClient,
    *,
    command: PersonaSession,
    requested_incident_id: UUID | None,
) -> dict[str, Any]:
    body = _object(
        _expect(
            client.request(
                "GET",
                "/v1/command/incidents/active",
                headers=_bearer(command.access_token),
            ),
            {200},
            operation="discover command incidents",
        ),
        operation="discover command incidents",
    )
    if body.get("persona") != "command":
        raise DemoFailure("command discovery returned the wrong persona boundary")
    incidents = [
        _object(item, operation="command incident summary")
        for item in _list(body.get("incidents"), operation="command discovery")
    ]
    eligible = [item for item in incidents if item.get("state") == "escalating"]
    if requested_incident_id is not None:
        requested = str(requested_incident_id)
        matching = [item for item in eligible if item.get("incident_id") == requested]
        if len(matching) != 1:
            raise DemoFailure(
                "requested incident is not one uniquely discoverable escalating incident",
                diagnostic=f"eligible_count={len(eligible)}",
            )
        return matching[0]
    if len(eligible) != 1:
        raise DemoFailure(
            "command discovery must contain exactly one escalating incident; "
            "pass --incident-id when other demo incidents are active",
            diagnostic=f"eligible_count={len(eligible)}",
        )
    return eligible[0]


def trigger_fresh_agent_run(
    client: ApiClient,
    *,
    command: PersonaSession,
    incident: dict[str, Any],
    expected_sandbox: str,
    expected_model: str,
    poll_timeout_seconds: float,
    poll_interval_seconds: float,
) -> TriggerResult:
    incident_id = _required_string(
        incident.get("incident_id"),
        field="incident_id",
        operation="command incident discovery",
    )
    state_version = incident.get("state_version")
    if not isinstance(state_version, int) or state_version < 1:
        raise DemoFailure("command incident discovery returned invalid state_version")
    run_id = str(uuid4())
    response = client.request(
        "POST",
        f"/v1/incidents/{incident_id}/agent-runs",
        headers=_bearer(command.access_token),
        payload={
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "expected_state_version": state_version,
        },
    )
    if response.status == 200:
        raise DemoFailure(
            "agent start returned a terminal replay; replay is not fresh demo evidence",
            diagnostic=_safe_diagnostic(response.body),
        )
    run = _object(
        _expect(response, {201}, operation="start fresh agent run"),
        operation="start fresh agent run",
    )
    _assert_run_identity(
        run,
        incident_id=incident_id,
        run_id=run_id,
        expected_sandbox=expected_sandbox,
        expected_model=expected_model,
    )

    deadline = time.monotonic() + poll_timeout_seconds
    while True:
        durable = _object(
            _expect(
                client.request(
                    "GET",
                    f"/v1/incidents/{incident_id}/agent-runs/{run_id}",
                    headers=_bearer(command.access_token),
                ),
                {200},
                operation="read durable agent run",
            ),
            operation="read durable agent run",
        )
        _assert_run_identity(
            durable,
            incident_id=incident_id,
            run_id=run_id,
            expected_sandbox=expected_sandbox,
            expected_model=expected_model,
        )
        status = durable.get("status")
        if status in TERMINAL_AGENT_STATUSES:
            run = durable
            break
        if status != "running":
            raise DemoFailure(
                "durable agent run returned an unknown status",
                diagnostic=_safe_diagnostic(durable),
            )
        _poll_sleep(deadline, poll_interval_seconds, "durable agent completion")

    if run.get("status") != "completed":
        raise DemoFailure(
            "agent returned control to a human; the automated demo did not complete",
            diagnostic=_safe_diagnostic(run),
        )
    traces = [
        _object(item, operation="agent tool trace")
        for item in _list(run.get("tool_trace"), operation="durable agent run")
    ]
    completed_dispatch = [
        trace
        for trace in traces
        if trace.get("tool_name") == "coordinate_dispatch"
        and trace.get("status") == "completed"
    ]
    if not completed_dispatch:
        raise DemoFailure(
            "fresh agent run completed without an authoritative coordinate_dispatch tool result",
            diagnostic=f"tool_trace_count={len(traces)}",
        )
    return TriggerResult(incident=incident, run=run)


def _assert_run_identity(
    run: dict[str, Any],
    *,
    incident_id: str,
    run_id: str,
    expected_sandbox: str,
    expected_model: str,
) -> None:
    if run.get("run_id") != run_id or run.get("incident_id") != incident_id:
        raise DemoFailure("agent run receipt identity does not match the request")
    if run.get("sandbox") != expected_sandbox:
        raise DemoFailure(
            "agent run used a different sandbox than requested",
            diagnostic=f"actual_sandbox={run.get('sandbox')!r}",
        )
    if run.get("model_id") != expected_model:
        raise DemoFailure(
            "agent run used a different model than requested",
            diagnostic=f"actual_model={run.get('model_id')!r}",
        )


def _poll_sleep(deadline: float, interval: float, operation: str) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DemoFailure(f"timed out polling {operation}")
    time.sleep(min(interval, remaining))


def create_manual_sos(
    client: ApiClient,
    *,
    community: PersonaSession,
    latitude: float,
    longitude: float,
) -> dict[str, Any]:
    if community.user_id is None:
        raise DemoFailure("community enrollment receipt omitted its user binding")
    observed_at = datetime.now(UTC).isoformat()
    event_id = str(uuid4())
    response = client.request(
        "POST",
        "/v1/wearable/events",
        headers=_device(community.access_token),
        payload={
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "user_id": community.user_id,
            "event_type": "manual_sos",
            "source": "manual_sos",
            "simulated": False,
            "observed_at": observed_at,
            "device_id": community.installation_id,
            "sequence": time.time_ns() & ((1 << 63) - 1),
            "location": {
                "latitude": latitude,
                "longitude": longitude,
                "horizontal_accuracy_m": 6.0,
                "captured_at": observed_at,
            },
            "payload": {"activation_method": "iphone_button"},
        },
    )
    body = _object(
        _expect(response, {201}, operation="create fresh manual SOS incident"),
        operation="create fresh manual SOS incident",
    )
    if body.get("status") != "accepted" or body.get("canonical_event_id") != event_id:
        raise DemoFailure("manual SOS ingestion was not accepted as a fresh event")
    incident = _object(body.get("incident"), operation="manual SOS incident")
    if (
        incident.get("state") != "escalating"
        or incident.get("kind") != "manual_sos"
        or incident.get("simulated") is not False
    ):
        raise DemoFailure("manual SOS did not open a real escalating incident")
    return incident


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        result = set(value)
        for item in value.values():
            result.update(_all_keys(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_all_keys(item))
        return result
    return set()


def _assert_preacceptance_privacy(value: Any, *, operation: str) -> None:
    leaked = sorted(EXACT_RESPONDER_FIELDS.intersection(_all_keys(value)))
    if leaked:
        raise DemoFailure(
            f"{operation} exposed responder-only exact incident data before acceptance",
            diagnostic=f"field_count={len(leaked)}",
        )


def wait_for_pending_invitation(
    client: ApiClient,
    *,
    incident_id: str,
    responders: list[PersonaSession],
    poll_timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[PersonaSession, dict[str, Any]]:
    deadline = time.monotonic() + poll_timeout_seconds
    while True:
        pending: list[tuple[PersonaSession, dict[str, Any]]] = []
        for responder in responders:
            body = _object(
                _expect(
                    client.request(
                        "GET",
                        "/v1/responders/me/incidents/active",
                        headers=_bearer(responder.access_token),
                    ),
                    {200},
                    operation="poll responder invitation discovery",
                ),
                operation="poll responder invitation discovery",
            )
            if body.get("persona") != "responder":
                raise DemoFailure("responder discovery crossed its persona boundary")
            _assert_preacceptance_privacy(body, operation="responder discovery")
            incidents = _list(body.get("incidents"), operation="responder discovery")
            for item in incidents:
                summary = _object(item, operation="responder incident summary")
                if (
                    summary.get("incident_id") == incident_id
                    and summary.get("invitation_status") == "pending"
                ):
                    pending.append((responder, summary))
        if len(pending) == 1:
            return pending[0]
        if len(pending) > 1:
            raise DemoFailure("multiple responders received the same pending invitation")
        _poll_sleep(deadline, poll_interval_seconds, "responder invitation")


def accept_invitation(
    client: ApiClient,
    *,
    incident_id: str,
    responder: PersonaSession,
    summary: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    responder_id = responder.responder_id
    if responder_id is None:
        raise DemoFailure("responder session omitted its responder binding")
    invitation_id = _required_string(
        summary.get("invitation_id"),
        field="invitation_id",
        operation="responder discovery",
    )
    invitation_path = (
        f"/v1/incidents/{incident_id}/responders/{responder_id}/invitation"
    )
    invitation = _object(
        _expect(
            client.request(
                "GET",
                invitation_path,
                headers=_responder(responder.access_token),
            ),
            {200},
            operation="read durable responder invitation",
        ),
        operation="read durable responder invitation",
    )
    _assert_preacceptance_privacy(invitation, operation="responder invitation")
    invitation_record = _object(
        invitation.get("invitation"),
        operation="responder invitation record",
    )
    if (
        invitation.get("state") != "escalating"
        or invitation_record.get("invitation_id") != invitation_id
        or invitation_record.get("status") != "pending"
    ):
        raise DemoFailure("durable responder invitation did not match discovery")

    decision_id = str(uuid4())
    accepted = _object(
        _expect(
            client.request(
                "POST",
                f"/v1/incidents/{incident_id}/responders/{responder_id}/response",
                headers=_responder(responder.access_token),
                payload={
                    "schema_version": SCHEMA_VERSION,
                    "decision_id": decision_id,
                    "invitation_id": invitation_id,
                    "decision": "accept",
                    "responded_at": datetime.now(UTC).isoformat(),
                },
            ),
            {200},
            operation="accept responder invitation",
        ),
        operation="accept responder invitation",
    )
    dispatch = _object(
        accepted.get("accepted_dispatch"),
        operation="accepted responder dispatch",
    )
    transition = _object(
        accepted.get("transition"),
        operation="responder acceptance transition",
    )
    if (
        accepted.get("status") != "accepted"
        or accepted.get("decision_id") != decision_id
        or accepted.get("decision") != "accept"
        or transition.get("to_state") != "response_active"
        or transition.get("trigger") != "responder_accepted"
        or dispatch.get("incident_id") != incident_id
        or dispatch.get("state") != "response_active"
        or dispatch.get("simulated") is not False
    ):
        raise DemoFailure("responder acceptance did not activate the real dispatch")
    return accepted, dispatch


def assert_active_dispatch_and_protocol(
    client: ApiClient,
    *,
    incident_id: str,
    command: PersonaSession,
    responder: PersonaSession,
    acceptance_dispatch: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    responder_id = responder.responder_id
    if responder_id is None:
        raise DemoFailure("responder session omitted its responder binding")
    dispatch = _object(
        _expect(
            client.request(
                "GET",
                f"/v1/incidents/{incident_id}/responders/{responder_id}/dispatch",
                headers=_responder(responder.access_token),
            ),
            {200},
            operation="read durable accepted dispatch",
        ),
        operation="read durable accepted dispatch",
    )
    if dispatch != acceptance_dispatch:
        raise DemoFailure("durable accepted dispatch does not match acceptance receipt")
    route = _object(dispatch.get("route"), operation="accepted dispatch route")
    if route.get("travel_mode") != "walking" or not _list(
        route.get("legs"), operation="accepted dispatch route"
    ):
        raise DemoFailure("accepted dispatch did not include a walking route")

    responder_protocol = _object(
        _expect(
            client.request(
                "GET",
                f"/v1/incidents/{incident_id}/responders/{responder_id}/protocol",
                headers=_responder(responder.access_token),
            ),
            {200},
            operation="read responder fixed protocol",
        ),
        operation="read responder fixed protocol",
    )
    command_protocol = _object(
        _expect(
            client.request(
                "GET",
                f"/v1/incidents/{incident_id}/protocol",
                headers=_device(command.access_token),
            ),
            {200},
            operation="read command fixed protocol",
        ),
        operation="read command fixed protocol",
    )
    if responder_protocol != command_protocol:
        raise DemoFailure("command and responder protocol presentations differ")
    protocol = _object(
        responder_protocol.get("protocol"),
        operation="fixed protocol presentation",
    )
    steps = _list(protocol.get("steps"), operation="fixed protocol presentation")
    if (
        responder_protocol.get("responder_id") != responder_id
        or protocol.get("protocol_id") != "manual-sos-response"
        or protocol.get("emergency_kind") != "manual_sos"
        or not isinstance(protocol.get("content_sha256"), str)
        or len(protocol["content_sha256"]) != 64
        or [step.get("sequence") for step in steps if isinstance(step, dict)]
        != list(range(1, len(steps) + 1))
    ):
        raise DemoFailure("fixed manual-SOS protocol failed its presentation contract")
    return route, protocol


def close_and_assert_teardown(
    client: ApiClient,
    *,
    incident_id: str,
    community: PersonaSession,
    command: PersonaSession,
    responders: list[PersonaSession],
    accepting_responder: PersonaSession,
    poll_timeout_seconds: float,
    poll_interval_seconds: float,
) -> None:
    resolution_id = str(uuid4())
    receipt = _object(
        _expect(
            client.request(
                "POST",
                f"/v1/incidents/{incident_id}/resolution",
                headers=_device(command.access_token),
                payload={
                    "schema_version": SCHEMA_VERSION,
                    "resolution_id": resolution_id,
                    "action": "close",
                },
            ),
            {200},
            operation="close incident",
        ),
        operation="close incident",
    )
    resolved_incident = _object(receipt.get("incident"), operation="close receipt")
    transition = _object(receipt.get("transition"), operation="close transition")
    if (
        receipt.get("status") != "accepted"
        or receipt.get("resolution_id") != resolution_id
        or resolved_incident.get("state") != "resolved"
        or transition.get("trigger") != "close"
        or transition.get("to_state") != "resolved"
    ):
        raise DemoFailure("incident closure was not durably accepted")

    responder_id = accepting_responder.responder_id
    if responder_id is None:
        raise DemoFailure("accepting responder session omitted its responder binding")
    deadline = time.monotonic() + poll_timeout_seconds
    while True:
        command_active = _active_ids(
            client,
            "/v1/command/incidents/active",
            command,
            expected_persona="command",
        )
        community_active = _active_ids(
            client,
            "/v1/community/incidents/active",
            community,
            expected_persona="community",
        )
        responder_active = set()
        for responder in responders:
            responder_active.update(
                _active_ids(
                    client,
                    "/v1/responders/me/incidents/active",
                    responder,
                    expected_persona="responder",
                )
            )
        exact_dispatch = client.request(
            "GET",
            f"/v1/incidents/{incident_id}/responders/{responder_id}/dispatch",
            headers=_responder(accepting_responder.access_token),
        )
        exact_protocol = client.request(
            "GET",
            f"/v1/incidents/{incident_id}/responders/{responder_id}/protocol",
            headers=_responder(accepting_responder.access_token),
        )
        absent_from_discovery = all(
            incident_id not in values
            for values in (command_active, community_active, responder_active)
        )
        exact_revoked = (
            exact_dispatch.status == 404
            and _detail_code(exact_dispatch.body) == "accepted_dispatch_not_found"
            and exact_protocol.status == 404
            and _detail_code(exact_protocol.body)
            == "protocol_presentation_not_found"
        )
        if absent_from_discovery and exact_revoked:
            break
        _poll_sleep(deadline, poll_interval_seconds, "incident privacy teardown")

    incident = _object(
        _expect(
            client.request(
                "GET",
                f"/v1/incidents/{incident_id}",
                headers=_device(command.access_token),
            ),
            {200},
            operation="read durable closed incident",
        ),
        operation="read durable closed incident",
    )
    if incident.get("state") != "resolved":
        raise DemoFailure("closed incident was not durably readable as resolved")
    invitation = _object(
        _expect(
            client.request(
                "GET",
                f"/v1/incidents/{incident_id}/responders/{responder_id}/invitation",
                headers=_responder(accepting_responder.access_token),
            ),
            {200},
            operation="read redacted resolved invitation",
        ),
        operation="read redacted resolved invitation",
    )
    if invitation.get("state") != "resolved":
        raise DemoFailure("resolved invitation did not reflect incident closure")
    _assert_preacceptance_privacy(invitation, operation="resolved invitation")


def _active_ids(
    client: ApiClient,
    path: str,
    session: PersonaSession,
    *,
    expected_persona: str,
) -> set[str]:
    body = _object(
        _expect(
            client.request("GET", path, headers=_bearer(session.access_token)),
            {200},
            operation=f"read {expected_persona} active incidents",
        ),
        operation=f"read {expected_persona} active incidents",
    )
    if body.get("persona") != expected_persona:
        raise DemoFailure("active incident discovery crossed its persona boundary")
    incidents = _list(body.get("incidents"), operation="active incident discovery")
    return {
        item["incident_id"]
        for item in incidents
        if isinstance(item, dict) and isinstance(item.get("incident_id"), str)
    }


def _detail_code(body: Any) -> str | None:
    if not isinstance(body, dict) or not isinstance(body.get("detail"), dict):
        return None
    code = body["detail"].get("code")
    return code if isinstance(code, str) else None


def run_trigger(args: argparse.Namespace) -> dict[str, Any]:
    client = _client_from_args(args)
    command_access = args.command_access_token or os.environ.get(
        "VITAL_RELAY_DEMO_COMMAND_ACCESS_TOKEN"
    )
    command_enrollment = args.command_enrollment_token or os.environ.get(
        "VITAL_RELAY_DEMO_COMMAND_ENROLLMENT_TOKEN"
    )
    if bool(command_access) == bool(command_enrollment):
        raise DemoFailure(
            "provide exactly one command access token or command enrollment token"
        )
    command = (
        existing_command_session(client, command_access)
        if command_access
        else enroll(
            client,
            enrollment_token=command_enrollment,
            installation_id=args.command_installation_id or uuid4(),
            expected_persona="command",
        )
    )
    incident = discover_command_incident(
        client,
        command=command,
        requested_incident_id=args.incident_id,
    )
    result = trigger_fresh_agent_run(
        client,
        command=command,
        incident=incident,
        expected_sandbox=args.expected_sandbox,
        expected_model=args.expected_model,
        poll_timeout_seconds=args.poll_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    return {
        "status": "agent_run_completed",
        "incident_id": result.incident["incident_id"],
        "run_id": result.run["run_id"],
        "model_id": result.run["model_id"],
        "sandbox": result.run["sandbox"],
        "tool_trace_count": len(result.run["tool_trace"]),
        "fresh_execution": True,
    }


def run_verify(args: argparse.Namespace) -> dict[str, Any]:
    client = _client_from_args(args)
    community_token = args.community_enrollment_token or os.environ.get(
        "VITAL_RELAY_DEMO_COMMUNITY_ENROLLMENT_TOKEN"
    )
    command_token = args.command_enrollment_token or os.environ.get(
        "VITAL_RELAY_DEMO_COMMAND_ENROLLMENT_TOKEN"
    )
    responder_tokens = list(args.responder_enrollment_token or [])
    if not responder_tokens:
        raw = os.environ.get("VITAL_RELAY_DEMO_RESPONDER_ENROLLMENT_TOKENS", "")
        responder_tokens = [part.strip() for part in raw.split(",") if part.strip()]
    if community_token is None or command_token is None or not responder_tokens:
        raise DemoFailure(
            "verify requires community, command, and at least one responder enrollment token"
        )

    community = enroll(
        client,
        enrollment_token=community_token,
        installation_id=args.community_installation_id or uuid4(),
        expected_persona="community",
    )
    command = enroll(
        client,
        enrollment_token=command_token,
        installation_id=args.command_installation_id or uuid4(),
        expected_persona="command",
    )
    responders = [
        enroll(
            client,
            enrollment_token=token,
            installation_id=uuid4(),
            expected_persona="responder",
        )
        for token in responder_tokens
    ]
    responder_ids = [responder.responder_id for responder in responders]
    if None in responder_ids or len(set(responder_ids)) != len(responder_ids):
        raise DemoFailure("responder enrollment tokens must identify distinct responders")

    incident = create_manual_sos(
        client,
        community=community,
        latitude=args.latitude,
        longitude=args.longitude,
    )
    discovered = discover_command_incident(
        client,
        command=command,
        requested_incident_id=UUID(incident["incident_id"]),
    )
    if discovered.get("state_version") != incident.get("state_version"):
        raise DemoFailure("command discovery did not match the fresh manual SOS")
    triggered = trigger_fresh_agent_run(
        client,
        command=command,
        incident=discovered,
        expected_sandbox=args.expected_sandbox,
        expected_model=args.expected_model,
        poll_timeout_seconds=args.poll_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    accepting_responder, invitation_summary = wait_for_pending_invitation(
        client,
        incident_id=incident["incident_id"],
        responders=responders,
        poll_timeout_seconds=args.poll_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    _, acceptance_dispatch = accept_invitation(
        client,
        incident_id=incident["incident_id"],
        responder=accepting_responder,
        summary=invitation_summary,
    )
    route, protocol = assert_active_dispatch_and_protocol(
        client,
        incident_id=incident["incident_id"],
        command=command,
        responder=accepting_responder,
        acceptance_dispatch=acceptance_dispatch,
    )
    close_and_assert_teardown(
        client,
        incident_id=incident["incident_id"],
        community=community,
        command=command,
        responders=responders,
        accepting_responder=accepting_responder,
        poll_timeout_seconds=args.poll_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    return {
        "status": "verified",
        "incident_id": incident["incident_id"],
        "run_id": triggered.run["run_id"],
        "model_id": triggered.run["model_id"],
        "sandbox": triggered.run["sandbox"],
        "fresh_agent_execution": True,
        "responder_id": accepting_responder.responder_id,
        "route_provider": route.get("provider"),
        "route_source": route.get("source"),
        "protocol_id": protocol.get("protocol_id"),
        "protocol_version": protocol.get("version"),
        "protocol_step_count": len(protocol["steps"]),
        "closure": "resolved",
        "responder_exact_data_teardown": "verified",
    }


def _client_from_args(args: argparse.Namespace) -> ApiClient:
    if not 0 < args.poll_timeout_seconds <= 600:
        raise DemoFailure("poll timeout must be between 0 and 600 seconds")
    if not 0 < args.poll_interval_seconds <= 10:
        raise DemoFailure("poll interval must be between 0 and 10 seconds")
    return ApiClient(args.base_url, timeout_seconds=args.request_timeout_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo-agent-e2e.py",
        description=(
            "Trigger a fresh real agent run or verify the full local Vital Relay loop. "
            "Prefer the documented VITAL_RELAY_DEMO_* environment variables for tokens."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    trigger = commands.add_parser(
        "trigger",
        help="discover an existing incident and run the real coordination agent",
    )
    verify = commands.add_parser(
        "verify",
        help="create and verify a fresh SOS-to-privacy-teardown product loop",
    )
    for command in (trigger, verify):
        command.add_argument(
            "--base-url",
            default=os.environ.get("VITAL_RELAY_DEMO_BASE_URL", DEFAULT_BASE_URL),
        )
        command.add_argument(
            "--request-timeout-seconds",
            type=float,
            default=300.0,
        )
        command.add_argument("--poll-timeout-seconds", type=float, default=45.0)
        command.add_argument("--poll-interval-seconds", type=float, default=0.5)
        command.add_argument(
            "--expected-sandbox",
            choices=("docker", "nemoclaw", "in_process"),
            default=os.environ.get("VITAL_RELAY_DEMO_EXPECTED_SANDBOX", "docker"),
        )
        command.add_argument(
            "--expected-model",
            default=os.environ.get(
                "VITAL_RELAY_DEMO_EXPECTED_MODEL",
                os.environ.get("VITAL_RELAY_VLLM_MODEL", "gpt-oss:20b"),
            ),
        )

    trigger.add_argument("--incident-id", type=UUID)
    trigger.add_argument("--command-access-token", help=argparse.SUPPRESS)
    trigger.add_argument("--command-enrollment-token", help=argparse.SUPPRESS)
    trigger.add_argument("--command-installation-id", type=UUID)

    verify.add_argument("--community-enrollment-token", help=argparse.SUPPRESS)
    verify.add_argument("--command-enrollment-token", help=argparse.SUPPRESS)
    verify.add_argument(
        "--responder-enrollment-token",
        action="append",
        help=argparse.SUPPRESS,
    )
    verify.add_argument("--community-installation-id", type=UUID)
    verify.add_argument("--command-installation-id", type=UUID)
    verify.add_argument("--latitude", type=float, default=DEFAULT_INCIDENT_LATITUDE)
    verify.add_argument("--longitude", type=float, default=DEFAULT_INCIDENT_LONGITUDE)
    return parser


def _validate_coordinates(args: argparse.Namespace) -> None:
    if args.command != "verify":
        return
    if not -90 <= args.latitude <= 90 or not -180 <= args.longitude <= 180:
        raise DemoFailure("demo venue latitude or longitude is out of range")


def main(argv: list[str] | None = None) -> NoReturn:
    try:
        args = build_parser().parse_args(argv)
        _validate_coordinates(args)
        result = run_trigger(args) if args.command == "trigger" else run_verify(args)
    except DemoFailure as exc:
        print(f"demo verification failed: {exc.message}", file=sys.stderr)
        if exc.diagnostic:
            print(f"diagnostic: {exc.diagnostic}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("demo verification interrupted", file=sys.stderr)
        raise SystemExit(130) from None
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
