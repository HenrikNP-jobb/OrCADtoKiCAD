#!/usr/bin/env python3
"""
orcad2kicad_sch.py – Convert a single-sheet OrCAD XML netlist to a self-contained
KiCad-9 schematic (.kicad_sch), preserving OrCAD rotation and flips.
"""

import argparse, re, sys, uuid, math
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Set, Tuple

# ─────────────── Constants ────────────────
MIL10 = 0.254
GRID = 2.54
REF_DY = 2 * GRID
VAL_DY = -2 * GRID
FONT = "(effects (font (size 1.27 1.27)) (justify left))"

EXT_RE = re.compile(r'\(extends\s+"([^"]+)"')
EXTENDS_CLAUSE = re.compile(r'\s*\(extends\s+"[^"]+"\)')

PIN_TYPE = {
    "0": "passive", "1": "input", "2": "output", "3": "bidirectional",
    "4": "passive", "5": "power_in", "6": "power_out",
}

LIB_ID: Dict = {
    "U": "Amplifier_Operational:LM6171xxN",
    "R": "Device:R_US",
    "C": "Device:C",
    ("J", "2"): "Connector_Generic:Conn_01x02",
    ("J", "4"): "Connector_Generic:Conn_01x04",
}
POWER_ALIAS = {"VCC_ARROW": "+5V", "GND_SIGNAL": "GND"}

# ─────────────── Helpers ────────────────
mm = lambda v: round(float(v) * MIL10, 3) if v not in (None, "") else 0.0
num = lambda x: ("%f" % x).rstrip("0").rstrip(".")
xy = lambda p: f"(xy {num(p[0])} {num(p[1])})"
q = lambda s: '"' + str(s).replace('"', r'\"') + '"'
uid = lambda: str(uuid.uuid4())
snap = lambda v: round(v, 3)

_symbol_blocks: Dict[str, str] = {}
_symbol_pin1: Dict[str, Tuple[float, float]] = {}

# ─────────────── KiCad Symbol Utilities ────────────────
def read_symbol(libroot: Path, lib_id: str) -> str:
    if lib_id in _symbol_blocks:
        return _symbol_blocks[lib_id]
    lib, sym = lib_id.split(":", 1)
    path = libroot / f"{lib}.kicad_sym"
    txt = path.read_text(encoding="utf-8")
    p0 = txt.find(f'(symbol "{sym}"')
    if p0 == -1:
        raise RuntimeError(f'"{sym}" not found in {path}')
    depth = 0
    for i, ch in enumerate(txt[p0:], p0):
        depth += (ch == "(") - (ch == ")")
        if depth == 0:
            block = txt[p0:i+1]
            _symbol_blocks[lib_id] = block
            m = re.search(
                r'\(pin\s+[^\)]*?\(at\s+([0-9.\-]+)\s+([0-9.\-]+)\s+[0-9]+\)[^\)]*?\(number\s+"1"',
                block, re.MULTILINE | re.DOTALL
            )
            if m:
                _symbol_pin1[lib_id] = (float(m.group(1)), float(m.group(2)))
            else:
                _symbol_pin1[lib_id] = (0.0, 0.0)
            return block
    raise RuntimeError("Unbalanced parentheses in symbol")

strip_extends = lambda blk: EXTENDS_CLAUSE.sub("", blk)

def rename(block: str, new: str) -> str:
    p1 = block.find('"') + 1
    p2 = block.find('"', p1)
    return block[:p1] + new + block[p2:]

def collect(libroot: Path, lib_id: str,
            placed: Set[str], done: Set[str], out: List[str]) -> None:
    if lib_id in done:
        return
    raw = read_symbol(libroot, lib_id)
    lib, short = lib_id.split(":", 1)
    for parent in EXT_RE.findall(raw):
        full = parent if ":" in parent else f"{lib}:{parent}"
        collect(libroot, full, placed, done, out)
        base = full.split(":", 1)[1]
        if base not in done:
            txt = read_symbol(libroot, full)
            out.append(rename(strip_extends(txt), base))
            done.add(base)
    name = lib_id if lib_id in placed else short
    if name not in done:
        out.append(rename(strip_extends(raw), name))
        done.add(name)

# ─────────────── Symbol Lookup ────────────────
def lib_id_for(ref: str, val: str, pkg: str) -> str:
    if ref and ref[0] == "J":
        pins = "4" if ("04" in pkg.lower() or val.endswith("004B")) else "2"
        return LIB_ID.get(("J", pins), pkg or ref)
    return LIB_ID.get(ref[0], pkg or ref)

# ─────────────── OrCAD Reader ────────────────
class OrCadReader:
    def __init__(self, xml_path: Path):
        self.root = ET.parse(str(xml_path)).getroot()

    def fallback_symbols(self, placed_ids: Set[str]) -> List[dict]:
        out = []
        for pkg in self.root.findall('.//Cache/Package'):
            name = pkg.find('Defn').get('name')
            if name not in placed_ids:
                continue
            if any(name == lid or name == lid.split(":", 1)[1]
                   for lid in placed_ids if ":" in lid):
                continue
            ptable, pins, gfx = {}, [], []
            for p in pkg.findall('.//PhysicalPart/PinNumber/Defn'):
                ptable[p.get('position')] = p.get('number')
            for ps in pkg.findall('.//NormalView/SymbolPinScalar'):
                d = ps.find('Defn')
                pins.append(dict(
                    num=ptable.get(d.get('position'), d.get('position')),
                    name=d.get('name'),
                    typ=PIN_TYPE.get(d.get('type', ''), 'passive'),
                    at=(mm(d.get('hotptX')), mm(d.get('hotptY')))
                ))
            for pl in pkg.findall('.//NormalView/Polyline'):
                pts = [(mm(pt.get('x')), mm(pt.get('y')))
                       for pt in pl.findall('PolylinePoint/Defn')]
                if pts:
                    gfx.append(pts)
            out.append(dict(name=name, pins=pins, gfx=gfx))
        return out

    def components(self) -> List[dict]:
        comps = []
        for pi in self.root.findall('.//Schematic//PartInst'):
            ref = pi.find('Reference/Defn').get('name')
            val = pi.find('PartValue/Defn').get('name', '')
            pkg = pi.get('pkgName') or ''
            lid = lib_id_for(ref, val, pkg)
            x, y, r, mx, my = self._coord(pi)
            props = OrderedDict(Reference=ref, Value=val)
            for p in pi.findall('PartInstUserProp/Defn'):
                props[p.get('name')] = p.get('val', '')
            comps.append(dict(
                lib=lid, at=(x, y, r), flip=(mx, my), props=props, uuid=uid()
            ))
        for g in self.root.findall('.//Global/Defn'):
            name = POWER_ALIAS.get(g.get('symbolName'))
            if not name:
                continue
            x = snap(mm(g.get('locX')))
            y = snap(mm(g.get('locY')))
            comps.append(dict(
                lib=f"power:{name}", at=(x, y, 0), flip=(False, False),
                props=OrderedDict(Reference="#PWR", Value=name), uuid=uid()
            ))
        return comps

    def _coord(self, pi) -> Tuple[float, float, int, bool, bool]:
        def xy(el):
            return snap(mm(el.get('locX') or el.get('x'))), \
                   snap(mm(el.get('locY') or el.get('y')))
        for tag, rot_attr in (('Defn', 'rotation'), ('Inst', 'rotation'), ('Location', 'orientation')):
            el = pi.find(tag)
            if el is not None and (el.get('locX') or el.get('x') or el.get('orientation')):
                x, y = xy(el)
                r0 = int(el.get(rot_attr, '0')) * 90
                mx = el.get('flipX', '0') == '1'
                my = el.get('flipY', '0') == '1'
                return x, y, (r0 % 360), mx, my
        return 0.0, 0.0, 0, False, False

    def nets(self):
        wires, juncs = [], []
        for w in self.root.findall('.//WireScalar/Defn'):
            a = (snap(mm(w.get('startX'))), snap(mm(w.get('startY'))))
            b = (snap(mm(w.get('endX'))), snap(mm(w.get('endY'))))
            wires.append((a, b))
        for j in self.root.findall('.//Junction/Defn'):
            juncs.append((snap(mm(j.get('locX'))), snap(mm(j.get('locY')))))
        return wires, juncs

# ─────────────── Fallback Symbol ────────────────
def fallback_block(sym, sp='    ') -> str:
    out = f'{sp}(symbol {q(sym["name"])}\n'
    out += f'{sp}  (pin_names (offset 0.254))\n'
    out += f'{sp}  (property "Reference" {q(sym["name"][0])} (at 0 2 0) {FONT})\n'
    out += f'{sp}  (property "Value"     {q(sym["name"])} (at 0 -2 0) {FONT})\n'
    out += f'{sp}  (symbol "{sym["name"]}_0_1"\n'
    for pts in sym['gfx']:
        out += f'{sp}    (polyline (pts {" ".join(xy(p) for p in pts)}) (stroke (width 0.254) (type default)) (fill (type none)))\n'
    for p in sym['pins']:
        x, y = num(p['at'][0]), num(p['at'][1])
        out += f'{sp}    (pin {p["typ"]} line (at {x} {y} 0) (length 2.54) (name {q(p["name"])} {FONT}) (number {q(p["num"])} {FONT}))\n'
    out += f'{sp}  )\n{sp})\n'
    return out

# ─────────────── Instance ────────────────
def inst_block(c: dict) -> str:
    lib, x0, y0, r0 = c['lib'], *c['at']
    mx, my = c.get('flip', (False, False))
    r = r0 % 360
    th = math.radians(r)
    px, py = _symbol_pin1.get(lib, (0.0, 0.0))

    # Apply mirroring first (local space)
    if mx: px = -px
    if my: py = -py

    # Rotate to global space
    dx = px * math.cos(th) - py * math.sin(th)
    dy = px * math.sin(th) + py * math.cos(th)

    x = snap(x0 - dx)
    y = snap(y0 - dy)
    xs, ys = num(x), num(y)

    lines = [
        "  (symbol",
        f"    (lib_id {q(lib)})",
        f"    (at {xs} {ys} {r})"
    ]
    if mx: lines.append("    (mirror x)")
    if my: lines.append("    (mirror y)")
    lines.append(f"    (unit 1) (uuid {q(c['uuid'])})")
    
    def place(name, txt, off_x, off_y):
        px_, py_ = off_x, off_y
        if mx: px_ = -px_
        if my: py_ = -py_
        dx_t = px_ * math.cos(th) - py_ * math.sin(th)
        dy_t = px_ * math.sin(th) + py_ * math.cos(th)
        px = x + dx_t
        py = y + dy_t
        return f'    (property "{name}" {q(txt)} (at {num(px)} {num(py)} {r}) {FONT})'

    props = c['props']
    ref = props.pop('Reference', None)
    val = props.pop('Value', None)
    if ref: lines.append(place("Reference", ref, 0, REF_DY))
    if val: lines.append(place("Value", val, 0, VAL_DY))
    for k, v in props.items():
        lines.append(place(k, v, 0, 0))

    lines.append("  )\n")
    return "\n".join(lines)

wire = lambda w: f"  (wire (pts {xy(w[0])} {xy(w[1])}) (stroke (width 0)))\n"
junc = lambda j: f"  (junction (at {num(j[0])} {num(j[1])}) (diameter 0))\n"

# ─────────────── Main Writer ────────────────
def write_schematic(rdr: OrCadReader, outfile: Path, libroot: Path):
    comps = rdr.components()
    print(f"[debug] Found {len(comps)} components")
    placed_ids = {c['lib'] for c in comps}
    embedded, done = [], set()
    for lid in placed_ids:
        if ":" in lid:
            collect(libroot, lid, placed_ids, done, embedded)
    wires, juncs = rdr.nets()
    with outfile.open('w', encoding='utf-8') as fp:
        fp.write('(kicad_sch\n'
                 '  (version 20250114)\n'
                 '  (generator "orcad2kicad")\n'
                 f'  (uuid {q(uid())})\n'
                 '  (paper "A4")\n'
                 '  (title_block (title "") (date "") (rev ""))\n'
                 '  (lib_symbols\n')
        for blk in embedded:
            fp.write('    ' + blk.replace('\n', '\n    ').rstrip() + '\n')
        for fb in rdr.fallback_symbols(placed_ids):
            fp.write(fallback_block(fb, '    '))
        fp.write('  )\n')
        for c in comps:
            fp.write(inst_block(c))
        for w in wires:
            fp.write(wire(w))
        for j in juncs:
            fp.write(junc(j))
        fp.write(')\n')

# ─────────────── CLI ────────────────
def main():
    parser = argparse.ArgumentParser(description='Convert OrCAD XML → KiCad-9 schematic')
    parser.add_argument('--xml', required=True, help='Input OrCAD *.xml')
    parser.add_argument('--out', required=True, help='Output *.kicad_sch')
    parser.add_argument('--symdir', default='/usr/share/kicad/symbols',
                        help='Root directory of KiCad *.kicad_sym libraries')
    args = parser.parse_args()
    xin = Path(args.xml)
    if not xin.is_file():
        sys.exit(f'[error] {xin} not found')
    xout = Path(args.out).with_suffix('.kicad_sch')
    write_schematic(OrCadReader(xin), xout, Path(args.symdir))
    print(f'✓ Wrote {xout}')

if __name__ == '__main__':
    main()
