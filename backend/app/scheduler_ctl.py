"""CLI helper for scheduler loop pause checks."""

from __future__ import annotations

import sys

from app.scheduler_control import is_scheduler_paused


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "should-run":
        print("Usage: python -m app.scheduler_ctl should-run", file=sys.stderr)
        return 2
    return 0 if not is_scheduler_paused() else 1


if __name__ == "__main__":
    raise SystemExit(main())
