#!/usr/bin/env python3
"""
convert_capsym_log.py – Convert OpenOrCadParser CAPSYM *.log → KiCad .kicad_sym

Typical usage with your batch runner:
  python3 run_all_logs.py "/path/to/logs/capsym.olb/Symbols" \
    --scale 0.254 \
    --convert-script /path/to/convert_capsym_log.py \
    --out-lib /path/to/converted/converted_capsym.kicad_sym

Direct usage:
  python3 convert_capsym_log.py GND.log --scale 0.254
  python3 convert_capsym_log.py VCC_ARROW.log --scale 0.254 --lib power_capsym.kicad_sym
"""

import argparse, re, sys, math, hashlib
from pathlib import Path

# ───────────────── helpers ─────────────────
def angle_from_vec(vx, vy, eps=1e-6):
    if abs(vx) <= eps: vx = 0.0
    if abs(vy) <= eps: vy = 0.0
    if vx == 0.0 and vy == 0.0: return None
    if vy == 0.0: return 0 if vx > 0 else 180
    if vx == 0.0: return 90 if vy > 0 else 270
    if abs(vx) >= abs(vy): return 0 if vx > 0 else 180
    return 90 if vy > 0 else 270

def knum(x):
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"

def make_safe_name(raw: str, fallback: str) -> str:
    """Sanitize symbol name for KiCad; ensure non-empty, printable, no quotes/spaces-only."""
    if raw is None:
        raw = ""
    raw = raw.strip().strip('"').strip("'")
    # replace spaces with underscores, drop forbidden parens/quotes
    safe = re.sub(r'[^A-Za-z0-9_.+\- ]+', "_", raw).replace(" ", "_")
    if not safe:
        fb = fallback.strip().strip('"').strip("'").replace(" ", "_")
        if not fb:
            # deterministic tiny hash so multiple empties don't collide
            fb = "SYM_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        safe = fb
    # KiCad dislikes leading dots
    safe = safe.lstrip(".")
    if not safe:
        safe = "SYM_" + hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:8]
    return safe

def polyline_block(pts, stroke_mm, sp="      "):
    pts_s = " ".join(f"(xy {knum(x)} {knum(y)})" for x, y in pts)
    # KiCad requires (stroke (width ..) (type ..)) and (fill (type ..))
    return (
        f'{sp}(polyline (pts {pts_s}) '
        f'(stroke (width {knum(stroke_mm)}) (type default)) '
        f'(fill (type none)))\n'
    )

def heuristic_value_and_angle(name):
    n = (name or "").upper()
    if "GND" in n: return ("GND", 90)          # pin UP into symbol
    if n.startswith("+") or n.startswith("-"): return (name, 270)  # rails from top → pin DOWN
    for k in ("VCC","VDD","VSS","VDDA","VSSA","VEE"):
        if k in n: return (k, 270)
    return (name, 90)

# ───────────── parser (tolerant) ───────────
def parse_capsym_log(path: Path):
    """
    Returns dict with:
      name: str
      segs: [(x1,y1,x2,y2), ...]
      pin:  dict or None with keys startX,startY,hotptX,hotptY and flags
    """
    lines = path.read_text(errors="ignore").splitlines()

    # name: prefer 'normalName = XYZ' else 'name = XYZ' else file stem
    raw_name = None
    for ln in lines:
        m = re.search(r'\bnormalName\s*=\s*(.+)', ln)
        if m:
            raw_name = m.group(1).strip()
            break
        m = re.search(r'\bname\s*=\s*(.+)', ln)
        if m:
            raw_name = m.group(1).strip()
    name = make_safe_name(raw_name, path.stem)

    segs = []
    pin  = None

    # states for primitive groups
    reading = None  # 'line'|'rect'|'ellipse'
    rect = {"x1":None,"y1":None,"x2":None,"y2":None}
    ell  = {"x1":None,"y1":None,"x2":None,"y2":None}
    line = {"x1":None,"y1":None,"x2":None,"y2":None}

    def flush_rect():
        if None in rect.values(): return
        x1,y1,x2,y2 = rect["x1"],rect["y1"],rect["x2"],rect["y2"]
        if x1==x2 or y1==y2: return
        segs.extend([(x1,y1,x2,y1),(x2,y1,x2,y2),(x2,y2,x1,y2),(x1,y2,x1,y1)])

    def flush_ellipse(ellipse_sides=36):
        if None in ell.values(): return
        x1,y1,x2,y2 = ell["x1"],ell["y1"],ell["x2"],ell["y2"]
        cx = (x1+x2)/2.0; cy = (y1+y2)/2.0
        rx = abs(x2-x1)/2.0; ry = abs(y2-y1)/2.0
        if rx<=0 or ry<=0: return
        sides = max(8, ellipse_sides)
        pts=[]
        for i in range(sides):
            t = 2*math.pi*i/sides
            pts.append((cx+rx*math.cos(t), cy+ry*math.sin(t)))
        for a,b in zip(pts, pts[1:]+[pts[0]]):
            segs.append((a[0],a[1],b[0],b[1]))

    for ln in lines:
        s = ln.strip()

        # start of primitives (be liberal with token names)
        if "PrimLine" in s or re.search(r'\bLine\b', s):
            reading = "line"; line = {"x1":None,"y1":None,"x2":None,"y2":None}; continue
        if "PrimRect" in s or re.search(r'\bRect\b', s):
            reading = "rect"; rect = {"x1":None,"y1":None,"x2":None,"y2":None}; continue
        if "PrimEllipse" in s or re.search(r'\bEllipse\b', s):
            reading = "ellipse"; ell = {"x1":None,"y1":None,"x2":None,"y2":None}; continue

        # numeric coords capture
        m = re.match(r'(x1|y1|x2|y2)\s*=\s*(-?\d+(\.\d+)?)', s)
        if m and reading:
            key = m.group(1); val = float(m.group(2))
            if reading == "line":
                line[key] = val
                if all(v is not None for v in line.values()):
                    segs.append((line["x1"],line["y1"],line["x2"],line["y2"]))
                    reading=None
            elif reading == "rect":
                rect[key] = val
            elif reading == "ellipse":
                ell[key] = val
            continue

        # primitive end markers (some logs have explicit endings)
        if "Ending OOCP::PrimRect::read" in s:
            flush_rect(); reading=None; continue
        if "Ending OOCP::PrimEllipse::read" in s:
            flush_ellipse(); reading=None; continue
        if reading=="rect" and s.startswith("[debug]"):
            flush_rect(); reading=None; continue
        if reading=="ellipse" and s.startswith("[debug]"):
            flush_ellipse(); reading=None; continue

        # pin bucket (one pin for globals)
        if "StructSymbolPin" in s or "SymbolPinScalar" in s:
            pin = {}
            continue
        if pin is not None:
            m = re.match(r'(startX|startY|hotptX|hotptY|isLeftPointing|isRightPointing|isUpPointing|isDownPointing|isClock)\s*=\s*([-\w.]+)', s)
            if m:
                k,v = m.group(1), m.group(2)
                if k in ("startX","startY","hotptX","hotptY"):
                    try: pin[k] = float(v)
                    except: pass
                else:
                    pin[k] = v.lower() in ("1","true","yes")

    # if rect/ellipse not closed by explicit marker, flush now
    flush_rect(); flush_ellipse()

    # small dedup of identical segments (direction-agnostic)
    def skey(sg):
        x1,y1,x2,y2 = sg
        a=(round(x1,6),round(y1,6)); b=(round(x2,6),round(y2,6))
        return tuple(sorted((a,b)))
    uniq = {}
    for sg in segs:
        uniq[skey(sg)] = sg
    segs = list(uniq.values())

    return {"name": name, "segs": segs, "pin": pin}

# ───────────── build KiCad block ───────────
def build_symbol(sym, scale=0.254, mirror_x=False, mirror_y=False, stroke_mm=0.10):
    name = make_safe_name(sym["name"].replace(".Normal","").replace(".","_"), "SYM")
    segs = sym["segs"]
    p = sym["pin"] or {}

    # anchor = OrCAD hot-point → place at (0,0), translate graphics by -hot
    hx = float(p.get("hotptX", p.get("startX", 0.0)))
    hy = float(p.get("hotptY", p.get("startY", 0.0)))

    def TX(x,y):
        x = (x - hx) * scale
        y = (y - hy) * scale
        if mirror_x: x = -x
        if mirror_y: y = -y
        return (x,y)

    # determine pin angle
    ang = None
    sx = p.get("startX"); sy = p.get("startY")
    if sx is not None and sy is not None and ("hotptX" in p or "hotptY" in p):
        ang = angle_from_vec(sx - hx, sy - hy, eps=1e-6)
    if ang is None:
        _, ang = heuristic_value_and_angle(name)
    if mirror_x: ang = (180 - ang) % 360
    if mirror_y: ang = (-ang) % 360

    # graphics unit
    unit = [f'  (symbol "{name}_1_1"\n']
    for x1,y1,x2,y2 in segs:
        unit.append(polyline_block([TX(x1,y1), TX(x2,y2)], stroke_mm))
    unit.append("  )\n")

    # properties
    val_txt, _ = heuristic_value_and_angle(name)
    props = [
        f'  (property "Reference" "#PWR" (at 0 {knum(2.54*scale/0.254)} 0) (effects (font (size 1.27 1.27))))\n',
        f'  (property "Value" "{val_txt}" (at 0 {knum(-2.54*scale/0.254)} 0) (effects (font (size 1.27 1.27))))\n',
    ]

    # pin at origin
    pin = (
        f'  (pin power_in line (at 0 0 {ang}) (length 0)\n'
        f'    (name "" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))\n'
    )

    # block
    out  = f'(symbol "{name}"\n'
    out += '  (pin_numbers hide) (pin_names (offset 0)) (in_bom no) (on_board no)\n'
    out += "".join(props)
    out += "".join(unit)
    out += pin
    out += ')\n'
    return out, name

def write_library(blocks, dest: Path):
    dest.write_text(
        "(kicad_symbol_lib\n"
        "  (version 20240205)\n"
        "  (generator convert_capsym_log)\n"
        + "\n".join(blocks) + "\n)",
        encoding="utf-8"
    )

def append_symbol_to_lib(lib: Path, block: str, sym_name: str):
    """Append block to an existing .kicad_sym; replace any prior symbol with same name."""
    if not lib.exists():
        write_library([block], lib)
        return

    txt = lib.read_text(encoding="utf-8")

    # Drop existing symbol with same name (top-level only).
    # Matches: newline + (symbol "NAME" ... ) up to the next top-level '(' or file end before final ')'
    pat = re.compile(rf'(\n\s*\(symbol\s+"{re.escape(sym_name)}"[\s\S]*?\)\s*)(?=\n\(|\n\))')
    txt = pat.sub("\n", txt)

    # Ensure file ends with a single ')' and append before it.
    m = re.search(r'\)\s*$', txt)
    if not m:
        # malformed library — regenerate
        write_library([block], lib)
        return

    new = txt[:m.start()] + "\n" + block + ")\n"
    lib.write_text(new, encoding="utf-8")

# ───────────────────── CLI ─────────────────
def main():
    pa = argparse.ArgumentParser(description="Convert CAPSYM OpenOrCadParser *.log → KiCad .kicad_sym")
    pa.add_argument("logfile")
    pa.add_argument("--scale", type=float, default=0.254, help="Coordinate scale (mil→mm = 0.254)")
    pa.add_argument("--mirror-x", action="store_true", help="Mirror X (left↔right)")
    pa.add_argument("--mirror-y", action="store_true", help="Mirror Y (up↔down)")
    pa.add_argument("--stroke-mm", type=float, default=0.10, help="Stroke width for graphics (mm)")
    pa.add_argument("--lib", help="Append/create this .kicad_sym instead of per-symbol files")
    args = pa.parse_args()

    log = Path(args.logfile).expanduser().resolve()
    if not log.is_file():
        sys.exit(f"Log not found: {log}")

    sym = parse_capsym_log(log)
    if not sym["segs"] and sym["pin"] is None:
        sys.exit("No drawable symbol found")

    block, safe = build_symbol(
        sym, scale=args.scale, mirror_x=args.mirror_x, mirror_y=args.mirror_y, stroke_mm=args.stroke_mm
    )

    if args.lib:
        lib = Path(args.lib).expanduser().resolve()
        append_symbol_to_lib(lib, block, safe)
        print(f'Wrote 1 symbol → {lib}')
    else:
        out = log.with_suffix("").with_name(f"{safe}.kicad_sym")
        write_library([block], out)
        print(f"Wrote {out}")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
convert_capsym_log.py – Convert OpenOrCadParser CAPSYM *.log → KiCad .kicad_sym

Typical usage with your batch runner:
  python3 run_all_logs.py "/path/to/logs/capsym.olb/Symbols" \
    --scale 0.254 \
    --convert-script /path/to/convert_capsym_log.py \
    --out-lib /path/to/converted/converted_capsym.kicad_sym

Direct usage:
  python3 convert_capsym_log.py GND.log --scale 0.254
  python3 convert_capsym_log.py VCC_ARROW.log --scale 0.254 --lib power_capsym.kicad_sym
"""

import argparse, re, sys, math, hashlib
from pathlib import Path


def angle_from_vec(vx, vy, eps=1e-6):
    if abs(vx) <= eps: vx = 0.0
    if abs(vy) <= eps: vy = 0.0
    if vx == 0.0 and vy == 0.0: return None
    if vy == 0.0: return 0 if vx > 0 else 180
    if vx == 0.0: return 90 if vy > 0 else 270
    if abs(vx) >= abs(vy): return 0 if vx > 0 else 180
    return 90 if vy > 0 else 270

def knum(x):
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"

def make_safe_name(raw: str, fallback: str) -> str:
    """Sanitize symbol name for KiCad; ensure non-empty, printable, no quotes/spaces-only."""
    if raw is None:
        raw = ""
    raw = raw.strip().strip('"').strip("'")
    # replace spaces with underscores, drop forbidden parens/quotes
    safe = re.sub(r'[^A-Za-z0-9_.+\- ]+', "_", raw).replace(" ", "_")
    if not safe:
        fb = fallback.strip().strip('"').strip("'").replace(" ", "_")
        if not fb:
            # deterministic tiny hash so multiple empties don't collide
            fb = "SYM_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        safe = fb
    # KiCad dislikes leading dots
    safe = safe.lstrip(".")
    if not safe:
        safe = "SYM_" + hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:8]
    return safe

def polyline_block(pts, stroke_mm, sp="      "):
    pts_s = " ".join(f"(xy {knum(x)} {knum(y)})" for x, y in pts)
    # KiCad requires (stroke (width ..) (type ..)) and (fill (type ..))
    return (
        f'{sp}(polyline (pts {pts_s}) '
        f'(stroke (width {knum(stroke_mm)}) (type default)) '
        f'(fill (type none)))\n'
    )

def heuristic_value_and_angle(name):
    n = (name or "").upper()
    if "GND" in n: return ("GND", 90)          # pin UP into symbol
    if n.startswith("+") or n.startswith("-"): return (name, 270)  # rails from top → pin DOWN
    for k in ("VCC","VDD","VSS","VDDA","VSSA","VEE"):
        if k in n: return (k, 270)
    return (name, 90)

def parse_capsym_log(path: Path):
    """
    Returns dict with:
      name: str
      segs: [(x1,y1,x2,y2), ...]
      pin:  dict or None with keys startX,startY,hotptX,hotptY and flags
    """
    lines = path.read_text(errors="ignore").splitlines()

    # name: prefer 'normalName = XYZ' else 'name = XYZ' else file stem
    raw_name = None
    for ln in lines:
        m = re.search(r'\bnormalName\s*=\s*(.+)', ln)
        if m:
            raw_name = m.group(1).strip()
            break
        m = re.search(r'\bname\s*=\s*(.+)', ln)
        if m:
            raw_name = m.group(1).strip()
    name = make_safe_name(raw_name, path.stem)

    segs = []
    pin  = None

    # states for primitive groups
    reading = None  # 'line'|'rect'|'ellipse'
    rect = {"x1":None,"y1":None,"x2":None,"y2":None}
    ell  = {"x1":None,"y1":None,"x2":None,"y2":None}
    line = {"x1":None,"y1":None,"x2":None,"y2":None}

    def flush_rect():
        if None in rect.values(): return
        x1,y1,x2,y2 = rect["x1"],rect["y1"],rect["x2"],rect["y2"]
        if x1==x2 or y1==y2: return
        segs.extend([(x1,y1,x2,y1),(x2,y1,x2,y2),(x2,y2,x1,y2),(x1,y2,x1,y1)])

    def flush_ellipse(ellipse_sides=36):
        if None in ell.values(): return
        x1,y1,x2,y2 = ell["x1"],ell["y1"],ell["x2"],ell["y2"]
        cx = (x1+x2)/2.0; cy = (y1+y2)/2.0
        rx = abs(x2-x1)/2.0; ry = abs(y2-y1)/2.0
        if rx<=0 or ry<=0: return
        sides = max(8, ellipse_sides)
        pts=[]
        for i in range(sides):
            t = 2*math.pi*i/sides
            pts.append((cx+rx*math.cos(t), cy+ry*math.sin(t)))
        for a,b in zip(pts, pts[1:]+[pts[0]]):
            segs.append((a[0],a[1],b[0],b[1]))

    for ln in lines:
        s = ln.strip()

        # start of primitives (be liberal with token names)
        if "PrimLine" in s or re.search(r'\bLine\b', s):
            reading = "line"; line = {"x1":None,"y1":None,"x2":None,"y2":None}; continue
        if "PrimRect" in s or re.search(r'\bRect\b', s):
            reading = "rect"; rect = {"x1":None,"y1":None,"x2":None,"y2":None}; continue
        if "PrimEllipse" in s or re.search(r'\bEllipse\b', s):
            reading = "ellipse"; ell = {"x1":None,"y1":None,"x2":None,"y2":None}; continue

        # numeric coords capture
        m = re.match(r'(x1|y1|x2|y2)\s*=\s*(-?\d+(\.\d+)?)', s)
        if m and reading:
            key = m.group(1); val = float(m.group(2))
            if reading == "line":
                line[key] = val
                if all(v is not None for v in line.values()):
                    segs.append((line["x1"],line["y1"],line["x2"],line["y2"]))
                    reading=None
            elif reading == "rect":
                rect[key] = val
            elif reading == "ellipse":
                ell[key] = val
            continue

        # primitive end markers (some logs have explicit endings)
        if "Ending OOCP::PrimRect::read" in s:
            flush_rect(); reading=None; continue
        if "Ending OOCP::PrimEllipse::read" in s:
            flush_ellipse(); reading=None; continue
        if reading=="rect" and s.startswith("[debug]"):
            flush_rect(); reading=None; continue
        if reading=="ellipse" and s.startswith("[debug]"):
            flush_ellipse(); reading=None; continue

        # pin bucket (one pin for globals)
        if "StructSymbolPin" in s or "SymbolPinScalar" in s:
            pin = {}
            continue
        if pin is not None:
            m = re.match(r'(startX|startY|hotptX|hotptY|isLeftPointing|isRightPointing|isUpPointing|isDownPointing|isClock)\s*=\s*([-\w.]+)', s)
            if m:
                k,v = m.group(1), m.group(2)
                if k in ("startX","startY","hotptX","hotptY"):
                    try: pin[k] = float(v)
                    except: pass
                else:
                    pin[k] = v.lower() in ("1","true","yes")

    # if rect/ellipse not closed by explicit marker, flush now
    flush_rect(); flush_ellipse()

    # small dedup of identical segments (direction-agnostic)
    def skey(sg):
        x1,y1,x2,y2 = sg
        a=(round(x1,6),round(y1,6)); b=(round(x2,6),round(y2,6))
        return tuple(sorted((a,b)))
    uniq = {}
    for sg in segs:
        uniq[skey(sg)] = sg
    segs = list(uniq.values())

    return {"name": name, "segs": segs, "pin": pin}

def build_symbol(sym, scale=0.254, mirror_x=False, mirror_y=False, stroke_mm=0.10):
    name = make_safe_name(sym["name"].replace(".Normal","").replace(".","_"), "SYM")
    segs = sym["segs"]
    p = sym["pin"] or {}

    # anchor = OrCAD hot-point → place at (0,0), translate graphics by -hot
    hx = float(p.get("hotptX", p.get("startX", 0.0)))
    hy = float(p.get("hotptY", p.get("startY", 0.0)))

    def TX(x,y):
        x = (x - hx) * scale
        y = (y - hy) * scale
        if mirror_x: x = -x
        if mirror_y: y = -y
        return (x,y)

    # determine pin angle
    ang = None
    sx = p.get("startX"); sy = p.get("startY")
    if sx is not None and sy is not None and ("hotptX" in p or "hotptY" in p):
        ang = angle_from_vec(sx - hx, sy - hy, eps=1e-6)
    if ang is None:
        _, ang = heuristic_value_and_angle(name)
    if mirror_x: ang = (180 - ang) % 360
    if mirror_y: ang = (-ang) % 360

    # graphics unit
    unit = [f'  (symbol "{name}_1_1"\n']
    for x1,y1,x2,y2 in segs:
        unit.append(polyline_block([TX(x1,y1), TX(x2,y2)], stroke_mm))
    unit.append("  )\n")

    # properties
    val_txt, _ = heuristic_value_and_angle(name)
    props = [
        f'  (property "Reference" "#PWR" (at 0 {knum(2.54*scale/0.254)} 0) (effects (font (size 1.27 1.27))))\n',
        f'  (property "Value" "{val_txt}" (at 0 {knum(-2.54*scale/0.254)} 0) (effects (font (size 1.27 1.27))))\n',
    ]

    # pin at origin
    pin = (
        f'  (pin power_in line (at 0 0 {ang}) (length 0)\n'
        f'    (name "" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))\n'
    )

    # block
    out  = f'(symbol "{name}"\n'
    out += '  (pin_numbers hide) (pin_names (offset 0)) (in_bom no) (on_board no)\n'
    out += "".join(props)
    out += "".join(unit)
    out += pin
    out += ')\n'
    return out, name

def write_library(blocks, dest: Path):
    dest.write_text(
        "(kicad_symbol_lib\n"
        "  (version 20240205)\n"
        "  (generator convert_capsym_log)\n"
        + "\n".join(blocks) + "\n)",
        encoding="utf-8"
    )

def append_symbol_to_lib(lib: Path, block: str, sym_name: str):
    """Append block to an existing .kicad_sym; replace any prior symbol with same name."""
    if not lib.exists():
        write_library([block], lib)
        return

    txt = lib.read_text(encoding="utf-8")

    # Drop existing symbol with same name (top-level only).
    # Matches: newline + (symbol "NAME" ... ) up to the next top-level '(' or file end before final ')'
    pat = re.compile(rf'(\n\s*\(symbol\s+"{re.escape(sym_name)}"[\s\S]*?\)\s*)(?=\n\(|\n\))')
    txt = pat.sub("\n", txt)

    # Ensure file ends with a single ')' and append before it.
    m = re.search(r'\)\s*$', txt)
    if not m:
        # malformed library — regenerate
        write_library([block], lib)
        return

    new = txt[:m.start()] + "\n" + block + ")\n"
    lib.write_text(new, encoding="utf-8")

def main():
    pa = argparse.ArgumentParser(description="Convert CAPSYM OpenOrCadParser *.log → KiCad .kicad_sym")
    pa.add_argument("logfile")
    pa.add_argument("--scale", type=float, default=0.254, help="Coordinate scale (mil→mm = 0.254)")
    pa.add_argument("--mirror-x", action="store_true", help="Mirror X (left↔right)")
    pa.add_argument("--mirror-y", action="store_true", help="Mirror Y (up↔down)")
    pa.add_argument("--stroke-mm", type=float, default=0.10, help="Stroke width for graphics (mm)")
    pa.add_argument("--lib", help="Append/create this .kicad_sym instead of per-symbol files")
    args = pa.parse_args()

    log = Path(args.logfile).expanduser().resolve()
    if not log.is_file():
        sys.exit(f"Log not found: {log}")

    sym = parse_capsym_log(log)
    if not sym["segs"] and sym["pin"] is None:
        sys.exit("No drawable symbol found")

    block, safe = build_symbol(
        sym, scale=args.scale, mirror_x=args.mirror_x, mirror_y=args.mirror_y, stroke_mm=args.stroke_mm
    )

    if args.lib:
        lib = Path(args.lib).expanduser().resolve()
        append_symbol_to_lib(lib, block, safe)
        print(f'Wrote 1 symbol → {lib}')
    else:
        out = log.with_suffix("").with_name(f"{safe}.kicad_sym")
        write_library([block], out)
        print(f"Wrote {out}")

if __name__ == "__main__":
    main()
