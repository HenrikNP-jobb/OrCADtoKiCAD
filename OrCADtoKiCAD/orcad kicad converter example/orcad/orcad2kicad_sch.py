# [START OF FILE]
#!/usr/bin/env python3
"""
orcad2kicad_sch.py – Convert OrCAD XML netlist to KiCad 9 schematic with symbol rotation/mirroring.
"""

import argparse, re, sys, uuid, math
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Set, Tuple

# ────────────── Constants ───────────────
MIL10 = 0.254
GRID = 2.54
REF_DY = 2 * GRID
VAL_DY = -2 * GRID
FONT = "(effects (font (size 1.27 1.27)) (justify left))"

EXT_RE = re.compile(r'\(extends\s+"([^"]+)"')
EXTENDS_CLAUSE = re.compile(r'\s*\(extends\s+"[^"]+"\)')
PIN_TYPE = {"0": "passive", "1": "input", "2": "output", "3": "bidirectional", "4": "passive", "5": "power_in", "6": "power_out"}
LIB_ID: Dict = {
    "R": "Device:R_US",
    "C": "Device:C",
    "U": "Amplifier_Operational:LM324",
    ("J", "2"): "Connector_Generic:Conn_01x02",
    ("J", "4"): "Connector_Generic:Conn_01x04",
}

mm = lambda v: round(float(v) * MIL10, 3) if v not in (None, "") else 0.0
num = lambda x: ("%f" % x).rstrip("0").rstrip(".")
xy = lambda p: f"(xy {num(p[0])} {num(p[1])})"
q = lambda s: '"' + str(s).replace('"', r'\"') + '"'
uid = lambda: str(uuid.uuid4())
snap = lambda v: round(v, 3)

_symbol_blocks: Dict[str, str] = {}
_symbol_pin1: Dict[str, Tuple[float, float]] = {}

# ───────────── Symbol Handling ─────────────
def read_symbol(libroot: Path, lib_id: str) -> str:
    if lib_id in _symbol_blocks:
        return _symbol_blocks[lib_id]
    lib, sym = lib_id.split(":", 1)
    path = libroot / f"{lib}.kicad_sym"
    if not path.exists():
        raise RuntimeError(f'"{sym}" not found in {path}')
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
            m = re.search(r'\(pin\s+[^\)]*?\(at\s+([0-9.\-]+)\s+([0-9.\-]+)\s+[0-9]+\)[^\)]*?\(number\s+"1"',
                          block, re.MULTILINE | re.DOTALL)
            _symbol_pin1[lib_id] = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)
            return block
    raise RuntimeError("Unbalanced parentheses in symbol")

strip_extends = lambda blk: EXTENDS_CLAUSE.sub("", blk)
def rename(block: str, new: str) -> str:
    p1 = block.find('"') + 1
    p2 = block.find('"', p1)
    return block[:p1] + new + block[p2:]

def collect(libroot: Path, lib_id: str, placed: Set[str], done: Set[str], out: List[str]) -> None:
    if lib_id in done:
        return
    try:
        raw = read_symbol(libroot, lib_id)
    except Exception as e:
        print(f"[warn] Skipping missing symbol for {lib_id}: {e}", file=sys.stderr)
        return
    lib, short = lib_id.split(":", 1)
    for parent in EXT_RE.findall(raw):
        full = parent if ":" in parent else f"{lib}:{parent}"
        collect(libroot, full, placed, done, out)
        base = full.split(":", 1)[1]
        if base not in done:
            try:
                txt = read_symbol(libroot, full)
                out.append(rename(strip_extends(txt), base))
                done.add(base)
            except Exception as e:
                print(f"[warn] Skipping missing parent symbol {full}: {e}", file=sys.stderr)
    name = lib_id if lib_id in placed else short
    if name not in done:
        out.append(rename(strip_extends(raw), name))
        done.add(name)

# ───────────── Rotation Detection ─────────────
def infer_rotation_from_wires(x0, y0, wires, margin=2.0):
    horizontal = vertical = 0
    for (x1, y1), (x2, y2) in wires:
        if min(x1, x2) - margin <= x0 <= max(x1, x2) + margin and min(y1, y2) - margin <= y0 <= max(y1, y2) + margin:
            if abs(x2 - x1) > abs(y2 - y1):
                horizontal += 1
            elif abs(y2 - y1) > abs(x2 - x1):
                vertical += 1
    return 90 if vertical > horizontal else 0

# ───────────── Symbol Placement ─────────────
def inst_block(c: dict) -> str:
    lib, x0, y0, r0 = c['lib'], *c['at']
    mx, my = c.get('flip', (False, False))
    r = r0 % 360
    th = math.radians(r)

    px, py = _symbol_pin1.get(lib, (0.0, 0.0))
    if mx: px = -px
    if my: py = -py
    dx = px * math.cos(th) - py * math.sin(th)
    dy = px * math.sin(th) + py * math.cos(th)
    x = snap(x0 - dx)
    y = snap(y0 - dy)

    lines = [
        "  (symbol",
        f"    (lib_id {q(lib)})",
        f"    (at {num(x)} {num(y)} {r})"
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
        return f'    (property "{name}" {q(txt)} (at {num(x + dx_t)} {num(y + dy_t)} {r}) {FONT})'

    props = c['props']
    ref = props.pop('Reference', None)
    val = props.pop('Value', None)
    if ref: lines.append(place("Reference", ref, 0, REF_DY))
    if val: lines.append(place("Value", val, 0, VAL_DY))
    for k, v in props.items():
        lines.append(place(k, v, 0, 0))

    lines.append("  )\n")
    return "\n".join(lines)

# ───────────── Reader ─────────────
class OrCadReader:
    def __init__(self, xml_path: Path):
        self.root = ET.parse(str(xml_path)).getroot()
        self.strip_ns(self.root)

    def strip_ns(self, elem):
        for el in elem.iter():
            if '}' in el.tag:
                el.tag = el.tag.split('}', 1)[1]

    def components(self) -> List[dict]:
        comps = []
        for pi in self.root.findall('.//PartInst'):
            ref_el = pi.find('Reference/Defn')
            val_el = pi.find('PartValue/Defn')
            defn = pi.find('Defn')
            if ref_el is None or defn is None:
                continue
            ref = ref_el.get('name')
            val = val_el.get('name') if val_el is not None else ''
            pkg = pi.get('pkgName') or ''
            pins = pi.get('pinCount') or ''
            key = (ref[0], pins)
            lid = LIB_ID.get(key) or LIB_ID.get(ref[0]) or f"Device:{ref[0]}"
            print(f"[debug] Component {ref} → lib_id = {lid}")
            x, y = snap(mm(defn.get('locX', '0'))), snap(mm(defn.get('locY', '0')))
            rot = int(defn.get('rotation', '0')) * 90
            mx = defn.get('flipX', '0') == '1'
            my = defn.get('flipY', '0') == '1'
            props = OrderedDict(Reference=ref, Value=val)
            comps.append(dict(lib=lid, at=(x, y, rot), flip=(mx, my), props=props, uuid=uid()))
        print(f"[debug] Found {len(comps)} components")
        return comps

    def fallback_symbols(self, placed_ids: Set[str]) -> List[dict]:
        return [
            {
                "name": "Fallback_" + lid.split(":")[-1],
                "pins": [
                    {"typ": "passive", "at": (-2.54, 0), "name": "1", "num": "1"},
                    {"typ": "passive", "at": (2.54, 0), "name": "2", "num": "2"},
                ]
            } for lid in placed_ids if lid not in _symbol_blocks
        ]

    def nets(self):
        wires, juncs = [], []
        for w in self.root.findall('.//WireScalar/Defn'):
            a = (snap(mm(w.get('startX'))), snap(mm(w.get('startY'))))
            b = (snap(mm(w.get('endX'))), snap(mm(w.get('endY'))))
            wires.append((a, b))
        for j in self.root.findall('.//Junction/Defn'):
            juncs.append((snap(mm(j.get('locX'))), snap(mm(j.get('locY')))))
        return wires, juncs

# ────────────── Writer ──────────────
def fallback_block(sym, sp='    ') -> str:
    out = f'{sp}(symbol {q(sym["name"])}\n{sp}  (pin_names (offset 0.254))\n'
    out += f'{sp}  (property "Reference" {q(sym["name"][0])} (at 0 2 0) {FONT})\n'
    out += f'{sp}  (property "Value" {q(sym["name"])} (at 0 -2 0) {FONT})\n'
    out += f'{sp}  (symbol "{sym["name"]}_0_1"\n'
    for p in sym['pins']:
        x, y = num(p['at'][0]), num(p['at'][1])
        out += f'{sp}    (pin {p["typ"]} line (at {x} {y} 0) (length 2.54) (name {q(p["name"])} {FONT}) (number {q(p["num"])} {FONT}))\n'
    out += f'{sp}  )\n{sp})\n'
    return out

def write_schematic(rdr: OrCadReader, outfile: Path, libroot: Path):
    comps = rdr.components()
    wires, juncs = rdr.nets()

    for c in comps:
        cx, cy, _ = c['at']
        c['at'] = (cx, cy, infer_rotation_from_wires(cx, cy, wires))

    placed_ids = {c['lib'] for c in comps}
    embedded, done = [], set()
    for lid in placed_ids:
        if ":" in lid:
            collect(libroot, lid, placed_ids, done, embedded)

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
            fp.write(f"  (wire (pts {xy(w[0])} {xy(w[1])}) (stroke (width 0)))\n")
        for j in juncs:
            fp.write(f"  (junction (at {num(j[0])} {num(j[1])}) (diameter 0))\n")
        fp.write(')\n')

# ────────────── CLI ──────────────
def main():
    parser = argparse.ArgumentParser(description='Convert OrCAD XML → KiCad-9 schematic')
    parser.add_argument('--xml', required=True, help='Input OrCAD *.xml')
    parser.add_argument('--out', required=True, help='Output *.kicad_sch')
    parser.add_argument('--symdir', default='/usr/share/kicad/symbols', help='Path to *.kicad_sym directory')
    args = parser.parse_args()
    xin = Path(args.xml)
    if not xin.is_file():
        sys.exit(f'[error] {xin} not found')
    xout = Path(args.out).with_suffix('.kicad_sch')
    write_schematic(OrCadReader(xin), xout, Path(args.symdir))
    print(f'✓ Wrote {xout}')

if __name__ == '__main__':
    main()
# [END OF FILE]
