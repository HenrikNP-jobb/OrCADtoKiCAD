#!/usr/bin/env python3
"""
convert_log.py – OpenOrCadParser *.log ➜ KiCad 7/8 .kicad_sym

• One file per symbol (default)          →  python3 convert_log.py E.log
• Append / create a library              →  python3 convert_log.py E.log --lib parts.kicad_sym
• Scale coordinates (mil→mm)             →  python3 convert_log.py E.log --scale 0.254
• Tolerant joining of small gaps         →  python3 convert_log.py E.log --join-eps 1
• Control stitching behavior             →  python3 convert_log.py E.log --stitch auto|on|off
• Optional X-mirror (left↔right)         →  python3 convert_log.py E.log --mirror-x
• Optional Y-mirror (up↔down)            →  python3 convert_log.py E.log --mirror-y
• Set outline stroke width (mm)          →  python3 convert_log.py E.log --stroke-mm 0.254
"""

import re, sys, argparse, math
from pathlib import Path
from collections import OrderedDict

def dedup(seq, key=lambda x: x):
    seen = set()
    for x in seq:
        k = key(x)
        if k not in seen:
            seen.add(k)
            yield x

def angle_from_vec(vx, vy, eps=1e-6):
    """Map a vector to KiCad angles (into-body): 0→, 90↑, 180←, 270↓.
    Returns None if the vector is (near) zero.
    """
    # snap components to axis to avoid tiny-diagonal flips
    if abs(vx) <= eps: vx = 0.0
    if abs(vy) <= eps: vy = 0.0
    if vx == 0.0 and vy == 0.0:
        return None
    if vy == 0.0:                   # horizontal
        return 0 if vx > 0 else 180
    if vx == 0.0:                   # vertical
        return 90 if vy > 0 else 270
    # slightly diagonal → dominant axis
    if abs(vx) >= abs(vy):
        return 0 if vx > 0 else 180
    else:
        return 90 if vy > 0 else 270

def pin_angle(pin, eps=1e-6):
    """Return KiCad pin angle (0→, 90↑, 180←, 270↓), INTO the body.
    Vector is HOT → START (i.e. start - hot).
    """
    sx = float(pin.get('startX', 0.0))
    sy = float(pin.get('startY', 0.0))
    hx = float(pin.get('hotptX', sx))
    hy = float(pin.get('hotptY', sy))

    # Explicit flags win if present
    if pin.get('isRightPointing') == 'true': return 0
    if pin.get('isLeftPointing')  == 'true': return 180
    if pin.get('isUpPointing')    == 'true': return 90
    if pin.get('isDownPointing')  == 'true' or pin.get('isClock') == 'true': return 270

    vx, vy = (sx - hx), (sy - hy)   # into-body vector
    ang = angle_from_vec(vx, vy, eps=eps)
    return 0 if ang is None else ang  # harmless fallback

# tolerant geometry utilities
def snap(v, eps):
    return float(round(v / eps) * eps) if eps > 0 else float(v)

def snap_pt(pt, eps):
    x, y = pt
    return (snap(x, eps), snap(y, eps))

def points_close(a, b, eps):
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps

def seg_len(s):
    x1, y1, x2, y2 = s
    return math.hypot(x2 - x1, y2 - y1)

def seg_key(s, prec=6):
    """Canonical key for dedup after snapping; ignores direction."""
    x1, y1, x2, y2 = s
    a = (round(x1, prec), round(y1, prec))
    b = (round(x2, prec), round(y2, prec))
    return tuple(sorted((a, b)))

def preprocess_segments(segs, eps, min_len=0.0):
    """Snap endpoints to an epsilon grid, drop tiny/duplicate segments."""
    out = []
    for x1, y1, x2, y2 in segs:
        a = snap_pt((float(x1), float(y1)), eps) if eps > 0 else (float(x1), float(y1))
        b = snap_pt((float(x2), float(y2)), eps) if eps > 0 else (float(x2), float(y2))
        s = (a[0], a[1], b[0], b[1])
        if min_len > 0 and seg_len(s) < min_len:
            continue
        out.append(s)
    return list(dedup(out, key=seg_key))  # direction-agnostic dedup

def stitch_all_tolerant(segs, eps):
    """Partition segments into multiple continuous polylines with tolerant endpoint matching."""
    if not segs:
        return []

    unused = segs[:]
    polys = []

    def pop_any():
        s = unused.pop()
        return [(s[0], s[1]), (s[2], s[3])]

    while unused:
        pts = pop_any()
        grown = True
        while grown:
            grown = False
            i = 0
            while i < len(unused):
                x1, y1, x2, y2 = unused[i]
                a, b = (x1, y1), (x2, y2)
                if points_close(a, pts[-1], eps):
                    pts.append(b); unused.pop(i); grown = True; continue
                if points_close(b, pts[-1], eps):
                    pts.append(a); unused.pop(i); grown = True; continue
                if points_close(a, pts[0], eps):
                    pts.insert(0, b); unused.pop(i); grown = True; continue
                if points_close(b, pts[0], eps):
                    pts.insert(0, a); unused.pop(i); grown = True; continue
                i += 1
        polys.append(pts)
    return polys

def poly_suspicious(pts):
    if len(pts) < 2:
        return True
    unique = set(pts)
    if len(unique) < 2:
        return True
    repeat_ratio = 1.0 - (len(unique) / len(pts))
    return repeat_ratio > 0.5

def infer_angle_from_segs(start_xy, segs, eps):
    """Try to deduce into-body direction from a segment that touches start.
    Returns an angle or None if nothing attaches.
    """
    sx, sy = start_xy
    best = None
    best_len = -1.0
    for x1, y1, x2, y2 in segs:
        a = (x1, y1); b = (x2, y2)
        if points_close(a, (sx, sy), eps):
            vx, vy = (b[0] - sx), (b[1] - sy)
        elif points_close(b, (sx, sy), eps):
            vx, vy = (a[0] - sx), (a[1] - sy)
        else:
            continue
        ang = angle_from_vec(vx, vy, eps=max(1e-6, 0.5*eps))
        if ang is None:
            continue
        L = math.hypot(vx, vy)
        if L > best_len:
            best_len = L
            best = ang
    return best  # may be None

# parse OOCP log
def parse_log(path, ellipse_sides=36):
    lines = Path(path).read_text(errors='ignore').splitlines()
    pool, cur = OrderedDict(), None
    seg_state = None        # for PrimLine
    rect_state = None       # for PrimRect
    ell_state = None        # for PrimEllipse

    def flush_rect():
        nonlocal rect_state
        if rect_state and all(rect_state.get(k) is not None for k in ('x1','y1','x2','y2')):
            x1, y1, x2, y2 = rect_state['x1'], rect_state['y1'], rect_state['x2'], rect_state['y2']
            # add the 4 edges
            cur['segments'].extend([
                (x1, y1, x2, y1),
                (x2, y1, x2, y2),
                (x2, y2, x1, y2),
                (x1, y2, x1, y1),
            ])
        rect_state = None

    def flush_ellipse():
        nonlocal ell_state
        if ell_state and all(ell_state.get(k) is not None for k in ('x1','y1','x2','y2')):
            x1, y1, x2, y2 = map(float, (ell_state['x1'], ell_state['y1'], ell_state['x2'], ell_state['y2']))
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            rx = abs(x2 - x1) / 2.0
            ry = abs(y2 - y1) / 2.0
            if rx > 0 and ry > 0 and ellipse_sides >= 8:
                pts = []
                for i in range(ellipse_sides):
                    t = 2.0 * math.pi * i / ellipse_sides
                    pts.append((cx + rx * math.cos(t), cy + ry * math.sin(t)))
                # close ring
                for a, b in zip(pts, pts[1:] + [pts[0]]):
                    cur['segments'].append((a[0], a[1], b[0], b[1]))
        ell_state = None

    for ln in lines:
        # new symbol bucket
        m = re.search(r'\bnormalName\s*=\s*([A-Za-z0-9_.-]+)', ln)
        if m:
            cur = pool.setdefault(
                m.group(1),
                {'name': m.group(1), 'pins': [], 'segments': [], 'props': {}}
            )
            seg_state = rect_state = ell_state = None
            continue
        if cur is None:
            continue

        # pin start
        if "OOCP::StructSymbolPin" in ln:
            cur['pins'].append({})
            seg_state = rect_state = ell_state = None
            continue

        # pin attributes
        if cur['pins']:
            mm = re.match(r'\s*(\w+)\s*=\s*(.+)', ln)
            if mm and mm.group(1) in (
                'name', 'startX', 'startY', 'hotptX', 'hotptY',
                'isLeftPointing', 'isRightPointing', 'isClock',
                'isUpPointing', 'isDownPointing'
            ):
                cur['pins'][-1][mm.group(1)] = mm.group(2).strip()

        # graphic primitives begin
        if "OOCP::PrimLine" in ln:
            seg_state = {'x1': None, 'y1': None, 'x2': None, 'y2': None}
            rect_state = ell_state = None
            continue
        if "OOCP::PrimRect" in ln:
            rect_state = {'x1': None, 'y1': None, 'x2': None, 'y2': None}
            seg_state = ell_state = None
            continue
        if "OOCP::PrimEllipse" in ln:
            ell_state = {'x1': None, 'y1': None, 'x2': None, 'y2': None}
            seg_state = rect_state = None
            continue

        # collect PrimLine coords
        if seg_state is not None:
            mm = re.match(r'\s*(x1|y1|x2|y2)\s*=\s*(-?\d+)', ln)
            if mm:
                seg_state[mm.group(1)] = int(mm.group(2))
                if all(v is not None for v in seg_state.values()):
                    cur['segments'].append((
                        seg_state['x1'], seg_state['y1'],
                        seg_state['x2'], seg_state['y2']
                    ))
                    seg_state = None
            elif ln.startswith('['):   # next trace block
                seg_state = None

        # collect PrimRect coords
        if rect_state is not None:
            mm = re.match(r'\s*(x1|y1|x2|y2)\s*=\s*(-?\d+)', ln)
            if mm:
                rect_state[mm.group(1)] = int(mm.group(2))
            elif "Ending OOCP::PrimRect::read" in ln or ln.startswith('[debug] 0x'):
                flush_rect()

        # collect PrimEllipse coords
        if ell_state is not None:
            mm = re.match(r'\s*(x1|y1|x2|y2)\s*=\s*(-?\d+)', ln)
            if mm:
                ell_state[mm.group(1)] = int(mm.group(2))
            elif "Ending OOCP::PrimEllipse::read" in ln or ln.startswith('[debug] 0x'):
                flush_ellipse()

        # properties
        if "partValue" in ln:
            mm = re.search(r'partValue\s*=\s*(.+)', ln)
            if mm:
                cur['props']['PartValue'] = mm.group(1).strip()
        if "pcbFootprint" in ln:
            mm = re.search(r'pcbFootprint\s*=\s*(.+)', ln)
            if mm:
                cur['props']['Footprint'] = mm.group(1).strip()

    # build list (keep pins-only symbols too)
    symbols = []
    for s in pool.values():
        s['segments'] = list(dedup(s['segments'], key=tuple))
        s['pins'] = list(dedup(
            s['pins'],
            key=lambda p: (
                p.get('startX'), p.get('startY'),
                p.get('hotptX', p.get('startX')),
                p.get('hotptY', p.get('startY'))
            )
        ))
        symbols.append(s)
    return symbols

# build KiCad symbol
def build_symbol(sym, scale=1.0, join_eps=1.0, stitch_mode='auto', close_eps=None,
                 mirror_x=False, mirror_y=False, stroke_mm=0.254):
    """
    Build a single-unit KiCad symbol block.

    • Stub length     = 10 × scale
    • Tolerant join   : endpoints within join_eps are connected
    • Auto-close loop : if first/last within close_eps (default join_eps)
    • Pins-only symbols are supported (no segments required)
    • mirror_x        : if True, apply x → −x to graphics & pins (left↔right)
    • mirror_y        : if True, apply y → −y to graphics & pins (up↔down)
    • stroke_mm       : outline stroke width in mm (matches KiCad pin stub by default)
    """
    if close_eps is None:
        close_eps = join_eps

    def TX(x, y):  # transform point
        if mirror_x: x = -x
        if mirror_y: y = -y
        return (x * scale, y * scale)

    def TA(a):     # transform angle (KiCad uses 0→,90↑,180←,270↓)
        if a is None:
            return 0
        if mirror_x:
            a = (180 - a) % 360      # horizontal flip (left↔right)
        if mirror_y:
            a = (-a) % 360           # vertical flip (up↔down)
        return a

    # safe symbol name
    clean = re.sub(r'\.Normal$', '', sym['name'])
    base  = clean.replace('.', '_')

    # geometry preprocessing
    segs = preprocess_segments(sym['segments'], eps=join_eps, min_len=0.0)

    child = []

    def draw_poly_pts(pts):
        pts_s = ' '.join(f'(xy {x:g} {y:g})' for (x, y) in pts)
        child.append(
            f'      (polyline (pts {pts_s})'
            f' (stroke (width {stroke_mm:g}) (type default)) (fill (type none)))'
        )

    def draw_raw_segments():
        for x1, y1, x2, y2 in segs:
            a = TX(x1, y1); b = TX(x2, y2)
            draw_poly_pts([a, b])

    if segs:
        if stitch_mode == 'off':
            draw_raw_segments()
        else:
            polys = stitch_all_tolerant(segs, eps=join_eps)
            suspicious = any(poly_suspicious(pts) for pts in polys)
            if stitch_mode == 'auto' and suspicious:
                draw_raw_segments()
            else:
                for pts in polys:
                    # auto-close if nearly closed
                    if points_close(pts[0], pts[-1], close_eps):
                        pts = pts[:] + [pts[0]]
                    pts_tx = [TX(x, y) for (x, y) in pts]
                    draw_poly_pts(pts_tx)

    STUB  = 10 * scale

    auto = 1
    for p in sym['pins']:
        if 'startX' not in p or 'startY' not in p:
            continue

        raw = (p.get('name') or '').strip()
        if raw and raw.isdigit():
            number = raw
            name   = "~"
        else:
            number = str(auto); auto += 1
            name   = raw if raw else "~"

        # KiCad pin anchor = electrical (outer) end → use HOT.
        hx = float(p.get('hotptX', p['startX']))
        hy = float(p.get('hotptY', p['startY']))
        sx = float(p['startX'])
        sy = float(p['startY'])

        # snap for robust matching and placement
        hx_s, hy_s = (snap(hx, join_eps), snap(hy, join_eps)) if join_eps > 0 else (hx, hy)
        sx_s, sy_s = (snap(sx, join_eps), snap(sy, join_eps)) if join_eps > 0 else (sx, sy)

        ax, ay = TX(hx_s, hy_s)

        # Angle with axis-aware tolerance (tie to join_eps)
        pin_eps = max(1e-6, 0.5 * join_eps) if join_eps > 0 else 1e-6

        # Normal angle: HOT→START (into-body)
        ang_into = angle_from_vec(sx - hx, sy - hy, eps=pin_eps)

        # Degenerate? infer from nearby segments touching START
        if ang_into is None:
            ang_guess = infer_angle_from_segs((sx_s, sy_s), segs, eps=join_eps)
            ang = TA(ang_guess if ang_guess is not None else 0)
        else:
            ang = TA(ang_into)

        child.append(
            '      (pin passive line (at {x:g} {y:g} {a}) (length {l:g})'
            ' (name "{name}") (number "{n}"))'
            .format(x=ax, y=ay, a=ang, l=STUB, name=name, n=number)
        )

    child_block = '\n'.join(child)

    lines = [
        f'  (symbol "{base}"',
        '    (pin_names   (hide yes))',
        '    (pin_numbers (hide yes))',
        '    (property "Reference" "U" (at 0 5 0)'
        ' (effects (font (size 1.27 1.27))))',
        f'    (property "Value" "{base}" (at 0 -5 0)'
        ' (effects (font (size 1.27 1.27))))'
    ]
    for k, v in sym['props'].items():
        lines.append(
            f'    (property "{k}" "{v}" (at 0 0 0)'
            ' (effects (font (size 1 1))))'
        )

    lines.append(f'    (symbol "{base}_1_1"')
    if child_block:
        lines.append(child_block)
    lines.append('    )')
    lines.append('  )')

    return '\n'.join(lines), base


def write_library(blocks, dest):
    Path(dest).write_text(
        "(kicad_symbol_lib\n  (version 20240205)\n"
        "  (generator OOCP-Log-Converter)\n"
        + "\n".join(blocks) + "\n)",
        encoding='utf-8'
    )

def main():
    pa = argparse.ArgumentParser(description="Convert OOCP log ➜ KiCad symbol")
    pa.add_argument('logfile')
    pa.add_argument('--scale', type=float, default=1.0,
                    help='coordinate × factor (mil→mm = 0.254)')
    pa.add_argument('--join-eps', type=float, default=1.0,
                    help='tolerant join/endpoint snapping in input units (e.g., mil)')
    pa.add_argument('--stitch', choices=['auto', 'on', 'off'], default='auto',
                    help='stitch segments into polylines (auto falls back to raw on bad geometry)')
    pa.add_argument('--ellipse-sides', type=int, default=36,
                    help='polygon resolution for ellipses/circles')
    pa.add_argument('--mirror-x', action='store_true',
                    help='mirror X (x → −x) for graphics & pins (left↔right)')
    pa.add_argument('--mirror-y', action='store_true',
                    help='mirror Y (y → −y) for graphics & pins (up↔down)')
    pa.add_argument('--stroke-mm', type=float, default=0.254,
                    help='stroke width for symbol graphics in mm')
    pa.add_argument('--lib',
                    help='append / create this .kicad_sym instead of per-symbol files')
    args = pa.parse_args()

    symbols = parse_log(args.logfile, ellipse_sides=args.ellipse_sides)
    if not symbols:
        sys.exit("No symbols found in log")

    blocks, safe_names = [], []
    for s in symbols:
        block, safe = build_symbol(
            s,
            scale=args.scale,
            join_eps=args.join_eps,
            stitch_mode=args.stitch,
            mirror_x=args.mirror_x,
            mirror_y=args.mirror_y,
            stroke_mm=args.stroke_mm
        )
        blocks.append(block)
        safe_names.append(safe)

    if args.lib:
        write_library(blocks, args.lib)
        print(f"Wrote {len(blocks)} symbol(s) ➜ {args.lib}")
    else:
        base_path = Path(args.logfile).with_suffix('')
        for safe, block in zip(safe_names, blocks):
            fname = base_path.with_name(f'{safe}.kicad_sym')
            write_library([block], fname)
            print("Wrote", fname)

if __name__ == '__main__':
    main()
