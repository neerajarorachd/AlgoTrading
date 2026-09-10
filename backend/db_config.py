from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse


@dataclass(frozen=True)
class DatabaseConfig:
    """Connection settings for the SQL Server instance reached through SSH."""

    url: str
    host: str
    port: int
    database: str
    username: str
    password: str
    driver: str


def load_database_config(environ: dict[str, str] | None = None) -> DatabaseConfig:
    values = os.environ if environ is None else environ
    connection_string = values.get("DB_CONNECTION_STRING", "").strip()
    if not connection_string:
        raise ValueError("DB_CONNECTION_STRING is required")

    parsed = urlparse(connection_string)
    if parsed.scheme != "mssql+pyodbc":
        raise ValueError("DB_CONNECTION_STRING must use the mssql+pyodbc scheme")

    host = parsed.hostname or ""
    port = parsed.port or 1433
    if host not in {"localhost", "127.0.0.1", "::1"} or port != 1433:
        raise ValueError(
            "DB_CONNECTION_STRING must use localhost:1433 through the SSH tunnel"
        )

    query = parse_qs(parsed.query)
    driver_values = query.get("driver", [])
    driver = unquote(driver_values[0]) if driver_values else ""
    if driver != "ODBC Driver 18 for SQL Server":
        raise ValueError("DB_CONNECTION_STRING must use ODBC Driver 18 for SQL Server")

    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database = unquote(parsed.path.lstrip("/"))
    if not username or not password or not database:
        raise ValueError("DB_CONNECTION_STRING must include username, password, and database")

    return DatabaseConfig(
        url=connection_string,
        host=host,
        port=port,
        database=database,
        username=username,
        password=password,
        driver=driver,
    )
