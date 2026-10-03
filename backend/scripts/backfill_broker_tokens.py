"""One-off backfill (2026-10-04): mirrors Trading's own BrokerAccount/
BrokerToken rows (~/Trading/Trading.db, SQLite, on the VM) into
AlgoTrading's SQL Server -- the "one-time backfill of the 5 current
tokens" CLAUDE.md's "Broker token pool" section names as not-yet-done.
AlgoTrading only ever READS Trading's data here (read-only sqlite3 over
SSH); nothing on the Trading side is touched. Safe to re-run (upsert by
primary key) whenever tokens are refreshed and need re-mirroring, until
~/Trading/LibSQLServerTokenMirror.py (the approved, automatic version of
this same mirror, called from Trading's own refresh cycle) exists.

Never prints AccessToken/RefreshToken values -- only row counts and
non-secret metadata (TokenID, TokenType, timestamps) -- so a run of this
script is safe to paste into a chat transcript or log.

    .venv/Scripts/python.exe backend/scripts/backfill_broker_tokens.py
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models import Base, BrokerAccount, BrokerToken
from db.session import build_engine, build_session_factory

SSH_HOST = "trading-server"
TRADING_DB_PATH = "~/Trading/Trading.db"


def _parse_dt(value):
    """SQLite's own TEXT timestamp format ("YYYY-MM-DD HH:MM:SS[.ffffff]")
    -> a real datetime, since SQL Server's DateTime columns need one, not
    a plain string. None/blank passes through unchanged (ExpiresAt is
    unset on every row seen so far)."""
    return None if not value else datetime.fromisoformat(value)


def _fetch_json(table: str) -> list:
    """Read-only sqlite3 query over SSH -- never writes to Trading.db."""
    result = subprocess.run(
        ["ssh", SSH_HOST, f"sqlite3 -readonly -json {TRADING_DB_PATH} 'SELECT * FROM {table}'"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else []


def main() -> None:
    accounts = _fetch_json("BrokerAccount")
    tokens = _fetch_json("BrokerToken")
    print(f"Read {len(accounts)} BrokerAccount row(s), {len(tokens)} BrokerToken row(s) from Trading.db (read-only).")

    engine = build_engine()
    Base.metadata.create_all(engine)  # creates BrokerAccount/BrokerToken on AlgoTrading's own DB if first run
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        for row in accounts:
            existing = session.get(BrokerAccount, row["AccountID"])
            fields = dict(
                Broker=row["Broker"], ClientID=row["ClientID"],
                ApiKey=row.get("ApiKey"), ApiSecret=row.get("ApiSecret"),
                IsActive=bool(row["IsActive"]),
            )
            if existing is None:
                session.add(BrokerAccount(AccountID=row["AccountID"], **fields))
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)

        for row in tokens:
            existing = session.get(BrokerToken, row["TokenID"])
            fields = dict(
                AccountID=row["AccountID"], TokenType=row["TokenType"],
                AccessToken=row["AccessToken"], RefreshToken=row.get("RefreshToken"),
                ExpiresAt=_parse_dt(row.get("ExpiresAt")), IsActive=bool(row["IsActive"]),
                UpdatedAt=_parse_dt(row["UpdatedAt"]), LastRefreshedAt=_parse_dt(row.get("LastRefreshedAt")),
            )
            if existing is None:
                session.add(BrokerToken(TokenID=row["TokenID"], **fields))
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
        session.commit()

    print(f"Upserted {len(accounts)} BrokerAccount row(s), {len(tokens)} BrokerToken row(s) into SQL Server.")
    print("TokenID -> TokenType, LastRefreshedAt (no secret values shown):")
    for row in sorted(tokens, key=lambda r: r["TokenID"]):
        print(f"  {row['TokenID']} -> TokenType={row['TokenType']}, LastRefreshedAt={row.get('LastRefreshedAt')}")


if __name__ == "__main__":
    main()
