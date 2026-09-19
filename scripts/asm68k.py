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
`equ`, `=`, `end`. Branches and unsuffixed absolute addresses take the
shortest encoding that fits, which is found by iterating the layout until
it stops changing.
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
        body = text if not text.startswith('*') else ''
        match = COMMENT.match(body)
        body = match.group(0) if match else body
        if not body.strip():
            lines.append(Line(number, text, None, None, None, []))
            continue
        label = None
        if body[0] not in ' \t':
            head, rest = re.match(r'^(\S+?)(?::|\b)(.*)$', body).groups()
            label, body = head, rest
            if not re.match(r'^[A-Za-z_.][A-Za-z0-9_.$]*$', label):
                raise AsmError('line %d: %r is not a label' % (number, label))
        parts = body.strip().split(None, 1)
        mnemonic, size = None, None
        arguments = []
        if parts:
            mnemonic = parts[0].lower()
            if mnemonic not in ('=',) and '.' in mnemonic[1:]:
                mnemonic, _, suffix = mnemonic.rpartition('.')
                size = suffix
                if size not in ('b', 'w', 'l', 's'):
                    raise AsmError('line %d: unknown size suffix .%s' % (number, size))
            if len(parts) > 1:
                arguments = split_arguments(parts[1])
                if arguments == ['']:
                    arguments = []
        lines.append(Line(number, text, label, mnemonic, size, arguments))
    return lines


class Pass:
    """The state built up by one layout pass over the source."""

    def __init__(self, symbols):
        self.known = symbols
        self.symbols = {}
        self.image = {}
        self.missing = set()
        self.spans = {}
        self.pc = 0

    def value(self, text, line):
        try:
            return evaluate(text, self.known, self.pc)
        except Unresolved as error:
            self.missing.add(str(error))
            return 0
        except AsmError as error:
            raise AsmError('line %d: %s' % (line.number, error))

    def emit(self, data, line):
        for offset, byte in enumerate(data):
            address = self.pc + offset
            if address in self.image:
                raise AsmError('line %d: address %#x written twice' % (line.number, address))
            self.image[address] = byte
        self.spans[line.number] = (self.pc, bytes(data))
        self.pc += len(data)


def constant(pass_state, line):
    """`label equ expr` and `label = expr`."""
    if line.label is None:
        raise AsmError('line %d: %s needs a label' % (line.number, line.mnemonic))
    if len(line.arguments) != 1:
        raise AsmError('line %d: %s takes one value' % (line.number, line.mnemonic))
    pass_state.symbols[line.label] = pass_state.value(line.arguments[0], line)


def define_constant(pass_state, line):
    """`dc.b/w/l`, including strings in `dc.b`."""
    size = line.size or 'w'
    if size not in SIZE_BYTES:
        raise AsmError('line %d: dc does not take size .%s' % (line.number, size))
    data = bytearray()
    for argument in line.arguments:
        text = argument.strip()
        if size == 'b' and len(text) > 1 and text[0] in '\'"' and text[-1] == text[0]:
            data += text[1:-1].replace(text[0] * 2, text[0]).encode('latin-1')
            continue
        value = pass_state.value(text, line)
        data += (value & (2 ** (8 * SIZE_BYTES[size]) - 1)).to_bytes(SIZE_BYTES[size], 'big')
    pass_state.emit(data, line)


def define_block(pass_state, line):
    """`blk`/`dcb` repeat a value; `ds` reserves zeroed space."""
    size = line.size or 'w'
    if size not in SIZE_BYTES:
        raise AsmError('line %d: %s does not take size .%s' % (line.number, line.mnemonic, size))
    if not line.arguments:
        raise AsmError('line %d: %s needs a count' % (line.number, line.mnemonic))
    count = pass_state.value(line.arguments[0], line)
    value = pass_state.value(line.arguments[1], line) if len(line.arguments) > 1 else 0
    if count < 0:
        raise AsmError('line %d: negative block count' % line.number)
    unit = (value & (2 ** (8 * SIZE_BYTES[size]) - 1)).to_bytes(SIZE_BYTES[size], 'big')
    pass_state.emit(unit * count, line)


def run_pass(lines, symbols):
    state = Pass(symbols)
    for line in lines:
        if line.mnemonic in ('equ', '='):
            constant(state, line)
            continue
        if line.label is not None:
            state.symbols[line.label] = state.pc
        if line.mnemonic is None:
            continue
        if line.mnemonic == 'org':
            if len(line.arguments) != 1:
                raise AsmError('line %d: org takes one address' % line.number)
            state.pc = state.value(line.arguments[0], line)
            if line.label is not None:
                state.symbols[line.label] = state.pc
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
        if line.mnemonic in ('blk', 'dcb'):
            define_block(state, line)
            continue
        if line.mnemonic == 'ds':
            define_block(state, Line(line.number, line.text, None, 'ds', line.size,
                                     line.arguments[:1]))
            continue
        if state.pc % 2:
            raise AsmError('line %d: instruction at odd address %#x'
                           % (line.number, state.pc))
        encoder = Encoder(symbols, state.pc, state.missing)
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
    """Iterate the layout to a fixed point and return (origin, bytes, symbols)."""
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
        return 0, b'', symbols
    origin, end = min(state.image), max(state.image) + 1
    image = bytearray(end - origin)
    for address, byte in state.image.items():
        image[address - origin] = byte
    return origin, bytes(image), symbols


def listing(source, origin, symbols, spans):
    rows = []
    for line in parse(source):
        address, data = spans.get(line.number, (None, b''))
        column = '%06x' % address if data else '      '
        bytes_shown = data[:8].hex(' ', 1)
        rows.append('%s  %-23s %s' % (column, bytes_shown, line.text))
    rows.append('')
    rows.append('Symbols:')
    for name in sorted(symbols, key=lambda item: (symbols[item], item)):
        rows.append('    %08x  %s' % (symbols[name], name))
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
        origin, image, symbols = assemble(text, arguments.source.name)
    except AsmError as error:
        raise SystemExit('%s: %s' % (arguments.source, error))
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(image)
    if arguments.listing:
        state = run_pass(parse(text), symbols)
        arguments.listing.parent.mkdir(parents=True, exist_ok=True)
        arguments.listing.write_text(listing(text, origin, symbols, state.spans))
    print('%s: %d bytes at %#08x' % (arguments.source, len(image), origin))
    return 0


if __name__ == '__main__':
    sys.exit(main())
