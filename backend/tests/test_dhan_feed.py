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
    # epoch 1789028162 -> 2026-09-10T08:16:02Z (verified via datetime.fromtimestamp);
    # the original "09:36:02Z" here was a stale hand-computed value that was never
    # actually exercised, since the LTP assertion above used to fail first every time.
    assert tick["timestamp"] == "2026-09-10T08:16:02Z"


def test_decode_dhan_json_message_for_existing_feed_tests():
    tick = decode_market_data('{"SecurityId": "1333", "LTP": 2456.30}')

    assert tick["SecurityId"] == "1333"
    assert tick["LTP"] == 2456.30