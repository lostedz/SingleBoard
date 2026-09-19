# J68 provenance

Source: https://github.com/fredrequin/j68_cpu

Commit: `538badf8def66dc900947be665d0eac6f9ff833e`

Author: Frederic Requin. Upstream declares ISC licensing in its README,
preserved as `UPSTREAM_README.md`. Original copyright headers are retained.
See the project LICENSE for the ISC text.

This directory contains the CPU dependencies, four memory initialization
images, and the microcode assembly source. It excludes the upstream SoC,
UART, third-party assembler, emulator, executables and unrelated ROMs.

Local RTL changes, reproducible with `scripts/import_j68.py`:

- Select inferred memories instead of Intel `altsyncram` primitives.
- Resolve initialization filenames relative to the project root.
- Use synchronous reset for the microcode read output and XST `ram_style` /
  `rom_style` attributes to request Spartan-6 block memories.
- Select the microcode image with an explicit conditional `$readmemb`, avoiding
  differently sized string parameters that older Icarus versions misinterpret.

The top-level wrapper uses the standard `USE_CLK_ENA=0` microcode, whose
decode pipeline includes the extra cycle needed for consecutive enabled
clocks. The upstream optimized clock-enable microcode assumes gaps between
enabled clocks and is not used by this wrapper.

To reproduce, clone upstream into `.upstream-j68`, check out the exact commit
above, and run `python scripts/import_j68.py`. No download is needed to build
or simulate the committed RTL.
