# MT48LC16M16A2-75 SDRAM

`rtl/sdram_mt48lc16m16a2.v` is a Verilog-2005 controller for the Micron
MT48LC16M16A2-75 at **50 MHz**. It exposes a 16-bit memory slave compatible
with the CPU's valid/ready bus and implements the full 32 MiB device geometry.
It is deliberately a single-outstanding, single-word controller: burst length
one, CAS latency two, explicit precharge after every access, no open-row cache,
and no self-refresh or clock stopping.

## Device and timing

The device has four banks, 8192 rows per bank, and 512 sixteen-bit columns.
Native bus addresses are 25-bit **byte** addresses:

| Address bits | SDRAM field |
| --- | --- |
| `[24:23]` | Bank BA1:BA0 |
| `[22:10]` | Row A12:A0 during ACTIVE |
| `[9:1]` | Column A8:A0 during READ/WRITE |
| `[0]` | Byte position, expressed by `bus_be`; not sent as a column bit |

The controller uses conservative fixed timing for the -75 at a 20 ns period.
Each access issues ACTIVE, waits, issues READ or WRITE, waits for data/recovery,
then PRECHARGE ALL and waits before allowing another operation. It does not
perform auto-precharge, so write recovery follows the explicit-precharge rule.

| Requirement | Device minimum / limit | Implementation |
| --- | --- | --- |
| Power-up pause | 100 us after stable power/clock | 200 us (`INIT_CYCLES=10000`) |
| Initialization | Precharge, at least 2 refreshes, mode set | PRECHARGE ALL, 2 AUTO REFRESH, MRS `0x020` |
| tRCD / tRP | 20 ns | At least 60 ns between applicable commands |
| tRFC / tRC | 66 ns | Refresh command spacing at least 100 ns; accesses slower |
| tRAS | 44 ns min / 120 us max | Rows closed after each access (at least 120 ns active) |
| tWR, explicit precharge | 15 ns | 60 ns after WRITE |
| tMRD | 2 clocks | At least 2 clocks |
| CL2 tAC | 6 ns max | Falling-edge input capture described below |
| Standard/industrial refresh | 8192 refreshes per 64 ms | Refresh due every 300 clocks (6 us), plus bounded in-flight access |

The FSM may delay a due refresh by at most one short transaction. The default
worst-case gap remains below 7.8125 us. A pending bus response never blocks
refresh. `REFRESH_CYCLES=75` supports the stricter 16 ms/8192-row automotive
refresh interval. Do not exceed 300 or reduce below 16; use the actual device's
temperature grade requirements. These parameters do **not** make the controller
frequency-independent: command delays and the read phase are fixed at 50 MHz.
Do not reduce `INIT_CYCLES` below 10000 for this configuration.

Source: Micron, *256Mb: x4, x8, x16 SDRAM*, Rev. W, May 2015
([manufacturer-authored datasheet mirror](https://www.rxelectronics.pl/datasheet/2e/MT48LC16M16A2P-75-D-TR.pdf)),
particularly the x16 block diagram, -75 AC timing table, initialization, and
READ/WRITE timing sections.

## Clock and FPGA boundary

Feed the controller the existing 50 MHz global clock. Instantiate
`rtl/sdram_spartan6_clock.v` beside it to forward **the inverted clock** to
the SDRAM pin using the Spartan-6 ODDR2 output primitive. The external SDRAM
clock remains running even when the CPU's `clk_en` is low.

```verilog
sdram_spartan6_clock clock_output (
    .clk(clk_50mhz), .sdram_clk(sdram_clk_pin)
);
```

Command/address/write signals launch on the FPGA clock's rising edge and the
SDRAM samples them on its falling edge, giving nominally half a cycle of setup.
For a READ launched at rising edge P0, the device samples it at N0 and supplies
CL2 data after N2. An input register captures that data at N3, before the
device's next clock-to-output transition; the FSM transfers the sample into
the response register at P4. The input register requests IOB placement. This
provides a nominal 20 ns data-arrival interval (14 ns after subtracting the
part's 6 ns maximum tAC), subject to forwarded-clock skew, PCB flight times,
FPGA input delay and setup/hold requirements.

Use a 3.3 V-compatible FPGA I/O bank and appropriate LVTTL/LVCMOS33 constraints
for the actual board. Add UCF LOCs for the SDRAM clock, CKE, CS/RAS/CAS/WE,
A[12:0], BA[1:0], DQM[1:0], and DQ[15:0]. DQM[1] is DQMH and DQM[0] is DQML.
Place the bidirectional DQ nets at the board boundary so XST can infer IOBUFs.
Include the ISE UNISIM library when simulating the ODDR2 wrapper.

The board UCF must constrain the 20 ns clock plus SDRAM input/output timing
relative to the forwarded clock, including its phase and board delays. The
standalone synthesis XCF is **not** a complete external-memory timing constraint
set. No pin assignments or guessed board trace delays are supplied.

## Bus contract and 68000 connection

Hold `bus_valid`, address, direction, byte enables and write data until a rising
edge with `bus_valid && bus_clk_en && bus_ready`. Requests are latched only
while `bus_clk_en` is high. Once accepted internally, an operation finishes
independently of CPU clock enable. The response stays asserted and read data
stays stable until that completion handshake. Tie `bus_clk_en` to the CPU's
`clk_en` (or 1 for an always-running master).

Byte lanes use the CPU's existing big-endian convention: bit 1 selects DQ15:8
at the even byte address; bit 0 selects DQ7:0 at the odd address. Write DQM is
`~bus_be`. Read returns the entire aligned word; the CPU selects the needed
byte. A zero-byte-enable write leaves memory unchanged. DQM remains low during
reads to account for the device's read-mask latency.

The CPU can address only 16 MiB. For an uncomplicated mapping, connect
`bus_addr` to `{1'b0, cpu_addr}` (the lower half of SDRAM). To expose the upper
half, use a software-controlled page bit: `{page, cpu_addr}`. That bit must be
stable for each request. A board-level address decoder should select SDRAM
versus boot ROM and peripherals:

```verilog
// Signals are wires in your board-level module; select comes from your map.
// Controller: bus_valid = cpu_valid && select_sdram
//             bus_clk_en = cpu_clk_en
//             bus_addr = {page, cpu_addr}
//             bus_write/be/wdata = corresponding CPU outputs
assign cpu_ready = select_sdram ? ram_ready : rom_io_ready;
assign cpu_rdata = select_sdram ? ram_rdata : rom_io_rdata;
```

Initialization stalls requests; `init_done` indicates the memory is ready.
SDRAM powers up with undefined contents. Provide a boot ROM at the CPU reset
vectors and a loader (or another master) to populate SDRAM. The integration
test preloads the simulation model; that is not a hardware initialization path.

Controller `reset` is synchronous and restarts initialization, cancelling any
pending response. Treat a controller reset as destructive to memory validity:
an already issued write cannot be undone, and refresh is suspended during
reinitialization. Use a separate CPU warm reset if SDRAM must be preserved;
the controller and SDRAM clock must continue running. Hold controller reset
until power and the FPGA clock are stable, and synchronize its release.

## Build and verification

The source list `rtl/files.f` includes the controller and clock-forwarding module.
Generate or run an ISE synthesis job for the controller with:

```sh
python scripts/synth_spartan6.py --top sdram_mt48lc16m16a2 --generate-only
python scripts/synth_spartan6.py --top sdram_mt48lc16m16a2
python scripts/test_sdram.py
```

The controller synthesis job produces a core netlist; synthesize the ODDR2,
I/O buffers, memory decode and CPU together in the final board design.

The tests use an independent, command-level sparse SDRAM model with complete
physical address tags, CL2 output delay, DQM handling, and checks for power-up,
MRS, tRCD, tRP, tRAS, tRC, tRRD, tWR, tRFC, and refresh deadlines. They cover
both byte lanes, masked writes, all-bank/row/column boundaries, deterministic
random addresses, read/write turnaround, idle refresh, and responses held
while the CPU is paused. The standalone bench includes 2 ns forwarded-clock
delay and 2 ns read flight delay. It checks both standard (64 ms) and automotive
(16 ms) refresh deadlines. The 68000 firmware also runs through SDRAM
with continuous and irregularly paused CPU execution and a CPU warm reset.

The model covers this controller's command subset, not the entire SDRAM
protocol or analog behavior. ISE fit/timing and physical-board validation remain
outstanding; passing simulation is not a claim of hardware timing closure.
