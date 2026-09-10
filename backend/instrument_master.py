from __future__ import annotations

import csv
import json
import os
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests


DHAN_COMPACT_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

_IndexKey = Tuple[str, str]  # (exchange upper, exchange_segment)


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
        self._index: Optional[Dict[_IndexKey, List[dict]]] = None

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
        self._index = None
        return data_path

    def _ensure_index(self) -> Dict[_IndexKey, List[dict]]:
        """Bucket rows by (exchange, exchange_segment), built once per CSV load.

        Search/resolve only ever care about one (exchange, segment) bucket at a
        time, and equities are a small fraction of the ~200k-row file (mostly
        F&O derivatives) — bucketing turns a full linear scan on every keystroke
        into one over just that bucket, and precomputes the uppercased fields
        once instead of on every comparison.
        """
        if self._index is not None:
            return self._index

        path = self.ensure_daily()
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = csv.DictReader(handle)
            index: Dict[_IndexKey, List[dict]] = {}
            for row in rows:
                row_exchange = _first(row, "SEM_EXM_EXCH_ID", "EXCHANGE", "exchange")
                security_id = _first(row, "SEM_SMST_SECURITY_ID", "SECURITY_ID", "security_id")
                if not row_exchange or not security_id:
                    continue
                row_segment = _first(row, "SEM_SEGMENT", "SEGMENT", "segment")
                exchange_segment = _normalise_segment(row_exchange, row_segment)

                symbol = _first(row, "SEM_TRADING_SYMBOL", "TRADING_SYMBOL", "trading_symbol")
                custom_symbol = _first(row, "SEM_CUSTOM_SYMBOL", "CUSTOM_SYMBOL", "custom_symbol")
                company_name = _first(row, "SM_SYMBOL_NAME", "SYMBOL_NAME", "company_name")

                entry = {
                    "symbol": symbol,
                    "symbol_upper": symbol.upper(),
                    "custom_symbol": custom_symbol or None,
                    "custom_symbol_upper": custom_symbol.upper(),
                    "company_name": company_name or None,
                    "company_name_upper": company_name.upper(),
                    "security_id": security_id,
                    "raw": row,
                }
                index.setdefault((row_exchange.upper(), exchange_segment), []).append(entry)

        self._index = index
        return index

    def search(self, query: str, exchange: str = "NSE", segment: str = "EQUITY", limit: int = 15) -> List[dict]:
        """Symbol/company-name search for a register-instrument autocomplete.

        Matches against trading symbol, custom/common name (e.g. "NALCO"), and
        company name; prefix matches rank above plain substring matches.
        """
        query_upper = query.strip().upper()
        if not query_upper:
            return []

        expected_segment = _normalise_segment(exchange, segment)
        bucket = self._ensure_index().get((exchange.upper(), expected_segment), [])
        prefix_matches: List[dict] = []
        contains_matches: List[dict] = []

        for entry in bucket:
            haystacks = [h for h in (entry["symbol_upper"], entry["custom_symbol_upper"], entry["company_name_upper"]) if h]
            if not any(query_upper in h for h in haystacks):
                continue

            result = {
                "symbol": entry["symbol"],
                "custom_symbol": entry["custom_symbol"],
                "company_name": entry["company_name"],
                "exchange": exchange.upper(),
                "segment": segment.upper(),
                "exchange_segment": expected_segment,
                "security_id": entry["security_id"],
            }

            if any(h.startswith(query_upper) for h in haystacks):
                prefix_matches.append(result)
            else:
                contains_matches.append(result)

            if len(prefix_matches) >= limit:
                break  # already have enough top-tier matches; no need to scan the rest of the bucket

        return (prefix_matches + contains_matches)[:limit]

    def resolve(self, symbol: str, exchange: str = "NSE", segment: str = "EQUITY") -> dict:
        expected_segment = _normalise_segment(exchange, segment)
        bucket = self._ensure_index().get((exchange.upper(), expected_segment), [])
        symbol_upper = symbol.upper()
        fallback = None  # first entry matching by the common/custom name, e.g. "NALCO" for NATIONALUM

        for entry in bucket:
            if entry["symbol_upper"] == symbol_upper:
                return _resolved(entry, exchange, segment, expected_segment)
            if fallback is None and entry["custom_symbol_upper"] == symbol_upper:
                fallback = entry

        if fallback is not None:
            return _resolved(fallback, exchange, segment, expected_segment)
        raise LookupError(f"Dhan instrument not found: {exchange}:{symbol} ({segment})")

    @staticmethod
    def _download(url: str) -> bytes:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.content


def _resolved(entry: dict, exchange: str, segment: str, exchange_segment: str) -> dict:
    return {
        "symbol": entry["symbol"],
        "exchange": exchange.upper(),
        "segment": segment.upper(),
        "exchange_segment": exchange_segment,
        "security_id": entry["security_id"],
        "raw": entry["raw"],
    }


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
