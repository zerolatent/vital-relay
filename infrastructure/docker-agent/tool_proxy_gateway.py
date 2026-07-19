"""Capability-preserving proxy for the one host Agent A3 tool route."""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


_HOST_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_CAPABILITY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
UPSTREAM_SCHEME = os.environ.get("TOOL_PROXY_UPSTREAM_SCHEME", "http")
UPSTREAM_HOST = os.environ.get(
    "TOOL_PROXY_UPSTREAM_HOST",
    "host.docker.internal",
)
UPSTREAM_PORT = int(os.environ.get("TOOL_PROXY_UPSTREAM_PORT", "8000"))
TOOL_PROXY_PATH = "/internal/v1/agent/tools/invoke"
CAPABILITY_HEADER = "X-Vital-Relay-Agent-Capability"
ALLOWED = {("POST", TOOL_PROXY_PATH)}
MAX_CAPABILITY_BYTES = 8_192
MAX_REQUEST_BYTES = 32_768
MAX_RESPONSE_BYTES = 131_072
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080


def _validate_configuration() -> None:
    if UPSTREAM_SCHEME not in {"http", "https"}:
        raise ValueError("invalid tool proxy upstream scheme")
    if _HOST_PATTERN.fullmatch(UPSTREAM_HOST) is None:
        raise ValueError("invalid tool proxy upstream host")
    if not 1 <= UPSTREAM_PORT <= 65_535:
        raise ValueError("invalid tool proxy upstream port")


class Handler(BaseHTTPRequestHandler):
    server_version = "VitalRelayToolProxyGateway/1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._proxy()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._deny_path()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._deny_path()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._deny_path()

    def _deny_path(self) -> None:
        self._proxy_error(403, "tool_not_allowed")

    def _proxy(self) -> None:
        if (self.command, self.path) not in ALLOWED:
            self._deny_path()
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._proxy_error(422, "invalid_arguments")
            return
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            self._proxy_error(422, "invalid_arguments")
            return
        capability = self.headers.get(CAPABILITY_HEADER, "")
        try:
            capability_bytes = capability.encode("ascii")
        except UnicodeError:
            capability_bytes = b""
        if (
            not capability_bytes
            or len(capability_bytes) > MAX_CAPABILITY_BYTES
            or _CAPABILITY_PATTERN.fullmatch(capability) is None
        ):
            self._proxy_error(401, "invalid_capability")
            return
        raw_length = self.headers.get("Content-Length", "")
        try:
            content_length = int(raw_length)
        except ValueError:
            self._proxy_error(422, "invalid_arguments")
            return
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self._proxy_error(422, "invalid_arguments")
            return
        body = self.rfile.read(content_length)
        connection_class = (
            http.client.HTTPSConnection
            if UPSTREAM_SCHEME == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(UPSTREAM_HOST, UPSTREAM_PORT, timeout=5)
        try:
            connection.request(
                "POST",
                TOOL_PROXY_PATH,
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Connection": "close",
                    CAPABILITY_HEADER: capability,
                },
            )
            response = connection.getresponse()
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        except Exception:
            self._proxy_error(503, "application_failed")
            return
        finally:
            connection.close()
        if len(payload) > MAX_RESPONSE_BYTES:
            self._proxy_error(503, "invalid_result")
            return
        self.send_response(response.status)
        self.send_header(
            "Content-Type",
            response.getheader("Content-Type", "application/json"),
        )
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _proxy_error(self, status: int, code: str) -> None:
        body = json.dumps(
            {"detail": {"code": code}},
            separators=(",", ":"),
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Capabilities and incident metadata must never enter gateway logs.
        del format, args


def _check_readiness(
    *,
    host: str = "127.0.0.1",
    port: int = LISTEN_PORT,
) -> int:
    """Prove the reviewed local listener without requiring the API upstream."""

    connection = http.client.HTTPConnection(host, port, timeout=2)
    try:
        connection.request("GET", "/__vital_relay_gateway_readiness__")
        response = connection.getresponse()
        payload = response.read(256)
    except Exception:
        return 1
    finally:
        connection.close()
    expected = b'{"detail":{"code":"tool_not_allowed"}}'
    return 0 if response.status == 403 and payload == expected else 1


def main(argv: list[str]) -> int:
    try:
        _validate_configuration()
    except (TypeError, ValueError):
        return 2
    if argv == ["--check-readiness"]:
        return _check_readiness()
    if argv:
        return 2
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
