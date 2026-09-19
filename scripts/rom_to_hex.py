"""Convert the test program binary to the fixed 1 KiB byte ROM image."""
from pathlib import Path
import argparse

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--name', choices=['smoke', 'uart_smoke'], default='smoke')
args = parser.parse_args()
data = (root / 'build' / (args.name + '.bin')).read_bytes()
if len(data) > 1024:
    raise SystemExit('Test ROM exceeds 1024 bytes')
(root / 'tests' / (args.name + '.hex')).write_text(''.join(f'{v:02x}\n' for v in data.ljust(1024, b'\0')))
