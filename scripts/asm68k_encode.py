"""MC68000 instruction encoders for `scripts/asm68k.py`.

Every encoder takes the parsed operands and returns the instruction's words,
most significant first. Encodings follow Motorola's *M68000 Family
Programmer's Reference Manual*, section 4.
"""
from __future__ import annotations

from asm68k_syntax import (AsmError, MOVE_SIZE, Operand, Parser, SIZE_BITS,
                    address_register, data_register, immediate_words,
                    register_list, signed_fits)

CONDITIONS = {
    't': 0, 'f': 1, 'hi': 2, 'ls': 3, 'cc': 4, 'hs': 4, 'cs': 5, 'lo': 5,
    'ne': 6, 'eq': 7, 'vc': 8, 'vs': 9, 'pl': 10, 'mi': 11,
    'ge': 12, 'lt': 13, 'gt': 14, 'le': 15,
}

# Effective-address categories, as (mode, reg) predicates.
CONTROL = {2, 5, 6}          # (An), d(An), d(An,Xn), plus the 7/0-7/3 forms
CONTROL_SPECIAL = {0, 1, 2, 3}


def is_control(operand):
    if operand.mode in CONTROL:
        return True
    return operand.mode == 7 and operand.reg in CONTROL_SPECIAL


def is_alterable(operand):
    """Anything writable: excludes immediates and the PC-relative modes."""
    if operand.mode == 7:
        return operand.reg in (0, 1)
    return True


def require(condition, message):
    if not condition:
        raise AsmError(message)


def data_alterable(operand, mnemonic):
    require(operand.mode != 1 and is_alterable(operand),
            '%s cannot write %s' % (mnemonic, operand.kind))


def size_of(size, allowed, mnemonic, default='w'):
    size = size or default
    require(size in allowed, '%s does not take size .%s' % (mnemonic, size))
    return size


class Encoder:
    """Encodes one instruction at `pc` against the current symbol table."""

    def __init__(self, symbols, pc):
        self.symbols, self.pc = symbols, pc
        self.parser = Parser(symbols, pc)

    # -- helpers ----------------------------------------------------------
    def operand(self, text, size='w'):
        return self.parser.operand(text, size)

    def branch_target(self, text):
        value, known = self.parser.number(text)
        return value, known

    def encode(self, mnemonic, size, arguments):
        handler = HANDLERS.get(mnemonic)
        if handler is None:
            raise AsmError('unsupported instruction %r' % mnemonic)
        return handler(self, mnemonic, size, arguments)


# ---------------------------------------------------------------- data moves

def encode_move(self, mnemonic, size, arguments):
    require(len(arguments) == 2, 'move takes two operands')
    size = size_of(size, 'bwl', mnemonic)
    source_text, destination_text = arguments
    destination = self.operand(destination_text, size)

    if destination.kind == 'sr':
        source = self.operand(source_text, 'w')
        require(size == 'w', 'move to SR is word sized')
        return [0x46C0 | source.ea] + source.words
    if destination.kind == 'ccr':
        source = self.operand(source_text, 'w')
        require(size == 'w', 'move to CCR is word sized')
        return [0x44C0 | source.ea] + source.words
    if destination.kind == 'usp':
        reg = address_register(source_text)
        require(reg is not None, 'move to USP takes an address register')
        return [0x4E60 | reg]
    if source_text.strip().lower() == 'sr':
        require(size == 'w', 'move from SR is word sized')
        data_alterable(destination, mnemonic)
        return [0x40C0 | destination.ea] + destination.words
    if source_text.strip().lower() == 'usp':
        reg = address_register(destination_text)
        require(reg is not None, 'move from USP takes an address register')
        return [0x4E68 | reg]

    source = self.operand(source_text, size)
    if destination.kind == 'An':
        require(size in 'wl', 'movea is word or long')
        return [MOVE_SIZE[size] << 12 | destination.reg << 9 | 1 << 6 | source.ea] + source.words
    require(source.kind != 'An' or size != 'b', 'byte moves cannot use an address register')
    data_alterable(destination, mnemonic)
    words = [MOVE_SIZE[size] << 12 | destination.reg << 9 | destination.mode << 6 | source.ea]
    return words + source.words + destination.words


def encode_moveq(self, mnemonic, size, arguments):
    require(len(arguments) == 2, 'moveq takes two operands')
    size_of(size, 'l', mnemonic, default='l')
    require(arguments[0].startswith('#'), 'moveq takes an immediate source')
    value, known = self.parser.number(arguments[0][1:])
    reg = data_register(arguments[1])
    require(reg is not None, 'moveq takes a data register destination')
    require(not known or -128 <= value <= 255, 'moveq immediate %d out of range' % value)
    return [0x7000 | reg << 9 | (value & 0xff)]


def encode_lea(self, mnemonic, size, arguments):
    require(len(arguments) == 2, 'lea takes two operands')
    size_of(size, 'l', mnemonic, default='l')
    source = self.operand(arguments[0], 'l')
    reg = address_register(arguments[1])
    require(reg is not None, 'lea takes an address register destination')
    require(is_control(source), 'lea needs a control addressing mode')
    return [0x41C0 | reg << 9 | source.ea] + source.words


def encode_pea(self, mnemonic, size, arguments):
    require(len(arguments) == 1, 'pea takes one operand')
    size_of(size, 'l', mnemonic, default='l')
    source = self.operand(arguments[0], 'l')
    require(is_control(source), 'pea needs a control addressing mode')
    return [0x4840 | source.ea] + source.words


def encode_movem(self, mnemonic, size, arguments):
    require(len(arguments) == 2, 'movem takes two operands')
    size = size_of(size, 'wl', mnemonic)
    long_transfer = size == 'l'
    first, second = arguments
    if data_register(first) is not None or address_register(first) is not None \
            or '/' in first or '-' in first:
        destination = self.operand(second, size)
        require(is_alterable(destination) and destination.mode in (2, 4, 5, 6, 7),
                'movem writes to memory only')
        mask = register_list(first, destination.mode == 4)
        return [0x4880 | long_transfer << 6 | destination.ea, mask] + destination.words
    source = self.operand(first, size)
    require(source.mode in (2, 3, 5, 6, 7), 'movem reads from memory only')
    mask = register_list(second, False)
    return [0x4C80 | long_transfer << 6 | source.ea, mask] + source.words


def encode_exg(self, mnemonic, size, arguments):
    require(len(arguments) == 2, 'exg takes two operands')
    size_of(size, 'l', mnemonic, default='l')
    first_data, second_data = (data_register(text) for text in arguments)
    first_addr, second_addr = (address_register(text) for text in arguments)
    if first_data is not None and second_data is not None:
        return [0xC140 | first_data << 9 | second_data]
    if first_addr is not None and second_addr is not None:
        return [0xC148 | first_addr << 9 | second_addr]
    if first_data is not None and second_addr is not None:
        return [0xC188 | first_data << 9 | second_addr]
    if first_addr is not None and second_data is not None:
        return [0xC188 | second_data << 9 | first_addr]
    raise AsmError('exg takes two registers')


# ------------------------------------------------------------- single operand

UNARY = {'clr': 0x4200, 'neg': 0x4400, 'not': 0x4600, 'tst': 0x4A00}


def encode_unary(self, mnemonic, size, arguments):
    require(len(arguments) == 1, '%s takes one operand' % mnemonic)
    size = size_of(size, 'bwl', mnemonic)
    operand = self.operand(arguments[0], size)
    require(operand.mode != 1, '%s cannot use an address register' % mnemonic)
    if mnemonic != 'tst':
        data_alterable(operand, mnemonic)
    else:
        require(is_alterable(operand), 'tst cannot use an immediate on the 68000')
    return [UNARY[mnemonic] | SIZE_BITS[size] << 6 | operand.ea] + operand.words


def encode_tas(self, mnemonic, size, arguments):
    require(len(arguments) == 1, 'tas takes one operand')
    size_of(size, 'b', mnemonic, default='b')
    operand = self.operand(arguments[0], 'b')
    data_alterable(operand, mnemonic)
    return [0x4AC0 | operand.ea] + operand.words


def encode_swap(self, mnemonic, size, arguments):
    require(len(arguments) == 1, 'swap takes one operand')
    size_of(size, 'w', mnemonic)
    reg = data_register(arguments[0])
    require(reg is not None, 'swap takes a data register')
    return [0x4840 | reg]


def encode_ext(self, mnemonic, size, arguments):
    require(len(arguments) == 1, 'ext takes one operand')
    size = size_of(size, 'wl', mnemonic)
    reg = data_register(arguments[0])
    require(reg is not None, 'ext takes a data register')
    return [(0x48C0 if size == 'l' else 0x4880) | reg]


def encode_jump(self, mnemonic, size, arguments):
    require(len(arguments) == 1, '%s takes one operand' % mnemonic)
    size_of(size, 'l', mnemonic, default='l')
    target = self.operand(arguments[0], 'l')
    require(is_control(target), '%s needs a control addressing mode' % mnemonic)
    base = 0x4EC0 if mnemonic == 'jmp' else 0x4E80
    return [base | target.ea] + target.words


# --------------------------------------------------------------- arithmetic

QUICK = {'addq': 0x5000, 'subq': 0x5100}


def encode_quick(self, mnemonic, size, arguments):
    require(len(arguments) == 2, '%s takes two operands' % mnemonic)
    size = size_of(size, 'bwl', mnemonic)
    require(arguments[0].startswith('#'), '%s takes an immediate source' % mnemonic)
    count, known = self.parser.number(arguments[0][1:])
    require(not known or 1 <= count <= 8, '%s count %d is not 1 to 8' % (mnemonic, count))
    destination = self.operand(arguments[1], size)
    require(is_alterable(destination), '%s cannot write %s' % (mnemonic, destination.kind))
    require(destination.mode != 1 or size != 'b', 'address register byte operations are illegal')
    return [QUICK[mnemonic] | (count & 7) << 9 | SIZE_BITS[size] << 6
            | destination.ea] + destination.words


IMMEDIATE = {'ori': 0x0000, 'andi': 0x0200, 'subi': 0x0400,
             'addi': 0x0600, 'eori': 0x0A00, 'cmpi': 0x0C00}
STATUS_IMMEDIATE = {'ori': 0x003C, 'andi': 0x023C, 'eori': 0x0A3C}


def encode_immediate(self, mnemonic, size, arguments):
    require(len(arguments) == 2, '%s takes two operands' % mnemonic)
    size = size_of(size, 'bwl', mnemonic)
    require(arguments[0].startswith('#'), '%s takes an immediate source' % mnemonic)
    value, _ = self.parser.number(arguments[0][1:])
    target = arguments[1].strip().lower()
    if target in ('ccr', 'sr'):
        require(mnemonic in STATUS_IMMEDIATE, '%s cannot address %s' % (mnemonic, target))
        require(size == ('b' if target == 'ccr' else 'w'),
                '%s to %s is %s sized' % (mnemonic, target, 'byte' if target == 'ccr' else 'word'))
        opcode = STATUS_IMMEDIATE[mnemonic] | (0x40 if target == 'sr' else 0)
        return [opcode] + immediate_words(value, 'w' if target == 'sr' else 'b')
    destination = self.operand(arguments[1], size)
    require(destination.mode != 1, '%s cannot use an address register' % mnemonic)
    if mnemonic == 'cmpi':
        require(is_alterable(destination), 'cmpi cannot use an immediate destination')
    else:
        data_alterable(destination, mnemonic)
    words = [IMMEDIATE[mnemonic] | SIZE_BITS[size] << 6 | destination.ea]
    return words + immediate_words(value, size) + destination.words


BINARY = {'add': 0xD000, 'sub': 0x9000, 'and': 0xC000, 'or': 0x8000,
          'cmp': 0xB000, 'eor': 0xB000}
ADDRESS_BINARY = {'adda': 0xD000, 'suba': 0x9000, 'cmpa': 0xB000}


def encode_binary(self, mnemonic, size, arguments):
    require(len(arguments) == 2, '%s takes two operands' % mnemonic)
    size = size_of(size, 'bwl', mnemonic)
    source_text, destination_text = arguments
    if source_text.startswith('#'):
        raise AsmError('write %si/%sq for an immediate source' % (mnemonic, mnemonic))
    if address_register(destination_text) is not None and mnemonic in ('add', 'sub', 'cmp'):
        return encode_address_binary(self, mnemonic + 'a', size, arguments)

    destination_reg = data_register(destination_text)
    source_reg = data_register(source_text)

    if mnemonic == 'eor' or (source_reg is not None and destination_reg is None):
        require(source_reg is not None, '%s takes a data register source here' % mnemonic)
        destination = self.operand(destination_text, size)
        data_alterable(destination, mnemonic)
        require(mnemonic != 'cmp', 'cmp writes nothing; use cmp <ea>,Dn')
        direction = 4
        return [BINARY[mnemonic] | source_reg << 9 | (direction + SIZE_BITS[size]) << 6
                | destination.ea] + destination.words
    require(destination_reg is not None, '%s needs a data register operand' % mnemonic)
    source = self.operand(source_text, size)
    require(source.mode != 1 or size != 'b', 'address register byte operations are illegal')
    return [BINARY[mnemonic] | destination_reg << 9 | SIZE_BITS[size] << 6
            | source.ea] + source.words


def encode_address_binary(self, mnemonic, size, arguments):
    require(len(arguments) == 2, '%s takes two operands' % mnemonic)
    size = size_of(size, 'wl', mnemonic)
    source = self.operand(arguments[0], size)
    reg = address_register(arguments[1])
    require(reg is not None, '%s takes an address register destination' % mnemonic)
    opmode = 7 if size == 'l' else 3
    return [ADDRESS_BINARY[mnemonic] | reg << 9 | opmode << 6 | source.ea] + source.words


MULTIPLY = {'mulu': 0xC0C0, 'muls': 0xC1C0, 'divu': 0x80C0, 'divs': 0x81C0}


def encode_multiply(self, mnemonic, size, arguments):
    require(len(arguments) == 2, '%s takes two operands' % mnemonic)
    size_of(size, 'w', mnemonic)
    source = self.operand(arguments[0], 'w')
    reg = data_register(arguments[1])
    require(reg is not None, '%s takes a data register destination' % mnemonic)
    require(source.mode != 1, '%s cannot use an address register' % mnemonic)
    return [MULTIPLY[mnemonic] | reg << 9 | source.ea] + source.words


# ------------------------------------------------------------------- shifts

SHIFTS = {'as': 0, 'ls': 1, 'rox': 2, 'ro': 3}


def encode_shift(self, mnemonic, size, arguments):
    kind, direction = SHIFTS[mnemonic[:-1]], 1 if mnemonic[-1] == 'l' else 0
    if len(arguments) == 1:
        size_of(size, 'w', mnemonic)
        destination = self.operand(arguments[0], 'w')
        require(destination.mode not in (0, 1) and is_alterable(destination),
                '%s on memory needs a memory alterable operand' % mnemonic)
        return [0xE0C0 | kind << 9 | direction << 8 | destination.ea] + destination.words
    require(len(arguments) == 2, '%s takes one or two operands' % mnemonic)
    size = size_of(size, 'bwl', mnemonic)
    reg = data_register(arguments[1])
    require(reg is not None, '%s takes a data register destination' % mnemonic)
    if arguments[0].startswith('#'):
        count, known = self.parser.number(arguments[0][1:])
        require(not known or 1 <= count <= 8, 'shift count %d is not 1 to 8' % count)
        return [0xE000 | (count & 7) << 9 | direction << 8 | SIZE_BITS[size] << 6
                | kind << 3 | reg]
    count_reg = data_register(arguments[0])
    require(count_reg is not None, '%s takes an immediate or data register count' % mnemonic)
    return [0xE000 | count_reg << 9 | direction << 8 | SIZE_BITS[size] << 6
            | 1 << 5 | kind << 3 | reg]


BITS = {'btst': 0, 'bchg': 1, 'bclr': 2, 'bset': 3}


def encode_bit(self, mnemonic, size, arguments):
    require(len(arguments) == 2, '%s takes two operands' % mnemonic)
    kind = BITS[mnemonic]
    destination = self.operand(arguments[1], 'b')
    require(destination.mode != 1, '%s cannot use an address register' % mnemonic)
    if mnemonic == 'btst':
        require(destination.mode != 7 or destination.reg != 4,
                'btst cannot use an immediate destination')
    else:
        data_alterable(destination, mnemonic)
    if arguments[0].startswith('#'):
        number, _ = self.parser.number(arguments[0][1:])
        limit = 32 if destination.mode == 0 else 8
        require(0 <= number < limit, 'bit number %d out of range' % number)
        return [0x0800 | kind << 6 | destination.ea, number & 0xff] + destination.words
    reg = data_register(arguments[0])
    require(reg is not None, '%s takes an immediate or data register bit number' % mnemonic)
    return [0x0100 | reg << 9 | kind << 6 | destination.ea] + destination.words


# ----------------------------------------------------------- flow of control

def encode_branch(self, mnemonic, size, arguments):
    require(len(arguments) == 1, '%s takes one operand' % mnemonic)
    condition = 0 if mnemonic == 'bra' else 1 if mnemonic == 'bsr' else CONDITIONS[mnemonic[1:]]
    size = size_of(size, 'bswl', mnemonic, default=None) if size else None
    target, known = self.branch_target(arguments[0])
    offset = target - (self.pc + 2)
    if size in ('b', 's'):
        require(not known or (signed_fits(offset, 8) and offset & 0xff != 0),
                'short branch to %#x is out of range' % target)
        return [0x6000 | condition << 8 | (offset & 0xff)]
    if size is None and known and signed_fits(offset, 8) and offset & 0xff != 0:
        return [0x6000 | condition << 8 | (offset & 0xff)]
    require(not known or signed_fits(offset, 16), 'branch to %#x is out of range' % target)
    return [0x6000 | condition << 8, offset & 0xffff]


def encode_dbcc(self, mnemonic, size, arguments):
    require(len(arguments) == 2, '%s takes two operands' % mnemonic)
    size_of(size, 'w', mnemonic)
    suffix = mnemonic[2:]
    condition = 1 if suffix in ('ra', 'f') else CONDITIONS[suffix]
    reg = data_register(arguments[0])
    require(reg is not None, '%s takes a data register counter' % mnemonic)
    target, known = self.branch_target(arguments[1])
    offset = target - (self.pc + 2)
    require(not known or signed_fits(offset, 16), 'dbcc target %#x is out of range' % target)
    return [0x50C8 | condition << 8 | reg, offset & 0xffff]


def encode_scc(self, mnemonic, size, arguments):
    require(len(arguments) == 1, '%s takes one operand' % mnemonic)
    size_of(size, 'b', mnemonic, default='b')
    condition = CONDITIONS[mnemonic[1:]]
    destination = self.operand(arguments[0], 'b')
    data_alterable(destination, mnemonic)
    return [0x50C0 | condition << 8 | destination.ea] + destination.words


SIMPLE = {'rts': 0x4E75, 'rte': 0x4E73, 'rtr': 0x4E77, 'nop': 0x4E71,
          'reset': 0x4E70, 'trapv': 0x4E76, 'illegal': 0x4AFC}


def encode_simple(self, mnemonic, size, arguments):
    require(not arguments or arguments == [''], '%s takes no operands' % mnemonic)
    return [SIMPLE[mnemonic]]


def encode_trap(self, mnemonic, size, arguments):
    require(len(arguments) == 1 and arguments[0].startswith('#'),
            'trap takes an immediate vector')
    number, known = self.parser.number(arguments[0][1:])
    require(not known or 0 <= number <= 15, 'trap vector %d is not 0 to 15' % number)
    return [0x4E40 | (number & 15)]


def encode_stop(self, mnemonic, size, arguments):
    require(len(arguments) == 1 and arguments[0].startswith('#'),
            'stop takes an immediate status word')
    value, _ = self.parser.number(arguments[0][1:])
    return [0x4E72, value & 0xffff]


def encode_link(self, mnemonic, size, arguments):
    require(len(arguments) == 2, 'link takes two operands')
    reg = address_register(arguments[0])
    require(reg is not None and arguments[1].startswith('#'),
            'link takes an address register and an immediate')
    value, _ = self.parser.number(arguments[1][1:])
    require(signed_fits(value, 16), 'link displacement %d does not fit a word' % value)
    return [0x4E50 | reg, value & 0xffff]


def encode_unlk(self, mnemonic, size, arguments):
    require(len(arguments) == 1, 'unlk takes one operand')
    reg = address_register(arguments[0])
    require(reg is not None, 'unlk takes an address register')
    return [0x4E58 | reg]


HANDLERS = {
    'move': encode_move, 'movea': encode_move, 'moveq': encode_moveq,
    'lea': encode_lea, 'pea': encode_pea, 'movem': encode_movem,
    'exg': encode_exg, 'swap': encode_swap, 'ext': encode_ext,
    'tas': encode_tas, 'jmp': encode_jump, 'jsr': encode_jump,
    'trap': encode_trap, 'stop': encode_stop,
    'link': encode_link, 'unlk': encode_unlk,
}
HANDLERS.update({name: encode_unary for name in UNARY})
HANDLERS.update({name: encode_quick for name in QUICK})
HANDLERS.update({name: encode_immediate for name in IMMEDIATE})
HANDLERS.update({name: encode_binary for name in BINARY})
HANDLERS.update({name: encode_address_binary for name in ADDRESS_BINARY})
HANDLERS.update({name: encode_multiply for name in MULTIPLY})
HANDLERS.update({name: encode_simple for name in SIMPLE})
HANDLERS.update({kind + direction: encode_shift
                 for kind in SHIFTS for direction in 'lr'})
HANDLERS.update({name: encode_bit for name in BITS})
HANDLERS.update({'b' + name: encode_branch for name in CONDITIONS
                 if name not in ('t', 'f')})
HANDLERS.update({'bra': encode_branch, 'bsr': encode_branch})
HANDLERS.update({'db' + name: encode_dbcc for name in CONDITIONS})
HANDLERS.update({'dbra': encode_dbcc})
HANDLERS.update({'s' + name: encode_scc for name in CONDITIONS})
