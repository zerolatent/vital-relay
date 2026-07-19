"""Path-restricted reverse proxy to one exact host-side Ollama model."""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


_HOST_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UPSTREAM_HOST = os.environ.get("VLLM_UPSTREAM_HOST", "host.docker.internal")
UPSTREAM_PORT = int(os.environ.get("VLLM_UPSTREAM_PORT", "11434"))
EXPECTED_MODEL = os.environ.get("VLLM_EXPECTED_MODEL", "")
EXPECTED_MODEL_DIGEST = os.environ.get("VLLM_EXPECTED_MODEL_DIGEST", "")
ALLOWED = {
    ("GET", "/v1/models"),
    ("POST", "/v1/chat/completions"),
}
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 8_388_608
MAX_AUTHORIZATION_BYTES = 8_192


def _validate_configuration() -> None:
    if _HOST_PATTERN.fullmatch(UPSTREAM_HOST) is None:
        raise ValueError("invalid vLLM upstream host")
    if not 1 <= UPSTREAM_PORT <= 65_535:
        raise ValueError("invalid vLLM upstream port")
    if (
        not EXPECTED_MODEL
        or EXPECTED_MODEL != EXPECTED_MODEL.strip()
        or len(EXPECTED_MODEL) > 200
        or any(character.isspace() for character in EXPECTED_MODEL)
    ):
        raise ValueError("expected Ollama model is required")
    if _SHA256_PATTERN.fullmatch(EXPECTED_MODEL_DIGEST) is None:
        raise ValueError("expected Ollama model digest is required")


class Handler(BaseHTTPRequestHandler):
    server_version = "VitalRelayVLLMGateway/1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._proxy()

    def _proxy(self) -> None:
        if (self.command, self.path) not in ALLOWED:
            self._json_response(403, {"error": "path_denied"})
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._json_response(400, {"error": "streaming_request_denied"})
            return
        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_length)
        except ValueError:
            self._json_response(400, {"error": "invalid_content_length"})
            return
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self._json_response(413, {"error": "request_too_large"})
            return
        if self.command == "GET" and content_length != 0:
            self._json_response(400, {"error": "unexpected_request_body"})
            return
        content_type = self.headers.get("Content-Type", "")
        if (
            self.command == "POST"
            and content_type.split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            self._json_response(415, {"error": "json_required"})
            return
        authorization = self.headers.get("Authorization")
        if authorization is not None and (
            not authorization.startswith("Bearer ")
            or len(authorization.encode("utf-8")) > MAX_AUTHORIZATION_BYTES
        ):
            self._json_response(401, {"error": "invalid_authorization"})
            return
        body = self.rfile.read(content_length) if content_length else None
        if self.command == "POST":
            request_error = _validate_chat_request(body)
            if request_error is not None:
                self._json_response(403, {"error": request_error})
                return
            if not _expected_manifest_ready():
                self._json_response(
                    503,
                    {"error": "model_provenance_unavailable"},
                )
                return
        headers = {"Accept": "application/json", "Connection": "close"}
        if content_type:
            headers["Content-Type"] = content_type
        if authorization:
            headers["Authorization"] = authorization
        connection = http.client.HTTPConnection(
            UPSTREAM_HOST,
            UPSTREAM_PORT,
            timeout=180,
        )
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        except Exception:
            self._json_response(502, {"error": "upstream_unavailable"})
            return
        finally:
            connection.close()
        if len(payload) > MAX_RESPONSE_BYTES:
            self._json_response(502, {"error": "upstream_response_too_large"})
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

    def _json_response(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Requests can contain model identifiers and operational metadata. The
        # gateway intentionally emits no request log.
        del format, args


def _request_json(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
) -> object | None:
    connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=2)
    try:
        connection.request(
            method,
            path,
            body=body,
            headers={
                "Accept": "application/json",
                "Connection": "close",
                **(
                    {"Content-Type": "application/json"}
                    if body is not None
                    else {}
                ),
            },
        )
        response = connection.getresponse()
        response_body = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 200 or len(response_body) > MAX_RESPONSE_BYTES:
            return None
        return json.loads(response_body)
    except Exception:
        return None
    finally:
        connection.close()


def _check_readiness() -> int:
    if not _expected_manifest_ready():
        return 1

    show_body = json.dumps(
        {"model": EXPECTED_MODEL, "verbose": False},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    model = _request_json("POST", "/api/show", body=show_body)
    if not _model_supports_tools(model):
        return 1

    catalog = _request_json("GET", "/v1/models")
    return 0 if _catalog_contains_expected_model(catalog) else 1


def _expected_manifest_ready() -> bool:
    return _tags_bind_expected_manifest(_request_json("GET", "/api/tags"))


def _validate_chat_request(body: bytes | None) -> str | None:
    try:
        payload = json.loads(body or b"")
    except (UnicodeError, json.JSONDecodeError):
        return "invalid_json"
    if not isinstance(payload, dict):
        return "invalid_request"
    if payload.get("model") != EXPECTED_MODEL:
        return "model_denied"
    if payload.get("stream") is True:
        return "streaming_response_denied"
    return None


def _tags_bind_expected_manifest(payload: object) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return False
    matches = [
        item
        for item in payload["models"]
        if isinstance(item, dict) and item.get("name") == EXPECTED_MODEL
    ]
    if len(matches) != 1:
        return False
    return _normalized_ollama_digest(matches[0].get("digest")) == (
        EXPECTED_MODEL_DIGEST
    )


def _model_supports_tools(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    capabilities = payload.get("capabilities")
    return (
        isinstance(capabilities, list)
        and all(isinstance(item, str) for item in capabilities)
        and "tools" in capabilities
    )


def _catalog_contains_expected_model(payload: object) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return False
    matches = [
        item
        for item in payload["data"]
        if isinstance(item, dict) and item.get("id") == EXPECTED_MODEL
    ]
    return len(matches) == 1


def _normalized_ollama_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.removeprefix("sha256:")
    return normalized if _SHA256_PATTERN.fullmatch(normalized) else None


def main(argv: list[str]) -> int:
    try:
        _validate_configuration()
    except (TypeError, ValueError):
        return 2
    if argv == ["--check-readiness"]:
        return _check_readiness()
    if argv:
        return 2
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
