# Local Two-Simulator End-to-End Demo — Design

Date: 2026-07-19
Status: Approved

## Goal

Run Vital Relay end to end entirely on one Mac using two iOS simulators — a
general/community person and a responder — with the real database, the agentic
coordination system, and a local LLM. No physical device, no cloud services.

## Chosen stack (from what is installed on the demo machine)

| Concern | Decision | Rationale |
|---|---|---|
| Database | Postgres.app + PostGIS 3 | Installed; PostGIS dispatch (`ST_DWithin`/`ST_Distance`) required |
| LLM | Ollama `gpt-oss:20b` (tool-capable), `qwen3.6:35b` fallback | Running on `:11434`, OpenAI-compatible `/v1` with tool calling |
| Agent sandbox | Docker (real) with an in-process dev seam as tested fallback | Docker Desktop up; compose graph is content-locked so a fallback de-risks the stage demo |
| SOS trigger | Manual SOS button (+ optional scripted fall) | Real code path that works on a simulator via `--demo-venue-location` |
| Responder alert | In-app polling (APNs-to-sim as stretch) | Zero external dependencies; reliable on a simulator |
| Clients | Two iPhone simulators (Xcode 26.6): community + responder | Sims available for iOS 18.5–26.5 |

## End-to-end flow

1. Person sim: enroll community persona → tap Manual SOS (demo venue location) →
   incident created via the real API.
2. Backend persists the incident (optional fall path shows verify-timeout
   escalation).
3. Command triggers `POST /v1/incidents/{id}/agent-runs`; the agent (Ollama via
   the Docker sandbox) observes the incident and calls its bounded tools to rank
   and invite the nearest responder.
4. Responder sim: polling surfaces the invitation → Accept → receives exact
   location, static route, and fixed protocol.
5. Command closes the incident → responder exact data is torn down.

## Work breakdown

- **A. Local infra bring-up** — start Postgres, `CREATE EXTENSION postgis`,
  install the `[agent]` extra, run migrations, seed scope/response-network/persona
  accounts; local loopback `.env`. Mostly existing CLI.
- **B. LLM ↔ sandbox adapter** — bind Ollama to `0.0.0.0`, point the vLLM gateway
  upstream at `:11434`, set `EXPECTED_MODEL`; supply or loosen model provenance
  (`MODEL_REVISION`, `ARTIFACT_SHA256`) for the Ollama digest. New: thin
  config/seam + possible compose override.
- **C. Agent trigger in the flow** — no UI button exists to start an agent run.
  Provide a one-command demo script (and/or a command-app control) that fires the
  run. New.
- **D. Two-sim orchestration** — `scripts/demo-up.sh` that boots DB + backend +
  Ollama check + Docker, launches both sims, installs the app, and prints
  enrollment codes. New.
- **E. Verification** — scripted full-loop run with assertions plus a manual
  runbook doc.

## Key risks / notes

- Compose graph is "content-locked"; wiring Ollama behind Docker is the riskiest
  step — the in-process seam is the fallback.
- Model provenance is fail-closed (64-hex SHA-256 required); use the Ollama model
  digest or a guarded demo seam.
- Ollama must bind `0.0.0.0` (`OLLAMA_HOST`) to be reachable from Docker via
  `host.docker.internal`.
- Health data stays non-authoritative; only manual SOS / fall / check-in / timeout
  / responder acceptance drive transitions (unchanged).
