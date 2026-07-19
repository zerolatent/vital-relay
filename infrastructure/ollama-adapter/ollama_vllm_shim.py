"""Local demo adapter: present Ollama as a colon-free vLLM-style endpoint.

The reviewed Docker vLLM gateway probes ``GET /v1/models`` and requires the
configured model id (``VITAL_RELAY_VLLM_MODEL``, which forbids ``:``) to appear
verbatim in the returned ids. Ollama always reports ids as ``name:tag`` (e.g.
``gemma4:latest``). This shim sits on the host at ``:8001`` (the port the
reviewed, hash-locked compose expects for the vLLM upstream), forwards to Ollama,
and normalizes model ids by stripping the ``:latest`` tag so ``gemma4:latest``
is advertised as ``gemma4``. Chat-completion requests are forwarded unchanged;
Ollama resolves the bare ``gemma4`` name to ``gemma4:latest``.

Local demo only. Bind is ``0.0.0.0`` so Docker can reach it via
``host.docker.internal``; keep it behind the host firewall.

Run:
    python infrastructure/ollama-adapter/ollama_vllm_shim.py
Environment:
    OLLAMA_HOST_PORT   upstream Ollama port (default 11434)
    SHIM_BIND_PORT     port this shim listens on (default 8001)
"""

from __future__ import annotations

import http.client
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = int(os.environ.get("OLLAMA_HOST_PORT", "11434"))
BIND_PORT = int(os.environ.get("SHIM_BIND_PORT", "8001"))
MAX_BYTES = 8 * 1024 * 1024


def _strip_latest(model_id: str) -> str:
    return model_id[: -len(":latest")] if model_id.endswith(":latest") else model_id


class Handler(BaseHTTPRequestHandler):
    server_version = "OllamaVLLMShim/1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._proxy()

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None
        headers = {"Accept": "application/json", "Connection": "close"}
        ctype = self.headers.get("Content-Type")
        if ctype:
            headers["Content-Type"] = ctype
        try:
            conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=180)
            conn.request(self.command, self.path, body=body, headers=headers)
            upstream = conn.getresponse()
            payload = upstream.read(MAX_BYTES + 1)
            status = upstream.status
            resp_ctype = upstream.getheader("Content-Type", "application/json")
        except Exception:  # noqa: BLE001 - report a bounded gateway error
            self._send(502, b'{"error":"ollama_unavailable"}', "application/json")
            return
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

        # Normalize the model-list ids so a colon-free model name matches.
        if self.command == "GET" and self.path.rstrip("/") == "/v1/models":
            try:
                doc = json.loads(payload)
                for item in doc.get("data", []):
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        item["id"] = _strip_latest(item["id"])
                payload = json.dumps(doc, separators=(",", ":")).encode()
            except Exception:  # noqa: BLE001 - forward as-is on any parse issue
                pass
        self._send(status, payload, resp_ctype)

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_: object) -> None:
        return


def main() -> int:
    print(
        f"ollama-vllm-shim: 0.0.0.0:{BIND_PORT} -> "
        f"{UPSTREAM_HOST}:{UPSTREAM_PORT} (/v1/models ids normalized)",
        flush=True,
    )
    ThreadingHTTPServer(("0.0.0.0", BIND_PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
