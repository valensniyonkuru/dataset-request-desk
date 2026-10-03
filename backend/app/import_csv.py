"""Import an episodes CSV from the command line, with the same rules as POST /imports.

    python -m app.import_csv /app/seed/episodes.csv

Prints the headline counts and the first 20 problems. The full report is
stored in import_runs, like an upload's. There is no file size limit here.
"""

import sys
from pathlib import Path

from app.db import SessionLocal
from app.importer import ImportFileError, import_episodes

PROBLEMS_SHOWN = 20


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python -m app.import_csv <path to csv>", file=sys.stderr)
        return 2
    path = Path(argv[1])

    try:
        with SessionLocal() as db, path.open("rb") as file:
            run_id, report = import_episodes(db, file, file_name=path.name, started_by=None)
    except (ImportFileError, OSError) as error:
        print(f"Import refused: {getattr(error, 'detail', error)}", file=sys.stderr)
        return 1

    print(f"Import run {run_id}: {report['file_name']} ({report['duration_ms']} ms)")
    print(f"  rows: {report['total_rows']}, imported: {report['imported']}, blank lines: {report['blank_lines']}")
    for bucket in ("skipped", "rejected", "normalised"):
        counts = ", ".join(f"{reason} {count}" for reason, count in report[bucket].items() if count)
        print(f"  {bucket}: {counts or 'none'}")
    if report["previous_runs_with_same_file"]:
        print(f"  same file imported before in runs: {report['previous_runs_with_same_file']}")
    for problem in report["problems"][:PROBLEMS_SHOWN]:
        detail = problem.get("value") or problem.get("fields") or ""
        print(f"  line {problem['line']}: {problem['reason']} {problem['episode_id'] or ''} {detail}")
    if len(report["problems"]) > PROBLEMS_SHOWN or report["problems_truncated"]:
        print(f"  ... more problems in the stored report (GET /imports/{run_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
