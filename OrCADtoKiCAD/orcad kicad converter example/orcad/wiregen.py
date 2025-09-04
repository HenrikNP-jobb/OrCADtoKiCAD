
"""
wiregen.py – Convert OrCAD XML to KiCad 9 schematic + parts metadata (with wiring only).
"""

import argparse, sys, json
import xml.etree.ElementTree as ET
from pathlib import Path


MIL10 = 0.254  # OrCAD unit to mm
num = lambda x: ("%f" % x).rstrip("0").rstrip(".")
xy = lambda p: f"(xy {num(p[0])} {num(p[1])})"
snap = lambda v: round(v, 3)
mm = lambda v: round(float(v) * MIL10, 3) if v not in (None, "") else 0.0

class OrCadReader:
    def __init__(self, xml_path: Path):
        self.root = ET.parse(str(xml_path)).getroot()
        self.strip_ns(self.root)

    def strip_ns(self, elem):
        for el in elem.iter():
            if '}' in el.tag:
                el.tag = el.tag.split('}', 1)[1]

    def nets(self):
        wires, juncs = [], []
        for w in self.root.findall('.//WireScalar/Defn'):
            a = (snap(mm(w.get('startX'))), snap(mm(w.get('startY'))))
            b = (snap(mm(w.get('endX'))), snap(mm(w.get('endY'))))
            wires.append((a, b))
        for j in self.root.findall('.//Junction/Defn'):
            juncs.append((snap(mm(j.get('locX'))), snap(mm(j.get('locY')))))
        return wires, juncs



def build_pkg_map(xml_root):
    """
    Return {pkgName: {"cellName": str, "pinCount": int}}
    by scanning the <Cache>/<Package> definitions.
    """
    packages = {}
    for pkg in xml_root.findall('.//Package'):
        d = pkg.find('Defn')
        if d is None:
            continue
        name = d.get('name', '')
        cell = ''
        pins = 0

        lp_defn = pkg.find('.//LibPart/Defn')
        if lp_defn is not None:
            cell = lp_defn.get('CellName', '')

        pin_elems = pkg.findall('.//PhysicalPart/PinNumber')
        pins = len(pin_elems)

        packages[name] = {"cellName": cell, "pinCount": pins}

    return packages


def extract_part_types(xml_root) -> dict:
    """
    Return {RefDes: metadata…} with location, rotation, package info, etc.
    """
    pkg_map = build_pkg_map(xml_root)
    part_info = {}

    for pi in xml_root.findall('.//PartInst'):
        ref_el  = pi.find('Reference/Defn')
        defn_el = pi.find('Defn')

        if ref_el is None or defn_el is None:
            continue                        # malformed element, skip

        ref = ref_el.get('name', '')

        pkg  = defn_el.get('pkgName', '')
        meta = {
            "partName"         : "",        # filled in from pkg_map below
            "pkgName"          : pkg,
            "pinCount"         : "",
            "libName"          : defn_el.get('libName', ""),
            "deviceDesignator" : defn_el.get('deviceDesignator', ""),
            "rotation"         : defn_el.get('rotation', ""),
            "mirror"           : defn_el.get('mirror', ""),
            "locX"             : defn_el.get('locX', ""),
            "locY"             : defn_el.get('locY', "")
        }

        if pkg in pkg_map:
            meta["partName"] = pkg_map[pkg]["cellName"]
            meta["pinCount"] = str(pkg_map[pkg]["pinCount"])

        part_info[ref] = meta

    return part_info




def write_part_types_json(part_data: dict, output_path: Path):
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(part_data, f, indent=2)
    print(f"✓ Wrote {output_path}")

# --- KiCad Net + Junction Writer ---
def write_wiring_only(rdr: OrCadReader, outfile: Path):
    wires, juncs = rdr.nets()
    with outfile.open('w', encoding='utf-8') as fp:
        fp.write('(kicad_sch\n'
                 '  (version 20250114)\n'
                 '  (generator "orcad2kicad_wires_only")\n'
                 '  (paper "A4")\n'
                 '  (title_block (title "") (date "") (rev ""))\n')
        for w in wires:
            fp.write(f"  (wire (pts {xy(w[0])} {xy(w[1])}) (stroke (width 0)))\n")
        for j in juncs:
            fp.write(f"  (junction (at {num(j[0])} {num(j[1])}) (diameter 0))\n")
        fp.write(')\n')

# --- Entry Point ---
def main():
    parser = argparse.ArgumentParser(description='Convert OrCAD XML → KiCad schematic + part metadata')
    parser.add_argument('--xml', required=True, help='Input OrCAD XML netlist')
    parser.add_argument('--out', required=True, help='Output KiCad schematic filename (no suffix)')
    args = parser.parse_args()

    xin = Path(args.xml)
    xout = Path(args.out).with_suffix('.kicad_sch')
    jout = Path(args.out).with_suffix('.parts.json')

    if not xin.is_file():
        sys.exit(f'[error] {xin} not found')

    reader = OrCadReader(xin)
    parts = extract_part_types(reader.root)
    write_part_types_json(parts, jout)
    write_wiring_only(reader, xout)
    print(f'✓ Wrote {xout}')

if __name__ == '__main__':
    main()
