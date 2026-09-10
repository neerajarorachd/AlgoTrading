import json

from instrument_master import InstrumentMaster


CSV = "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL\nNSE,E,17939,HINDCOPPER\nNSE,E,1333,RELIANCE\n"


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