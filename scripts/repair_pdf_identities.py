#!/usr/bin/env python3
"""Plan, apply, or undo the reversible repair of wrongly identified PDFs.

Without --apply or --undo the command prints the plan as JSON and leaves PDFs
and catalog rows alone. Opening the catalog still runs its idempotent schema
initialisation, so the database file is not strictly read-only.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.identity_repair import (
    apply_repairs,
    build_plan,
    load_manifest,
    undo_repair,
)
from adsfolio.pdf.rename_settings import RenameSettings

_SUMMARY_KEYS = (
    "old_rel",
    "new_rel",
    "sha256",
    "location_id",
    "attachment_id",
    "document_id_before",
    "after_bibcode",
    "metadata_before",
    "metadata_after",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True, help="catalog SQLite")
    parser.add_argument("--root", type=Path, help="library root (plan and apply)")
    parser.add_argument("--manifest", type=Path, help="manifest JSON list")
    parser.add_argument("--rename-settings", type=Path, help="rename settings JSON")
    parser.add_argument("--journal-dir", type=Path, help="journal and backup directory")
    parser.add_argument("--apply", action="store_true", help="apply the plan")
    parser.add_argument("--undo", type=Path, metavar="JOURNAL", help="undo a journal")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command line and return the process exit status."""
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    catalog = PdfCatalog(args.catalog)
    try:
        if args.undo is not None:
            sys.stdout.write(f"Undone: {undo_repair(catalog, args.undo)}\n")
            return 0
        if args.root is None or args.manifest is None or args.rename_settings is None:
            sys.stderr.write("--root, --manifest and --rename-settings are required\n")
            return 2
        entries = load_manifest(args.manifest)
        settings = RenameSettings.from_mapping(
            json.loads(args.rename_settings.read_text(encoding="utf-8"))
        )
        if args.apply:
            if args.journal_dir is None:
                sys.stderr.write("--apply requires --journal-dir\n")
                return 2
            journal = apply_repairs(
                catalog, args.root, entries, settings, args.journal_dir
            )
            sys.stdout.write(f"Applied: {journal}\n")
            return 0
        plan = build_plan(catalog, args.root, entries, settings)
        items = [{key: item[key] for key in _SUMMARY_KEYS} for item in plan["items"]]
        sys.stdout.write(json.dumps({"root": plan["root"], "items": items}, indent=2))
        sys.stdout.write("\n")
        return 0
    finally:
        catalog.close()


if __name__ == "__main__":
    raise SystemExit(main())
