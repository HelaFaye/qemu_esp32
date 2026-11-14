# Fault injection

What a voltage glitch or an EM pulse does to firmware, injected exactly where it's aimed
in an emulated ESP32: an instruction that doesn't run, a register or a byte of memory
that comes out wrong, a reset. The plan this is part of: [docs/stress-testing.md](../docs/stress-testing.md).

It works through QEMU's GDB stub (`-gdb tcp::PORT`), so any board in
`firmware/boards.py` and any firmware can be glitched, with no change to either.

## The API (`inject.py`)

```python
from boards import boot
from inject import Injector

node = boot("esp32s3-devkitc", "flash.bin", "run", extra=["-gdb", "tcp:127.0.0.1:1234"])
node.wait_for("ready")
inj = Injector(1234)
inj.skip(0x42001201)                              # Next time it gets there: not executed
inj.flip_register_after(0x420011ef, "a2", bit=4)  # Executed, its result with bit 4 flipped
inj.set_register_after(0x420011ef, "a2", 0)       # ... or zeroed
inj.flip_memory(0x3fc90000, bit=7)                # Now (or at=ADDR: when it gets there)
node.qmp("system_reset")                          # A reset, no more
inj.close()
```

`count=N` picks the N-th time an address is reached. Registers: `pc`, `a0`-`a15` (the
current window), `ar0`-`ar63`, `sar`, `windowbase`, `windowstart`, `ps`.

## Power (`boards.Node`, patch 0010)

```python
node.set_vdd(2300)            # Below the brown-out threshold (2440 mV): ESP-IDF restarts
node.brownout(2300, 0.05)     # A 50 ms dip
node.power_cut(0.1)           # No supply for 100 ms, then a power-on reset
inj.run_to(esp_flash_write_addr); node.set_vdd(0); inj.cont()   # A cut as a flash write starts
```

Parts on their own supply (patch 0011): `boards.power_domain("lcd_pwr", "lcd", "touch",
pin=None)` in a board, then `node.part_power("lcd_pwr", False)`; off, the parts leave
their buses, and on again they're as at power-on. With `pin=N` the firmware switches it
(the T-Deck's GPIO10 does).

`power_demo.py` with `power-target/` (a counter in NVS, committed every 10 ms) runs
random cuts, cuts as a flash write starts, and brown-outs, and checks each comes back
with the right reset reason, NVS opening, and nothing it reported committed lost:

```sh
faults/power-target/build.sh out && faults/power_demo.py out/flash.bin out/firmware.elf out/run 12
```

## Bus errors and pin glitches (`boards`, patch 0012)

```python
extra = ["-device", "mcp23017,bus=i2c0,address=0x20,id=exp"] + \
        bus_noise("noise", "exp", ber=1e-3, nack=0.01, seed=3) + \
        pin_glitch("g", pin=16, rate=5)                    # On an input with a pull-up
node = boot("esp32s3-devkitc", "flash.bin", "run", extra=extra)
node.noise("noise", ber=0, nack=0.05)    # Change the rates; returns {flips, nacks} so far
node.glitch("g", pulse=True)             # One pulse now
```

`bus_demo.py` with `bus-target/` (an MCP23017 written and read back with ESP-IDF's I2C
master driver) runs clean, noisy (5% lost ACKs, 0.2% bit errors) and clean again, and
checks the driver reports every error, keeps going, and recovers:

```
clean:      ops 7000, ok 7000
noisy:      ops 9400, ok 7980, nack 921, mismatch 499   (941 lost ACKs, 548 bit errors injected)
recovered:  ops 6800, ok 6800
```

ESP-IDF 5's `i2c_master` reports a lost ACK as `ESP_ERR_INVALID_STATE`; a flipped data
bit comes back as `ESP_OK` with the wrong value.

## Campaigns (`campaign.py`)

Every instruction of the functions you name, one fault at a time, each outcome taken from
the console: `success` (your `--success` pattern: a bypass), `normal`, `crash`, `silent`
(a hang, or the firmware stopped working; it's reset), or `-` (never reached).

```sh
faults/campaign.py --flash flash.bin --elf firmware.elf \
    --function check_plain --function attempt_plain \
    --ready "fault target ready" --normal "plain denied" --success "plain GRANTED" \
    --fault skip --fault zero --json results.json
```

Faults: `skip`; `zero` and `flip:N` (the register the instruction writes, zeroed or
bit N flipped).

## The demo (`target/`, `demo.py`)

`target/` is a PIN check written twice: plainly (one compare, one branch), and hardened
(result values far apart in bits, the compare done twice with its operands swapped, the
loop's end checked, the decision checked again where it's used). `demo.py` runs a skip
and a zero campaign on each, and checks:

| | skipped instruction | zeroed result |
|---|---|---|
| plain | 4 bypasses (the `call8` to the check, `beqz`, `nsau`, `srli`) | none |
| hardened | none | none |

```sh
faults/target/build.sh out && faults/demo.py out/flash.bin out/firmware.elf out/run
```

Limits: a fault here is its effect, placed exactly; real glitches are probabilistic and
can hit several instructions. To approximate that, run a campaign with `count=` spread
over many attempts, or fault pairs of instructions. Faults in code that runs on both cores
stop whichever core gets there first.
