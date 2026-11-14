"""A radio environment for the air hub: where the nodes are, how the signal falls off
between them, the noise and interference at each receiver, and what a LoRa receiver can
still demodulate.

Configured in the hub's JSON (--links) under "environment":

    "environment": {
      "path_loss_exponent": 2.7,       # 2 free space; 2.7-3.5 urban; 4-6 indoor, obstructed
      "shadowing_db": 4.0,             # log-normal shadowing sigma, per packet (0: none)
      "noise_figure_db": 6.0,          # the receiver's (SX126x: about 6 dB)
      "nodes": {"0": {"pos": [0, 0, 1.5], "antenna_dbi": 2.15},
                "1": {"pos": [800, 0, 1.5]}},
      "interferers": [{"pos": [400, 50, 2], "power_dbm": 14, "freq_mhz": 868.1,
                       "bw_khz": 125, "duty": 0.3}]
    }

Units: metres, dBm, dB. Node names are the hub's ("0", "1", ...: connection order).
Sources: free-space path loss (Friis) at 1 m, then the log-distance model; the noise floor
is thermal noise (-174 dBm/Hz) over the bandwidth plus the noise figure; the demodulation
limits are Semtech's SNR limits per spreading factor (SX1261/2 datasheet, table 6-1).
"""
import math

# Minimum SNR to demodulate, per spreading factor (dB)
SNR_LIMIT = {5: -2.5, 6: -5.0, 7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}
C = 299_792_458.0


def fspl_1m_db(freq_hz):
    """Free-space path loss at 1 m."""
    return 20 * math.log10(4 * math.pi * freq_hz / C)


def path_loss_db(distance_m, freq_hz, exponent, shadowing=0.0):
    d = max(distance_m, 1.0)
    return fspl_1m_db(freq_hz) + 10 * exponent * math.log10(d) + shadowing


def noise_floor_dbm(bw_hz, noise_figure_db):
    return -174 + 10 * math.log10(bw_hz) + noise_figure_db


def dbm_sum(*levels):
    return 10 * math.log10(sum(10 ** (x / 10) for x in levels if x is not None and x > -300))


class Reception:
    def __init__(self, rssi, snr, lost, corrupt_p):
        self.rssi, self.snr, self.lost, self.corrupt_p = rssi, snr, lost, corrupt_p


class Environment:
    def __init__(self, cfg, rng):
        self.rng = rng
        self.exponent = cfg.get("path_loss_exponent", 2.7)
        self.shadowing = cfg.get("shadowing_db", 0.0)
        self.nf = cfg.get("noise_figure_db", 6.0)
        self.nodes = {str(k): v for k, v in cfg.get("nodes", {}).items()}
        self.interferers = cfg.get("interferers", [])

    def knows(self, a, b):
        return a in self.nodes and b in self.nodes

    @staticmethod
    def distance(p, q):
        return math.dist(p, q)

    def interference_dbm(self, rx_pos, freq_hz, bw_hz):
        """Interference power at a receiver, now: each in-band interferer, if it's on (its
        duty cycle), through the same path loss."""
        levels = []
        for itf in self.interferers:
            f = itf.get("freq_mhz", freq_hz / 1e6) * 1e6
            ibw = itf.get("bw_khz", bw_hz / 1e3) * 1e3
            overlap = min(f + ibw / 2, freq_hz + bw_hz / 2) - max(f - ibw / 2, freq_hz - bw_hz / 2)
            if overlap <= 0 or self.rng.random() >= itf.get("duty", 1.0):
                continue
            pl = path_loss_db(self.distance(itf["pos"], rx_pos), f, self.exponent)
            # The share of its power inside the receiver's bandwidth
            levels.append(itf["power_dbm"] + itf.get("antenna_dbi", 0) - pl + 10 * math.log10(overlap / ibw))
        return levels

    def reception(self, src, dst, power_dbm, freq_hz, bw_hz, sf):
        a, b = self.nodes[src], self.nodes[dst]
        shadow = self.rng.gauss(0, self.shadowing) if self.shadowing else 0.0
        pl = path_loss_db(self.distance(a["pos"], b["pos"]), freq_hz, self.exponent, shadow)
        rssi = power_dbm + a.get("antenna_dbi", 0) + b.get("antenna_dbi", 0) - pl
        noise = dbm_sum(noise_floor_dbm(bw_hz, self.nf), *self.interference_dbm(b["pos"], freq_hz, bw_hz))
        snr = rssi - noise
        margin = snr - SNR_LIMIT.get(sf, -7.5)
        # Below the limit: nothing (no preamble lock). Within 3 dB above it: some packets
        # are received with errors, fewer the further above.
        lost = margin < 0
        corrupt_p = 0.0 if margin >= 3 else max(0.0, 1 - margin / 3) * 0.5
        return Reception(rssi, snr, lost, corrupt_p)
