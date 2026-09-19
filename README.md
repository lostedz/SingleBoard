# 68000 Verilog CPU

Target: **Xilinx Spartan-6 XC6SLX16 / FTG256 at 50 MHz** (speed grade -2
provisional). See `fpga/spartan6/README.md` for the ISE/XST
synthesis flow and board integration requirements.

An **MT48LC16M16A2-75 SDRAM controller** is included for the same 50 MHz clock.
See [SDRAM integration](docs/sdram.md) for its 32 MiB address mapping, CPU bus
connection, Spartan-6 clock forwarding, initialization and refresh behavior.
Run its standalone and CPU integration tests with `python scripts/test_sdram.py`.

A [memory-mapped UART](docs/uart.md) is available at **0xFF0000**, defaulting
to **115200 baud, 8N1** at 50 MHz. It includes TX/RX buffering, receive error
flags and optional interrupts. Run `python scripts/test_uart.py` for serial
and 68000 interrupt/echo tests.

A [schematic and board](hardware/README.md) for this target are in `hardware/`, as
a KiCad 10 project covering power, FPGA supply and configuration, the boot flash,
the SDRAM and the UART/clock/reset front panel. Both are generated:
`python scripts/gen_schematic.py` emits the drawing and the matching
`fpga/spartan6/singleboard.ucf` from one ball-assignment table, and
`python scripts/gen_pcb.py` builds the board from the schematic's own netlist —
a 120 × 90 mm four-layer placement whose floorplan follows which bank each
block's balls are in. The schematic passes ERC, the board passes DRC, and the
copper has been read back and compared to the assignment table. It is still a
concept: **the board has no tracks on it**, and there has been no ISE run and no
hardware validation.

`rtl/m68000.v` provides a synthesizable Verilog-2005 CPU for FPGA integration,
based on Frederic Requin's ISC-licensed [J68](https://github.com/fredrequin/j68_cpu).
The execution engine is vendored J68 RTL, not a newly written CPU. Local work
adds a 24-bit address interface, portable inferred memories, and executable
self-checking integration tests.

J68 implements the 68000 instruction set using a microcoded 16-bit datapath:
eight 32-bit data registers, eight address registers, separate user and
supervisor stacks, condition codes, effective-address decoding, multiply/divide,
traps, and autovectored interrupts. See `rtl/j68/PROVENANCE.md` for the pinned
revision and changes.

## Interface

All signals use one rising-edge clock. Inputs must be synchronous to `clk`.

| Signal | Meaning |
| --- | --- |
| `clk`, `reset` | Clock and active-high reset; hold reset for at least two clock edges and deassert synchronously |
| `clk_en` | CPU step enable; tie high for continuous execution |
| `ipl_n[2:0]` | Active-low encoded interrupt level; `111` is no interrupt, `101` is level 2 |
| `bus_valid` | Pending memory transaction |
| `bus_write` | 1 for write, 0 for read; meaningful while valid |
| `bus_addr[23:0]` | Byte address, including the low bit; high internal address bits are truncated to the 68000's 24-bit address space |
| `bus_be[1:0]` | Active-high byte lanes: bit 1 selects the even-address/high byte, bit 0 the odd-address/low byte |
| `bus_wdata[15:0]` | Write data already positioned in the selected lanes |
| `bus_rdata[15:0]` | Read data for the aligned word containing `bus_addr` |
| `bus_ready` | Memory acknowledgement; reads must have valid data on the completion edge |
| `bus_fc[2:0]` | J68 function-code output (user/supervisor, data/program/vector) |
| `debug_pc`, `debug_sr` | Internal 32-bit program counter and 16-bit status register |

A transfer completes on a rising edge when **`clk_en && bus_valid && bus_ready`**.
The memory slave must use that same condition for write side effects. Address,
direction, lanes, function code and write data remain stable during a pending
transaction, including CPU pauses. A slave may keep `bus_ready` high, or delay
it for any number of cycles. If the CPU is paused, retain read data/acknowledgement
until an enabled completion edge. Reset cancels a pending transaction.

Memory is big-endian. A longword uses two word transactions, high word first
for normal forward accesses. Fetch the initial SSP from addresses 0–3 and
initial PC from 4–7. Supply a valid reset-vector table and executable memory.
The testbench is a complete example memory slave.

## Build and test

Run from the project root so `$readmemb` can locate `rtl/j68/*.mem`. Include
every source in `rtl/files.f` and select `m68000` as the synthesis top. Memory
initialization must be supported by the target FPGA/toolchain; this is not
an ASIC ROM implementation. There are no vendor primitives or SystemVerilog
dependencies in the RTL.

With Python 3 and Icarus Verilog (`iverilog` and `vvp`) on PATH:

```sh
python scripts/test.py
python scripts/test.py --vcd
```

The runner also recognizes a local portable Windows installation in
`.tools/bin`. Test firmware is committed as `tests/smoke.hex`; an assembler
is not needed to run tests. To rebuild it with vasm:

```sh
vasmm68k -m68000 -Fbin -o build/smoke.bin tests/smoke.asm
python scripts/rom_to_hex.py
```

The self-checking program covers reset vectors, MOVE and MOVEQ, big-endian
byte/word/long transfers, postincrement/predecrement/indexed addressing,
arithmetic and overflow/carry/zero/negative flags, logic, shifts, bit operations,
DBRA, subroutine calls, signed/unsigned multiply/divide, MOVEM, TRAP/RTE,
STOP wakeup, and level-2 interrupts. It runs twice per simulation to check
reset recovery, with zero/one/seven wait cycles, periodic CPU pauses, and
deterministic irregular pauses with thirteen wait cycles. The bench also
checks request stability while stalled. Any failure or missing PASS fails
the Python runner, including with older VVP versions that return zero on `$fatal`.

## Compatibility limits

- This is not cycle-exact or a pin-compatible replacement for a physical MC68000.
  The interface is synchronous valid/ready, without AS/UDS/LDS, bus arbitration,
  a peripheral E clock, or a locked bus-cycle signal. External logic is needed
  for such system interfaces; multiprocessor atomic TAS is not provided.
- Bus-error exceptions and externally supplied interrupt vectors are not
  implemented. Interrupts use internal autovectors, without an external IACK cycle.
- J68's `RESET` opcode enters its CPU initialization routine; it does not emit
  the original processor's external peripheral-reset pulse.
- Address-error and other exception machinery comes from upstream. The included
  tests are smoke/integration coverage, not exhaustive instruction or exception
  conformance certification. No FPGA synthesis, timing closure, or hardware
  validation has been performed for this project.

Licensed under ISC; upstream attribution and licensing are retained.
