"""The radio environment's physics, and the hub using it: python3 -m pytest radio/"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airhub import HEADER, Hub  # noqa: E402
from environment import SNR_LIMIT, Environment, fspl_1m_db, noise_floor_dbm  # noqa: E402
from test_airhub import tx_frame  # noqa: E402

F, BW = 868.1e6, 125e3


def env(distance, exponent=2.7, interferers=(), shadowing=0.0, seed=1):
    return Environment({"path_loss_exponent": exponent, "shadowing_db": shadowing,
                        "nodes": {"0": {"pos": [0, 0, 0]}, "1": {"pos": [distance, 0, 0]}},
                        "interferers": list(interferers)}, random.Random(seed))


def test_free_space_and_noise_floor():
    assert abs(fspl_1m_db(F) - 31.2) < 0.1                 # 868 MHz at 1 m
    assert abs(noise_floor_dbm(BW, 6) - (-117.0)) < 0.1     # 125 kHz, NF 6 dB


def test_rssi_falls_with_distance():
    near = env(100).reception("0", "1", 14, F, BW, 7)
    far = env(1000).reception("0", "1", 14, F, BW, 7)
    assert abs((near.rssi - far.rssi) - 27.0) < 0.01        # 10 x distance, exponent 2.7
    assert not near.lost and not far.lost


def test_spreading_factor_sets_the_range():
    """Where SF7 can't be heard any more, SF12 still is: its 12.5 dB lower SNR limit."""
    lo, hi = 1.0, 1e6
    for _ in range(60):                                     # The SF7 edge, by bisection
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if not env(mid).reception("0", "1", 14, F, BW, 7).lost else (lo, mid)
    edge7 = lo
    assert env(edge7 * 1.05).reception("0", "1", 14, F, BW, 7).lost
    r12 = env(edge7 * 1.05).reception("0", "1", 14, F, BW, 12)
    assert not r12.lost and r12.snr > SNR_LIMIT[12]
    # 12.5 dB more link budget at exponent 2.7: about 2.9 x the range
    assert not env(edge7 * 2.8).reception("0", "1", 14, F, BW, 12).lost
    assert env(edge7 * 3.0).reception("0", "1", 14, F, BW, 12).lost


def test_near_the_limit_some_packets_are_corrupted():
    lo, hi = 1.0, 1e6
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if not env(mid).reception("0", "1", 14, F, BW, 7).lost else (lo, mid)
    edge = env(lo * 0.98).reception("0", "1", 14, F, BW, 7)
    assert 0 < edge.corrupt_p <= 0.5
    assert env(lo / 4).reception("0", "1", 14, F, BW, 7).corrupt_p == 0


def test_an_interferer_near_the_receiver():
    jammer = {"pos": [1000, 20, 0], "power_dbm": 14, "freq_mhz": 868.1, "bw_khz": 125, "duty": 1.0}
    clean = env(1000).reception("0", "1", 14, F, BW, 7)
    jammed = env(1000, interferers=[jammer]).reception("0", "1", 14, F, BW, 7)
    assert jammed.snr < clean.snr - 20 and jammed.lost
    off_band = dict(jammer, freq_mhz=869.5)
    assert not env(1000, interferers=[off_band]).reception("0", "1", 14, F, BW, 7).lost
    idle = dict(jammer, duty=0.0)
    assert not env(1000, interferers=[idle]).reception("0", "1", 14, F, BW, 7).lost


def test_the_hub_uses_positions_and_explicit_links_win():
    cfg = {"environment": {"nodes": {"0": {"pos": [0, 0, 0]}, "1": {"pos": [300, 0, 0]},
                                     "2": {"pos": [1e6, 0, 0]}}},
           "links": {"0-2": {"rssi": -100, "snr": 0}}}
    hub = Hub(cfg, seed=1)
    hub.nodes = [(str(i), None) for i in range(3)]
    out = dict(hub.deliver("0", tx_frame()))
    rssi1 = HEADER.unpack_from(out["1"])[10]
    assert -100 < rssi1 < -60                                # From the path loss
    assert HEADER.unpack_from(out["2"])[10] == -100          # The hand-set link, though far
    out = dict(hub.deliver("1", tx_frame()))
    assert "0" in out and "2" not in out                     # 1-2: a million metres
