#!/usr/bin/env python3
"""Generate the SingleBoard 68K KiCad schematic and the matching board UCF.

The schematic is emitted from tables rather than drawn by hand so the FPGA
ball assignment, the ISE constraints file and the drawing cannot drift apart:
SDRAM_MAP / BANK0_MAP / BANK2_MAP below are the single source of truth for
all three.

Run from the repository root:

    python scripts/gen_schematic.py

Outputs into hardware/ (project, five sheets, project symbol library) and
fpga/spartan6/singleboard.ucf.
"""

import os
import re
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HW = os.path.join(ROOT, "hardware")
SYMDIR = os.path.join(HW, "symbols")
PROJECT = "singleboard"
DATE = "2026-09-19"

# s-expression revisions as written by KiCad 10.0.
SCH_VERSION = "20260101"
SYM_VERSION = "20251024"
GEN_VERSION = "10.0"

STOCK = None  # KiCad stock symbol directory, resolved in main()

NS = uuid.UUID("6a1f9f1c-6e44-4f1e-9f6e-5a6800006800")


def uid(*parts):
    """Deterministic UUID, so regenerating produces no diff churn."""
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))


ROOT_UUID = uid("file", "singleboard.kicad_sch")


# ---------------------------------------------------------------------------
# XC6SLX16-FTG256 ball map, read from the vendored legacy KiCad library.
# ---------------------------------------------------------------------------

def load_fpga_pins():
    path = os.path.join(HW, "sources", "XC6SLX16-FTG256.lib")
    pins = []
    for line in open(path, encoding="utf-8"):
        if not line.startswith("X "):
            continue
        f = line.split()
        pins.append({"name": f[1], "ball": f[2], "unit": int(f[9]), "etype": f[11]})
    if len(pins) != 256:
        sys.exit("expected 256 balls in XC6SLX16-FTG256.lib, got %d" % len(pins))
    return pins


def bank_of(name):
    m = re.match(r"^IO_.*_(\d)$", name)
    return int(m.group(1)) if m else None


CONFIG_PIN_TYPE = {
    "TCK": "input", "TDI": "input", "TMS": "input", "TDO": "output",
    "DONE_2": "open_collector", "PROGRAM_B_2": "input",
    "SUSPEND": "input", "CMPCS_B_2": "input",
}


def pin_sort_key(p):
    m = re.match(r"^IO_L(\d+)([PN])", p["name"])
    if m:
        return (int(m.group(1)), 0 if m.group(2) == "P" else 1, p["name"])
    return (9999, 0, p["name"])


# ---------------------------------------------------------------------------
# Board pin assignment: net name -> FTG256 ball.
#
# The SDRAM ball assignment is not invented here: it is read out of
# fpga/spartan6/sp6_SDRAM.ucf, the constraints file for the reference
# Spartan-6 SDRAM board. That file is the authority, so the schematic follows
# the physical board rather than the other way round. All 39 signals land in
# bank 3; the soft controller in rtl/sdram_mt48lc16m16a2.v drives them as
# ordinary LVCMOS33 I/O, and the hard memory controller is not instantiated.
# ---------------------------------------------------------------------------

SDRAM_UCF = os.path.join(ROOT, "fpga", "spartan6", "sp6_SDRAM.ucf")

# Signal names used by sp6_SDRAM.ucf -> the net names used in this schematic.
SDRAM_UCF_NETS = dict(
    [("SDCLK0", "SDRAM_CLK_FPGA"), ("SDCKE0", "SDRAM_CKE"),
     ("SDCS0", "SDRAM_CS_N"), ("RAS", "SDRAM_RAS_N"), ("CAS", "SDRAM_CAS_N"),
     ("SDWE", "SDRAM_WE_N"), ("DQM[0]", "SDRAM_LDQM"), ("DQM[1]", "SDRAM_UDQM")]
    + [("Address[%d]" % i, "SDRAM_A%d" % i) for i in range(13)]
    + [("Bank[%d]" % i, "SDRAM_BA%d" % i) for i in range(2)]
    + [("Data[%d]" % i, "SDRAM_DQ%d" % i) for i in range(16)])


def load_sdram_map():
    """Read the SDRAM ball assignment from the reference board's UCF."""
    text = open(SDRAM_UCF, encoding="utf-8").read()
    locs = dict(re.findall(r'NET\s+"([^"]+)"\s+LOC\s*=\s*(\w+)', text))
    out = []
    for ucf_name, net in SDRAM_UCF_NETS.items():
        if ucf_name not in locs:
            sys.exit("%s has no LOC for %s" % (SDRAM_UCF, ucf_name))
        out.append((net, locs[ucf_name]))
    return out


SDRAM_MAP = load_sdram_map()

# Bank 0: clock, UART, reset button, status LEDs, HSWAPEN strap.
BANK0_MAP = [
    ("CLK50M", "E7"),
    ("UART_RX", "B12"), ("UART_TX", "A12"),
    ("RESET_BTN_N", "C13"),
    ("LED0", "B14"), ("LED1", "A14"), ("LED2", "D11"), ("LED3", "D12"),
    ("HSWAPEN", "C4"),
]

# Bank 2: master-SPI configuration interface to the boot flash, plus mode pins.
BANK2_MAP = [
    ("CFG_CCLK", "R11"), ("CFG_MOSI", "T10"), ("CFG_DIN", "P10"),
    ("CFG_CSO_N", "T3"), ("INIT_B", "R3"),
    ("CFG_M0", "T11"), ("CFG_M1", "N11"),
]

# Dedicated configuration / JTAG pins (symbol unit 3): FPGA pin name -> net.
UNIT3_MAP = {
    "TCK": "JTAG_TCK", "TDI": "JTAG_TDI", "TDO": "JTAG_TDO", "TMS": "JTAG_TMS",
    "DONE_2": "DONE", "PROGRAM_B_2": "PROGRAM_B", "SUSPEND": "GND",
    "CMPCS_B_2": "CMPCS_B",
}

IO_MAP = dict(SDRAM_MAP + BANK0_MAP + BANK2_MAP)
BALL_NET = {b: n for n, b in IO_MAP.items()}
if len(BALL_NET) != len(IO_MAP):
    sys.exit("duplicate ball in pin assignment tables")

RAIL_OF_BANK = {0: "+3V3", 1: "+3V3", 2: "+3V3", 3: "+3V3"}


def validate_assignment(pins):
    """Check every assigned ball exists, is an I/O, and sits in a sane bank."""
    by_ball = {p["ball"]: p for p in pins}
    banks = {}
    for net, ball in sorted(IO_MAP.items()):
        p = by_ball.get(ball)
        if p is None:
            sys.exit("net %s is assigned to %s, which is not an FTG256 ball"
                     % (net, ball))
        if p["etype"] != "B":
            sys.exit("net %s is assigned to %s (%s), which is not an I/O pin"
                     % (net, ball, p["name"]))
        banks.setdefault(net.split("_")[0], set()).add(bank_of(p["name"]))
    if banks.get("SDRAM") != {3}:
        sys.exit("SDRAM signals span banks %s; expected bank 3 only"
                 % sorted(banks.get("SDRAM", [])))
    return banks


# ---------------------------------------------------------------------------
# s-expression emitters
# ---------------------------------------------------------------------------

def eff(size=1.27, hide=False, justify=None):
    s = "(effects (font (size %s %s))" % (size, size)
    if justify:
        s += " (justify %s)" % justify
    if hide:
        s += " (hide yes)"
    return s + ")"


class Sheet:
    def __init__(self, name, filename, title, paper="A3", is_root=False, page="1"):
        self.name = name
        self.filename = filename
        self.title = title
        self.paper = paper
        self.is_root = is_root
        self.page = page
        self.file_uuid = uid("file", filename)
        # Path component used by symbol instances: the root sheet's box uuid.
        self.box_uuid = "" if is_root else uid("sheetbox", name)
        self.items = []
        self.libs = set()
        self.parts = []
        self._pwr = 0

    def _instpath(self):
        return "/" + ROOT_UUID if self.is_root else "/%s/%s" % (ROOT_UUID, self.box_uuid)

    # -- placement ---------------------------------------------------------
    def symbol(self, lib_id, ref, value, at, unit=1, footprint="", rot=0,
               hide_fields=(), ref_off=(0, -3.81), val_off=(0, 3.81), extra=None,
               in_bom=True, dnp=False):
        self.libs.add(lib_id)
        x, y = snap(at)
        props = [("Reference", ref, (x + ref_off[0], y + ref_off[1]),
                  "Reference" in hide_fields),
                 ("Value", value, (x + val_off[0], y + val_off[1]),
                  "Value" in hide_fields),
                 ("Footprint", footprint, (x, y), True),
                 ("Datasheet", "", (x, y), True),
                 ("Description", "", (x, y), True)]
        for k, v in sorted((extra or {}).items()):
            props.append((k, v, (x, y), True))
        ptxt = ""
        for pname, val, (px, py), hid in props:
            ptxt += ('\n\t\t(property "%s" "%s"\n\t\t\t(at %s %s 0)\n\t\t\t%s\n\t\t)'
                     % (pname, val, px, py, eff(1.27, hid)))
        self.items.append(
            '\t(symbol\n\t\t(lib_id "%s")\n\t\t(at %s %s %d)\n\t\t(unit %d)\n'
            '\t\t(exclude_from_sim no)\n\t\t(in_bom %s)\n\t\t(on_board yes)\n'
            '\t\t(dnp %s)\n\t\t(uuid "%s")%s\n'
            '\t\t(instances\n\t\t\t(project "%s"\n\t\t\t\t(path "%s"\n'
            '\t\t\t\t\t(reference "%s")\n\t\t\t\t\t(unit %d)\n\t\t\t\t)\n'
            '\t\t\t)\n\t\t)\n\t)'
            % (lib_id, x, y, rot, unit, "yes" if in_bom else "no",
               "yes" if dnp else "no", uid("sym", self.filename, ref, unit),
               ptxt, PROJECT, self._instpath(), ref, unit))
        if not ref.startswith("#"):
            self.parts.append((ref, value, footprint, lib_id))
        return x, y

    def power(self, kind, at, rot=0):
        """Place a power port; its pin sits exactly on `at`."""
        self._pwr += 1
        ref = "#PWR%s%03d" % (self.page, self._pwr)
        return self.symbol("power:" + kind, ref, kind, at, rot=rot,
                           hide_fields=("Reference", "Value"), in_bom=False,
                           val_off=(0, -3.81) if rot == 0 else (0, 3.81))

    def wire(self, x1, y1, x2, y2):
        if (x1, y1) == (x2, y2):
            return
        self.items.append(
            '\t(wire\n\t\t(pts\n\t\t\t(xy %s %s) (xy %s %s)\n\t\t)\n'
            '\t\t(stroke (width 0) (type default))\n\t\t(uuid "%s")\n\t)'
            % (x1, y1, x2, y2, uid("w", self.filename, x1, y1, x2, y2)))

    def route(self, p1, p2):
        """Two-segment orthogonal connection (horizontal first)."""
        (x1, y1), (x2, y2) = p1, p2
        if abs(y1 - y2) < 1e-9 or abs(x1 - x2) < 1e-9:
            self.wire(x1, y1, x2, y2)
        else:
            self.wire(x1, y1, x2, y1)
            self.wire(x2, y1, x2, y2)

    def junction(self, x, y):
        self.items.append(
            '\t(junction\n\t\t(at %s %s)\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n'
            '\t\t(uuid "%s")\n\t)' % (x, y, uid("j", self.filename, x, y)))

    def glabel(self, name, at, rot=0, shape="bidirectional", size=1.27):
        x, y = at
        just = "left" if rot in (0, 90) else "right"
        self.items.append(
            '\t(global_label "%s"\n\t\t(shape %s)\n\t\t(at %s %s %d)\n\t\t%s\n'
            '\t\t(uuid "%s")\n'
            '\t\t(property "Intersheetrefs" "${INTERSHEET_REFS}"\n'
            '\t\t\t(at %s %s 0)\n\t\t\t%s\n\t\t)\n\t)'
            % (name, shape, x, y, rot, eff(size, justify=just),
               uid("gl", self.filename, name, x, y), x, y, eff(1.27, hide=True)))

    def label(self, name, at, rot=0, size=1.27):
        x, y = at
        just = "left" if rot in (0, 90) else "right"
        self.items.append(
            '\t(label "%s"\n\t\t(at %s %s %d)\n\t\t%s\n\t\t(uuid "%s")\n\t)'
            % (name, x, y, rot, eff(size, justify=just),
               uid("lb", self.filename, name, x, y)))

    def nc(self, x, y):
        self.items.append('\t(no_connect\n\t\t(at %s %s)\n\t\t(uuid "%s")\n\t)'
                          % (x, y, uid("nc", self.filename, x, y)))

    def text(self, body, at, size=1.27, rot=0):
        x, y = at
        self.items.append(
            '\t(text "%s"\n\t\t(exclude_from_sim yes)\n\t\t(at %s %s %d)\n\t\t%s\n'
            '\t\t(uuid "%s")\n\t)'
            % (body.replace('"', "'"), x, y, rot, eff(size, justify="left"),
               uid("t", self.filename, body, x, y)))

    def box(self, x, y, w, h):
        self.items.append(
            '\t(rectangle\n\t\t(start %s %s)\n\t\t(end %s %s)\n'
            '\t\t(stroke (width 0.1) (type dash))\n\t\t(fill (type none))\n'
            '\t\t(uuid "%s")\n\t)' % (x, y, x + w, y + h,
                                      uid("rect", self.filename, x, y, w, h)))

    def sheet_box(self, child, at, size):
        x, y = at
        w, h = size
        self.items.append(
            '\t(sheet\n\t\t(at %s %s)\n\t\t(size %s %s)\n\t\t(exclude_from_sim no)\n'
            '\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n'
            '\t\t(stroke (width 0.1524) (type solid))\n'
            '\t\t(fill (color 0 0 0 0.0000))\n\t\t(uuid "%s")\n'
            '\t\t(property "Sheetname" "%s"\n\t\t\t(at %s %s 0)\n\t\t\t%s\n\t\t)\n'
            '\t\t(property "Sheetfile" "%s"\n\t\t\t(at %s %s 0)\n\t\t\t%s\n\t\t)\n'
            '\t\t(instances\n\t\t\t(project "%s"\n\t\t\t\t(path "/%s"\n'
            '\t\t\t\t\t(page "%s")\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)'
            % (x, y, w, h, child.box_uuid, child.name, x, y - 0.7,
               eff(1.524, justify="left bottom"), child.filename, x, y + h + 0.7,
               eff(1.524, justify="left top"), PROJECT, ROOT_UUID, child.page))

    # -- rendering ---------------------------------------------------------
    def render(self, all_sheets=None):
        libs = "\n".join(symbol_def(l) for l in sorted(self.libs))
        tail = ""
        if self.is_root:
            paths = "".join('\t\t(path "/%s"\n\t\t\t(page "%s")\n\t\t)\n'
                            % (("" if s.is_root else s.box_uuid), s.page)
                            for s in all_sheets)
            tail = "\t(sheet_instances\n%s\t)\n" % paths
        return ('(kicad_sch\n\t(version %s)\n\t(generator "singleboard-gen")\n'
                '\t(generator_version "%s")\n\t(uuid "%s")\n\t(paper "%s")\n'
                '\t(title_block\n\t\t(title "SingleBoard 68K -- %s")\n'
                '\t\t(date "%s")\n\t\t(rev "A")\n'
                '\t\t(company "SingleBoard project -- concept schematic, not fabricated")\n\t)\n'
                '\t(lib_symbols\n%s\n\t)\n%s\n%s\t(embedded_fonts no)\n)\n'
                % (SCH_VERSION, GEN_VERSION,
                   ROOT_UUID if self.is_root else self.file_uuid,
                   self.paper, self.title, DATE, libs, "\n".join(self.items), tail))


# ---------------------------------------------------------------------------
# Symbol library access
# ---------------------------------------------------------------------------

_symcache = {}
_pincache = {}
CUSTOM_SYMBOLS = {}


def _extract(text, name):
    key = '\n\t(symbol "%s"\n' % name
    if key not in text:
        return None
    i = text.index(key) + 1
    m = re.search(r'\n\t\(symbol "', text[i + 12:])
    if m:
        return text[i:i + 12 + m.start()]
    body = text[i:].rstrip()
    return body[:body.rindex(")")].rstrip() + "\n"


def raw_symbol(lib_id):
    lib, name = lib_id.split(":", 1)
    if lib == "SingleBoard":
        return CUSTOM_SYMBOLS[name], name
    path = os.path.join(STOCK, lib + ".kicad_sym")
    body = _extract(open(path, encoding="utf-8").read(), name)
    if body is None:
        sys.exit("symbol %s not found in %s" % (lib_id, path))
    if re.search(r'^\t\t\(extends ', body, re.M):
        sys.exit("%s is a derived symbol; use a self-contained one" % lib_id)
    return body, name


def symbol_def(lib_id):
    """lib_symbols entry for `Lib:Name`, re-indented for a .kicad_sch."""
    if lib_id in _symcache:
        return _symcache[lib_id]
    body, name = raw_symbol(lib_id)
    # Only the outer symbol carries the library prefix; the per-unit child
    # symbols keep their bare "<name>_<unit>_<style>" form.
    body = body.replace('(symbol "%s"' % name, '(symbol "%s"' % lib_id, 1)
    out = "\n".join(("\t" + l if l.strip() else l) for l in body.rstrip().split("\n"))
    _symcache[lib_id] = out
    return out


def sym_pins(lib_id):
    """{pin number: (x, y, angle, length)} in library coordinates."""
    if lib_id in _pincache:
        return _pincache[lib_id]
    body, _ = raw_symbol(lib_id)
    pins = {}
    for m in re.finditer(
            r'\(pin \w+ \w+\s*\n\s*\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)\s*\n'
            r'\s*\(length ([\d.]+)\).*?\n\s*\(number "([^"]*)"', body, re.S):
        x, y, a, ln, num = m.groups()
        pins[num] = (float(x), float(y), float(a), float(ln))
    if not pins:
        sys.exit("no pins parsed for %s" % lib_id)
    _pincache[lib_id] = pins
    return pins


GRID = 1.27


def snap(p):
    """Round a placement onto the 1.27 mm connection grid."""
    return (round(round(p[0] / GRID) * GRID, 4),
            round(round(p[1] / GRID) * GRID, 4))


def _rot(px, py, rot):
    """Library (x, y) -> schematic offset for a symbol placed at rotation `rot`."""
    if rot == 0:
        return px, -py
    if rot == 90:
        return py, px
    if rot == 180:
        return -px, py
    if rot == 270:
        return -py, -px
    raise ValueError(rot)


def pin_at(lib_id, num, at, rot=0):
    px, py, _, _ = sym_pins(lib_id)[str(num)]
    dx, dy = _rot(px, py, rot)
    at = snap(at)
    return (round(at[0] + dx, 4), round(at[1] + dy, 4))


def pin_dir(lib_id, num, rot=0):
    """Screen-space direction the stub should leave the pin, as (dx, dy)."""
    _, _, ang, _ = sym_pins(lib_id)[str(num)]
    a = int((rot - ang) % 360)          # direction the pin body points
    a = (a + 180) % 360                  # stub leaves the other way
    return {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[a], a


# ---------------------------------------------------------------------------
# Stub-and-label helper: the whole schematic is drawn in this net-label style.
# ---------------------------------------------------------------------------

def tag(sh, lib_id, num, at, net, rot=0, stub=5.08, kind="global",
        shape="bidirectional"):
    """Draw a stub off one pin and terminate it with a label or power port."""
    p = pin_at(lib_id, num, at, rot)
    if kind == "nc":
        sh.nc(p[0], p[1])
        return p
    (dx, dy), ang = pin_dir(lib_id, num, rot)
    end = (round(p[0] + dx * stub, 4), round(p[1] + dy * stub, 4))
    sh.wire(p[0], p[1], end[0], end[1])
    if kind == "power":
        sh.power(net, end, rot=0 if dy <= 0 else 180)
    elif kind == "local":
        sh.label(net, end, rot=ang)
    else:
        sh.glabel(net, end, rot=ang, shape=shape)
    return end


def two_pin(sh, lib_id, ref, value, at, a_net, b_net, rot=0, footprint="",
            a_kind="local", b_kind="local", dnp=False, stub=3.81):
    """Place a two-pin part with a labelled stub on each end."""
    sh.symbol(lib_id, ref, value, at, rot=rot, footprint=footprint, dnp=dnp,
              ref_off=(3.81, -1.27) if rot in (90, 270) else (0, -3.81),
              val_off=(3.81, 1.27) if rot in (90, 270) else (0, 3.81))
    tag(sh, lib_id, 1, at, a_net, rot, stub=stub, kind=a_kind)
    tag(sh, lib_id, 2, at, b_net, rot, stub=stub, kind=b_kind)


def decap(sh, ref, value, at, rail, footprint="Capacitor_SMD:C_0402_1005Metric"):
    """Vertical decoupling capacitor between `rail` and GND."""
    two_pin(sh, "Device:C", ref, value, at, rail, "GND",
            rot=0, footprint=footprint, a_kind="power", b_kind="power")


# ---------------------------------------------------------------------------
# Custom symbols: the FPGA (built from the ball map) and the SDRAM.
# ---------------------------------------------------------------------------

def _pin_sexp(etype, name, number, x, y, ang, length=5.08, nsize=1.0):
    return ('\t\t\t(pin %s line\n\t\t\t\t(at %s %s %d)\n\t\t\t\t(length %s)\n'
            '\t\t\t\t(name "%s"\n\t\t\t\t\t%s\n\t\t\t\t)\n'
            '\t\t\t\t(number "%s"\n\t\t\t\t\t%s\n\t\t\t\t)\n\t\t\t)'
            % (etype, x, y, ang, length, name, eff(nsize), number, eff(0.762)))


def _rect(x1, y1, x2, y2):
    return ('\t\t\t(rectangle\n\t\t\t\t(start %s %s)\n\t\t\t\t(end %s %s)\n'
            '\t\t\t\t(stroke (width 0.254) (type default))\n'
            '\t\t\t\t(fill (type background))\n\t\t\t)' % (x1, y1, x2, y2))


def _sym_header(name, ref, value, datasheet="", desc=""):
    return ('\t(symbol "%s"\n\t\t(pin_names\n\t\t\t(offset 1.016)\n\t\t)\n'
            '\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n'
            '\t\t(property "Reference" "%s"\n\t\t\t(at 0 0 0)\n\t\t\t%s\n\t\t)\n'
            '\t\t(property "Value" "%s"\n\t\t\t(at 0 0 0)\n\t\t\t%s\n\t\t)\n'
            '\t\t(property "Footprint" ""\n\t\t\t(at 0 0 0)\n\t\t\t%s\n\t\t)\n'
            '\t\t(property "Datasheet" "%s"\n\t\t\t(at 0 0 0)\n\t\t\t%s\n\t\t)\n'
            '\t\t(property "Description" "%s"\n\t\t\t(at 0 0 0)\n\t\t\t%s\n\t\t)\n'
            % (name, ref, eff(1.27, True), value, eff(1.27, True), eff(1.27, True),
               datasheet, eff(1.27, True), desc, eff(1.27, True)))


PITCH = 2.54


def build_fpga_symbol(pins):
    """Four-unit XC6SLX16-FTG256 symbol laid out from the ball map."""
    name = "XC6SLX16-FTG256"
    by_unit = {}
    for p in pins:
        by_unit.setdefault(p["unit"], []).append(p)

    def split(unit, left_pred):
        left = sorted([p for p in by_unit[unit] if left_pred(p)], key=pin_sort_key)
        right = sorted([p for p in by_unit[unit] if not left_pred(p)], key=pin_sort_key)
        return left, right

    units = {}
    units[1] = split(1, lambda p: bank_of(p["name"]) == 0 or p["name"] == "VCCO_0")
    units[2] = split(2, lambda p: bank_of(p["name"]) == 3 or p["name"] == "VCCO_3")
    units[3] = (sorted(by_unit[3], key=lambda p: p["name"]), [])
    pwr = by_unit[4]
    units[4] = (sorted([p for p in pwr if p["name"] != "GND"], key=lambda p: p["name"]),
                sorted([p for p in pwr if p["name"] == "GND"],
                       key=lambda p: (p["ball"][0], int(p["ball"][1:]))))

    out = _sym_header(name, "U", name,
                      "https://docs.amd.com/v/u/en-US/ds160",
                      "Xilinx Spartan-6 XC6SLX16, FTG256 BGA")
    half_w = 44.45
    geometry = {}
    for u in (1, 2, 3, 4):
        left, right = units[u]
        rows = max(len(left), len(right))
        h = (rows + 1) * PITCH
        top = h / 2.0
        out += '\t\t(symbol "%s_%d_1"\n' % (name, u)
        out += _rect(-half_w, top, half_w, -top) + "\n"
        for side, xoff, ang in ((left, -half_w - 5.08, 0), (right, half_w + 5.08, 180)):
            for i, p in enumerate(side):
                y = top - (i + 1) * PITCH
                if p["etype"] == "W":
                    et = "power_in"
                else:
                    et = CONFIG_PIN_TYPE.get(p["name"], "bidirectional")
                out += _pin_sexp(et, p["name"], p["ball"], xoff, y, ang) + "\n"
                geometry[(u, p["ball"])] = (xoff, y)
        out += "\t\t)\n"
    out += "\t\t(embedded_fonts no)\n\t)\n"
    CUSTOM_SYMBOLS[name] = out
    return units


# 54-pin TSOP-II x16 SDRAM. Pin numbers follow the JEDEC 54-pin TSOP-II
# arrangement; cross-check against the Micron datasheet before fabrication.
SDRAM_LEFT = [
    ("BA0", "20", "input"), ("BA1", "21", "input"),
    ("A0", "23", "input"), ("A1", "24", "input"), ("A2", "25", "input"),
    ("A3", "26", "input"), ("A4", "29", "input"), ("A5", "30", "input"),
    ("A6", "31", "input"), ("A7", "32", "input"), ("A8", "33", "input"),
    ("A9", "34", "input"), ("A10", "22", "input"), ("A11", "35", "input"),
    ("A12", "36", "input"),
    ("~{CS}", "19", "input"), ("~{RAS}", "18", "input"), ("~{CAS}", "17", "input"),
    ("~{WE}", "16", "input"), ("CKE", "37", "input"), ("CLK", "38", "input"),
    ("DQML", "15", "input"), ("DQMH", "39", "input"),
]
SDRAM_RIGHT = [("DQ%d" % i, n, "bidirectional") for i, n in enumerate(
    ["2", "4", "5", "7", "8", "10", "11", "13",
     "42", "44", "45", "47", "48", "50", "51", "53"])] + [("NC", "40", "no_connect")]
SDRAM_TOP = [("VDD", "1"), ("VDD", "14"), ("VDD", "27"),
             ("VDDQ", "3"), ("VDDQ", "9"), ("VDDQ", "43"), ("VDDQ", "49")]
SDRAM_BOT = [("VSS", "28"), ("VSS", "41"), ("VSS", "54"),
             ("VSSQ", "6"), ("VSSQ", "12"), ("VSSQ", "46"), ("VSSQ", "52")]


def build_sdram_symbol():
    name = "MT48LC16M16A2P"
    half_w = 27.94
    rows = max(len(SDRAM_LEFT), len(SDRAM_RIGHT))
    h = (rows + 1) * PITCH
    top = h / 2.0
    out = _sym_header(name, "U", name,
                      "https://www.rxelectronics.pl/datasheet/2e/MT48LC16M16A2P-75-D-TR.pdf",
                      "256Mb SDRAM, 16Mx16, 54-pin TSOP-II")
    out += '\t\t(symbol "%s_1_1"\n' % name
    out += _rect(-half_w, top, half_w, -top) + "\n"
    for i, (nm, num, et) in enumerate(SDRAM_LEFT):
        out += _pin_sexp(et, nm, num, -half_w - 5.08, top - (i + 1) * PITCH, 0) + "\n"
    for i, (nm, num, et) in enumerate(SDRAM_RIGHT):
        out += _pin_sexp(et, nm, num, half_w + 5.08, top - (i + 1) * PITCH, 180) + "\n"
    for i, (nm, num) in enumerate(SDRAM_TOP):
        x = -half_w + PITCH + i * 3 * PITCH
        out += _pin_sexp("power_in", nm, num, round(x, 3), top + 5.08, 270) + "\n"
    for i, (nm, num) in enumerate(SDRAM_BOT):
        x = -half_w + PITCH + i * 3 * PITCH
        out += _pin_sexp("power_in", nm, num, round(x, 3), -top - 5.08, 90) + "\n"
    out += "\t\t)\n\t\t(embedded_fonts no)\n\t)\n"
    CUSTOM_SYMBOLS[name] = out


def write_project_symbol_lib():
    body = "".join(CUSTOM_SYMBOLS[k] for k in sorted(CUSTOM_SYMBOLS))
    txt = ('(kicad_symbol_lib\n\t(version %s)\n\t(generator "singleboard-gen")\n'
           '\t(generator_version "%s")\n%s)\n' % (SYM_VERSION, GEN_VERSION, body))
    os.makedirs(SYMDIR, exist_ok=True)
    open(os.path.join(SYMDIR, "singleboard.kicad_sym"), "w",
         encoding="utf-8", newline="\n").write(txt)


# ---------------------------------------------------------------------------
# Footprints used by the design
# ---------------------------------------------------------------------------

FP = {
    "fpga":    "Package_BGA:Xilinx_FTG256",
    "sdram":   "Package_SO:TSOP-II-54_22.2x10.16mm_P0.8mm",
    "flash":   "Package_SO:SOIC-8_5.3x5.3mm_P1.27mm",
    "buck":    "Package_TO_SOT_SMD:SOT-23-6",
    "osc":     "Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm",
    "r":       "Resistor_SMD:R_0402_1005Metric",
    "c":       "Capacitor_SMD:C_0402_1005Metric",
    "c0805":   "Capacitor_SMD:C_0805_2012Metric",
    "c1206":   "Capacitor_SMD:C_1206_3216Metric",
    "l":       "Inductor_SMD:L_Bourns-SRN4018",
    "led":     "LED_SMD:LED_0603_1608Metric",
    "diode":   "Diode_SMD:D_SMA",
    "fuse":    "Fuse:Fuse_1812_4532Metric",
    "jack":    "Connector_BarrelJack:BarrelJack_Horizontal",
    "hdr4":    "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    "hdr6":    "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
    "sw":      "Button_Switch_SMD:SW_SPST_SKQG_WithStem",
    "tp":      "TestPoint:TestPoint_Pad_D1.5mm",
}

FPGA = "SingleBoard:XC6SLX16-FTG256"
SDRAM = "SingleBoard:MT48LC16M16A2P"


def flag(sh, rail, at):
    """Rail symbol plus PWR_FLAG, so ERC sees the net as driven."""
    x, y = snap(at)
    sh.wire(x, y, x, y + 7.62)
    sh.power(rail, (x, y))
    sh._pwr += 1
    sh.symbol("power:PWR_FLAG", "#FLG%s%03d" % (sh.page, sh._pwr),
              "PWR_FLAG", (x, y + 7.62), rot=180,
              hide_fields=("Reference", "Value"), in_bom=False)


# ---------------------------------------------------------------------------
# Sheet: power
# ---------------------------------------------------------------------------

def build_power(sh):
    sh.text("5 V input protection: polyfuse, series Schottky for reverse polarity,", (20, 18), 1.6)
    sh.text("TVS clamp. The Schottky costs ~0.4 V; both bucks accept 4.5 V minimum.", (20, 23), 1.6)

    sh.symbol("Connector:Barrel_Jack", "J1", "5V DC 2.1mm", (30, 45),
              footprint=FP["jack"])
    tag(sh, "Connector:Barrel_Jack", 1, (30, 45), "VIN_RAW", kind="local")
    tag(sh, "Connector:Barrel_Jack", 2, (30, 45), "GND", kind="power")

    two_pin(sh, "Device:Polyfuse", "F1", "1.1A hold", (76, 42.46), "VIN_F", "VIN_RAW",
            rot=90, footprint=FP["fuse"], stub=7.62)
    two_pin(sh, "Device:D_Schottky", "D1", "SS34", (117, 42.46), "+5V", "VIN_F",
            rot=180, footprint=FP["diode"], a_kind="power", stub=7.62)
    two_pin(sh, "Device:D_TVS", "D2", "SMAJ5.0A", (145, 55), "+5V", "GND",
            rot=90, footprint=FP["diode"], a_kind="power", b_kind="power")
    two_pin(sh, "Device:C", "C1", "22uF/16V", (168, 55), "+5V", "GND",
            footprint=FP["c1206"], a_kind="power", b_kind="power")
    two_pin(sh, "Device:C", "C2", "100nF", (186, 55), "+5V", "GND",
            footprint=FP["c"], a_kind="power", b_kind="power")
    flag(sh, "+5V", (205, 55))
    flag(sh, "GND", (222, 55))

    rails = [("+3V3", 110, "R1", "100k", "R2", "30.1k", "U2", "L1",
              "EN_3V3", "SW_3V3", "BST_3V3", "FB_3V3", "3.3uH",
              "0.768 V ref x (1 + 100k/30.1k) = 3.32 V"),
             ("+1V2", 210, "R5", "56.2k", "R6", "100k", "U3", "L2",
              "EN_1V2", "SW_1V2", "BST_1V2", "FB_1V2", "2.2uH",
              "0.768 V ref x (1 + 56.2k/100k) = 1.20 V")]
    for (rail, y0, rt, rtv, rb, rbv, uref, lref, en, sw, bst, fb, lval,
         note) in rails:
        sh.text("%s buck -- %s" % (rail, note), (20, y0 - 26), 1.8)
        at = (200, y0)
        sh.symbol("Regulator_Switching:TPS562200", uref, "TPS562200", at,
                  footprint=FP["buck"], ref_off=(-4, -14), val_off=(-4, 14))
        tag(sh, "Regulator_Switching:TPS562200", 3, at, "+5V", kind="power")
        tag(sh, "Regulator_Switching:TPS562200", 5, at, en, kind="local")
        tag(sh, "Regulator_Switching:TPS562200", 1, at, "GND", kind="power")
        tag(sh, "Regulator_Switching:TPS562200", 2, at, sw, kind="local")
        tag(sh, "Regulator_Switching:TPS562200", 6, at, bst, kind="local")
        tag(sh, "Regulator_Switching:TPS562200", 4, at, fb, kind="local")

        two_pin(sh, "Device:L", lref, lval, (250, y0 - 14), sw, rail,
                rot=90, footprint=FP["l"], b_kind="power")
        two_pin(sh, "Device:C", "C%s0" % uref[-1], "100nF", (250, y0 + 6), bst, sw,
                footprint=FP["c"])
        two_pin(sh, "Device:C", "C%s1" % uref[-1], "22uF/6.3V", (278, y0 - 6), rail, "GND",
                footprint=FP["c0805"], a_kind="power", b_kind="power")
        two_pin(sh, "Device:C", "C%s2" % uref[-1], "22uF/6.3V", (296, y0 - 6), rail, "GND",
                footprint=FP["c0805"], a_kind="power", b_kind="power")
        two_pin(sh, "Device:C", "C%s3" % uref[-1], "100nF", (314, y0 - 6), rail, "GND",
                footprint=FP["c"], a_kind="power", b_kind="power")
        two_pin(sh, "Device:R", rt, rtv, (340, y0 - 16), rail, fb,
                footprint=FP["r"], a_kind="power")
        two_pin(sh, "Device:R", rb, rbv, (340, y0 + 6), fb, "GND",
                footprint=FP["r"], b_kind="power")
        two_pin(sh, "Device:C", "C%s4" % uref[-1], "22pF", (362, y0 - 16), rail, fb,
                footprint=FP["c"], a_kind="power")
        flag(sh, rail, (388, y0 - 4))

    # Enable network: VCCINT (1V2) is released first, VCCO/VCCAUX (3V3) after
    # an RC delay. Spartan-6 does not require a sequence; this follows the
    # recommended VCCINT-before-VCCO order.
    two_pin(sh, "Device:R", "R3", "100k", (150, 100), "+5V", "EN_3V3",
            footprint=FP["r"], a_kind="power")
    two_pin(sh, "Device:C", "C3", "1uF", (150, 124), "EN_3V3", "GND",
            footprint=FP["c"], b_kind="power")
    two_pin(sh, "Device:R", "R7", "100k", (150, 200), "+5V", "EN_1V2",
            footprint=FP["r"], a_kind="power")
    sh.text("R3/C3 delay the 3V3 enable ~100 ms behind 1V2.", (95, 140), 1.6)

    # Power indicator and test points.
    two_pin(sh, "Device:R", "R4", "1k", (30, 240), "+3V3", "PWR_LED",
            footprint=FP["r"], a_kind="power")
    two_pin(sh, "Device:LED", "D3", "PWR green", (58, 262), "GND", "PWR_LED",
            footprint=FP["led"], a_kind="power")
    for i, (rail, x) in enumerate([("+5V", 110), ("+3V3", 135), ("+1V2", 160),
                                   ("GND", 185)]):
        sh.symbol("Connector:TestPoint", "TP%d" % (i + 1), rail, (x, 262),
                  footprint=FP["tp"], ref_off=(0, 5), val_off=(0, 9))
        tag(sh, "Connector:TestPoint", 1, (x, 262), rail, kind="power", stub=5.08)

    sh.text("Estimated load: VCCINT ~0.3 A, VCCAUX+VCCO ~0.4 A, SDRAM ~0.15 A.", (20, 282), 1.6)
    sh.text("VCCAUX runs at 3.3 V, so the bitstream must carry CONFIG VCCAUX = 3.3.", (20, 287), 1.6)


# ---------------------------------------------------------------------------
# Sheet: fpga_core (power/ground unit, configuration unit, boot flash, JTAG)
# ---------------------------------------------------------------------------

def build_fpga_core(sh, units):
    at = (95, 155)
    sh.symbol(FPGA, "U1", "XC6SLX16-FTG256", at, unit=4, footprint=FP["fpga"],
              ref_off=(-40, -44), val_off=(-40, -40))
    for p in units[4][0] + units[4][1]:
        rail = {"GND": "GND", "VCCINT": "+1V2", "VCCAUX": "+3V3"}[p["name"]]
        tag(sh, FPGA, p["ball"], at, rail, kind="power", stub=5.08)

    sh.text("VCCINT 1.2 V on 8 balls, VCCAUX 3.3 V on 8 balls, 26 ground balls.", (20, 100), 1.6)
    sh.text("One 100 nF per supply ball, to sit against its ball in layout.", (20, 105), 1.6)

    # Decoupling for VCCINT and VCCAUX.
    for i in range(8):
        decap(sh, "C%d" % (100 + i), "100nF", (200 + i * 25, 30), "+1V2",
              footprint=FP["c"])
    for i in range(8):
        decap(sh, "C%d" % (120 + i), "100nF", (200 + i * 25, 70), "+3V3",
              footprint=FP["c"])
    decap(sh, "C140", "4.7uF", (200, 50), "+1V2", footprint=FP["c0805"])
    decap(sh, "C141", "4.7uF", (225, 50), "+1V2", footprint=FP["c0805"])
    decap(sh, "C142", "47uF/6.3V", (250, 50), "+1V2", footprint=FP["c1206"])
    decap(sh, "C143", "4.7uF", (200, 90), "+3V3", footprint=FP["c0805"])
    decap(sh, "C144", "47uF/6.3V", (225, 90), "+3V3", footprint=FP["c1206"])

    # Dedicated configuration / JTAG unit.
    cat = (250, 240)
    sh.symbol(FPGA, "U1", "XC6SLX16-FTG256", cat, unit=3, footprint=FP["fpga"],
              ref_off=(-40, -18), val_off=(-40, -14))
    for p in units[3][0]:
        net = UNIT3_MAP[p["name"]]
        if net == "GND":
            tag(sh, FPGA, p["ball"], cat, "GND", kind="power")
        else:
            tag(sh, FPGA, p["ball"], cat, net, kind="global")

    # Configuration straps.
    two_pin(sh, "Device:R", "R10", "4.7k", (330, 120), "+3V3", "PROGRAM_B",
            footprint=FP["r"], a_kind="power", b_kind="global")
    sh.symbol("Switch:SW_Push", "SW2", "PROG", (340, 145), footprint=FP["sw"])
    tag(sh, "Switch:SW_Push", 1, (340, 145), "PROGRAM_B", kind="global")
    tag(sh, "Switch:SW_Push", 2, (340, 145), "GND", kind="power")
    two_pin(sh, "Device:R", "R11", "4.7k", (365, 120), "+3V3", "INIT_B",
            footprint=FP["r"], a_kind="power", b_kind="global")
    two_pin(sh, "Device:R", "R12", "4.7k", (398, 120), "+3V3", "CMPCS_B",
            footprint=FP["r"], a_kind="power", b_kind="global")

    # DONE: 330 ohm pull-up per UG380 plus an LED lit while unconfigured.
    two_pin(sh, "Device:R", "R13", "330", (330, 180), "+3V3", "DONE",
            footprint=FP["r"], a_kind="power", b_kind="global")
    two_pin(sh, "Device:R", "R14", "1k", (365, 180), "+3V3", "DONE_LED",
            footprint=FP["r"], a_kind="power")
    two_pin(sh, "Device:LED", "D4", "nDONE red", (395, 192), "DONE", "DONE_LED",
            footprint=FP["led"], a_kind="global")

    # Mode pins: M[1:0] = 01 selects master SPI on Spartan-6. Each strap has a
    # fitted resistor and a DNP alternate so the mode can be changed by rework.
    two_pin(sh, "Device:R", "R15", "4.7k", (330, 230), "+3V3", "CFG_M0",
            footprint=FP["r"], a_kind="power", b_kind="global")
    two_pin(sh, "Device:R", "R16", "4.7k", (352, 230), "CFG_M0", "GND",
            footprint=FP["r"], a_kind="global", b_kind="power", dnp=True)
    two_pin(sh, "Device:R", "R17", "4.7k", (378, 230), "+3V3", "CFG_M1",
            footprint=FP["r"], a_kind="power", b_kind="global", dnp=True)
    two_pin(sh, "Device:R", "R18", "4.7k", (400, 230), "CFG_M1", "GND",
            footprint=FP["r"], a_kind="global", b_kind="power")
    sh.text("M[1:0] = 01 selects master SPI. R16/R17 are DNP alternates;", (320, 258), 1.4)
    sh.text("confirm the encoding against UG380 Table 2-3 before build.", (320, 262), 1.4)

    # Boot flash.
    fat = (75, 245)
    sh.symbol("Memory_Flash:W25Q32JVSS", "U4", "W25Q32JVSS", fat,
              footprint=FP["flash"], ref_off=(-4, -18), val_off=(-4, -14))
    for num, net in ((1, "CFG_CSO_N"), (6, "CFG_CCLK"), (5, "CFG_MOSI"),
                     (2, "CFG_DIN")):
        tag(sh, "Memory_Flash:W25Q32JVSS", num, fat, net, kind="global")
    tag(sh, "Memory_Flash:W25Q32JVSS", 3, fat, "FLASH_WP_N", kind="local")
    tag(sh, "Memory_Flash:W25Q32JVSS", 7, fat, "FLASH_HOLD_N", kind="local")
    tag(sh, "Memory_Flash:W25Q32JVSS", 8, fat, "+3V3", kind="power")
    tag(sh, "Memory_Flash:W25Q32JVSS", 4, fat, "GND", kind="power")
    two_pin(sh, "Device:R", "R19", "10k", (135, 235), "+3V3", "FLASH_WP_N",
            footprint=FP["r"], a_kind="power")
    two_pin(sh, "Device:R", "R20", "10k", (160, 235), "+3V3", "FLASH_HOLD_N",
            footprint=FP["r"], a_kind="power")
    decap(sh, "C150", "100nF", (135, 265), "+3V3", footprint=FP["c"])
    sh.text("32 Mbit boot flash; an XC6SLX16 bitstream is about 3.73 Mbit.", (20, 215), 1.6)

    # JTAG header (Digilent 6-pin order: TMS TDI TDO TCK GND VREF).
    jat = (395, 55)
    sh.symbol("Connector_Generic:Conn_01x06", "J3", "JTAG", jat,
              footprint=FP["hdr6"], ref_off=(2, -14), val_off=(2, -10))
    for num, net in ((1, "JTAG_TMS"), (2, "JTAG_TDI"), (3, "JTAG_TDO"),
                     (4, "JTAG_TCK")):
        tag(sh, "Connector_Generic:Conn_01x06", num, jat, net, kind="global")
    tag(sh, "Connector_Generic:Conn_01x06", 5, jat, "GND", kind="power")
    tag(sh, "Connector_Generic:Conn_01x06", 6, jat, "+3V3", kind="power")
    sh.text("JTAG 1:TMS 2:TDI 3:TDO 4:TCK 5:GND 6:VREF", (330, 30), 1.6)


# ---------------------------------------------------------------------------
# Sheet: fpga_io (the two I/O bank units)
# ---------------------------------------------------------------------------

def build_fpga_io(sh, units):
    placements = [(1, (175, 235)), (2, (435, 235))]
    vcco_count = 0
    for unit, at in placements:
        sh.symbol(FPGA, "U1", "XC6SLX16-FTG256", at, unit=unit,
                  footprint=FP["fpga"], ref_off=(-40, -86), val_off=(-40, -82))
        for p in units[unit][0] + units[unit][1]:
            ball, name = p["ball"], p["name"]
            if name.startswith("VCCO_"):
                tag(sh, FPGA, ball, at, "+3V3", kind="power")
                vcco_count += 1
            elif ball in BALL_NET:
                tag(sh, FPGA, ball, at, BALL_NET[ball], kind="global")
            else:
                tag(sh, FPGA, ball, at, "", kind="nc")

    sh.text("All four bank VCCO rails are 3.3 V. Unused bank pins carry a "
            "no-connect flag.", (25, 25), 2.2)
    sh.text("To bring a spare ball out later, delete its flag here and add a "
            "LOC to the UCF.", (25, 31), 2.2)
    sh.text("The SDRAM occupies bank 3, on the balls fpga/spartan6/sp6_SDRAM.ucf "
            "fixes; bank 1 is entirely spare.", (25, 37), 2.2)

    # One 100 nF per VCCO ball plus bulk per bank.
    for i in range(vcco_count):
        decap(sh, "C%d" % (200 + i), "100nF", (22 + i * 28, 65), "+3V3",
              footprint=FP["c"])
    for i in range(4):
        decap(sh, "C%d" % (230 + i), "4.7uF", (35 + i * 28, 100), "+3V3",
              footprint=FP["c0805"])


# ---------------------------------------------------------------------------
# Sheet: sdram
# ---------------------------------------------------------------------------

def build_sdram(sh):
    at = (175, 165)
    sh.symbol(SDRAM, "U5", "MT48LC16M16A2P-75", at, footprint=FP["sdram"],
              ref_off=(-24, -42), val_off=(-24, -38))
    net_of_pin = {
        "20": "SDRAM_BA0", "21": "SDRAM_BA1", "19": "SDRAM_CS_N",
        "18": "SDRAM_RAS_N", "17": "SDRAM_CAS_N", "16": "SDRAM_WE_N",
        "37": "SDRAM_CKE", "38": "SDRAM_CLK", "15": "SDRAM_LDQM",
        "39": "SDRAM_UDQM",
    }
    for num, bit in {"23": 0, "24": 1, "25": 2, "26": 3, "29": 4, "30": 5,
                     "31": 6, "32": 7, "33": 8, "34": 9, "22": 10, "35": 11,
                     "36": 12}.items():
        net_of_pin[num] = "SDRAM_A%d" % bit
    for i, num in enumerate(["2", "4", "5", "7", "8", "10", "11", "13",
                             "42", "44", "45", "47", "48", "50", "51", "53"]):
        net_of_pin[num] = "SDRAM_DQ%d" % i

    for num, net in net_of_pin.items():
        kind = "local" if net == "SDRAM_CLK" else "global"
        tag(sh, SDRAM, num, at, net, kind=kind)
    for _nm, num in SDRAM_TOP:
        tag(sh, SDRAM, num, at, "+3V3", kind="power")
    for _nm, num in SDRAM_BOT:
        tag(sh, SDRAM, num, at, "GND", kind="power")
    tag(sh, SDRAM, "40", at, "", kind="nc")

    two_pin(sh, "Device:R", "R30", "22", (310, 135), "SDRAM_CLK_FPGA", "SDRAM_CLK",
            rot=90, footprint=FP["r"], a_kind="global", b_kind="local")
    sh.text("Ball assignment taken verbatim from fpga/spartan6/sp6_SDRAM.ucf.", (265, 102), 1.6)
    sh.text("R30 damps the forwarded clock. rtl/sdram_spartan6_clock.v drives", (265, 112), 1.6)
    sh.text("SDRAM_CLK_FPGA from an ODDR2 with the clock inverted, so the", (265, 117), 1.6)
    sh.text("device samples mid-cycle; see docs/sdram.md.", (265, 122), 1.6)

    for i in range(7):
        decap(sh, "C%d" % (300 + i), "100nF", (35 + i * 22, 45), "+3V3",
              footprint=FP["c"])
    decap(sh, "C310", "10uF/6.3V", (200, 45), "+3V3", footprint=FP["c0805"])
    decap(sh, "C311", "10uF/6.3V", (225, 45), "+3V3", footprint=FP["c0805"])
    sh.text("32 MiB: 4 banks x 8192 rows x 512 columns x 16 bits.", (30, 22), 1.8)
    sh.text("One 100 nF per VDD/VDDQ ball; VDD and VDDQ share the 3.3 V rail.", (30, 28), 1.8)
    sh.text("Pin numbers follow the JEDEC 54-pin TSOP-II arrangement -- verify "
            "against the Micron datasheet before fabrication.", (30, 285), 1.6)


# ---------------------------------------------------------------------------
# Sheet: io (oscillator, UART header, reset, LEDs, HSWAPEN)
# ---------------------------------------------------------------------------

def build_io(sh):
    oat = (75, 60)
    sh.symbol("Oscillator:ASE-xxxMHz", "X1", "50 MHz 3V3 CMOS", oat,
              footprint=FP["osc"], ref_off=(-2, -16), val_off=(-2, -12))
    tag(sh, "Oscillator:ASE-xxxMHz", 4, oat, "+3V3", kind="power")
    tag(sh, "Oscillator:ASE-xxxMHz", 2, oat, "GND", kind="power")
    tag(sh, "Oscillator:ASE-xxxMHz", 3, oat, "CLK50M", kind="global")
    tag(sh, "Oscillator:ASE-xxxMHz", 1, oat, "OSC_EN", kind="local")
    two_pin(sh, "Device:R", "R40", "10k", (40, 75), "+3V3", "OSC_EN",
            footprint=FP["r"], a_kind="power")
    decap(sh, "C400", "100nF", (120, 60), "+3V3", footprint=FP["c"])
    sh.text("50 MHz oscillator into GCLK ball E7; the UCF carries the 20 ns "
            "PERIOD.", (20, 28), 1.8)

    # UART header: silkscreen TX is the board output, RX the board input.
    uat = (80, 155)
    sh.symbol("Connector_Generic:Conn_01x04", "J2", "UART 3V3", uat,
              footprint=FP["hdr4"], ref_off=(2, -12), val_off=(2, -8))
    tag(sh, "Connector_Generic:Conn_01x04", 1, uat, "GND", kind="power")
    tag(sh, "Connector_Generic:Conn_01x04", 2, uat, "HDR_TX", kind="local")
    tag(sh, "Connector_Generic:Conn_01x04", 3, uat, "HDR_RX", kind="local")
    tag(sh, "Connector_Generic:Conn_01x04", 4, uat, "+3V3", kind="power")
    two_pin(sh, "Device:R", "R41", "33", (40, 155), "UART_TX", "HDR_TX",
            rot=90, footprint=FP["r"], a_kind="global")
    two_pin(sh, "Device:R", "R42", "100", (40, 175), "UART_RX", "HDR_RX",
            rot=90, footprint=FP["r"], a_kind="global")
    two_pin(sh, "Device:R", "R43", "10k", (25, 200), "+3V3", "UART_RX",
            footprint=FP["r"], a_kind="power", b_kind="global")
    sh.text("J2 1:GND 2:TX board output 3:RX board input 4:3V3. 115200 8N1.", (20, 128), 1.8)
    sh.text("R43 idles RX high when nothing is plugged in.", (20, 134), 1.8)

    # Reset pushbutton.
    rat = (255, 60)
    sh.symbol("Switch:SW_Push", "SW1", "RESET", rat, footprint=FP["sw"])
    tag(sh, "Switch:SW_Push", 1, rat, "RESET_BTN_N", kind="global")
    tag(sh, "Switch:SW_Push", 2, rat, "GND", kind="power")
    two_pin(sh, "Device:R", "R44", "10k", (225, 42), "+3V3", "RESET_BTN_N",
            footprint=FP["r"], a_kind="power", b_kind="global")
    two_pin(sh, "Device:C", "C401", "100nF", (300, 78), "RESET_BTN_N", "GND",
            footprint=FP["c"], a_kind="global", b_kind="power")
    sh.text("Active-low button. Synchronise and stretch it in the RTL: the CPU", (215, 25), 1.6)
    sh.text("reset must be held for at least two running clock edges.", (215, 30), 1.6)

    # Status LEDs.
    for i in range(4):
        y = 135 + i * 28
        two_pin(sh, "Device:R", "R%d" % (50 + i), "330", (235, y), "LED%d" % i,
                "LED%d_A" % i, rot=90, footprint=FP["r"], a_kind="global")
        two_pin(sh, "Device:LED", "D%d" % (10 + i), "LED%d" % i, (280, y),
                "GND", "LED%d_A" % i, footprint=FP["led"], a_kind="power")
    sh.text("Status LEDs, about 4 mA each, driven high-true from bank 0.", (215, 118), 1.8)

    # HSWAPEN: pulled low so the internal pull-ups stay active during config.
    two_pin(sh, "Device:R", "R45", "4.7k", (370, 255), "HSWAPEN", "GND",
            footprint=FP["r"], a_kind="global", b_kind="power")
    sh.text("HSWAPEN low keeps the configuration pull-ups enabled.", (320, 238), 1.6)


# ---------------------------------------------------------------------------
# Root sheet
# ---------------------------------------------------------------------------

def build_root(sh, children):
    sh.text("SingleBoard 68K", (25, 30), 5.0)
    sh.text("A J68-derived 68000 soft CPU in an XC6SLX16-FTG256 at 50 MHz, with", (25, 42), 2.2)
    sh.text("32 MiB of MT48LC16M16A2-75 SDRAM and a 3.3 V logic-level UART.", (25, 48), 2.2)
    sh.text("CONCEPT SCHEMATIC, generated by scripts/gen_schematic.py.", (25, 60), 2.2)
    sh.text("Not simulated, not fabricated, not electrically validated.", (25, 66), 2.2)

    boxes = [((30, 95), (110, 45)), ((170, 95), (110, 45)), ((310, 95), (85, 45)),
             ((30, 170), (110, 45)), ((170, 170), (110, 45))]
    for child, (at, size) in zip(children, boxes):
        sh.sheet_box(child, at, size)

    sh.text("Cross-sheet connectivity uses global labels, so the sheets carry no", (30, 240), 1.8)
    sh.text("hierarchy pins. Rails are +5V, +3V3 (VCCO, VCCAUX, SDRAM, flash)", (30, 246), 1.8)
    sh.text("and +1V2 (VCCINT).", (30, 252), 1.8)
    sh.text("SDRAM balls come from fpga/spartan6/sp6_SDRAM.ucf; the rest live in", (30, 262), 1.8)
    sh.text("scripts/gen_schematic.py. One run emits this drawing and the matching", (30, 268), 1.8)
    sh.text("fpga/spartan6/singleboard.ucf.", (30, 274), 1.8)


# ---------------------------------------------------------------------------
# UCF
# ---------------------------------------------------------------------------

UCF_PORTS = (
    [("clk50m", "CLK50M"), ("uart_rx", "UART_RX"), ("uart_tx", "UART_TX"),
     ("reset_btn_n", "RESET_BTN_N")]
    + [("led<%d>" % i, "LED%d" % i) for i in range(4)]
    + [("sdram_addr<%d>" % i, "SDRAM_A%d" % i) for i in range(13)]
    + [("sdram_ba<%d>" % i, "SDRAM_BA%d" % i) for i in range(2)]
    + [("sdram_dq<%d>" % i, "SDRAM_DQ%d" % i) for i in range(16)]
    + [("sdram_dqm<0>", "SDRAM_LDQM"), ("sdram_dqm<1>", "SDRAM_UDQM"),
       ("sdram_cke", "SDRAM_CKE"), ("sdram_cs_n", "SDRAM_CS_N"),
       ("sdram_ras_n", "SDRAM_RAS_N"), ("sdram_cas_n", "SDRAM_CAS_N"),
       ("sdram_we_n", "SDRAM_WE_N"), ("sdram_clk", "SDRAM_CLK_FPGA")]
)


def write_ucf():
    L = ["# SingleBoard 68K board constraints for XC6SLX16-FTG256.",
         "#",
         "# Generated by scripts/gen_schematic.py from the same ball assignment",
         "# as hardware/singleboard.kicad_sch. Do not edit by hand: change the",
         "# tables in that script and re-run it.",
         "#",
         "# The SDRAM LOCs are read from sp6_SDRAM.ucf, the reference Spartan-6",
         "# SDRAM board's constraints, so the schematic follows that board.",
         "# Only the signal names are translated; no ball was chosen here.",
         "#",
         "# Port names follow rtl/sdram_mt48lc16m16a2.v, rtl/uart_mmio.v and",
         "# rtl/sdram_spartan6_clock.v. Rename the left-hand side if the",
         "# board-level top module uses different port names.",
         "#",
         "# No ISE run has validated this file. Nothing here has been placed,",
         "# routed or timed.",
         "",
         "CONFIG VCCAUX = 3.3;",
         "",
         'NET "clk50m" TNM_NET = "clk50m";',
         'TIMESPEC TS_clk50m = PERIOD "clk50m" 20.0 ns HIGH 50%;',
         ""]
    width = max(len(p) for p, _ in UCF_PORTS) + 3
    last, last_vector = None, False
    for port, net in UCF_PORTS:
        group = port.split("<")[0]
        vector = "<" in port
        if last is not None and group != last and (vector or last_vector):
            L.append("")
        last, last_vector = group, vector
        L.append('NET %-*s LOC = %-4s | IOSTANDARD = LVCMOS33;'
                 % (width, '"%s"' % port, IO_MAP[net]))
    L += ["",
          "# Dedicated configuration pins are fixed by the device and need no",
          "# LOC: TCK C14, TDI C12, TDO E14, TMS A15, DONE P13, PROGRAM_B T2,",
          "# SUSPEND P14, CMPCS_B L11.",
          "# Master-SPI configuration uses CCLK R11, MOSI T10, DIN P10,",
          "# CSO_B T3 and INIT_B R3, with mode pins M0 T11 high and M1 N11 low.",
          "# HSWAPEN C4 is strapped low on the board.",
          ""]
    path = os.path.join(ROOT, "fpga", "spartan6", "singleboard.ucf")
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(L))
    return path


# ---------------------------------------------------------------------------

# The project file is shared with the board that scripts/gen_pcb.py writes,
# so the design rules live here. Default is sized for an escape out of a
# 1.0 mm pitch FTG256 -- 0.15 mm track on 0.13 mm clearance, 0.45/0.25 vias --
# which is a common four-layer capability. Power nets get a fatter class.
PRO = """{
  "board": {"design_settings": {
    "meta": {"version": 2},
    "rules": {
      "min_clearance": 0.13, "min_track_width": 0.13,
      "min_through_hole_diameter": 0.25, "min_via_diameter": 0.45,
      "min_hole_to_hole": 0.25, "min_via_annular_width": 0.1,
      "min_text_height": 0.8, "min_text_thickness": 0.12}}},
  "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
  "meta": {"filename": "singleboard.kicad_pro", "version": 3},
  "net_settings": {
    "meta": {"version": 5},
    "classes": [
      {"name": "Default", "clearance": 0.13, "track_width": 0.15,
       "via_diameter": 0.45, "via_drill": 0.25, "priority": 2147483647},
      {"name": "Power", "clearance": 0.2, "track_width": 0.6,
       "via_diameter": 0.8, "via_drill": 0.4, "priority": 1}],
    "netclass_patterns": [
      {"netclass": "Power", "pattern": "GND"},
      {"netclass": "Power", "pattern": "+5V"},
      {"netclass": "Power", "pattern": "+3V3"},
      {"netclass": "Power", "pattern": "+1V2"},
      {"netclass": "Power", "pattern": "/power/VIN_*"},
      {"netclass": "Power", "pattern": "/power/SW_*"}]},
  "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
  "sheets": [],
  "text_variables": {}
}
"""

SYM_LIB_TABLE = """(sym_lib_table
  (version 7)
  (lib (name "SingleBoard")(type "KiCad")(uri "${KIPRJMOD}/symbols/singleboard.kicad_sym")(options "")(descr "SingleBoard 68K project symbols"))
)
"""


def main():
    global STOCK
    for cand in (os.environ.get("KICAD_SYMBOL_DIR"),
                 r"C:/Program Files/KiCad/10.0/share/kicad/symbols",
                 "/usr/share/kicad/symbols"):
        if cand and os.path.isdir(cand):
            STOCK = cand
            break
    if STOCK is None:
        sys.exit("KiCad stock symbol directory not found; set KICAD_SYMBOL_DIR")

    pins = load_fpga_pins()
    validate_assignment(pins)
    units = build_fpga_symbol(pins)
    build_sdram_symbol()

    root = Sheet("singleboard", "singleboard.kicad_sch", "Overview",
                 is_root=True, page="1")
    power = Sheet("power", "power.kicad_sch", "Power", page="2")
    core = Sheet("fpga_core", "fpga_core.kicad_sch",
                 "FPGA power and configuration", page="3")
    iob = Sheet("fpga_io", "fpga_io.kicad_sch", "FPGA I/O banks",
                paper="A2", page="4")
    sdr = Sheet("sdram", "sdram.kicad_sch", "SDRAM", page="5")
    per = Sheet("io", "io.kicad_sch", "Clock, UART, reset and LEDs", page="6")
    children = [power, core, iob, sdr, per]

    build_power(power)
    build_fpga_core(core, units)
    build_fpga_io(iob, units)
    build_sdram(sdr)
    build_io(per)
    build_root(root, children)

    os.makedirs(HW, exist_ok=True)
    write_project_symbol_lib()
    allsheets = [root] + children
    for sh in allsheets:
        open(os.path.join(HW, sh.filename), "w", encoding="utf-8",
             newline="\n").write(sh.render(allsheets))
    open(os.path.join(HW, "singleboard.kicad_pro"), "w", encoding="utf-8",
         newline="\n").write(PRO)
    open(os.path.join(HW, "sym-lib-table"), "w", encoding="utf-8",
         newline="\n").write(SYM_LIB_TABLE)
    ucf = write_ucf()

    parts = sum(len(s.parts) for s in allsheets)
    print("wrote %d sheets, %d placed parts" % (len(allsheets), parts))
    print("wrote %s" % os.path.relpath(ucf, ROOT))


if __name__ == "__main__":
    main()
