"""Run raw-layer data quality checks and write a JSON report.

    python scripts/run_quality.py

Exit code 1 if any `error`-severity check failed. `warn` failures are printed
and recorded but do not fail the run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gbfs.quality import has_blocking_failure, run_checks, write_report  # noqa: E402


def main() -> int:
    checks = run_checks()
    width = max(len(c.name) for c in checks)

    for check in checks:
        if check.passed:
            mark = "PASS"
        else:
            mark = "FAIL" if check.severity == "error" else "WARN"
        print("{:<4}  {:<{w}}  observed={}".format(mark, check.name, check.observed, w=width))
        if not check.passed:
            print("      -> {}".format(check.detail))

    path = write_report(checks)
    print("\nreport written to {}".format(path))

    return 1 if has_blocking_failure(checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
