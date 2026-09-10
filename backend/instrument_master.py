from __future__ import annotations

import csv
import json
import os
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests


DHAN_COMPACT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


class InstrumentMaster:
    """Cache and resolve Dhan's broker-specific instrument master."""

    def __init__(
        self,
        cache_dir: str = "data/instruments",
        downloader: Optional[Callable[[str], bytes]] = None,
        source_url: str = DHAN_COMPACT_URL,
    ):
        self.cache_dir = Path(cache_dir)
        self.downloader = downloader or self._download
        self.source_url = source_url
        self._rows: Optional[List[dict]] = None

    def ensure_daily(self, today: Optional[date] = None) -> Path:
        today = today or date.today()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        data_path = self.cache_dir / "dhan_security_master.csv"
        metadata_path = self.cache_dir / "dhan_security_master.json"

        cached_date = None
        if metadata_path.exists():
            try:
                cached_date = json.loads(metadata_path.read_text(encoding="utf-8")).get("download_date")
            except (OSError, ValueError):
                cached_date = None

        if data_path.exists() and cached_date == today.isoformat():
            return data_path

        content = self.downloader(self.source_url)
        temporary_path = data_path.with_suffix(".tmp")
        temporary_path.write_bytes(content)
        os.replace(str(temporary_path), str(data_path))
        metadata_path.write_text(
            json.dumps({"download_date": today.isoformat(), "source": self.source_url}),
            encoding="utf-8",
        )
        self._rows = None
        return data_path

    def resolve(self, symbol: str, exchange: str = "NSE", segment: str = "EQUITY") -> dict:
        path = self.ensure_daily()
        if self._rows is None:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                self._rows = list(csv.DictReader(handle))

        expected_segment = _normalise_segment(exchange, segment)
        symbol_upper = symbol.upper()
        for row in self._rows:
            row_symbol = _first(row, "SEM_TRADING_SYMBOL", "TRADING_SYMBOL", "trading_symbol")
            row_exchange = _first(row, "SEM_EXM_EXCH_ID", "EXCHANGE", "exchange")
            row_segment = _first(row, "SEM_SEGMENT", "SEGMENT", "segment")
            if row_symbol.upper() != symbol_upper or row_exchange.upper() != exchange.upper():
                continue
            if _normalise_segment(row_exchange, row_segment) != expected_segment:
                continue

            security_id = _first(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID", "security_id")
            if security_id:
                return {
                    "symbol": row_symbol,
                    "exchange": exchange.upper(),
                    "segment": segment.upper(),
                    "exchange_segment": expected_segment,
                    "security_id": security_id,
                    "raw": row,
                }
        raise LookupError(f"Dhan instrument not found: {exchange}:{symbol} ({segment})")

    @staticmethod
    def _download(url: str) -> bytes:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.content


def _first(row: dict, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _normalise_segment(exchange: str, segment: str) -> str:
    value = segment.upper()
    if value in ("NSE_EQ", "BSE_EQ", "NSE_FNO", "BSE_FNO", "NSE_CURRENCY", "BSE_CURRENCY"):
        return value
    exchange = exchange.upper()
    suffix = {
        "E": "EQ",
        "EQUITY": "EQ",
        "F": "FNO",
        "FNO": "FNO",
        "C": "CURRENCY",
        "CURRENCY": "CURRENCY",
        "COMM": "COMM",
        "COMMODITY": "COMM",
    }.get(value, value)
    return f"{exchange}_{suffix}"