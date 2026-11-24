# Logic analyzer

The emulated S3's GPIO model records pad levels (patch 0029):

    -global driver=esp32s3.gpio,property=la-file,value=run.vcd
    -global driver=esp32s3.gpio,property=la-pins,value=0x3ffe      (pins 1-13; default all)

or at run time: `qom-set /machine/soc/gpio la-file run.vcd` starts, `""` stops. The VCD
has a wire per pin (GPIOn), virtual time in ns. PulseView: File > Import > Value Change
Dump. Or convert it to PulseView's own format, sampled like your analyzer:

    la/la.py sr run.vcd run.sr --rate 24e6 --map GPIO1=D0,GPIO2=D1,...

`la.py compare` and `la.py fit` set a real capture (PulseView: File > Save, `.sr`)
against an emulated one; see [the plan](../docs/plan.md#4-gpio-waveforms-and-the-logic-analyzer).
Tests: `python3 -m pytest la/`.
