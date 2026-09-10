import json

import pytest

from instrument_master import InstrumentMaster


CSV = "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL\nNSE,E,17939,HINDCOPPER\nNSE,E,1333,RELIANCE\n"

CSV_WITH_CUSTOM_SYMBOL = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_CUSTOM_SYMBOL\n"
    "NSE,E,6364,NATIONALUM,NALCO\n"
)

CSV_FOR_SEARCH = (
    "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_CUSTOM_SYMBOL,SM_SYMBOL_NAME\n"
    "NSE,E,6364,NATIONALUM,NALCO,NATIONAL ALUMINIUM CO LTD\n"
    "NSE,E,1333,RELIANCE,,RELIANCE INDUSTRIES LTD\n"
    "NSE,E,21951,RCOM,,RELIANCE COMMUNICATIONS LTD\n"
    "NSE,D,999,NATIONALUM-FUT,,NATIONALUM NOV FUT\n"  # derivative row — must not leak into an EQUITY search
    "BSE,E,500325,RELIANCE,,RELIANCE INDUSTRIES LTD\n"  # same symbol, different exchange
)


def test_instrument_master_downloads_once_per_day_and_resolves_symbol(tmp_path):
    downloads = []

    def download(url):
        downloads.append(url)
        return CSV.encode("utf-8")

    master = InstrumentMaster(cache_dir=str(tmp_path), downloader=download)
    first = master.resolve("HINDCOPPER", exchange="NSE", segment="EQUITY")
    second = master.resolve("RELIANCE", exchange="NSE", segment="EQUITY")

    assert first["security_id"] == "17939"
    assert first["exchange_segment"] == "NSE_EQ"
    assert second["security_id"] == "1333"
    assert len(downloads) == 1
    assert json.loads((tmp_path / "dhan_security_master.json").read_text())["download_date"]


def test_resolve_falls_back_to_custom_symbol(tmp_path):
    master = InstrumentMaster(cache_dir=str(tmp_path), downloader=lambda url: CSV_WITH_CUSTOM_SYMBOL.encode("utf-8"))

    resolved = master.resolve("NALCO", exchange="NSE", segment="EQUITY")

    assert resolved["security_id"] == "6364"
    assert resolved["symbol"] == "NATIONALUM"  # the real trading symbol, not the alias searched for


def test_resolve_exact_trading_symbol_wins_over_custom_symbol_match(tmp_path):
    csv_with_conflict = (
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_CUSTOM_SYMBOL\n"
        "NSE,E,999,ALIAS,OTHER\n"
        "NSE,E,111,OTHER,SOMETHING\n"
    )
    master = InstrumentMaster(cache_dir=str(tmp_path), downloader=lambda url: csv_with_conflict.encode("utf-8"))

    resolved = master.resolve("OTHER", exchange="NSE", segment="EQUITY")

    assert resolved["security_id"] == "111"  # exact trading-symbol match, not the custom-symbol alias hit


def test_resolve_raises_lookup_error_for_unknown_symbol(tmp_path):
    master = InstrumentMaster(cache_dir=str(tmp_path), downloader=lambda url: CSV.encode("utf-8"))

    with pytest.raises(LookupError):
        master.resolve("DOESNOTEXIST", exchange="NSE", segment="EQUITY")


def test_search_matches_custom_symbol_and_company_name(tmp_path):
    master = InstrumentMaster(cache_dir=str(tmp_path), downloader=lambda url: CSV_FOR_SEARCH.encode("utf-8"))

    results = master.search("nalco", exchange="NSE", segment="EQUITY")

    assert len(results) == 1
    assert results[0]["symbol"] == "NATIONALUM"
    assert results[0]["custom_symbol"] == "NALCO"
    assert results[0]["security_id"] == "6364"


def test_search_matches_via_prefix_on_any_field(tmp_path):
    master_csv = (
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SM_SYMBOL_NAME\n"
        "NSE,E,1333,RELIANCE,RELIANCE INDUSTRIES LTD\n"
        "NSE,E,21951,RCOM,RELIANCE COMMUNICATIONS LTD\n"
        "NSE,E,6364,NATIONALUM,NATIONAL ALUMINIUM CO LTD\n"
    )
    master = InstrumentMaster(cache_dir=str(tmp_path), downloader=lambda url: master_csv.encode("utf-8"))

    results = master.search("REL", exchange="NSE", segment="EQUITY")
    symbols = {r["symbol"] for r in results}

    assert symbols == {"RELIANCE", "RCOM"}  # NATIONALUM's name/symbol don't contain "REL" at all


def test_search_excludes_other_segments_and_exchanges(tmp_path):
    master = InstrumentMaster(cache_dir=str(tmp_path), downloader=lambda url: CSV_FOR_SEARCH.encode("utf-8"))

    results = master.search("NATIONALUM", exchange="NSE", segment="EQUITY")

    assert len(results) == 1  # the FUT row (segment D) must not appear
    assert results[0]["security_id"] == "6364"

    bse_results = master.search("RELIANCE", exchange="BSE", segment="EQUITY")
    assert len(bse_results) == 1
    assert bse_results[0]["security_id"] == "500325"


def test_search_empty_query_returns_no_results(tmp_path):
    master = InstrumentMaster(cache_dir=str(tmp_path), downloader=lambda url: CSV_FOR_SEARCH.encode("utf-8"))

    assert master.search("", exchange="NSE", segment="EQUITY") == []
    assert master.search("   ", exchange="NSE", segment="EQUITY") == []


def test_search_respects_limit(tmp_path):
    rows = ["SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL"]
    rows += [f"NSE,E,{i},TEST{i}" for i in range(30)]
    master = InstrumentMaster(cache_dir=str(tmp_path), downloader=lambda url: "\n".join(rows).encode("utf-8"))

    results = master.search("TEST", exchange="NSE", segment="EQUITY", limit=5)

    assert len(results) == 5