import struct

import pytest

from brokers.dhan_feed import decode_market_data


def test_decode_dhan_binary_ticker_packet():
    packet = struct.pack("<BHBIfI", 2, 16, 1, 1333, 2854.65, 1789028162)

    tick = decode_market_data(packet)

    assert tick["exchange_segment"] == "NSE_EQ"
    assert tick["security_id"] == "1333"
    # LTP round-trips through a 4-byte wire float (struct "f"), so it loses precision
    # relative to the Python float64 literal above (2854.64990234375 != 2854.65) —
    # this is expected float32 rounding, not a decode bug.
    assert tick["LTP"] == pytest.approx(2854.65, abs=1e-3)
    # epoch 1789028162 -> 2026-09-10T08:16:02Z via a plain fromtimestamp, but Dhan's
    # live WS epoch field turned out to actually be "the true instant, computed as
    # if IST were UTC" (found 2026-09-17 by cross-checking against the REST
    # historical endpoint's own, independently-correct timestamp decode — see
    # _base_tick's docstring) — the real, correct instant is 5:30 earlier.
    assert tick["timestamp"] == "2026-09-10T02:46:02Z"


def test_decode_dhan_json_message_for_existing_feed_tests():
    tick = decode_market_data('{"SecurityId": "1333", "LTP": 2456.30}')

    assert tick["SecurityId"] == "1333"
    assert tick["LTP"] == 2456.30


def test_decode_dhan_full_packet_includes_five_level_depth():
    header = struct.pack(
        "<BHBIfHIfIIIIIIffff",
        8, 162, 1, 1333, 2854.65, 10, 1789028162, 2850.0,
        500000, 1000, 2000, 0, 0, 0,
        2800.0, 2828.4, 2860.0, 2790.0,
    )
    level1 = struct.pack("<IIHHff", 100, 150, 3, 4, 2854.50, 2855.00)
    level2 = struct.pack("<IIHHff", 200, 250, 5, 6, 2854.25, 2855.25)
    remaining_levels = b"\x00" * (20 * 3)
    packet = header + level1 + level2 + remaining_levels

    tick = decode_market_data(packet)

    assert tick["type"] == "Full Data"
    assert len(tick["buy_depth"]) == 5
    assert len(tick["sell_depth"]) == 5
    assert tick["buy_depth"][0]["quantity"] == 100
    assert tick["buy_depth"][0]["orders"] == 3
    assert tick["buy_depth"][0]["price"] == pytest.approx(2854.50, abs=1e-2)
    assert tick["sell_depth"][0]["quantity"] == 150
    assert tick["sell_depth"][0]["orders"] == 4
    assert tick["sell_depth"][0]["price"] == pytest.approx(2855.00, abs=1e-2)
    assert tick["buy_depth"][1]["quantity"] == 200
    assert tick["sell_depth"][1]["quantity"] == 250