"""Generate/run an ISE XST core synthesis build for a specified Spartan-6 part."""
import argparse
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    target = json.loads((ROOT / 'fpga/spartan6/target.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--part', default=target['part'],
                        help='XST part name, e.g. xc6slx9-2-tqg144 (use your actual device)')
    parser.add_argument('--clock-mhz', default=target['clock_mhz'], type=float)
    parser.add_argument('--generate-only', action='store_true')
    parser.add_argument('--top', choices=['m68000', 'sdram_mt48lc16m16a2', 'uart_mmio'], default='m68000')
    args = parser.parse_args()
    if not re.fullmatch(r'xc6s[a-z0-9]+-[1-4][a-z]?-[a-z]+[0-9]+', args.part.lower()):
        parser.error('Specify a Spartan-6 part in device-speed-package form')
    if not math.isfinite(args.clock_mhz) or args.clock_mhz <= 0:
        parser.error('--clock-mhz must be positive and finite')
    if args.top == 'sdram_mt48lc16m16a2' and args.clock_mhz != 50:
        parser.error('The SDRAM controller timing is fixed at 50 MHz')
    if args.top == 'uart_mmio' and args.clock_mhz != 50:
        parser.error('The default UART CLOCK_HZ parameter requires 50 MHz')
    out = ROOT / 'build/spartan6'
    (out / 'tmp').mkdir(parents=True, exist_ok=True)
    sources = (ROOT / 'rtl/files.f').read_text().splitlines()
    name = args.top
    (out / f'{name}.prj').write_text(''.join(f'verilog work "{p}"\n' for p in sources))
    period = 1000 / args.clock_mhz
    (out / f'{name}.xcf').write_text(
        'NET "clk" TNM_NET = "cpu_clock";\n'
        f'TIMESPEC "TS_cpu_clock" = PERIOD "cpu_clock" {period:.6f} ns HIGH 50%;\n')
    (out / f'{name}.xst').write_text(
        'set -tmpdir "build/spartan6/tmp"\n'
        'run\n'
        f'-ifn build/spartan6/{name}.prj\n'
        '-ifmt mixed\n'
        f'-ofn build/spartan6/{name}.ngc\n'
        '-ofmt NGC\n'
        f'-p {args.part.lower()}\n'
        f'-top {name}\n'
        '-opt_mode Speed\n'
        '-opt_level 1\n'
        f'-uc build/spartan6/{name}.xcf\n'
        '-keep_hierarchy Yes\n'
        '-read_cores Yes\n'
        '-iobuf NO\n'
        '-bufg 0\n'
        '-ram_extract YES\n'
        '-ram_style Block\n'
        '-rom_extract YES\n'
        '-rom_style Block\n')
    print(f'Generated XST inputs in {out}')
    if not args.generate_only:
        xst = shutil.which('xst')
        if not xst:
            raise SystemExit('XST not found. Initialize the ISE environment or use --generate-only.')
        subprocess.run([xst, '-ifn', f'build/spartan6/{name}.xst',
                        '-ofn', f'build/spartan6/{name}.syr'], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
