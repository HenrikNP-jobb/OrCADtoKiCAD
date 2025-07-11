#!/usr/bin/env python3
import re
from pathlib import Path

# --- Configuration ---
SYMDIR = Path("/usr/share/kicad/symbols")  # Change if needed
LIBRARIES_OF_INTEREST = {"Device", "Connector_Generic", "Amplifier_Operational"}
SYMBOL_NAME_FILTER = "R"  # Case-insensitive filter (e.g., "R" for resistors)
OUTPUT_FILE = Path("filtered_symbols.txt")

# --- Symbol Listing ---
symbol_pattern = re.compile(r'\(symbol\s+"([^"]+)"')

def list_filtered_symbols(symdir: Path, libs: set, name_filter: str, outfile: Path):
    name_filter = name_filter.lower()
    lines = []

    for sym_file in sorted(symdir.glob("*.kicad_sym")):
        lib_name = sym_file.stem
        if lib_name not in libs:
            continue

        try:
            content = sym_file.read_text(encoding='utf-8')
        except Exception as e:
            lines.append(f"# [error] Could not read {sym_file}: {e}\n")
            continue

        matches = symbol_pattern.findall(content)
        filtered = [sym for sym in matches if name_filter in sym.lower()]
        if filtered:
            lines.append(f"# {lib_name}.kicad_sym → matches:")
            for sym in filtered:
                lines.append(f'"{lib_name}:{sym}",')
            lines.append("")  # Blank line for spacing

    # Write results to file
    with outfile.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[✓] Written filtered symbol list to {outfile}")

if __name__ == "__main__":
    list_filtered_symbols(SYMDIR, LIBRARIES_OF_INTEREST, SYMBOL_NAME_FILTER, OUTPUT_FILE)
