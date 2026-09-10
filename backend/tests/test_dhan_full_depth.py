import struct

from brokers.dhan_feed import decode_full_market_depth, decode_full_market_depth_packets, decode_market_data


def test_decode_twenty_level_buy_depth_packet():
    levels = b"".join(struct.pack("<dII", 536.0 + index, 100 + index, 2 + index) for index in range(20))
    packet = struct.pack("<HBBII", 332, 41, 1, 17939, 20) + levels

    depth = decode_full_market_depth(packet)

    assert depth["type"] == "Full Market Depth"
    assert depth["exchange_segment"] == "NSE_EQ"
    assert depth["security_id"] == "17939"
    assert depth["side"] == "buy"
    assert depth["level_count"] == 20
    assert depth["levels"][0] == {"price": 536.0, "quantity": 100, "orders": 2}
    assert decode_market_data(packet)["levels"][-1]["quantity"] == 119


def test_decode_two_hundred_level_sell_depth_packet():
    levels = b"".join(struct.pack("<dII", 537.0 + index, 200 + index, 4) for index in range(200))
    packet = struct.pack("<HBBII", 3212, 51, 1, 17939, 200) + levels

    depth = decode_full_market_depth(packet)

    assert depth["side"] == "sell"
    assert depth["level_count"] == 200
    assert len(depth["levels"]) == 200
    assert depth["levels"][-1]["price"] == 736.0


def test_decode_stacked_bid_and_ask_packets():
    buy_levels = b"".join(struct.pack("<dII", 536.0 - index, 100, 2) for index in range(20))
    sell_levels = b"".join(struct.pack("<dII", 537.0 + index, 200, 3) for index in range(20))
    buy_packet = struct.pack("<HBBII", 332, 41, 1, 17939, 1) + buy_levels
    sell_packet = struct.pack("<HBBII", 332, 51, 1, 17939, 2) + sell_levels

    packets = decode_full_market_depth_packets(buy_packet + sell_packet)

    assert [packet["side"] for packet in packets] == ["buy", "sell"]
    assert packets[0]["levels"][0]["price"] == 536.0
    assert packets[1]["levels"][0]["price"] == 537.0