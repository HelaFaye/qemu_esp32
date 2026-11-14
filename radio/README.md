# The air between emulated radios

`airhub.py` joins the LoRa radios (`sx1262`, `sx1261`, `sx1268`) of several QEMUs: each
connects to it over a socket chardev, and a packet one radio sends reaches the others as
they would hear it, with an RSSI and SNR, or not at all, or corrupted. Collisions are
decided in each radio, from the packets' times and strengths (capture at 6 dB).

```sh
python3 radio/airhub.py --port 7400 [--links links.json] [--seed 1] [--log]
```

```sh
-chardev socket,id=air,host=127.0.0.1,port=7400 \
-device sx1262,bus=spi2,cs=1,id=lora,chardev=air
```

(`boards.sx1262(..., air=7400)` does this.) Nodes are named by connection order, "0",
"1", ...

## Links by hand

```json
{"default": {"rssi": -70, "snr": 8, "loss": 0.0, "corrupt": 0.0},
 "links": {"0-1": {"rssi": -95, "snr": 2, "loss": 0.1}, "0-2": null}}
```

`null` puts a pair out of range. Links are symmetric unless the reverse is given too.

## A radio environment

Or positions and physics (`environment.py`), for every pair without a hand-set link:

```json
{"environment": {
   "path_loss_exponent": 2.7, "shadowing_db": 4.0, "noise_figure_db": 6.0,
   "nodes": {"0": {"pos": [0, 0, 1.5], "antenna_dbi": 2.15}, "1": {"pos": [800, 0, 1.5]}},
   "interferers": [{"pos": [400, 50, 2], "power_dbm": 14, "freq_mhz": 868.1,
                    "bw_khz": 125, "duty": 0.3}]}}
```

RSSI from transmit power, antenna gains and log-distance path loss (free space at 1 m,
then the exponent; log-normal shadowing per packet). SNR against thermal noise over the
bandwidth plus the receiver's noise figure, raised by in-band interferers while they're
on. Below the demodulator's SNR limit for the spreading factor (SF7 -7.5 dB to SF12
-20 dB, from Semtech's SX1261/2 datasheet) a packet is lost; within 3 dB above it, some
arrive corrupted.

Tests: `test_airhub.py`, `test_environment.py`. Plans (moving nodes, interferers as
packets, recording and replay): [stress testing](../docs/stress-testing.md).
