"""Fixed Docker fault material for live runner-boundary evidence."""

from __future__ import annotations

import sys
import time


def main(argv: list[str]) -> int:
    if argv == ["crash"]:
        return 23
    if argv == ["timeout"]:
        time.sleep(30)
        return 0
    if argv == ["malformed"]:
        sys.stdout.write("{")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
