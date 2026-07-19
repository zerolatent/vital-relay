"""Repository-root launcher for the Docker live-evidence collector."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "backend/src"))

from vital_relay.agent.docker_live_evidence import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
