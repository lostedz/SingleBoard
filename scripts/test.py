"""Compile Verilog-2005 RTL and execute the self-checking bus/CPU tests."""
import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    installed = shutil.which(name)
    if installed:
        return installed
    portable = ROOT / '.tools' / 'bin' / (name + '.exe')
    if portable.exists():
        return str(portable)
    raise SystemExit(f'{name} not found: install Icarus Verilog and add it to PATH')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vcd', action='store_true')
    args = parser.parse_args()
    (ROOT / 'build').mkdir(exist_ok=True)
    image = 'build/tb_m68000.vvp'
    sources = (ROOT / 'rtl/files.f').read_text().splitlines()
    subprocess.run([tool('iverilog'), '-g2005', '-Wall', '-s', 'tb_m68000',
                    '-o', image, 'tests/tb_m68000.v', *sources],
                   cwd=ROOT, check=True)
    for wait, pause in [(0, 0), (1, 0), (7, 0), (3, 1), (13, 2)]:
        command = [tool('vvp'), image, f'+WAIT={wait}', f'+PAUSE={pause}']
        if args.vcd and pause:
            command.append('+VCD')
        result = subprocess.run(command, cwd=ROOT, check=True, timeout=60,
                                capture_output=True, text=True)
        print(result.stdout, end='')
        print(result.stderr, end='')
        # Older Windows VVP builds return zero even after $fatal.
        if 'FATAL:' in result.stdout or 'PASS ' not in result.stdout:
            raise SystemExit('Simulation failed or did not report PASS')


if __name__ == '__main__':
    main()
