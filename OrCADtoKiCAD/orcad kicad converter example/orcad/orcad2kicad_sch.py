import argparse, re, sys, uuid, math
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

# constants
MIL10 = 0.254
GRID = 2.54
REF_DY = 2 * GRID
VAL_DY = -2 * GRID
FONT = "(effects (font (size 1.27 1.27)) (justify left))"

EXT_RE = re.compile(r'\(extends\s+"([^"]+)"')
EXTENDS_CLAUSE = re.compile(r'\s*\(extends\s+"[^"]+"\)')
PIN_TYPE = {
    "0": "passive",
    "1": "input",
    "2": "output",
    "3": "bidirectional",
    "4": "passive",
    "5": "power_in",
    "6": "power_out",
}

# fallback guesses for stock libs (only used if no converted match found)
LIB_ID: Dict = {
    "R": "Device:R_US",
    "C": "Device:C",
    "U": "Amplifier_Operational:LM324",
    ("J", "2"): "Connector_Generic:Conn_01x2",
    ("J", "4"): "Connector_Generic:Conn_01x4",
}

mm = lambda v: round(float(v) * MIL10, 3) if v not in (None, "") else 0.0
num = lambda x: ("%f" % x).rstrip("0").rstrip(".")
xy = lambda p: f"(xy {num(p[0])} {num(p[1])})"
q = lambda s: '"' + str(s).replace('"', r'\"') + '"'
uid = lambda: str(uuid.uuid4())
snap = lambda v: round(v, 3)

# caches
_symbol_blocks: Dict[str, str] = {}
_symbol_pin1: Dict[str, Tuple[float, float]] = {}
_combined_text_cache: Dict[str, str] = {}
_combined_names_cache: Dict[str, Set[str]] = {}

# ───────── helpers ─────────
def _clean_name(s: Optional[str]) -> str:
    if not s:
        return ""
    s = s.strip()
    s = re.sub(r'\.Normal$', '', s, flags=re.IGNORECASE)
    s = s.replace('.', '_')
    s = re.sub(r'[^A-Za-z0-9_\-+]', '_', s)  # keep + and -
    s = re.sub(r'__+', '_', s)
    return s

def _first_nonempty(*vals: Optional[str]) -> str:
    for v in vals:
        if v and v.strip():
            return v.strip()
    return ""

def _extract_symbol_block(txt: str, sym: str) -> str:
    p0 = txt.find(f'(symbol "{sym}"')
    if p0 == -1:
        raise RuntimeError(f'"{sym}" not found')
    depth = 0
    for i, ch in enumerate(txt[p0:], p0):
        depth += (ch == "(") - (ch == ")")
        if depth == 0:
            return txt[p0 : i + 1]
    raise RuntimeError("Unbalanced parentheses in symbol")

def _load_combined_text(path: Path) -> str:
    key = str(path)
    if key not in _combined_text_cache:
        _combined_text_cache[key] = path.read_text(encoding="utf-8", errors="ignore")
    return _combined_text_cache[key]

def _combined_symbol_names(path: Optional[Path]) -> Set[str]:
    """Index all (symbol "Name") from the chosen combined library file."""
    if not path or not path.is_file():
        return set()
    key = str(path)
    if key in _combined_names_cache:
        return _combined_names_cache[key]
    txt = _load_combined_text(path)
    names = set(m.group(1) for m in re.finditer(r'\(symbol\s+"([^"]+)"', txt))
    _combined_names_cache[key] = names
    return names

def _locate_combined_library(conv_root: Optional[Path],
                             converted_lib_file: Optional[Path],
                             converted_lib: str) -> Optional[Path]:
    """Pick a combined .kicad_sym file if present (explicit > common names)."""
    if converted_lib_file and converted_lib_file.is_file():
        return converted_lib_file
    if not conv_root:
        return None
    candidates = [
        conv_root / f"{converted_lib}.kicad_sym",
        conv_root / "converted_sch.kicad_sym",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None

def _converted_has_symbol(conv_root: Optional[Path],
                          combined_path: Optional[Path],
                          sym: str) -> bool:
    """Fast existence check in converted set (per-file or the chosen combined file).
       Tries exact, UPPERCASE, and cleaned variants for robustness."""
    if not sym:
        return False

    # per-file, exact filename
    if conv_root:
        per = conv_root / f"{sym}.kicad_sym"
        if per.is_file():
            return True

    names = _combined_symbol_names(combined_path)
    if not names:
        return False

    candidates = []
    s_clean = _clean_name(sym)
    for c in (sym, s_clean, sym.upper(), s_clean.upper()):
        if c and c not in candidates:
            candidates.append(c)

    for c in candidates:
        if c in names:
            return True
    return False

# Power symbol mapping (fallback only)
def choose_power_lib(symbol_name: str, name: str) -> Tuple[str, str]:
    """
    Map OrCAD Global symbols to KiCad power lib_ids and values.
    Returns (lib_id, value_text).
    """
    sym = (symbol_name or "").upper().strip()
    nm  = (name or "").strip()

    # Common grounds
    if sym in {"GND_SIGNAL", "GND", "GND_POWER", "DGND", "AGND"} or nm.upper() in {"GND", "DGND", "AGND"}:
        gmap = {"DGND": "DGND", "AGND": "AGND"}
        tag = gmap.get(nm.upper(), "GND")
        return (f"power:{tag}", tag)

    # Explicit rails like +5V, +3V3, -5V, etc.
    if nm.startswith("+") or nm.startswith("-"):
        clean = _clean_name(nm)  # keeps '+' and '-'
        return (f"power:{clean}", clean)

    # Named rails (VCC / VDD / VSS / VDDA / VSSA / VEE ... )
    named = nm.upper()
    for k in ("VCC", "VDD", "VSS", "VDDA", "VSSA", "VEE"):
        if named == k:
            return (f"power:{k}", k)

    # Arrow without explicit name → assume VCC
    if sym.startswith("VCC"):
        return ("power:VCC", "VCC")

    # Fallback
    fallback = _clean_name(nm) or "VCC"
    return ("power:VCC", fallback)

# Symbol Handling 
def read_symbol(sys_root: Path,
                conv_root: Optional[Path],
                combined_path: Optional[Path],
                converted_lib: str,
                lib_id: str) -> str:

    """Read & cache a symbol block by lib_id 'Lib:Sym' from:
       - converted per-symbol file  <conv>/<Sym>.kicad_sym
       - converted combined lib     combined_path
       - system KiCad lib           <symdir>/<Lib>.kicad_sym
    """
    if lib_id in _symbol_blocks:
        return _symbol_blocks[lib_id]

    lib, sym = lib_id.split(":", 1)

    if lib == converted_lib:
        if conv_root:
            per_sym = conv_root / f"{sym}.kicad_sym"
            if per_sym.is_file():
                txt = per_sym.read_text(encoding="utf-8")
                block = _extract_symbol_block(txt, sym)
            else:
                if not combined_path or not combined_path.is_file():
                    raise RuntimeError(f'"{sym}" not found (checked {per_sym} and no combined lib)')
                txt = _load_combined_text(combined_path)
                block = _extract_symbol_block(txt, sym)
        else:
            if not combined_path or not combined_path.is_file():
                raise RuntimeError(f'Converted library not available for "{sym}"')
            txt = _load_combined_text(combined_path)
            block = _extract_symbol_block(txt, sym)
    else:
        path = sys_root / f"{lib}.kicad_sym"
        if not path.is_file():
            raise RuntimeError(f'Library file not found: {path}')
        txt = path.read_text(encoding="utf-8")
        block = _extract_symbol_block(txt, sym)

    _symbol_blocks[lib_id] = block

    # cache pin 1 (best-effort anchor if needed)
    m = re.search(
        r'\(pin\s+[^\)]*?\(at\s+([0-9.\-]+)\s+([0-9.\-]+)\s+[0-9]+\)[^\)]*?\(number\s+"1"',
        block, re.MULTILINE | re.DOTALL,
    )
    _symbol_pin1[lib_id] = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)
    return block

strip_extends = lambda blk: EXTENDS_CLAUSE.sub("", blk)

def rename(block: str, new: str) -> str:
    p1 = block.find('"') + 1
    p2 = block.find('"', p1)
    return block[:p1] + new + block[p2:]

def collect(sys_root: Path, conv_root: Optional[Path], combined_path: Optional[Path],
            converted_lib: str, lib_id: str, placed: Set[str], done: Set[str], out: List[str]) -> None:
    if lib_id in done:
        return
    try:
        raw = read_symbol(sys_root, conv_root, combined_path, converted_lib, lib_id)
    except Exception as e:
        print(f"[warn] Skipping missing symbol for {lib_id}: {e}", file=sys.stderr)
        return

    lib, short = lib_id.split(":", 1)
    for parent in EXT_RE.findall(raw):
        full = parent if ":" in parent else f"{lib}:{parent}"
        collect(sys_root, conv_root, combined_path, converted_lib, full, placed, done, out)
        base = full.split(":", 1)[1]
        if base not in done:
            try:
                txt = read_symbol(sys_root, conv_root, combined_path, converted_lib, full)
                out.append(rename(strip_extends(txt), base))
                done.add(base)
            except Exception as e:
                print(f"[warn] Skipping missing parent symbol {full}: {e}", file=sys.stderr)

    name = lib_id if lib_id in placed else short
    if name not in done:
        out.append(rename(strip_extends(raw), name))
        done.add(name)

# instance block (pin-1 anchor) 
def inst_block(c: dict) -> str:
    lib, x0, y0, r0 = c['lib'], *c['at']
    mx, my = c.get('flip', (False, False))
    r = r0 % 360
    th = math.radians(r)

    # Anchor by pin 1 with mirror and rotation
    px, py = _symbol_pin1.get(lib, (0.0, 0.0))
    if mx:               # (mirror x) = vertical flip
        py = -py
    if my:               # (mirror y) = horizontal flip
        px = -px
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

    # Property placements (same transform as above)
    def place(name, txt, off_x, off_y):
        px_, py_ = off_x, off_y
        if mx: py_ = -py_
        if my: px_ = -px_
        dx_t = px_ * math.cos(th) - py_ * math.sin(th)
        dy_t = px_ * math.sin(th) + py_ * math.cos(th)
        return f'    (property "{name}" {q(txt)} (at {num(x + dx_t)} {num(y + dy_t)} {r}) {FONT})'

    props = c['props'].copy()
    ref = props.pop('Reference', None)
    val = props.pop('Value', None)
    if ref: lines.append(place("Reference", ref, 0, REF_DY))
    if val: lines.append(place("Value", val, 0, VAL_DY))
    for k, v in props.items():
        lines.append(place(k, v, 0, 0))

    lines.append("  )\n")
    return "\n".join(lines)

# OrCAD XML reader
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
            val = (val_el.get('name') if val_el is not None else '')
            pins = pi.get('pinCount') or ''

            # OrCAD CellName from pkgName or GraphicName (strip ".Normal")
            cell = pi.get('pkgName') or ''
            gndef = pi.find('GraphicName/Defn')
            if not cell and gndef is not None:
                nm = gndef.get('name') or ''
                cell = nm.split('.', 1)[0] if nm else ''
            cell = _clean_name(cell)

            # Position
            x, y = snap(mm(defn.get('locX', '0'))), snap(mm(defn.get('locY', '0')))

            # Rotation 
            rot_steps_raw = defn.get('rotation', '0').strip()
            try:
                rot_steps = int(rot_steps_raw) % 4
            except Exception:
                rot_steps = 0

            # We don't apply mirror-y by default
            my = False

            #Custom placement policy 
            # Default: mirror x ON, rotation = OrCAD*90
            mx = True
            rot = (rot_steps * 90) % 360

            # Exception: rotation="1" = DO NOT mirror x, rotate clockwise 90° (270°)
            if rot_steps == 1:
                mx = False
                rot = 270
           

            props = OrderedDict(Reference=ref, Value=val)
            comps.append(dict(
                ref=ref, val=val, cell=cell, pins=pins,
                at=(x, y, rot),
                flip=(mx, my),
                props=props, uuid=uid(),
            ))
        return comps

    def nets(self):
        wires, juncs = [], []
        for w in self.root.findall('.//WireScalar/Defn'):
            a = (snap(mm(w.get('startX'))), snap(mm(w.get('startY'))))
            b = (snap(mm(w.get('endX'))), snap(mm(w.get('endY'))))
            wires.append((a, b))
        for j in self.root.findall('.//Junction/Defn'):
            juncs.append((snap(mm(j.get('locX'))), snap(mm(j.get('locY')))))
        return wires, juncs

    def power_globals(self) -> List[dict]:
        """
        Collect OrCAD Global (power) symbols with their positions.
        """
        globs = []
        for ge in self.root.findall('.//Global/Defn'):
            name = ge.get('name') or ""
            sym  = ge.get('symbolName') or ""
            x = snap(mm(ge.get('locX', '0')))
            y = snap(mm(ge.get('locY', '0')))

            # OrCAD rotation is in 90° steps (0..3)
            rot_steps_raw = ge.get('rotation', '0').strip()
            try:
                rot_steps = int(rot_steps_raw) % 4
            except Exception:
                rot_steps = 0
            rot = (rot_steps * 90) % 360

            # Do NOT mirror power symbols by default.
            mx, my = False, False

            globs.append(dict(
                name=name, symbol=sym,
                at=(x, y, rot),
                flip=(mx, my),
                props=OrderedDict(),
                uuid=uid(),
            ))
        return globs

# simple fallback symbol (only if nothing found)
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

# lib_id selection 
def choose_lib_id(ref: str, val: str, pins: str, cell: str,
                  sys_root: Path, conv_root: Optional[Path],
                  combined_path: Optional[Path], converted_lib: str) -> str:
    """Prefer converted symbol by CellName, then by Value; else stock mapping."""
    # 1) Exact cell match in converted set
    cell_clean = _clean_name(cell)
    if cell_clean and _converted_has_symbol(conv_root, combined_path, cell_clean):
        print(f"[map] {ref}: using converted by CellName → {converted_lib}:{cell_clean}")
        return f"{converted_lib}:{cell_clean}"

    # 2) Try by PartValue (common for vendor PNs etc.)
    val_clean = _clean_name(val)
    if val_clean and _converted_has_symbol(conv_root, combined_path, val_clean):
        print(f"[map] {ref}: using converted by Value → {converted_lib}:{val_clean}")
        return f"{converted_lib}:{val_clean}"

    # 3) Stock mapping fallback
    key = (ref[0], pins)
    lid = LIB_ID.get(key) or LIB_ID.get(ref[0]) or f"Device:{ref[0]}"
    print(f"[map] {ref}: using stock mapping → {lid}")
    return lid

def _normalize_power_candidate(s: str) -> str:
    """Normalize OrCAD power names like VCC_ARROW, etc., to common rail tags."""
    if not s:
        return ""
    c = _clean_name(s)
    u = c.upper()
    # strip common suffixes from CAPSYM names
    u = re.sub(r'_(ARROW|POWER|SIGNAL|BAR|UP|DOWN)$', '', u)
    if u.startswith("VCC"): return "VCC"
    if u.startswith("VDD"): return "VDD"
    if u.startswith("VSS"): return "VSS"
    if u.startswith("VDDA"): return "VDDA"
    if u.startswith("VSSA"): return "VSSA"
    if u.startswith("VEE"): return "VEE"
    if u.startswith("GND"): return "GND"
    if c.startswith("+") or c.startswith("-"):
        return c  # keep explicit voltages like +5V, -12V
    return u

def _name_variants(raw: str) -> List[str]:
    """Generate robust candidate spellings for matching in converted libs."""
    if not raw:
        return []
    s = raw.strip()
    cand = _clean_name(s)
    out: List[str] = []
    for v in (cand, cand.upper()):
        if v and v not in out:
            out.append(v)
    # For strings that start with +/-, include exact too
    if s.startswith(("+","-")) and s not in out:
        out.insert(0, s)
    return out

def choose_power_preferring_converted(symbol_name: str, name: str,
                                      conv_root: Optional[Path],
                                      combined_path: Optional[Path],
                                      converted_lib: str) -> Tuple[str, str]:
    """
    Prefer converted power symbol by several name variants (symbolName + displayed name),
    then by normalized tokens; fall back to KiCad stock power lib.
    """
    ordered: List[str] = []

    # direct variants from OrCAD fields
    for raw in (symbol_name, name):
        for v in _name_variants(raw or ""):
            if v and v not in ordered:
                ordered.append(v)

    # normalized tokens (e.g., VCC_ARROW → VCC)
    for raw in (symbol_name, name):
        norm = _normalize_power_candidate(raw or "")
        for v in _name_variants(norm):
            if v and v not in ordered:
                ordered.append(v)

    # search converted libs
    for cand in ordered:
        if _converted_has_symbol(conv_root, combined_path, cand):
            value_txt = name.strip() if (name and name.strip()) else cand
            print(f"[map] POWER: using converted → {converted_lib}:{cand}")
            return f"{converted_lib}:{cand}", value_txt

    # Fallback to stock power mapping
    lib_id, value_txt = choose_power_lib(symbol_name, name)
    print(f"[map] POWER: using stock → {lib_id}")
    return lib_id, value_txt

# write schematic
def write_schematic(rdr: OrCadReader, outfile: Path, sys_root: Path,
                    conv_root: Optional[Path], combined_path: Optional[Path], converted_lib: str):
    comps = rdr.components()
    wires, juncs = rdr.nets()
    pwr_syms = rdr.power_globals()

    # pick lib_id per component (using cell/value with existence checks)
    for c in comps:
        c['lib'] = choose_lib_id(c['ref'], c['val'], c['pins'], c['cell'],
                                 sys_root, conv_root, combined_path, converted_lib)

    # pick lib_id for power symbols + assign properties
    pwr_ref_base = 1000
    for i, p in enumerate(pwr_syms, start=1):
        lib_id, val_txt = choose_power_preferring_converted(
            p['symbol'], p['name'], conv_root, combined_path, converted_lib
        )
        p['lib'] = lib_id
        p['props'] = OrderedDict(Reference=f"#PWR{pwr_ref_base + i}", Value=val_txt)

    # embed used symbol defs (components + power symbols)
    placed_ids = {c['lib'] for c in comps} | {p['lib'] for p in pwr_syms}
    embedded, done = [], set()
    for lid in placed_ids:
        if ":" in lid:
            collect(sys_root, conv_root, combined_path, converted_lib, lid, placed_ids, done, embedded)

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

        # Add tiny fallbacks for any still-missing symbol libs
        unresolved = [lid for lid in placed_ids if lid not in _symbol_blocks]
        for lid in unresolved:
            fbname = "Fallback_" + lid.split(":")[-1]
            print(f"[warn] No symbol found for {lid}; embedding {fbname}")
            fp.write(fallback_block(
                {"name": fbname, "pins": [
                    {"typ": "passive", "at": (-2.54, 0), "name": "1", "num": "1"},
                    {"typ": "passive", "at": ( 2.54, 0), "name": "2", "num": "2"},
                ]}, '    '))
        fp.write('  )\n')

        # Place normal components
        for c in comps:
            fp.write(inst_block(c))

        # Place power symbols
        for p in pwr_syms:
            fp.write(inst_block(p))

        # Nets & junctions
        for w in wires:
            fp.write(f"  (wire (pts {xy(w[0])} {xy(w[1])}) (stroke (width 0)))\n")
        for j in juncs:
            fp.write(f"  (junction (at {num(j[0])} {num(j[1])}) (diameter 0))\n")
        fp.write(')\n')


def main():
    ap = argparse.ArgumentParser(description='Convert OrCAD XML → KiCad-9 schematic (prefer converted symbols first, incl. power)')
    ap.add_argument('--xml', required=True, help='Input OrCAD *.xml')
    ap.add_argument('--out', required=True, help='Output *.kicad_sch (extension added if missing)')
    ap.add_argument('--symdir', default='/usr/share/kicad/symbols', help='Path to stock *.kicad_sym directory')
    ap.add_argument('--converted-dir', default=None, help='Directory with converted *.kicad_sym (per-file and/or combined)')
    ap.add_argument('--converted-lib-file', default=None, help='Explicit combined *.kicad_sym file (e.g., converted_sch.kicad_sym)')
    ap.add_argument('--converted-lib', default='converted', help='Library name to use for converted symbols (lib_id prefix)')
    args = ap.parse_args()

    xin = Path(args.xml)
    if not xin.is_file():
        sys.exit(f'[error] {xin} not found')

    xout = Path(args.out).with_suffix('.kicad_sch')
    sys_root = Path(args.symdir)

    conv_root = Path(args.converted_dir).resolve() if args.converted_dir else None
    conv_lib_file = Path(args.converted_lib_file).resolve() if args.converted_lib_file else None
    combined_path = _locate_combined_library(conv_root, conv_lib_file, args.converted_lib)

    if conv_lib_file and not conv_lib_file.is_file():
        sys.exit(f'[error] Combined library file not found: {conv_lib_file}')
    if args.converted_dir and conv_root and not conv_root.is_dir():
        sys.exit(f'[error] Converted directory not found: {conv_root}')

    write_schematic(OrCadReader(xin), xout, sys_root, conv_root, combined_path, args.converted_lib)
    print(f'✓ Wrote {xout}')

if __name__ == '__main__':
    main()
