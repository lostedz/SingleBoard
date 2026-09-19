#!/usr/bin/env python3
"""Expression, operand and register-list syntax for `scripts/asm68k.py`.

The assembler exists so `firmware/boot.hex` and the test images can be rebuilt with
nothing but Python. It accepts the subset of vasm's Motorola syntax that
`firmware/boot.asm` and `tests/*.asm` use and, for that subset, produces
byte-identical output to `vasmm68k -m68000 -Fbin`; `scripts/test_asm68k.py`
checks that against the committed vasm-built binaries.

It is not a complete 68000 assembler. Missing, among others: BCD and
extended-precision arithmetic (ABCD/SBCD/NBCD/ADDX/SUBX/CMPM), MOVEP,
macros, conditional assembly, relocatable sections, and expression
operators beyond the ones below. Unsupported input is an error, never a
silently wrong encoding.

Sizes follow vasm's defaults: branches, and absolute addresses written
without an explicit `.w`/`.l`, take the shortest encoding that fits, found
by iterating the layout to a fixed point.
"""
from __future__ import annotations

import re


class AsmError(Exception):
    """A fatal assembly error, reported with its source line."""


class Unresolved(Exception):
    """A symbol whose value is not known yet in this pass."""


# ---------------------------------------------------------------- expressions

TOKEN = re.compile(r"""
    \s*(?:
      (?P<hex>\$[0-9A-Fa-f]+)
    | (?P<bin>%[01]+)
    | (?P<dec>\d+)
    | (?P<char>'(?:[^']|'')*'|"(?:[^"]|"")*")
    | (?P<name>[A-Za-z_.][A-Za-z0-9_.$]*)
    | (?P<op><<|>>|[-+*/&|^~()])
    )""", re.VERBOSE)


def tokenize(text):
    tokens, pos = [], 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = TOKEN.match(text, pos)
        if not match or match.end() == match.start():
            raise AsmError('cannot parse expression at %r' % text[pos:])
        pos = match.end()
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
    return tokens


class Expr:
    """Recursive-descent evaluator over the token list."""

    def __init__(self, tokens, symbols, here):
        self.tokens, self.pos = tokens, 0
        self.symbols, self.here = symbols, here

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def eat(self):
        token = self.peek()
        self.pos += 1
        return token

    def value(self):
        result = self.bitwise()
        if self.pos != len(self.tokens):
            raise AsmError('trailing junk in expression: %s' % (self.tokens[self.pos:],))
        return result

    def bitwise(self):
        left = self.shift()
        while self.peek() in (('op', '&'), ('op', '|'), ('op', '^')):
            operator = self.eat()[1]
            right = self.shift()
            left = {'&': left & right, '|': left | right, '^': left ^ right}[operator]
        return left

    def shift(self):
        left = self.additive()
        while self.peek() in (('op', '<<'), ('op', '>>')):
            operator = self.eat()[1]
            right = self.additive()
            left = left << right if operator == '<<' else left >> right
        return left

    def additive(self):
        left = self.multiplicative()
        while self.peek() in (('op', '+'), ('op', '-')):
            operator = self.eat()[1]
            right = self.multiplicative()
            left = left + right if operator == '+' else left - right
        return left

    def multiplicative(self):
        left = self.unary()
        while self.peek() in (('op', '*'), ('op', '/')):
            operator = self.eat()[1]
            right = self.unary()
            if operator == '/' and right == 0:
                raise AsmError('division by zero')
            left = left * right if operator == '*' else int(left / right)
        return left

    def unary(self):
        token = self.peek()
        if token == ('op', '-'):
            self.eat()
            return -self.unary()
        if token == ('op', '+'):
            self.eat()
            return self.unary()
        if token == ('op', '~'):
            self.eat()
            return ~self.unary()
        return self.primary()

    def primary(self):
        kind, text = self.eat()
        if kind == 'hex':
            return int(text[1:], 16)
        if kind == 'bin':
            return int(text[1:], 2)
        if kind == 'dec':
            return int(text, 10)
        if kind == 'char':
            body = text[1:-1].replace(text[0] * 2, text[0])
            if not 1 <= len(body) <= 4:
                raise AsmError('character constant %s must be 1 to 4 characters' % text)
            return int.from_bytes(body.encode('latin-1'), 'big')
        if kind == 'name':
            if text not in self.symbols:
                raise Unresolved(text)
            return self.symbols[text]
        if (kind, text) == ('op', '*'):
            return self.here
        if (kind, text) == ('op', '('):
            inner = self.bitwise()
            if self.eat() != ('op', ')'):
                raise AsmError('unbalanced parenthesis')
            return inner
        raise AsmError('unexpected %r in expression' % (text,))


def evaluate(text, symbols, here):
    if not text.strip():
        raise AsmError('empty expression')
    return Expr(tokenize(text), symbols, here).value()


# ------------------------------------------------------------------- operands

DATA_REG = re.compile(r'^d([0-7])$', re.I)
ADDR_REG = re.compile(r'^(?:a([0-7])|sp)$', re.I)
SIZE_BITS = {'b': 0, 'w': 1, 'l': 2}
MOVE_SIZE = {'b': 1, 'w': 3, 'l': 2}


def data_register(text):
    match = DATA_REG.match(text.strip())
    return int(match.group(1)) if match else None


def address_register(text):
    match = ADDR_REG.match(text.strip())
    if not match:
        return None
    return 7 if match.group(1) is None else int(match.group(1))


def index_register(text):
    """`d3.w`, `a0.l`, `d3` (word is the default) -> (is_address, reg, long)."""
    text = text.strip()
    long_index = False
    if text.lower().endswith(('.w', '.l')):
        long_index = text.lower().endswith('.l')
        text = text[:-2]
    reg = data_register(text)
    if reg is not None:
        return 0, reg, long_index
    reg = address_register(text)
    if reg is not None:
        return 1, reg, long_index
    raise AsmError('%r is not an index register' % text)


def split_arguments(text):
    """Split on commas that are outside parentheses and quotes."""
    parts, depth, current, quote = [], 0, '', None
    for character in text:
        if quote:
            current += character
            if character == quote:
                quote = None
            continue
        if character in ('"', "'"):
            quote = character
        elif character == '(':
            depth += 1
        elif character == ')':
            depth -= 1
        if character == ',' and depth == 0:
            parts.append(current)
            current = ''
        else:
            current += character
    parts.append(current)
    return [part.strip() for part in parts]


class Operand:
    """One parsed operand, sized against the current symbol table.

    `mode`/`reg` are the 68000 effective-address fields and `words` are the
    extension words that follow the opcode. `kind` keeps the source form, so
    instructions can reject the modes they do not allow.
    """

    def __init__(self, kind, mode=None, reg=None, words=(), value=0, known=True):
        self.kind = kind
        self.mode, self.reg = mode, reg
        self.words = list(words)
        self.value, self.known = value, known

    @property
    def ea(self):
        if self.mode is None:
            raise AsmError('a %s operand has no effective address' % self.kind)
        return (self.mode << 3) | self.reg


def signed_fits(value, bits):
    return -(1 << (bits - 1)) <= value < (1 << (bits - 1))


def immediate_words(value, size):
    if size == 'b':
        if not -128 <= value <= 255:
            raise AsmError('byte immediate %d out of range' % value)
        return [value & 0xff]
    if size == 'w':
        if not -0x8000 <= value <= 0xffff:
            raise AsmError('word immediate %d out of range' % value)
        return [value & 0xffff]
    if not -0x80000000 <= value <= 0xffffffff:
        raise AsmError('long immediate %d out of range' % value)
    return [(value >> 16) & 0xffff, value & 0xffff]


class Parser:
    def __init__(self, symbols, here, missing=None):
        self.symbols, self.here = symbols, here
        self.missing = missing if missing is not None else set()

    def number(self, text):
        """Evaluate, recording and reporting any symbol not known yet."""
        try:
            return evaluate(text, self.symbols, self.here), True
        except Unresolved as error:
            qualify = getattr(self.symbols, 'qualify', lambda name: name)
            self.missing.add(qualify(str(error)))
            return 0, False

    def operand(self, text, size='w'):
        text = text.strip()
        if not text:
            raise AsmError('missing operand')

        reg = data_register(text)
        if reg is not None:
            return Operand('Dn', 0, reg)
        reg = address_register(text)
        if reg is not None:
            return Operand('An', 1, reg)
        if text.lower() in ('sr', 'ccr', 'usp'):
            return Operand(text.lower())

        if text.startswith('#'):
            value, known = self.number(text[1:])
            return Operand('#', 7, 4, immediate_words(value, size), value, known)

        if text.startswith('-('):
            reg = address_register(text[2:-1]) if text.endswith(')') else None
            if reg is None:
                raise AsmError('malformed predecrement %r' % text)
            return Operand('-(An)', 4, reg)

        # (an), (an)+, (d,an), (d,an,xn), (d,pc), (d,pc,xn)
        if text.startswith('('):
            postincrement = text.endswith(')+')
            if postincrement:
                body = text[1:-2]
            elif text.endswith(')'):
                body = text[1:-1]
            else:
                raise AsmError('malformed operand %r' % text)
            parts = split_arguments(body)
            if len(parts) == 1:
                reg = address_register(parts[0])
                if reg is None:
                    raise AsmError('malformed indirect %r' % text)
                return Operand('(An)+' if postincrement else '(An)',
                               3 if postincrement else 2, reg)
            if postincrement:
                raise AsmError('postincrement takes a plain address register: %r' % text)
            return self.displacement(parts[0], parts[1:])

        # d(an), d(an,xn), d(pc), d(pc,xn)
        match = re.match(r'^(.*?)\(([^()]*)\)$', text)
        if match and match.group(1).strip():
            return self.displacement(match.group(1), split_arguments(match.group(2)))

        # absolute, with an optional explicit size
        forced = None
        if text.lower().endswith(('.w', '.l')):
            forced, text = text[-1].lower(), text[:-2]
        value, known = self.number(text)
        if forced == 'w' or (forced is None and known and signed_fits(value, 16)):
            if known and not signed_fits(value, 16):
                raise AsmError('absolute address %#x does not fit a word' % value)
            return Operand('abs.w', 7, 0, [value & 0xffff], value, known)
        return Operand('abs.l', 7, 1,
                       [(value >> 16) & 0xffff, value & 0xffff], value, known)

    def displacement(self, offset_text, rest):
        """The shared tail of d(An), d(An,Xn), d(PC) and d(PC,Xn)."""
        base = rest[0].strip()
        value, known = self.number(offset_text) if offset_text.strip() else (0, True)
        program_counter = base.lower() == 'pc'
        reg = None if program_counter else address_register(base)
        if reg is None and not program_counter:
            raise AsmError('%r is not an address register or PC' % base)
        if len(rest) == 1:
            if program_counter:
                offset = value - (self.here + 2)
                if known and not signed_fits(offset, 16):
                    raise AsmError('PC-relative displacement out of range')
                return Operand('d(PC)', 7, 2, [offset & 0xffff], value, known)
            if known and not signed_fits(value, 16):
                raise AsmError('displacement %d does not fit a word' % value)
            return Operand('d(An)', 5, reg, [value & 0xffff], value, known)
        if len(rest) != 2:
            raise AsmError('too many components in an indexed operand')
        is_address, index, long_index = index_register(rest[1])
        offset = value - (self.here + 2) if program_counter else value
        if known and not signed_fits(offset, 8):
            raise AsmError('index displacement %d does not fit a byte' % offset)
        extension = (is_address << 15) | (index << 12) | (long_index << 11) | (offset & 0xff)
        if program_counter:
            return Operand('d(PC,Xn)', 7, 3, [extension], value, known)
        return Operand('d(An,Xn)', 6, reg, [extension], value, known)


def register_list(text, predecrement):
    """`d0-d2/a0/a4-a6` -> a MOVEM mask, reversed for -(An)."""
    mask = 0
    for piece in text.split('/'):
        piece = piece.strip()
        if not piece:
            raise AsmError('empty register list entry')
        bounds = piece.split('-')
        if len(bounds) > 2:
            raise AsmError('malformed register range %r' % piece)
        numbers = []
        for bound in bounds:
            reg = data_register(bound)
            if reg is None:
                reg = address_register(bound)
                if reg is None:
                    raise AsmError('%r is not a register' % bound)
                reg += 8
            numbers.append(reg)
        first, last = numbers[0], numbers[-1]
        if (first < 8) != (last < 8) or last < first:
            raise AsmError('malformed register range %r' % piece)
        for bit in range(first, last + 1):
            mask |= 1 << bit
    if predecrement:
        mask = int('{:016b}'.format(mask)[::-1], 2)
    return mask
