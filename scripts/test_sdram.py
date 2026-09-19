"""Run the SDRAM command/timing/functional regression at 50 MHz."""
import argparse
import subprocess
from test import ROOT, tool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vcd', action='store_true')
    args = parser.parse_args()
    (ROOT / 'build').mkdir(exist_ok=True)
    image = 'build/tb_sdram.vvp'
    for interval in (300, 75):
        subprocess.run([tool('iverilog'), '-g2005', '-s', 'tb_sdram', '-o', image,
                        f'-Ptb_sdram.REFRESH_INTERVAL={interval}',
                        'tests/tb_sdram.v', 'tests/mt48lc16m16a2_model.v',
                        'rtl/sdram_mt48lc16m16a2.v'], cwd=ROOT, check=True)
        result = subprocess.run([tool('vvp'), image] + (['+VCD'] if args.vcd else []),
                                cwd=ROOT, capture_output=True, text=True, timeout=60, check=True)
        print(f'Refresh interval {interval}: ' + result.stdout, end='')
        print(result.stderr, end='')
        if 'FATAL:' in result.stdout or 'PASS SDRAM:' not in result.stdout:
            raise SystemExit('SDRAM regression failed')
    image = 'build/tb_cpu_sdram.vvp'
    sources = (ROOT / 'rtl/files.f').read_text().splitlines()
    subprocess.run([tool('iverilog'), '-g2005', '-DTEST_SDRAM', '-s', 'tb_m68000',
                    '-o', image, 'tests/tb_m68000.v', 'tests/mt48lc16m16a2_model.v',
                    *sources], cwd=ROOT, check=True)
    for pause in (0, 2):
        result = subprocess.run([tool('vvp'), image, f'+PAUSE={pause}'],
                                cwd=ROOT, capture_output=True, text=True, timeout=60, check=True)
        print('CPU + SDRAM: ' + result.stdout, end='')
        print(result.stderr, end='')
        if 'FATAL:' in result.stdout or 'PASS ' not in result.stdout:
            raise SystemExit('CPU/SDRAM integration failed')


if __name__ == '__main__':
    main()
