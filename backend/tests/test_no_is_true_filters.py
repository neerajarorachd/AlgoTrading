"""Guard: no `.is_(True)` / `.is_(False)` filters anywhere in the backend.

They compile to "IS 1" / "IS 0" on SQL Server, which is invalid T-SQL, while
SQLite (this test suite's database) accepts them silently. It broke live
twice: Strategy.active (2026-10-03) and Pattern.enabled (2026-10-15, the app
didn't start). Filter on the bare column, or `== False` / `~column`, instead."""
import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"\.is_\((True|False)\)|\.isnot\((True|False)\)")


def test_no_is_true_or_is_false_filters_in_backend_code():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts or ".venv" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if PATTERN.search(code):
                offenders.append(f"{path.relative_to(BACKEND)}:{n}: {line.strip()}")
    assert not offenders, "invalid on SQL Server -- use the bare column:\n" + "\n".join(offenders)
