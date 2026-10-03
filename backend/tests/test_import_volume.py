"""A larger, clean file from seed/generate_episodes.py.

20 000 rows here, to keep CI fast. (200 000 rows were timed once by hand; see NOTES.md.)
"""

import subprocess
import sys
from pathlib import Path

from app.config import settings

ROWS = 20_000


def generate_clean_csv(rows: int) -> bytes:
    script = Path(settings.SEED_DIR) / "generate_episodes.py"
    return subprocess.run([sys.executable, str(script), str(rows)], capture_output=True, check=True).stdout


def test_a_large_file_imports_once_and_is_all_already_exists_the_second_time(import_file):
    content = generate_clean_csv(ROWS)

    _, first = import_file(content)
    _, second = import_file(content)

    assert (first["total_rows"], first["imported"]) == (ROWS, ROWS)
    assert sum(first["rejected"].values()) == 0
    assert second["imported"] == 0
    assert second["skipped"]["already_exists"] == ROWS
