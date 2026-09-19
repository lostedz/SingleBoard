#!/usr/bin/env python3
"""Generate the SingleBoard 68K KiCad board from the schematic netlist.

Like the schematic, the board is emitted from tables rather than drawn by
hand. The netlist is exported from hardware/singleboard.kicad_sch at run time
by kicad-cli, so footprints, references and nets cannot drift away from the
drawing; PLACE below is the only thing this script adds, and the decoupling
placement is derived from the FTG256 ball map rather than typed out.

    python scripts/gen_pcb.py

The script needs KiCad 10 installed: it re-executes itself under KiCad's
bundled Python so that pcbnew, the stock footprint libraries and kicad-cli all
come from the same installation.

What it produces is a placed, planed and unrouted board:

  * four copper layers -- F.Cu signal, In1.Cu ground, In2.Cu power, B.Cu
    signal and bypass;
  * every schematic footprint placed and linked back to its symbol, so KiCad's
    "update PCB from schematic" sees a synchronised board;
  * one 100 nF per FPGA supply ball on the back, each assigned to a specific
    ball and placed on the nearest free slot in its shadow;
  * ground, +3V3 and +1V2 zones;
  * board outline and mounting holes.

There are NO tracks. Every signal is left as ratsnest. See hardware/README.md
for what that means before ordering anything.

Before it writes anything it checks that every schematic part has a placement,
that every pad named in the netlist exists on its footprint, that no pad is
left without a net, and that nothing hangs off the board outline. Afterwards it
runs kicad-cli's DRC over what it wrote and exits non-zero on any error.

Outputs hardware/singleboard.kicad_pcb.
"""

import math
import os
import re
import subprocess
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HW = os.path.join(ROOT, "hardware")
SCH = os.path.join(HW, "singleboard.kicad_sch")
PCB = os.path.join(HW, "singleboard.kicad_pcb")

KICAD_DIRS = [
    r"C:/Program Files/KiCad/10.0",
    r"C:/Program Files/KiCad/9.0",
    "/usr/lib/kicad",
    "/usr",
]


def find_kicad():
    """Return (python, kicad-cli, footprint dir) for an installed KiCad."""
    for base in [os.environ.get("KICAD_HOME")] + KICAD_DIRS:
        if not base or not os.path.isdir(base):
            continue
        for py in ("bin/python.exe", "bin/python3", "bin/python"):
            exe = os.path.join(base, py)
            cli = os.path.join(base, "bin", "kicad-cli.exe")
            if not os.path.isfile(cli):
                cli = os.path.join(base, "bin", "kicad-cli")
            fps = os.path.join(base, "share", "kicad", "footprints")
            if os.path.isfile(exe) and os.path.isfile(cli) and os.path.isdir(fps):
                return exe, cli, fps
    return None, None, None


try:
    import pcbnew
except ImportError:  # not running under KiCad's interpreter yet
    _py, _cli, _fps = find_kicad()
    if _py is None:
        sys.exit("KiCad 10 not found; set KICAD_HOME to its install directory")
    sys.exit(subprocess.call([_py, os.path.abspath(__file__)] + sys.argv[1:]))

_, KICAD_CLI, FPLIB = find_kicad()
if FPLIB is None:
    sys.exit("KiCad footprint libraries not found; set KICAD_HOME")

# Same namespace as gen_schematic.py, so board UUIDs are stable across runs.
NS = uuid.UUID("6a1f9f1c-6e44-4f1e-9f6e-5a6800006800")


def mm(v):
    return pcbnew.FromMM(float(v))


def xy(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


# ---------------------------------------------------------------------------
# Netlist
# ---------------------------------------------------------------------------

def parse_sexpr(text):
    tokens = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text)
    stack, cur = [], []
    for t in tokens:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        elif t.startswith('"'):
            cur.append(t[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        else:
            cur.append(t)
    return cur[0]


def kids(node, key):
    return [n for n in node if isinstance(n, list) and n and n[0] == key]


def kid(node, key):
    got = kids(node, key)
    return got[0] if got else None


def val(node, key, default=""):
    got = kid(node, key)
    return got[1] if got and len(got) > 1 else default


def export_netlist():
    out = os.path.join(HW, ".netlist.tmp.net")
    rc = subprocess.call([KICAD_CLI, "sch", "export", "netlist",
                          "--format", "kicadsexpr", "-o", out, SCH],
                         stdout=subprocess.DEVNULL)
    if rc != 0 or not os.path.isfile(out):
        sys.exit("kicad-cli failed to export the netlist from %s" % SCH)
    text = open(out, encoding="utf-8").read()
    os.remove(out)
    return parse_sexpr(text)


def read_design(export):
    """-> (components, nets). Components keep their schematic path."""
    comps = []
    for c in kids(kid(export, "components"), "comp"):
        sheet = "/"
        for p in kids(c, "property"):
            if val(p, "name") == "Sheetname":
                sheet = val(p, "value")
        path = val(kid(c, "sheetpath"), "tstamps", "/") + val(c, "tstamps")
        comps.append({"ref": val(c, "ref"), "value": val(c, "value"),
                      "fp": val(c, "footprint"), "sheet": sheet, "path": path})
    nets = []
    for n in kids(kid(export, "nets"), "net"):
        nodes = [(val(x, "ref"), val(x, "pin")) for x in kids(n, "node")]
        nets.append((val(n, "name"), nodes))
    return comps, nets


# ---------------------------------------------------------------------------
# FTG256 ball map -- the same legacy library the schematic symbol is built from
# ---------------------------------------------------------------------------

def load_supply_balls():
    """-> {'VCCINT': [ball, ...], 'VCCAUX': [...], 'VCCO': [...]}"""
    groups = {"VCCINT": [], "VCCAUX": [], "VCCO": []}
    path = os.path.join(HW, "sources", "XC6SLX16-FTG256.lib")
    for line in open(path, encoding="utf-8"):
        if not line.startswith("X "):
            continue
        f = line.split()
        name, ball = f[1], f[2]
        key = "VCCO" if name.startswith("VCCO_") else name
        if key in groups:
            groups[key].append(ball)
    for k in groups:
        groups[k].sort(key=lambda b: (b[0], int(b[1:])))
    return groups


# ---------------------------------------------------------------------------
# Board geometry
# ---------------------------------------------------------------------------

BOARD_W, BOARD_H = 120.0, 90.0
EDGE = 0.15          # Edge.Cuts line width
HOLE_INSET = 4.5     # M3 hole centre from each corner

FPGA_AT = (44.0, 46.0)
SDRAM_AT = (16.0, 46.0)

# ref -> (x, y, rotation, side). Side "B" places the part on the back.
#
# The FPGA sits in the middle. The SDRAM is to its left because all 39 SDRAM
# balls are in bank 3, which is the -X side of the package; the clock, reset,
# LEDs and UART are above it because bank 0 is the -Y side; the boot flash and
# JTAG are below it because bank 2 is the +Y side. Bank 1 is spare, so the
# power section takes the whole right-hand column.
PLACE = {
    # --- big parts ------------------------------------------------------
    "U1": FPGA_AT + (0, "F"),            # XC6SLX16-FTG256
    "U5": SDRAM_AT + (0, "F"),           # MT48LC16M16A2P-75, bank 3 side
    "U4": (44.0, 68.0, 0, "F"),          # W25Q32JVSS boot flash, bank 2 side

    # --- bulk decoupling around the FPGA --------------------------------
    "C140": (35.0, 33.0, 0, "F"), "C141": (39.5, 33.0, 0, "F"),
    "C142": (45.0, 33.0, 0, "F"),
    "C143": (35.0, 59.0, 0, "F"), "C144": (40.5, 59.0, 0, "F"),
    "C230": (50.0, 33.0, 0, "F"),        # VCCO bank 0 bulk (top)
    "C231": (57.5, 44.0, 90, "F"),       # VCCO bank 1 bulk (right)
    "C232": (46.0, 59.0, 0, "F"),        # VCCO bank 2 bulk (bottom)
    "C233": (30.0, 50.0, 90, "F"),       # VCCO bank 3 bulk (left)

    # --- SDRAM ----------------------------------------------------------
    "C310": (27.0, 37.0, 90, "F"), "C311": (27.0, 55.0, 90, "F"),
    "R30": (27.0, 46.0, 90, "F"),        # 22R series damping on SDRAM_CLK

    # --- clock, reset, LEDs, UART (bank 0, top strip) -------------------
    "X1": (44.0, 25.0, 0, "F"), "C400": (44.0, 21.0, 0, "F"),
    "R41": (39.0, 25.0, 90, "F"),        # 33R series on the oscillator output
    "SW1": (57.0, 25.0, 0, "F"),         # RESET
    "R40": (50.0, 21.0, 0, "F"), "C401": (50.0, 23.5, 0, "F"),
    "J2": (64.0, 25.0, 90, "F"),         # UART 3V3 header
    "R42": (64.0, 18.0, 0, "F"), "R45": (64.0, 20.5, 0, "F"),
    "R43": (57.0, 18.0, 0, "F"), "R44": (60.5, 18.0, 0, "F"),
    "D10": (22.0, 14.0, 90, "F"), "R50": (22.0, 18.0, 90, "F"),
    "D11": (26.0, 14.0, 90, "F"), "R51": (26.0, 18.0, 90, "F"),
    "D12": (30.0, 14.0, 90, "F"), "R52": (30.0, 18.0, 90, "F"),
    "D13": (34.0, 14.0, 90, "F"), "R53": (34.0, 18.0, 90, "F"),

    # --- configuration (bank 2, bottom strip) ---------------------------
    "C150": (44.0, 63.0, 0, "F"),        # flash decoupling
    "R15": (36.0, 64.0, 0, "F"), "R16": (36.0, 67.5, 0, "F"),
    "R17": (36.0, 71.0, 0, "F"), "R18": (36.0, 74.5, 0, "F"),
    "R19": (52.0, 64.0, 0, "F"), "R20": (52.0, 67.5, 0, "F"),
    "R10": (58.0, 65.0, 0, "F"), "R11": (58.0, 68.5, 0, "F"),
    "R12": (58.0, 72.0, 0, "F"),
    "SW2": (66.0, 78.0, 0, "F"),         # PROG
    "R13": (52.0, 71.0, 0, "F"), "R14": (52.0, 74.5, 0, "F"),
    "D4": (52.0, 78.0, 0, "F"),          # nDONE
    "J3": (25.0, 78.0, 90, "F"),         # JTAG, Digilent pinout

    # --- power (bank 1 is spare, so the right-hand column is free) ------
    "J1": (105.9, 20.0, 180, "F"),       # 5 V barrel jack, right edge
    "F1": (88.0, 13.0, 0, "F"),          # 1.1 A polyfuse
    "D1": (88.0, 20.0, 0, "F"),          # SS34 reverse-polarity series diode
    "D2": (88.0, 27.0, 0, "F"),          # SMAJ5.0A clamp
    "C1": (80.0, 20.0, 90, "F"), "C2": (80.0, 26.0, 90, "F"),
    "TP1": (96.0, 34.0, 0, "F"), "TP4": (101.0, 34.0, 0, "F"),

    # 3.3 V buck
    "U2": (82.0, 42.0, 0, "F"), "L1": (91.0, 42.0, 0, "F"),
    "C21": (98.0, 40.0, 0, "F"), "C22": (98.0, 44.0, 0, "F"),
    "C20": (82.0, 37.0, 0, "F"), "C24": (86.5, 46.0, 0, "F"),
    "C23": (91.0, 48.5, 0, "F"),
    "R1": (105.0, 40.0, 90, "F"), "R2": (105.0, 44.0, 90, "F"),
    "R3": (77.0, 42.0, 90, "F"), "C3": (77.0, 46.0, 90, "F"),
    "TP2": (101.0, 50.0, 0, "F"),

    # 1.2 V buck
    "U3": (82.0, 62.0, 0, "F"), "L2": (91.0, 62.0, 0, "F"),
    "C31": (98.0, 60.0, 0, "F"), "C32": (98.0, 64.0, 0, "F"),
    "C30": (82.0, 57.0, 0, "F"), "C34": (86.5, 66.0, 0, "F"),
    "C33": (91.0, 68.5, 0, "F"),
    "R5": (105.0, 60.0, 90, "F"), "R6": (105.0, 64.0, 90, "F"),
    "R7": (77.0, 62.0, 90, "F"),
    "TP3": (101.0, 70.0, 0, "F"),

    # power-good LED
    "R4": (77.0, 74.0, 0, "F"), "D3": (83.0, 74.0, 0, "F"),
}


def place_decoupling(fpga, groups):
    """Assign one 100 nF to each FPGA supply ball and one to each SDRAM rail.

    The FTG256 balls are on a 1.0 mm pitch, so a 0402 will not fit centred on
    every one of them. Instead a grid of slots is laid out on the back inside
    the ball field and each cap takes the free slot nearest the ball it is
    there for -- closest pair first, so the tightly packed VCCINT cluster in
    the middle of the package keeps the middle slots.

    Returns ({ref: placement}, {ref: ball}).
    """
    ball_at = {p.GetNumber(): (pcbnew.ToMM(p.GetX()), pcbnew.ToMM(p.GetY()))
               for p in fpga.Pads()}

    # 0402 courtyard is 1.91 x 1.01 mm; 2.2 x 1.8 mm slots clear it both ways.
    cx, cy = FPGA_AT
    free = [(round(cx + i * 2.2, 3), round(cy + j * 1.8, 3))
            for i in range(-3, 4) for j in range(-4, 4)]

    caps = ([("C%d" % (100 + i), b) for i, b in enumerate(groups["VCCINT"])]
            + [("C%d" % (120 + i), b) for i, b in enumerate(groups["VCCAUX"])]
            + [("C%d" % (200 + i), b) for i, b in enumerate(groups["VCCO"])])
    if len(caps) != 36:
        sys.exit("expected 36 FPGA supply balls, got %d" % len(caps))
    if len(free) < len(caps):
        sys.exit("only %d bypass slots for %d caps" % (len(free), len(caps)))

    paired = dict(caps)
    pending = {ref: ball_at[ball] for ref, ball in caps}
    out = {}
    while pending:
        _, ref, slot = min(((math.dist(t, s), ref, s)
                            for ref, t in pending.items() for s in free),
                           key=lambda e: (round(e[0], 6), e[1]))
        out[ref] = (slot[0], slot[1], 0, "B")
        free.remove(slot)
        del pending[ref]

    # SDRAM: seven 100 nF in a column on the back, under the body.
    sx, sy = SDRAM_AT
    for i in range(7):
        out["C%d" % (300 + i)] = (sx, sy - 9.0 + i * 3.0, 0, "B")
    return out, paired


# ---------------------------------------------------------------------------
# Board assembly helpers
# ---------------------------------------------------------------------------

def load_footprint(fpid):
    lib, name = fpid.split(":", 1)
    fp = pcbnew.FootprintLoad(os.path.join(FPLIB, lib + ".pretty"), name)
    if fp is None:
        sys.exit("footprint %s not found under %s" % (fpid, FPLIB))
    return fp


SILK_SIZE = 0.8      # reference designator height, mm (board minimum)
SILK_THICK = 0.15


def add_footprint(board, fpid, ref, value, at, path=None, board_only=False,
                  silk_ref=True):
    """Place one footprint. silk_ref=False keeps its name on the fab layer
    only, which is what has to happen where parts are too dense to letter."""
    x, y, rot, side = at
    fp = load_footprint(fpid)
    board.Add(fp)
    fp.SetPosition(xy(x, y))
    if side == "B":
        fp.Flip(fp.GetPosition(), False)
    fp.SetOrientationDegrees(rot)
    fp.SetReference(ref)
    fp.SetValue(value)
    ref_field = fp.Reference()
    if silk_ref:
        ref_field.SetTextSize(pcbnew.VECTOR2I(mm(SILK_SIZE), mm(SILK_SIZE)))
        ref_field.SetTextThickness(mm(SILK_THICK))
    else:
        ref_field.SetVisible(False)
    if path:
        fp.SetPath(pcbnew.KIID_PATH(path))
    if board_only:
        fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_BOARD_ONLY
                         | pcbnew.FP_EXCLUDE_FROM_BOM
                         | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
    return fp


def edge_rect(board, x0, y0, x1, y1):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for i in range(4):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(xy(*pts[i]))
        seg.SetEnd(xy(*pts[(i + 1) % 4]))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(mm(EDGE))
        board.Add(seg)


def add_zone(board, layers, net, pts, priority=0, name=""):
    zone = pcbnew.ZONE(board)
    board.Add(zone)
    ls = pcbnew.LSET()
    for lay in layers:
        ls.addLayer(lay)
    zone.SetLayerSet(ls)
    zone.SetNet(net)
    zone.SetAssignedPriority(priority)
    if name:
        zone.SetZoneName(name)
    chain = pcbnew.SHAPE_LINE_CHAIN()
    for x, y in pts:
        chain.Append(mm(x), mm(y))
    chain.SetClosed(True)
    # AddPolygon copies into the zone's own outline. Handing SetOutline() a
    # SHAPE_POLY_SET built here instead transfers ownership to C++ while
    # Python still frees it, and the filler then walks freed memory.
    zone.AddPolygon(chain)
    zone.SetLocalClearance(mm(0.25))
    zone.SetMinThickness(mm(0.2))
    zone.SetThermalReliefGap(mm(0.3))
    zone.SetThermalReliefSpokeWidth(mm(0.4))
    return zone


def silk(board, text, at, size=1.5, layer=None, bold=False, mirror=False):
    t = pcbnew.PCB_TEXT(board)
    board.Add(t)
    t.SetText(text)
    t.SetPosition(xy(*at))
    t.SetLayer(pcbnew.F_SilkS if layer is None else layer)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
    t.SetTextThickness(mm(size * (0.2 if bold else 0.15)))
    t.SetBold(bold)
    t.SetMirrored(mirror)
    return t


# J1's barrel has to reach the board edge to be pluggable, so it is the one
# part allowed to touch the outline.
EDGE_PARTS = {"J1"}

# J1 pad 3 is the jack's switched contact, which the schematic leaves open.
MECHANICAL_PADS = {"J1.3"}


def check_placement(board):
    """Fail loudly rather than emit a board with a part hanging off it."""
    bad = []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        if ref in EDGE_PARTS:
            continue
        bb = fp.GetBoundingBox(False, False)
        x0, y0 = pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop())
        x1, y1 = pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())
        if x0 < 0 or y0 < 0 or x1 > BOARD_W or y1 > BOARD_H:
            bad.append("%s (%.2f %.2f .. %.2f %.2f)" % (ref, x0, y0, x1, y1))
    if bad:
        sys.exit("outside the %g x %g mm outline: %s"
                 % (BOARD_W, BOARD_H, ", ".join(sorted(bad))))


def restore_project():
    """Put the project file's design rules back.

    hardware/singleboard.kicad_pro is shared with the schematic and is where
    gen_schematic.py keeps the net classes and design rules. Saving a board
    and running kicad-cli both rewrite that file from stock defaults, which
    silently drops them, so it is rewritten after anything that touches it.
    """
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import gen_schematic
    path = os.path.join(HW, "singleboard.kicad_pro")
    open(path, "w", encoding="utf-8", newline="\n").write(gen_schematic.PRO)


def run_drc(path):
    """-> (errors, warnings, unconnected). DRC is the board's ERC."""
    report = path + ".drc.tmp"
    out = subprocess.run([KICAD_CLI, "pcb", "drc", "--format", "json",
                          "--severity-error", "--severity-warning",
                          "-o", report, path],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True)
    if not os.path.isfile(report):
        sys.exit("kicad-cli could not run DRC:\n%s" % out.stdout)
    import json
    data = json.load(open(report, encoding="utf-8"))
    os.remove(report)
    errors = sum(1 for v in data["violations"] if v["severity"] == "error")
    warnings = sum(1 for v in data["violations"] if v["severity"] == "warning")
    return errors, warnings, len(data.get("unconnected_items", []))


def freeze_uuids(board):
    """Give every board item a UUID derived from what it is, not from chance.

    pcbnew hands out random KIIDs and the sexpr writer sorts footprints by
    them, so an unchanged design would otherwise come out reordered on every
    run. Seeding them from the reference designator makes both the order and
    the contents reproducible, the way gen_schematic.py does for the sheets.
    """
    def seed(item, key):
        item.SetUuid(pcbnew.KIID(str(uuid.uuid5(NS, key))))

    for fp in board.GetFootprints():
        ref = fp.GetReference()
        seed(fp, "fp:%s" % ref)
        for i, f in enumerate(fp.GetFields()):
            seed(f, "fld:%s:%d" % (ref, i))
        for i, pad in enumerate(fp.Pads()):
            seed(pad, "pad:%s:%d:%s" % (ref, i, pad.GetNumber()))
        for i, gi in enumerate(fp.GraphicalItems()):
            seed(gi, "gfx:%s:%d" % (ref, i))
        for i, z in enumerate(fp.Zones()):
            seed(z, "fpzone:%s:%d" % (ref, i))
    for i, d in enumerate(board.GetDrawings()):
        seed(d, "draw:%d" % i)
    for i, z in enumerate(board.Zones()):
        seed(z, "zone:%s:%d" % (z.GetZoneName(), i))


def stable_uuids(path):
    """Renumber anything freeze_uuids() could not reach, in file order.

    The (path ...) references back into the schematic are left alone, since
    those name symbols that already exist.
    """
    text = open(path, encoding="utf-8").read()
    seen = {}

    def repl(m):
        old = m.group(1)
        if old not in seen:
            seen[old] = str(uuid.uuid5(NS, "pcb-item-%d" % len(seen)))
        return '(uuid "%s")' % seen[old]

    text = re.sub(r'\(uuid "([0-9a-fA-F-]{36})"\)', repl, text)
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    return len(seen)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

LAYER_NAMES = {
    pcbnew.F_Cu: "F.Cu",
    pcbnew.In1_Cu: "GND",
    pcbnew.In2_Cu: "PWR",
    pcbnew.B_Cu: "B.Cu",
}


def main():
    comps, nets = read_design(export_netlist())
    groups = load_supply_balls()

    board = pcbnew.CreateEmptyBoard()
    board.SetCopperLayerCount(4)
    for lid, name in LAYER_NAMES.items():
        board.SetLayerName(lid, name)
    for lid in (pcbnew.In1_Cu, pcbnew.In2_Cu):
        board.SetLayerType(lid, pcbnew.LT_POWER)

    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(4)

    edge_rect(board, 0, 0, BOARD_W, BOARD_H)

    # The FPGA has to exist before the bypass caps can be aimed at its balls.
    fpga = None
    for c in comps:
        if c["ref"] == "U1":
            fpga = add_footprint(board, c["fp"], "U1", c["value"],
                                 PLACE["U1"], c["path"])
    if fpga is None:
        sys.exit("U1 (the FPGA) is not in the netlist")
    bypass, paired = place_decoupling(fpga, groups)

    placed = {"U1": fpga}
    missing = []
    for c in comps:
        ref = c["ref"]
        if ref == "U1":
            continue
        at = PLACE.get(ref) or bypass.get(ref)
        if at is None:
            missing.append(ref)
            continue
        placed[ref] = add_footprint(board, c["fp"], ref, c["value"], at,
                                    c["path"], silk_ref=ref not in bypass)
    if missing:
        sys.exit("no placement for: %s" % " ".join(sorted(missing)))

    # Mounting holes are board-only; they have no schematic symbol.
    for i, (hx, hy) in enumerate(
            [(HOLE_INSET, HOLE_INSET),
             (BOARD_W - HOLE_INSET, HOLE_INSET),
             (HOLE_INSET, BOARD_H - HOLE_INSET),
             (BOARD_W - HOLE_INSET, BOARD_H - HOLE_INSET)]):
        add_footprint(board, "MountingHole:MountingHole_3.2mm_M3",
                      "H%d" % (i + 1), "M3", (hx, hy, 0, "F"),
                      board_only=True, silk_ref=False)

    check_placement(board)

    # --- nets -----------------------------------------------------------
    unconnected = []
    for name, nodes in nets:
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
        for ref, pin in nodes:
            fp = placed.get(ref)
            if fp is None:
                continue
            # A pad number can appear more than once -- the SPST switches have
            # two pads per contact -- so every one of them has to be netted,
            # not just whichever FindPadByNumber() would hand back.
            hit = [p for p in fp.Pads() if p.GetNumber() == pin]
            if not hit:
                unconnected.append("%s pin %s" % (ref, pin))
                continue
            for pad in hit:
                pad.SetNet(net)
    if unconnected:
        sys.exit("pads named in the netlist but absent from the footprint: %s"
                 % ", ".join(unconnected))

    # Any copper pad left without a net is either a mechanical one or a wiring
    # mistake, so say which it is rather than letting it pass silently.
    stray = ["%s.%s" % (fp.GetReference(), p.GetNumber())
             for fp in board.GetFootprints() for p in fp.Pads()
             if not str(p.GetNetname()) and p.GetNumber()]
    if set(stray) - MECHANICAL_PADS:
        sys.exit("pads with no net: %s" % " ".join(sorted(stray)))

    by_name = {n.GetNetname(): n for n in board.GetNetsByName().values()}
    gnd, v33, v12 = by_name["GND"], by_name["+3V3"], by_name["+1V2"]

    # --- zones ----------------------------------------------------------
    m = 0.5                       # pull the copper back from the board edge
    full = [(m, m), (BOARD_W - m, m), (BOARD_W - m, BOARD_H - m), (m, BOARD_H - m)]

    add_zone(board, [pcbnew.In1_Cu], gnd, full, priority=0, name="GND plane")
    add_zone(board, [pcbnew.F_Cu, pcbnew.B_Cu], gnd, full, priority=0,
             name="GND fill")
    # In2.Cu is 3.3 V everywhere except where the 1.2 V island takes priority
    # over it. The island is one outline covering the FPGA core, the 1.2 V
    # buck and a neck between them, so the fill is a single connected region.
    add_zone(board, [pcbnew.In2_Cu], v33, full, priority=0, name="+3V3 plane")
    add_zone(board, [pcbnew.In2_Cu], v12,
             [(36.0, 38.0), (54.0, 38.0), (54.0, 50.0), (106.0, 50.0),
              (106.0, 72.0), (72.0, 72.0), (72.0, 58.0), (54.0, 58.0),
              (54.0, 54.0), (36.0, 54.0)],
             priority=1, name="+1V2 island")

    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())

    # --- silkscreen -----------------------------------------------------
    silk(board, "SingleBoard 68K", (BOARD_W / 2, 5.0), size=2.5, bold=True)
    silk(board, "XC6SLX16-FTG256 / MT48LC16M16A2 / rev A", (BOARD_W / 2, 8.5),
         size=1.2)
    silk(board, "CONCEPT -- UNROUTED, NOT VALIDATED",
         (BOARD_W / 2, BOARD_H - 3.0), size=1.6, bold=True)
    silk(board, "bypass caps this side", (BOARD_W / 2, BOARD_H - 3.0),
         size=1.4, layer=pcbnew.B_SilkS, mirror=True)

    freeze_uuids(board)
    pcbnew.SaveBoard(PCB, board)
    n = stable_uuids(PCB)

    print("wrote %s" % os.path.relpath(PCB, ROOT))
    print("  %d footprints, %d nets, %d zones, %d tracks"
          % (len(board.GetFootprints()), len(nets), len(board.Zones()),
             len(board.GetTracks())))
    print("  %d bypass caps paired with supply balls (%s ...)"
          % (len(paired), ", ".join("%s->%s" % (r, paired[r])
                                    for r in ("C100", "C120", "C200"))))
    print("  %d board uuids made deterministic" % n)

    restore_project()
    errors, warnings, open_nets = run_drc(PCB)
    restore_project()
    print("  DRC: %d errors, %d warnings, %d unconnected items (the board is "
          "unrouted)" % (errors, warnings, open_nets))
    if errors:
        sys.exit("DRC reported %d errors" % errors)


if __name__ == "__main__":
    main()
