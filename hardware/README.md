# SingleBoard 68K schematic and board

A KiCad schematic and board for the target the rest of this repository aims at: the
J68-derived 68000 soft CPU running inside an **XC6SLX16-FTG256** at 50 MHz, with the
**MT48LC16M16A2-75** SDRAM of [docs/sdram.md](../docs/sdram.md) and the 3.3 V
logic-level UART of [docs/uart.md](../docs/uart.md).

**This is a concept.** The schematic has not been simulated, fabricated or reviewed
by anyone, and the board is **placed but not routed** — it has no tracks on it at
all. Nothing here has been checked against silicon. Read
[Before you build one](#before-you-build-one) before spending money on it.

## Opening it

Open `singleboard.kicad_pro` in KiCad 10 (the files are written in KiCad 10's
formats: schematic `version 20260101`, board `version 20260206`).
`singleboard.pdf` is the drawing plotted out and `singleboard-pcb.pdf` is the
board's layer plot, if you only want to read them.

Project-specific symbols live in `symbols/singleboard.kicad_sym` and are found
through `sym-lib-table`. Everything else, footprints included, comes from KiCad's
stock libraries, so the board carries no local footprint library.

## Regenerating

Neither file is drawn by hand. The schematic comes first, and the board is
generated from the schematic's own netlist:

```sh
python scripts/gen_schematic.py
python scripts/gen_pcb.py
```

The first run writes the six sheets, the project symbol library, the project file
**and** `fpga/spartan6/singleboard.ucf`. The ball assignment exists once — the
SDRAM half is read from [sp6_SDRAM.ucf](../fpga/spartan6/sp6_SDRAM.ucf), the rest is
in the `BANK0_MAP` / `BANK2_MAP` tables at the top of the script — so the drawing and
the ISE constraints cannot drift apart. Edit the source, re-run; do not edit the
`.kicad_sch`, `.kicad_pcb`, `.kicad_pro` or `singleboard.ucf` by hand.

The second run exports the netlist from the schematic with `kicad-cli` and builds
the board from it, so footprints, references and nets cannot drift away from the
drawing either. It needs KiCad 10 installed and re-executes itself under KiCad's
bundled Python to get `pcbnew`; set `KICAD_HOME` if it is not in the usual place.

Before it draws anything the generator checks that every assigned ball exists on
the FTG256, is an I/O rather than a supply ball, and that the SDRAM sits wholly in
one bank. It fails loudly rather than emitting a wrong drawing.

The FPGA symbol itself is built from `sources/XC6SLX16-FTG256.lib`, so its 256
ball names and numbers are not retyped anywhere.

UUIDs are derived deterministically from sheet and reference names in both
generators, so regenerating an unchanged design produces byte-identical files.
`pcbnew` hands out random ones and sorts footprints by them, so the board
generator reseeds every item from its reference designator before saving.

## Sheets

| Page | Sheet | Contents |
| --- | --- | --- |
| 1 | `singleboard` | Overview and sheet index |
| 2 | `power` | 5 V input protection, 3.3 V and 1.2 V buck converters, test points |
| 3 | `fpga_core` | FPGA supply and ground balls, decoupling, configuration pins, boot flash, JTAG |
| 4 | `fpga_io` | The two I/O bank units, VCCO decoupling (A2 sheet) |
| 5 | `sdram` | MT48LC16M16A2P-75 and its decoupling |
| 6 | `io` | Oscillator, UART header, reset button, status LEDs |

Cross-sheet connectivity is by **global label**; the sheets carry no hierarchy
pins. Every device pin is drawn as a short stub terminated in a label or a power
port, which is the usual style for a part with this many pins and keeps the
generator honest.

## Power

Two rails from a 5 V barrel jack:

| Rail | Source | Feeds |
| --- | --- | --- |
| +3V3 | TPS562200 buck, 100k/30.1k feedback | VCCO banks 0–3, VCCAUX, SDRAM, boot flash, oscillator, LEDs |
| +1V2 | TPS562200 buck, 56.2k/100k feedback | VCCINT |

The input has a 1.1 A polyfuse, a series SS34 for reverse polarity (about 0.4 V
of drop; both bucks accept 4.5 V minimum) and an SMAJ5.0A clamp.

**VCCAUX is run at 3.3 V rather than 2.5 V**, which is what removes the third
rail. The bitstream has to agree, so the generated UCF carries
`CONFIG VCCAUX = 3.3;`.

R3/C3 hold the 3.3 V enable about 100 ms behind the 1.2 V enable, so VCCINT comes
up before VCCO and VCCAUX. This follows the recommended order; confirm against
DS162 whether your device actually requires a sequence.

Decoupling is one 100 nF per supply ball — 8 on VCCINT, 8 on VCCAUX, 20 across the
four VCCO banks, 7 on the SDRAM — plus bulk on each rail. They are only meaningful
if layout puts each one against its ball, which is what the board's
[bypass placement](#bypass-placement) is for.

## Pin assignment

**The SDRAM balls are not chosen here.** All 39 of them are read out of
[sp6_SDRAM.ucf](../fpga/spartan6/sp6_SDRAM.ucf), the constraints file for the
reference Spartan-6 SDRAM board, at generation time. Only the signal names are
translated (`Data[0]` → `SDRAM_DQ0`, `SDCS0` → `SDRAM_CS_N`, and so on); every
`LOC` is taken verbatim, so the schematic follows the physical board rather than
the other way round. Change that file and re-run the generator.

They all land in **bank 3**, and the generator refuses to run if they do not.
Note that the assignment does not line up with the hard memory controller's own
ball names — `SDRAM_DQ0` sits on `IO_L83N_VREF_3`, for instance. That is fine
here: the MCB is not instantiated, and the soft controller in
`rtl/sdram_mt48lc16m16a2.v` drives these as ordinary LVCMOS33 I/O.

Bank 0 carries the 50 MHz clock (on GCLK-capable ball E7), the UART, the reset
button, the four LEDs and the HSWAPEN strap. Bank 2 carries the master-SPI
configuration interface. **Bank 1 is entirely spare**, as is most of bank 0.

`sp6_SDRAM.ucf` also fixes four non-SDRAM pins for its own board — `CLK_50M_IN`
A10, `sys_rst_n` R7, `led_1` T9, `led_2` R9 — which this schematic does *not*
follow; its clock, reset and LEDs are on the bank-0 balls above. If the intent is
to match that board rather than to design a new one, those four should move too.

Unused bank pins carry a no-connect flag. To bring one out later, delete its flag
on the `fpga_io` sheet and add a `LOC` to the UCF.

`R30` (22 Ω) damps the forwarded SDRAM clock. The FPGA-side net is
`SDRAM_CLK_FPGA` and the device-side net is `SDRAM_CLK`;
`rtl/sdram_spartan6_clock.v` drives the FPGA side from an ODDR2 with the clock
inverted so the device samples mid-cycle.

## Configuration

Master SPI from a W25Q32JVSS (32 Mbit; an XC6SLX16 bitstream is about 3.73 Mbit),
with JTAG on a 6-pin 0.1" header in the Digilent order (1 TMS, 2 TDI, 3 TDO,
4 TCK, 5 GND, 6 VREF).

The mode pins are strapped **M[1:0] = 01**. Each strap has a fitted resistor and a
DNP alternate (R16 and R17) so the mode can be changed by rework.
DONE gets the 330 Ω pull-up UG380 asks for; D4 lights while the device is
*un*configured.

## Board

`singleboard.kicad_pcb` is **120 × 90 mm, four layers, placed and planed but with
no tracks on it**. All 129 footprints are down, every pad carries the net the
schematic gives it (bar the barrel jack's unused switched contact), the
planes are poured, and all 220 nets are still ratsnest lines. Routing it is the
next piece of work, not something this file has had.

| Layer | Use |
| --- | --- |
| F.Cu | Components and signal routing |
| In1.Cu (`GND`) | Solid ground plane, 10466 mm² poured |
| In2.Cu (`PWR`) | +3V3 plane with a 1180 mm² +1V2 island cut into it |
| B.Cu | Bypass capacitors, ground pour, signal routing |

Design rules live in `singleboard.kicad_pro` and are sized for an escape out of a
1.0 mm pitch BGA: 0.15 mm track on 0.13 mm clearance with 0.45/0.25 mm vias by
default, and a `Power` class at 0.6 mm track and 0.8/0.4 mm vias for GND, +5V,
+3V3, +1V2 and the two buck switch nodes.

### Floorplan

Nothing about the arrangement is arbitrary — each block sits on the side of the
package its balls are on, which the generator reads out of the same ball map the
schematic uses:

| Block | Where | Why |
| --- | --- | --- |
| SDRAM, `R30` | Left of the FPGA | All 39 SDRAM balls are in bank 3, the −X side |
| Oscillator, reset, LEDs, UART header | Above it | Bank 0 is the −Y side |
| Boot flash, mode straps, JTAG header, PROG | Below it | Bank 2 is the +Y side |
| Both bucks, barrel jack, fuse, TVS, test points | Right-hand column | Bank 1 is entirely spare, so nothing competes for it |

The 1.2 V island on In2.Cu is one connected region spanning the FPGA core, the
1.2 V buck and a neck between them; +3V3 fills the rest of that layer, and the
two are resolved by zone priority rather than by a hand-drawn split.

### Bypass placement

The 36 FPGA bypass capacitors are on the **back**, in the ball shadow. Each one is
assigned to a specific supply ball — `C100`–`C107` to the eight VCCINT balls,
`C120`–`C127` to the eight VCCAUX balls, `C200`–`C219` to the twenty VCCO balls,
in ball order — and then placed on the free slot nearest that ball, closest pair
first. The middle of the package is where the VCCINT cluster is, so it gets the
middle slots.

A 0402 is 1.91 × 1.01 mm and the FTG256 balls are on a 1.0 mm pitch, so one cap
cannot sit centred on every ball; the slot grid is 2.2 × 1.8 mm, which clears the
courtyards both ways. Their reference designators are on the fab layer only —
there is no room to letter them on silkscreen at that pitch. The seven SDRAM caps
are likewise on the back, in a column under the body.

## What has been checked

Run against the generated files with KiCad 10.0.6:

- **ERC: 0 violations** (`kicad-cli sch erc`), errors and warnings.
- **Netlist**: 220 nets. Every net in the assignment tables was confirmed to reach
  the intended FPGA ball, every SDRAM signal was confirmed to reach the intended
  TSOP pin, and every `LOC` in the generated UCF was confirmed against the
  exported netlist. No net has fewer than two nodes apart from the deliberate
  no-connects.
- **SDRAM balls**: all 39 `LOC` values in the generated UCF compared equal to
  `sp6_SDRAM.ucf`, and all 39 are bank-3 I/O balls on the FTG256.
- **Plot**: no content falls outside any sheet frame and no two text strings
  overlap on any sheet.
- **DRC: 0 errors, 1 warning** (`kicad-cli pcb drc`), run by `gen_pcb.py` itself
  on every generation; it exits non-zero if an error appears. The warning is an
  isolated copper fill on the +1V2 island, which is inherent to an unrouted
  board: nothing on an outer layer reaches In2.Cu until there are vias. The 252
  unconnected items are the unrouted nets.
- **Copper against the assignment**: all 55 assigned balls were read back off the
  board and compared to the generator's tables; every one carries the intended
  net. All 39 SDRAM signals land on the intended FTG256 ball, all 38 device-side
  ones reach the intended TSOP pin, and the clock reaches pin 38 through `R30`.
  Every VCCINT, VCCAUX, VCCO and GND ball is on the rail it should be.
- **Placement**: every footprint except `J1` is wholly inside the outline, which
  the generator checks and refuses to write a board otherwise; `J1`'s barrel is
  meant to reach the edge. No courtyards overlap and no silkscreen collides,
  which is what the clean DRC above covers.
- **Reproducibility**: both generators were run twice from scratch and produced
  byte-identical `.kicad_sch`, `.kicad_pcb` and `.kicad_pro`.

## Before you build one

None of the following has been done, and each could change the design:

- **No routing.** The board is placed and poured, and that is all. Every net is
  still a ratsnest line. The FTG256 escape has not been attempted, so whether 220
  nets actually get out of that package on four layers — and whether four layers
  are enough — is an open question; a real escape usually wants via-in-pad or
  more layers, and finding out could push the bypass caps somewhere else.
- **No signal or power integrity work.** No stackup or impedance target, no
  length or skew matching on the SDRAM bus at 50 MHz, no return-path analysis
  across the In2.Cu plane split, no thermal work on the two bucks.
- **No fabrication outputs.** No gerbers, no drill file, no pick-and-place, no
  BOM. The design rules in the project file are a plausible four-layer
  capability, not a quote from a fab.
- **No ISE run.** The UCF has never been through MAP, PAR or static timing. Bank
  and clock-region legality of the assignment is unverified, as is whether the
  design fits and closes timing.
- **No supply validation.** Rail currents are estimates (VCCINT ~0.3 A,
  VCCAUX+VCCO ~0.4 A, SDRAM ~0.15 A). Inductor and capacitor values were chosen by
  convention, not calculated against a load profile or checked for loop stability.
- **Datasheet cross-checks still owed.** In particular: the SDRAM pin numbers
  (drawn to the JEDEC 54-pin TSOP-II arrangement), the Spartan-6 configuration
  mode encoding against UG380 Table 2-3, and the VCCAUX = 3.3 V choice against
  DS162 for the speed grade you buy.
- **The speed grade is still provisional** (-2), as noted in
  [fpga/spartan6/README.md](../fpga/spartan6/README.md).
- **There is no board-level top module yet.** The UCF names ports after
  `rtl/sdram_mt48lc16m16a2.v`, `rtl/uart_mmio.v` and `rtl/sdram_spartan6_clock.v`;
  something still has to instantiate the CPU, the memory arbiter, the boot ROM and
  the UART together and bring those ports out.
