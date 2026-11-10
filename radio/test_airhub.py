"""The hub's link model, without QEMU: python3 -m pytest radio/test_airhub.py"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airhub import AIR_RX, AIR_TX, HEADER, MAGIC, Hub  # noqa: E402


def tx_frame(payload=b"hello mesh", sf=7):
    n = HEADER.size - 6 + len(payload)
    return HEADER.pack(MAGIC, n, AIR_TX, sf, 869525000, 125000, 1, 2, 0x1424, 14, 0, 0, 0,
                       41216, 8, len(payload)) + payload


def hub_with(n, links=None, seed=1):
    hub = Hub(links, seed)
    hub.nodes = [(str(i), None) for i in range(n)]
    return hub


def test_everyone_else_hears_it_with_the_link_rssi():
    hub = hub_with(3, {"default": {"rssi": -80, "snr": 5}})
    out = hub.deliver("0", tx_frame())
    assert [d for d, _ in out] == ["1", "2"]
    h = HEADER.unpack_from(out[0][1])
    assert h[2] == AIR_RX and h[10] == -80 and h[11] == 20 and h[12] == 0
    assert out[0][1][HEADER.size:] == b"hello mesh"
    assert struct.unpack_from("<H", out[0][1], 4)[0] == len(out[0][1]) - 6


def test_out_of_range_and_per_link_values():
    hub = hub_with(3, {"links": {"0-2": None, "1-0": {"rssi": -110, "snr": -5}}})
    out = dict(hub.deliver("0", tx_frame()))
    assert set(out) == {"1"}
    assert HEADER.unpack_from(out["1"])[10] == -110
    assert set(dict(hub.deliver("2", tx_frame()))) == {"1"}         # 2-0 is out of range both ways


def test_loss_and_corruption_by_chance():
    hub = hub_with(2, {"default": {"loss": 0.5, "corrupt": 0.5}}, seed=42)
    got = [hub.deliver("0", tx_frame()) for _ in range(400)]
    delivered = [o[0][1] for o in got if o]
    assert 150 < len(delivered) < 250
    corrupt = sum(HEADER.unpack_from(f)[12] for f in delivered)
    assert 0.3 * len(delivered) < corrupt < 0.7 * len(delivered)
