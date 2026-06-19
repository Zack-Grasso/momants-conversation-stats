#!/usr/bin/env python3
"""Render the conversation analysis report to a local HTML file for fast template iteration.

Typical workflow (Docker already running, no rebuild needed after template mount):

  docker compose exec api python scripts/preview_report.py dfd024ee-bb51-4e48-9e2e-fb8fbc5f80ea
  open preview/report.html

Save a snapshot once, then re-render from cached data while editing CSS/layout:

  docker compose exec api python scripts/preview_report.py AGENT_ID --save-snapshot /preview/snapshot.json
  docker compose exec api python scripts/preview_report.py --from-snapshot /preview/snapshot.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.database import SessionLocal
from app.services.report_service import ReportService, apply_report_template


def _default_output() -> Path:
    return Path("/preview/report.html") if Path("/preview").exists() else Path("report-preview.html")


def _load_snapshot(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_snapshot(context: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent_id", nargs="?", help="Agent UUID used to build report context from the DB")
    parser.add_argument(
        "--from-snapshot",
        type=Path,
        metavar="PATH",
        help="Render from a JSON snapshot saved with --save-snapshot (skips DB/API)",
    )
    parser.add_argument(
        "--save-snapshot",
        type=Path,
        metavar="PATH",
        help="When building from agent_id, also write the full context JSON here",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=_default_output(),
        help=f"HTML output path (default: {_default_output()})",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Override template path (default: backend/templates/conversation-analysis-template-v2.html)",
    )
    args = parser.parse_args(argv)

    if args.from_snapshot:
        context = _load_snapshot(args.from_snapshot)
    else:
        if not args.agent_id:
            parser.error("agent_id is required unless --from-snapshot is used")
        db = SessionLocal()
        try:
            service = ReportService(db)
            context = service.build_context(args.agent_id)
            if args.save_snapshot:
                _save_snapshot(context, args.save_snapshot)
        finally:
            db.close()

    html = apply_report_template(context, template_path=args.template)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"Wrote {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
