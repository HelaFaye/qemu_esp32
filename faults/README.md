# Fault injection

What a voltage glitch or an EM pulse does to firmware, injected exactly where it's aimed
in an emulated ESP32: an instruction that doesn't run, a register or a byte of memory
that comes out wrong, a reset. The plan this is part of: [docs/STRESS-TESTING.md](../docs/STRESS-TESTING.md).

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
node.qmp("system_reset")                          # Brown-out: the whole chip resets
inj.close()
```

`count=N` picks the N-th time an address is reached. Registers: `pc`, `a0`-`a15` (the
current window), `ar0`-`ar63`, `sar`, `windowbase`, `windowstart`, `ps`.

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
