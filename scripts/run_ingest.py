"""Take one snapshot of every enabled GBFS system.

    python scripts/run_ingest.py
    python scripts/run_ingest.py --system nyc --system chicago

Exit codes: 0 all systems ok, 1 at least one system failed. One bad feed does
not stop the others -- a scheduled job should still capture the cities it can.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gbfs.config import load_config  # noqa: E402
from gbfs.ingest import ingest_system  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot GBFS feeds to Parquet.")
    parser.add_argument(
        "--system",
        action="append",
        dest="systems",
        help="system key to ingest; repeatable. Default: all enabled in config.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    log = logging.getLogger("ingest")

    config = load_config()
    targets = (
        [config.get(key) for key in args.systems] if args.systems else config.enabled_systems()
    )
    if not targets:
        log.error("no systems selected -- everything in config/systems.yml is disabled")
        return 1

    failures = 0
    for system in targets:
        try:
            result = ingest_system(system, config)
            log.info("%s ok: %s", system.key, json.dumps(result))
        except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the rest
            failures += 1
            log.exception("%s failed: %s", system.key, exc)

    if failures:
        log.error("%s of %s systems failed", failures, len(targets))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
