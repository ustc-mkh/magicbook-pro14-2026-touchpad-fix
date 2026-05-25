#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF'
Usage: scripts/build-local-override.sh [WORKDIR]

Dump ACPI tables from the running Honor laptop, remove the bogus NTAG0001
NFC0 device from the local DSDT or I2C_DEVT SSDT, and compile override AML.

Default WORKDIR: build/local
EOF
    exit 0
fi

workdir="${1:-build/local}"
mkdir -p "$workdir"

if ! command -v acpidump >/dev/null 2>&1; then
    echo "acpidump not found. Install acpica-tools/acpica first." >&2
    exit 1
fi

if ! command -v iasl >/dev/null 2>&1; then
    echo "iasl not found. Install acpica-tools/acpica first." >&2
    exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! touch "$workdir/.write-test" 2>/dev/null; then
    echo "Cannot write to $workdir. Remove it or fix ownership, then rerun." >&2
    exit 1
fi
rm -f "$workdir/.write-test"

echo "Dumping ACPI tables to $workdir"
(
    cd "$workdir"
    rm -f ./*.dat ./*.dsl ./*.aml ./*.hex
    acpidump -b
)

mapfile -t externals < <(find "$workdir" -maxdepth 1 -type f -name 'ssdt*.dat' | sort)

echo "Scanning SSDT tables for NTAG0001 NFC0 node"
for ssdt in "$workdir"/ssdt*.dat; do
    [[ -e "$ssdt" ]] || continue
    if ! grep -aq 'NTAG0001' "$ssdt"; then
        continue
    fi

    base="${ssdt%.dat}"
    echo "Disassembling candidate $(basename "$ssdt")"
    iasl -p "$base" -d "$ssdt"
    dsl="$base.dsl"
    table_id="$(grep -m1 'OEM Table ID' "$dsl" | awk -F'"' '{print $2}')"
    echo "Found NTAG0001 NFC0 node in $(basename "$dsl") ($table_id)"
    python3 "$repo_root/scripts/remove_bad_nfc.py" "$dsl" "$base.patched.dsl"
    echo "Compiling patched $(basename "$ssdt")"
    iasl -ve -tc "$base.patched.dsl"
    cp "$base.patched.aml" "$workdir/$(basename "$base").aml"
    echo "Wrote $workdir/$(basename "$base").aml"
    if [[ "$table_id" == "I2C_DEVT" ]]; then
        cp "$base.patched.aml" "$workdir/i2c_devt.aml"
        echo "Wrote $workdir/i2c_devt.aml"
    fi
    exit 0
done

if grep -aq 'NTAG0001' "$workdir/dsdt.dat"; then
    echo "NTAG0001 appears in DSDT; disassembling DSDT without SSDT externals first"
    iasl -p "$workdir/dsdt" -d "$workdir/dsdt.dat"
else
    echo "No NTAG0001 NFC0 node found in SSDT binary tables or DSDT binary table"
    exit 1
fi

if ((${#externals[@]})); then
    echo "Disassembling DSDT with ${#externals[@]} SSDT external table(s)"
    if ! iasl -e "${externals[@]}" -d "$workdir/dsdt.dat"; then
        cat >&2 <<'EOF'
iasl failed while loading external SSDT tables.

Some Honor BIOS versions expose duplicate objects such as:
  \_SB.PC00.XHCI.RHUB.HS03._UPC

Move the duplicate ssdt*.dat out of the build directory and rerun this script,
or disassemble manually with only the non-duplicated SSDTs.
EOF
        exit 1
    fi
else
    echo "No SSDT tables found; disassembling DSDT alone"
    iasl -d "$workdir/dsdt.dat"
fi

if [[ ! -s "$workdir/dsdt.dsl" ]]; then
    echo "Disassembly did not produce a non-empty $workdir/dsdt.dsl" >&2
    exit 1
fi

if grep -q '"NTAG0001"' "$workdir/dsdt.dsl"; then
    python3 "$repo_root/scripts/remove_bad_nfc.py" \
        "$workdir/dsdt.dsl" \
        "$workdir/dsdt.patched.dsl"

    echo "Compiling patched DSDT"
    iasl -ve -tc "$workdir/dsdt.patched.dsl"

    cp "$workdir/dsdt.patched.aml" "$workdir/dsdt.aml"
    echo "Wrote $workdir/dsdt.aml"
    exit 0
fi

cat >&2 <<'EOF'
No Device (NFC0) block with _HID "NTAG0001" was found in DSDT or SSDT tables.
This firmware needs a different patch.
EOF
exit 1
