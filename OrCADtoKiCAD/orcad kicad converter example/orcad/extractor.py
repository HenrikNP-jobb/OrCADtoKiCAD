#!/usr/bin/env python3
"""
extractor.py — OrCAD XML ➜ JSON  (coords‐robust, Python 3.6)
"""
import argparse, json, xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Dict, List, Tuple

MIL_TO_MM = 0.0254                     # 1 mil = 0.0254 mm

# ───────────── union-find
class DSU:
    def __init__(self):  self.p = {}
    def find(self, x):   self.p.setdefault(x, x);               \
                         self.p[x] = x if self.p[x] == x else self.find(self.p[x]); \
                         return self.p[x]
    def union(self,a,b): self.p.setdefault(a,a); self.p.setdefault(b,b); \
                         ra,rb=self.find(a),self.find(b);                  \
                         self.p[rb]=ra if ra!=rb else ra
# ─────────────────────────

def get_coord_rot(pi):
    """Return (x_mil, y_mil, rot_deg).  Falls back through {Inst, Location, Defn}"""
    # 1) <Inst … locX="123" locY="456" rotation="1">
    inst = pi.find('Inst')
    if inst is not None and inst.get('locX'):
        return int(inst.get('locX')), int(inst.get('locY')), int(inst.get('rotation', '0'))*90

    # 2) <Location x="123" y="456" orientation="2"/>
    loc = pi.find('Location')
    if loc is not None and loc.get('x'):
        return int(loc.get('x')), int(loc.get('y')), int(loc.get('orientation', '0'))*90

    # 3) <Defn … locX="…" …
    d = pi.find('Defn')
    if d is not None and d.get('locX'):
        return int(d.get('locX')), int(d.get('locY')), int(d.get('rotation', '0'))*90

    # None of the above
    return 0, 0, 0

def parse_components_and_nets(root):
    # type: (ET.Element) -> Tuple[List[Dict], List[Dict]]
    comps = []                                       # type: List[Dict]
    pin_pts = defaultdict(list)                      # (x,y) → [(ref,pin)…]
    dsu = DSU()

    # ── parts & pins ─────────────────────────────
    for pi in root.findall('.//Schematic//PartInst'):
        ref_tag = pi.find('Reference/Defn')
        if ref_tag is None or not ref_tag.get('name'):
            continue
        ref = ref_tag.get('name')

        x_mil, y_mil, rot_deg = get_coord_rot(pi)
        if x_mil == y_mil == 0:
            print("-- warn: no coords for", ref)

        val_tag = pi.find('PartValue/Defn')
        value   = val_tag.get('name') if val_tag is not None else ''

        fp_tag  = pi.find("PartInstUserProp/Defn[@name='PCB Footprint']")
        footprint = fp_tag.get('val') if fp_tag is not None else ''

        comps.append(dict(
            ref=ref, value=value, footprint=footprint,
            x_mm=round(x_mil * MIL_TO_MM, 4),
            y_mm=round(y_mil * MIL_TO_MM, 4),
            rot=rot_deg))

        for port in pi.findall('PortInstScalar/Defn'):
            hx, hy = int(port.get('hotptX', '0')), int(port.get('hotptY', '0'))
            pin_pts[(hx, hy)].append((ref, port.get('name')))

    # ── wires → union-find ──────────────────────
    for w in root.findall('.//WireScalar/Defn'):
        dsu.union((int(w.get('startX')), int(w.get('startY'))),
                  (int(w.get('endX')),   int(w.get('endY'))))

    # ── nets list ───────────────────────────────
    nets_cl = defaultdict(list)          # root-coord → [(ref,pin)…]
    for pt, pins in pin_pts.items():
        nets_cl[dsu.find(pt)].extend(pins)

    nets = []
    for code, conns in enumerate(nets_cl.values(), 1):
        nets.append(dict(
            net_name   = "%s/%s" % conns[0],
            net_code   = code,
            connections= [{'ref': r, 'pin': p} for r, p in conns]))
    return comps, nets
# ────────────────────────────────────────────────

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--xml', required=True)
    ap.add_argument('--out', default='orcad_extracted.json')
    args = ap.parse_args()

    comps, nets = parse_components_and_nets(ET.parse(args.xml).getroot())
    json.dump({'components': comps, 'nets': nets}, open(args.out, 'w'), indent=2)
    print("Wrote %s : %d comps  %d nets" % (args.out, len(comps), len(nets)))
