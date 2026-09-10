import struct

from brokers.dhan_feed import decode_market_data


def test_decode_dhan_binary_ticker_packet():
    packet = struct.pack("<BHBIfI", 2, 16, 1, 1333, 2854.65, 1789028162)

    tick = decode_market_data(packet)

    assert tick["exchange_segment"] == "NSE_EQ"
    assert tick["security_id"] == "1333"
    assert tick["LTP"] == 2854.65
    assert tick["timestamp"] == "2026-09-10T09:36:02Z"


def test_decode_dhan_json_message_for_existing_feed_tests():
    tick = decode_market_data('{"SecurityId": "1333", "LTP": 2456.30}')

    assert tick["SecurityId"] == "1333"
    assert tick["LTP"] == 2456.30