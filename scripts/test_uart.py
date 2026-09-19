"""Compile and run the UART bus and independent serial-peer regression."""
import subprocess
from test import ROOT, tool

(ROOT / 'build').mkdir(exist_ok=True)
image = 'build/tb_uart.vvp'
subprocess.run([tool('iverilog'), '-g2005', '-s', 'tb_uart', '-o', image,
                'tests/tb_uart.v', 'rtl/uart_mmio.v'], cwd=ROOT, check=True)
result = subprocess.run([tool('vvp'), image], cwd=ROOT, capture_output=True,
                        text=True, timeout=60, check=True)
print(result.stdout, end='')
print(result.stderr, end='')
if 'FATAL:' in result.stdout or 'PASS UART:' not in result.stdout:
    raise SystemExit('UART regression failed')

image = 'build/tb_cpu_uart.vvp'
sources = (ROOT / 'rtl/files.f').read_text().splitlines()
subprocess.run([tool('iverilog'), '-g2005', '-s', 'tb_cpu_uart', '-o', image,
                'tests/tb_cpu_uart.v', *sources], cwd=ROOT, check=True)
result = subprocess.run([tool('vvp'), image], cwd=ROOT, capture_output=True,
                        text=True, timeout=60, check=True)
print(result.stdout, end='')
print(result.stderr, end='')
if 'FATAL:' in result.stdout or 'PASS CPU UART:' not in result.stdout:
    raise SystemExit('CPU UART integration failed')
