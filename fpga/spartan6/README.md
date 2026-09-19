# Spartan-6 / ISE integration

Configured target: **XC6SLX16, FTG256, 50 MHz (20 ns)** in `target.json`.
Speed grade **-2 is provisional**, since it was not specified. Confirm the
full device marking before relying on timing reports; `--part` overrides it.

For the MT48LC16M16A2-75 memory controller, see `docs/sdram.md` and use
`python scripts/synth_spartan6.py --top sdram_mt48lc16m16a2`. Its SDRAM clock
must be forwarded through the supplied ODDR2 module at the board boundary.

For serial I/O, see `docs/uart.md`. Generate the UART core synthesis inputs
with `python scripts/synth_spartan6.py --top uart_mmio --generate-only`.

Use Xilinx ISE/XST for this target. The RTL is Verilog-2005; the core has no
Intel primitives. Microcode uses a 2048 x 20-bit memory (with two byte-write
enables for the register region); instruction decode uses a 256 x 36-bit ROM.
The RAM read output has a synchronous reset, and both memories request block
implementation through XST attributes. Memory contents are included as `.mem`
files. Small internal stacks may map to distributed RAM or registers.

After initializing your ISE environment, synthesize using the saved target:

```sh
python scripts/synth_spartan6.py
```

Add `--generate-only` to inspect the `.prj`, `.xst`, and `.xcf` without ISE.
Override settings with `--part xc6slx16-3-ftg256 --clock-mhz 50` if the
actual speed grade is -3, for example.
Run XST from the repository root to resolve all RTL and `$readmemb` paths.
The resulting NGC is a **core netlist**, with I/O and clock-buffer insertion
disabled. Instantiate it in a board-level design, or include `rtl/files.f`
directly in that design's ISE project.

At board level:

- Feed `clk` from a global clock buffer. Use `clk_en` for throttling; do not
  gate the clock with fabric logic. Constrain the actual clock, without
  assuming multicycle paths from `clk_en`.
- Synchronize deassertion of the external reset and hold the CPU reset for
  at least two running clock edges. Synchronize external interrupt sources
  before forming the active-low priority code.
- Implement the memory slave/arbiter and boot-vector ROM. The microcode files
  are CPU internals, not program memory. Acknowledge each access according to
  the valid/ready contract in the root README.
- Add board-specific UCF pin LOCs, I/O standards and external memory timing.
  The generated XCF constrains only core synthesis; put the corresponding
  PERIOD constraint in the board UCF for implementation.
- Inspect the XST RAM/ROM inference and utilization report. Check that the
  large memories map to block RAM and fit the chosen device. Run MAP, PAR,
  and static timing analysis before generating a board bitstream.

No ISE installation was available for this implementation, so RAM mapping,
resource utilization, maximum frequency and hardware behavior are not yet
validated. Simulation covers the same inferred-memory RTL used for synthesis.

References: AMD/Xilinx [UG687, XST User Guide](https://docs.amd.com/api/khub/documents/PmWWcoPdaZa5uE2SYAF8Vg/content)
and [UG383, Spartan-6 Block RAM User Guide](https://docs.amd.com/api/khub/documents/Xs~b~O94R6gZSgSD34Yh5Q/content).
