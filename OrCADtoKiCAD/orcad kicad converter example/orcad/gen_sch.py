#!/usr/bin/env python3
"""
generate_sch.py – JSON ➜ self-contained KiCad-9 schematic

• Fixes “No parent for extended symbol …”
• Places every symbol on the regular 0.1-inch grid.
  (default: x_mm / y_mm × 10  →  snapped to 2 .54 mm grid)

Usage
-----
python3 generate_sch.py --json design.json --out design.kicad_sch \
        --symdir /usr/share/kicad/symbols [--scale 10.0]
"""

import argparse, json, uuid, datetime, re, math
from pathlib import Path
from typing  import Dict, List, Set

# ─────────────────────────  constants  ────────────────────────────────────
FONT  = '(effects (font (size 1.27 1.27)))'
TITLE = "This should be converted into KiCAD"
REV   = "0.1"

EXT_RE          = re.compile(r'\(extends\s+"([^"]+)"')
EXTENDS_CLAUSE  = re.compile(r'\s*\(extends\s+"[^"]+"\)')

GRID            = 2.54            # KiCad default 0.1″ → 2.54 mm
REF_DY          = 2*GRID          # +5.08 mm  (two grid units)
VAL_DY          = -2*GRID         # –5.08 mm

# very small mapping Orcad-ref ➜ KiCad symbol id
LIB_ID = {
    'U'        : 'Amplifier_Operational:LM6171xxN',
    'R'        : 'Device:R_US',
    'C'        : 'Device:C',
    ('J','2')  : 'Connector_Generic:Conn_01x02',
    ('J','4')  : 'Connector_Generic:Conn_01x04',
}

# ───────────────────────── helpers ────────────────────────────────────────
def strip_extends(block: str) -> str:
    """Remove any (extends "…") clause inside a symbol definition."""
    return EXTENDS_CLAUSE.sub('', block)

def lib_id_for(c: Dict) -> str:
    """Return the KiCad library ID that matches one JSON component."""
    if c['ref'][0] != 'J':
        return LIB_ID[c['ref'][0]]
    pins = '4' if ('04' in c.get('footprint','').lower()
                   or c['value'].endswith('004B')) else '2'
    return LIB_ID[('J', pins)]

def read_symbol(libroot: Path, lib_id: str) -> str:
    """Read one symbol out of KiCad’s global libraries (plain text)."""
    lib, sym = lib_id.split(':', 1)
    text     = (libroot / f'{lib}.kicad_sym').read_text()
    p0       = text.find(f'(symbol "{sym}"')
    if p0 == -1:
        raise RuntimeError(f'"{sym}" not found in {lib}.kicad_sym')
    depth = 0
    for i, ch in enumerate(text[p0:], p0):
        depth += (ch == '(') - (ch == ')')
        if depth == 0:
            return text[p0:i+1]
    raise RuntimeError('unbalanced parentheses reading symbol')

def rename(block: str, new_name: str) -> str:
    """Change (symbol "old" …) ⇒ (symbol "new" …)"""
    p1 = block.find('"') + 1
    p2 = block.find('"', p1)
    return block[:p1] + new_name + block[p2:]

def snap(value_mm: float) -> float:
    """Snap a millimetre value to the closest 0.1-inch (=2 .54 mm) grid."""
    return round(value_mm / GRID) * GRID

# ──────────────────  recursive collector (parent-first)  ───────────────────
def collect(libroot: Path, lib_id: str,
            placed: Set[str], done: Set[str], out: List[str]) -> None:
    if lib_id in done:
        return
    raw           = read_symbol(libroot, lib_id)
    lib, short_id = lib_id.split(':', 1)

    # first make sure every parent is embedded
    for parent in EXT_RE.findall(raw):
        if ':' in parent:
            full_parent, short_parent = parent, parent.split(':', 1)[1]
        else:
            full_parent, short_parent = f'{lib}:{parent}', parent
        collect(libroot, full_parent, placed, done, out)
        if short_parent not in done:
            out.append(rename(strip_extends(read_symbol(libroot, full_parent)),
                              short_parent))
            done.add(short_parent)

    # now embed the symbol itself
    need_name = lib_id if lib_id in placed else short_id
    if need_name not in done:
        out.append(rename(strip_extends(raw), need_name))
        done.add(need_name)

# ─────────────────────  sheet-level symbol instance  ──────────────────────
def instance(c: Dict, lib_id: str, scale: float) -> str:
    x = snap(c['x_mm'] * scale)
    y = snap(c['y_mm'] * scale)
    rot = int(c.get('rot', 0))

    return '\n'.join([
        f'  (symbol (lib_id "{lib_id}")',
        f'          (at {x:.2f} {y:.2f} {rot})',
        f'          (unit 1) (in_bom yes) (on_board yes) (uuid {uuid.uuid4()})',
        f'    (property "Reference" "{c["ref"]}" (id 0) '
        f'(at {x:.2f} {y+REF_DY:.2f} 0) {FONT})',
        f'    (property "Value" "{c["value"]}" (id 1) '
        f'(at {x:.2f} {y+VAL_DY:.2f} 0) {FONT})',
        '  )'])

# ─────────────────────────────────  main  ─────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json',   required=True)
    ap.add_argument('--out',    required=True)
    ap.add_argument('--symdir', default='/usr/share/kicad/symbols')
    ap.add_argument('--scale',  type=float, default=10.0,
                    help='multiply x_mm / y_mm by this and snap to 0.1″ grid '
                         '(default 10)')
    args = ap.parse_args()

    comps       = json.load(open(args.json))['components']         # type: List[Dict]
    placed_ids  = {lib_id_for(c) for c in comps}                   # unique symbols
    libroot     = Path(args.symdir)

    # embed every needed symbol exactly once
    embedded, done = [], set()
    for cid in placed_ids:
        collect(libroot, cid, placed_ids, done, embedded)

    now = datetime.datetime.now()

    lines = [
        '(kicad_sch',
        '  (version 20250114)',
        '  (generator "orcad-extractor")',
        '  (generator_version "9.0")',
        f'  (uuid "{uuid.uuid4()}")',
        '  (paper "A4")',
        '  (title_block',
        f'    (title "{TITLE}")',
        f'    (date "{now:%A, %B %d, %Y}")',
        f'    (rev "{REV}")',
        f'    (comment 1 "Generated: {now:%Y-%m-%d %H:%M:%S}")',
        '  )',
        '  (lib_symbols']
    lines.extend(embedded)
    lines.append('  )')

    for c in comps:
        lines.append(instance(c, lib_id_for(c), args.scale))
    lines.append(')')

    Path(args.out).write_text('\n'.join(lines))
    print(f'Wrote {args.out} — {len(comps)} sheet symbols, '
          f'{len(embedded)} embedded symbols  (scale={args.scale}, grid={GRID} mm)')

# ──────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    main()
