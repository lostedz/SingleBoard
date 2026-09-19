#!/usr/bin/env python3
"""Assemble MC68000 source to a flat binary.

    python scripts/asm68k.py firmware/boot.asm -o build/boot.bin

This exists so the firmware images in this repository can be rebuilt with
nothing but Python, since the documented `vasmm68k -m68000 -Fbin` is not
always installed. It implements the subset of vasm's Motorola syntax that
`firmware/boot.asm` and `tests/*.asm` use, and `scripts/test_asm68k.py`
checks it against the committed vasm-built binaries byte for byte.
See `scripts/asm68k_syntax.py` for what is deliberately missing.

Directives: `org`, `dc.b/w/l`, `dcb`/`blk.b/w/l`, `ds.b/w/l`, `even`,
`equ`, `=`, `end`. A label beginning with `.` is local to the preceding
global label. Branches and unsuffixed absolute addresses take the shortest
encoding that fits, which is found by iterating the layout until it stops
changing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asm68k_encode import Encoder
from asm68k_syntax import AsmError, Unresolved, evaluate, split_arguments

SIZE_BYTES = {'b': 1, 'w': 2, 'l': 4}
LABEL = re.compile(r'^([A-Za-z_.][A-Za-z0-9_.$]*):?(.*)$')
# Everything up to the first ';' that is not inside a quoted string.
COMMENT = re.compile(r"""^(?:[^;'"]|'(?:[^']|'')*'|"(?:[^"]|"")*")*""")


class Line:
    """One source line, already split into its fields."""

    def __init__(self, number, text, label, mnemonic, size, arguments):
        self.number, self.text = number, text
        self.label, self.mnemonic = label, mnemonic
        self.size, self.arguments = size, arguments


def parse(source):
    lines = []
    for number, raw in enumerate(source.splitlines(), 1):
        text = raw.rstrip()
        body = '' if text.startswith('*') else text
        match = COMMENT.match(body)
        body = match.group(0) if match else body
        label = None
        if body[:1] not in ('', ' ', '\t'):
            match = LABEL.match(body)
            if not match:
                raise AsmError('line %d: %r does not start with a label' % (number, text))
            label, body = match.groups()
        parts = body.strip().split(None, 1)
        mnemonic, size, arguments = None, None, []
        if parts:
            mnemonic = parts[0].lower()
            if '.' in mnemonic[1:]:
                mnemonic, _, size = mnemonic.rpartition('.')
                if size not in ('b', 'w', 'l', 's'):
                    raise AsmError('line %d: unknown size suffix .%s' % (number, size))
            if len(parts) > 1:
                arguments = [part for part in split_arguments(parts[1]) if part]
        lines.append(Line(number, text, label, mnemonic, size, arguments))
    return lines


class Scope(dict):
    """Symbol table where a `.name` label belongs to the last global label."""

    prefix = ''

    def qualify(self, name):
        return self.prefix + name if name.startswith('.') else name

    def __contains__(self, name):
        return dict.__contains__(self, self.qualify(name))

    def __getitem__(self, name):
        return dict.__getitem__(self, self.qualify(name))


class Pass:
    """The state built up by one layout pass over the source."""

    def __init__(self, symbols):
        self.known = Scope(symbols)
        self.symbols = {}
        self.image = {}
        self.missing = set()
        self.spans = {}
        self.pc = 0

    def define(self, label, value, line):
        name = self.known.qualify(label)
        if not label.startswith('.'):
            self.known.prefix = label
            name = label
        if name in self.symbols:
            raise AsmError('line %d: %r is defined twice' % (line.number, name))
        self.symbols[name] = value

    def value(self, text, line):
        try:
            return evaluate(text, self.known, self.pc)
        except Unresolved as error:
            self.missing.add(self.known.qualify(str(error)))
            return 0
        except AsmError as error:
            raise AsmError('line %d: %s' % (line.number, error))

    def emit(self, data, line):
        for offset, byte in enumerate(data):
            address = self.pc + offset
            if address in self.image:
                raise AsmError('line %d: address %#x is written twice'
                               % (line.number, address))
            self.image[address] = byte
        if data:
            self.spans[line.number] = (self.pc, bytes(data))
        self.pc += len(data)


def define_constant(state, line):
    """`dc.b/w/l`, including character strings in `dc.b`."""
    size = line.size or 'w'
    if size not in SIZE_BYTES:
        raise AsmError('line %d: dc does not take size .%s' % (line.number, size))
    data = bytearray()
    for argument in line.arguments:
        text = argument.strip()
        if size == 'b' and len(text) > 1 and text[0] in '\'"' and text[-1] == text[0]:
            data += text[1:-1].replace(text[0] * 2, text[0]).encode('latin-1')
            continue
        value = state.value(text, line)
        data += (value & (2 ** (8 * SIZE_BYTES[size]) - 1)).to_bytes(SIZE_BYTES[size], 'big')
    state.emit(data, line)


def define_block(state, line, zeroed):
    """`blk`/`dcb` repeat a value; `ds` reserves zeroed space."""
    size = line.size or 'w'
    if size not in SIZE_BYTES:
        raise AsmError('line %d: %s does not take size .%s'
                       % (line.number, line.mnemonic, size))
    if not line.arguments:
        raise AsmError('line %d: %s needs a count' % (line.number, line.mnemonic))
    count = state.value(line.arguments[0], line)
    value = 0 if zeroed or len(line.arguments) < 2 else state.value(line.arguments[1], line)
    if count < 0:
        raise AsmError('line %d: negative block count' % line.number)
    unit = (value & (2 ** (8 * SIZE_BYTES[size]) - 1)).to_bytes(SIZE_BYTES[size], 'big')
    state.emit(unit * count, line)


def run_pass(lines, symbols):
    state = Pass(symbols)
    for line in lines:
        if line.mnemonic in ('equ', '='):
            if line.label is None or len(line.arguments) != 1:
                raise AsmError('line %d: %s takes a label and one value'
                               % (line.number, line.mnemonic))
            state.define(line.label, state.value(line.arguments[0], line), line)
            continue
        if line.mnemonic == 'org':
            if len(line.arguments) != 1:
                raise AsmError('line %d: org takes one address' % line.number)
            state.pc = state.value(line.arguments[0], line)
        if line.label is not None:
            state.define(line.label, state.pc, line)
        if line.mnemonic in (None, 'org'):
            continue
        if line.mnemonic == 'end':
            break
        if line.mnemonic == 'even':
            if state.pc % 2:
                state.emit(b'\0', line)
            continue
        if line.mnemonic == 'dc':
            define_constant(state, line)
            continue
        if line.mnemonic in ('blk', 'dcb', 'ds'):
            define_block(state, line, line.mnemonic == 'ds')
            continue
        if state.pc % 2:
            raise AsmError('line %d: instruction at odd address %#x'
                           % (line.number, state.pc))
        encoder = Encoder(state.known, state.pc, state.missing)
        try:
            words = encoder.encode(line.mnemonic, line.size, line.arguments)
        except AsmError as error:
            raise AsmError('line %d: %s' % (line.number, error))
        data = bytearray()
        for word in words:
            data += (word & 0xffff).to_bytes(2, 'big')
        state.emit(data, line)
    return state


def assemble(source, name='<source>'):
    """Iterate the layout to a fixed point; return (origin, image, symbols, spans)."""
    lines = parse(source)
    symbols, state = {}, None
    for _ in range(16):
        state = run_pass(lines, symbols)
        if state.symbols == symbols:
            break
        symbols = state.symbols
    else:
        raise AsmError('%s: instruction sizes did not settle' % name)
    undefined = state.missing - set(symbols)
    if undefined:
        raise AsmError('%s: undefined symbol(s): %s' % (name, ', '.join(sorted(undefined))))
    if not state.image:
        return 0, b'', symbols, state.spans
    origin, end = min(state.image), max(state.image) + 1
    image = bytearray(end - origin)
    for address, byte in state.image.items():
        image[address - origin] = byte
    return origin, bytes(image), symbols, state.spans


def listing(source, symbols, spans):
    rows = []
    for line in parse(source):
        address, data = spans.get(line.number, (None, b''))
        column = '%06x' % address if data else '      '
        rows.append('%s  %-23s %s' % (column, data[:8].hex(' ', 1), line.text))
    rows += ['', 'Symbols:']
    rows += ['    %08x  %s' % (symbols[name], name)
             for name in sorted(symbols, key=lambda item: (symbols[item], item))]
    return '\n'.join(rows) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('-o', '--output', type=Path, help='flat binary image')
    parser.add_argument('--listing', type=Path, help='address/encoding/source listing')
    arguments = parser.parse_args(argv)
    text = arguments.source.read_text()
    try:
        origin, image, symbols, spans = assemble(text, arguments.source.name)
    except AsmError as error:
        raise SystemExit('%s: %s' % (arguments.source, error))
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(image)
    if arguments.listing:
        arguments.listing.parent.mkdir(parents=True, exist_ok=True)
        arguments.listing.write_text(listing(text, symbols, spans))
    print('%s: %d bytes at %#08x' % (arguments.source, len(image), origin))
    return 0


if __name__ == '__main__':
    sys.exit(main())
