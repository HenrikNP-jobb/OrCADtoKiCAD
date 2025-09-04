"""
run_all_logs.py – Convert *.log files (from one or many Packages/ folders)
into a SINGLE KiCad symbol library.

Flow:
  • Recursively find all *.log files under each input directory.
  • For each log, call convert_log.py (WITHOUT --lib) so it emits one-symbol .kicad_sym.
  • Merge all (symbol "...") blocks into one output library file.
  • Optionally keep the one-symbol files after merging.

Examples:
  python3 run_all_logs.py \
    "/tmp/OpenOrCadParser/aaa/logs/nat_semi.olb/Packages" \
    "/tmp/OpenOrCadParser/bbb/logs/analog.olb/Packages" \
    --scale 0.254 \
    --convert-script "/home/hnp/Desktop/OrCADtoKiCAD/orcad kicad converter example/orcad/convert_log.py" \
    --out-dir "/home/hnp/Desktop/OrCADtoKiCAD/orcad kicad converter example/orcad/converted"
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set
from collections import OrderedDict


def find_olb_root(path: Path) -> Optional[Path]:
    """Return <name> for the closest ancestor ending in '.olb', else None."""
    for p in [path, *path.parents]:
        if p.suffix.lower() == ".olb":
            return p.with_suffix("")
    return None


def run_convert(cmd: List[str], workdir: Path) -> Set[Path]:
    """
    Run convert_log.py inside *workdir*.
    Returns the set of NEW *.kicad_sym files it produced.
    """
    before = set(workdir.glob("*.kicad_sym"))

    proc = subprocess.run(
        cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out = (proc.stdout or b"") + (proc.stderr or b"")

    if proc.returncode == 0:
        sys.stdout.buffer.write(proc.stdout)
    elif b"No drawable symbol found" in out or b"No symbols found" in out:
        print("no drawable symbol – skipped")
        return set()
    else:
        # show full output so it's easy to debug
        sys.stderr.buffer.write(out)
        sys.exit(f"‼️  convert_log.py failed (exit {proc.returncode})")

    return set(workdir.glob("*.kicad_sym")) - before


def parse_symbol_blocks(txt: str) -> Dict[str, str]:
    """
    Extract top-level (symbol "Name" ...) blocks from a .kicad_sym text.
    Returns OrderedDict{name -> full_block_text}.
    """
    out: "OrderedDict[str, str]" = OrderedDict()

    # we’re going to walk the whole file once, tracking depth and grabbing
    # top-level (symbol "...") blocks that sit under the (kicad_symbol_lib ...) root.
    depth = 0
    i, n = 0, len(txt)

    while i < n:
        if txt.startswith('(symbol "', i) and depth == 1:
            # read name
            j = i + len('(symbol "')
            k = txt.find('"', j)
            if k == -1:
                break
            name = txt[j:k]

            # capture balanced block
            d = 0
            start = i
            while i < n:
                ch = txt[i]
                if ch == '(':
                    d += 1
                elif ch == ')':
                    d -= 1
                    if d == 0:
                        end = i + 1
                        out[name] = txt[start:end]
                        i = end
                        break
                i += 1
            continue

        ch = txt[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        i += 1

    return out


def read_existing_lib_symbols(path: Path) -> "OrderedDict[str, str]":
    """Return {symbol_name: block} from an existing .kicad_sym (if any)."""
    if not path.exists():
        return OrderedDict()
    return parse_symbol_blocks(path.read_text(encoding="utf-8"))


def write_library(blocks: List[str], dest: Path, generator: str = "run_all_logs") -> None:
    dest.write_text(
        "(kicad_symbol_lib\n"
        "  (version 20240205)\n"
        f"  (generator {generator})\n"
        + "\n".join(blocks) + "\n)",
        encoding="utf-8"
    )

def main() -> None:
    pa = argparse.ArgumentParser()
    pa.add_argument(
        "directories",
        nargs="+",
        help="One or more folders that contain *.log files (e.g. .../olb/Packages/)",
    )
    pa.add_argument("--scale", type=float,
                    help="Scale factor for convert_log.py (mil→mm = 0.254)")
    pa.add_argument("--convert-script", default="convert_log.py",
                    help="Path to convert_log.py if not alongside this script")
    pa.add_argument("--no-recursive", action="store_true",
                    help="Disable recursive search (default: recursive ON)")
    pa.add_argument("--out-dir", type=str, default=None,
                    help='Directory to write the merged library into. '
                         'Output file will be "<out-dir>/converted_sch.kicad_sym".')
    pa.add_argument("--out-lib", type=str, default=None,
                    help="Explicit output library path (*.kicad_sym). Overrides --out-dir.")
    pa.add_argument("--keep-single", action="store_true",
                    help="Keep the per-symbol .kicad_sym files (default: delete after merge)")
    args = pa.parse_args()

    # Gather all *.log files from all input directories
    all_logs: List[Path] = []
    for d in args.directories:
        root = Path(d).expanduser().resolve()
        if not root.is_dir():
            sys.exit(f"{root} is not a directory")
        it = root.rglob("*.log") if not args.no_recursive else root.glob("*.log")
        logs = sorted(it)
        if not logs:
            print(f"No *.log files found under {root}")
        else:
            print(f"Found {len(logs)} log(s) under {root}")
            all_logs.extend(logs)

    if not all_logs:
        sys.exit("⚠️  No *.log files to process.")

    # Decide output library path
    first_dir = Path(args.directories[0]).expanduser().resolve()

    if args.out_lib:
        out_lib = Path(args.out_lib).expanduser().resolve()
        out_lib.parent.mkdir(parents=True, exist_ok=True)
    else:
        if args.out_dir:
            out_dir = Path(args.out_dir).expanduser().resolve()
        else:
            # default: nearest <name>.olb parent of FIRST dir, then /converted
            base = find_olb_root(first_dir) or first_dir
            out_dir = (base / "converted").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_lib = out_dir / "converted_sch.kicad_sym"

    print(f"\n Output library: {out_lib}\n")

    # Make convert_log.py path absolute so it works after cwd change
    convert_py = Path(args.convert_script).expanduser()
    if not convert_py.is_absolute():
        convert_py = (Path(__file__).parent / convert_py).resolve()
    if not convert_py.exists():
        sys.exit(f" convert script not found: {convert_py}")

    # Load existing library (if any) so we can append/override
    lib_map = read_existing_lib_symbols(out_lib)
    total_added = 0
    converted_logs = skipped_logs = 0

    # Process each log
    for log_path in all_logs:
        print("→", log_path)
        cmd = ["python3", str(convert_py), str(log_path)]
        if args.scale is not None:
            cmd += ["--scale", str(args.scale)]

        new_syms = run_convert(cmd, workdir=log_path.parent)
        if not new_syms:
            skipped_logs += 1
            continue

        # Merge produced one-symbol libraries
        for sym_file in sorted(new_syms):
            try:
                text = sym_file.read_text(encoding="utf-8")
            except Exception as e:
                print(f"   [warn] could not read {sym_file.name}: {e}", file=sys.stderr)
                continue

            blocks = parse_symbol_blocks(text)
            if not blocks:
                print(f"   [warn] {sym_file.name} contains no (symbol ...) blocks", file=sys.stderr)
                continue

            for name, blk in blocks.items():
                old = "(overwrite)" if name in lib_map else "(new)"
                lib_map[name] = blk
                print(f"   + {name} {old}")
                total_added += 1

            if not args.keep_single:
                try:
                    sym_file.unlink()
                except Exception as e:
                    print(f"   [warn] could not delete {sym_file.name}: {e}", file=sys.stderr)

        converted_logs += 1

        # write incrementally so you can interrupt safely
        write_library(list(lib_map.values()), out_lib, generator="run_all_logs")

    print(
        f"\n Finished – {total_added} symbol definition(s) in {out_lib} "
        f"(from {converted_logs} log set(s), {skipped_logs} skipped)."
    )
    print("Library path:", out_lib)


if __name__ == "__main__":
    main()
