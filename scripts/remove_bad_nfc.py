#!/usr/bin/env python3
"""Remove the bogus NFC0 ACPI device that blocks touchpad enumeration.

The Honor MagicBook Pro 14 2025 firmware exposes an NTAG0001 NFC device on
the same I2C bus state used by the touchpad.  Linux can then bind resources
incorrectly and the I2C HID touchpad never appears.  Newer machines should be
patched from their own dumped DSDT, so this script performs the smallest known
safe edit instead of applying a full model-specific diff.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def find_matching_brace(text: str, open_brace: int) -> int:
    depth = 0
    for index in range(open_brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("could not find matching closing brace for Device (NFC0)")


def strip_known_compile_errors(text: str) -> tuple[str, int]:
    """Drop iasl-generated externals/calls known to fail on this firmware."""
    lines_to_drop = (
        "External (_SB_.PC00.CNVW.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP01.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP02.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP03.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP04.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP05.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP06.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP07.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP08.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP09.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP10.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP11.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.RP12.PXSX.EFUN.CRFI, UnknownObj)",
        "External (_SB_.PC00.XHCI._PS0.PS0X, MethodObj)    // 0 Arguments",
        "External (_SB_.PC00.XHCI._PS3.PS3X, MethodObj)    // 0 Arguments",
        "External (_SB_.PC02.XHCI._PS0.PS0X, MethodObj)    // 0 Arguments",
        "External (_SB_.PC02.XHCI._PS3.PS3X, MethodObj)    // 0 Arguments",
    )

    removed = 0
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if any(stripped == entry for entry in lines_to_drop):
            removed += 1
            continue
        if stripped in {"PS0X ()", "PS3X ()"}:
            previous = lines[index - 2].strip() if index >= 2 else ""
            next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if previous == f"If (CondRefOf ({stripped[:4]}))" and next_line == "}":
                removed += 1
                continue
        output.append(line)

    return "".join(output), removed


def remove_bad_nfc(text: str) -> tuple[str, int]:
    marker = "Device (NFC0)"
    count = 0
    offset = 0

    while True:
        start = text.find(marker, offset)
        if start == -1:
            break

        open_brace = text.find("{", start)
        if open_brace == -1:
            raise ValueError("Device (NFC0) has no opening brace")

        close_brace = find_matching_brace(text, open_brace)
        block = text[start : close_brace + 1]

        if '"NTAG0001"' not in block:
            offset = close_brace + 1
            continue

        # Preserve indentation and leave a short breadcrumb in the ASL source.
        line_start = text.rfind("\n", 0, start) + 1
        indent = text[line_start:start]
        next_line = close_brace + 1
        if next_line < len(text) and text[next_line] == "\n":
            next_line += 1

        replacement = (
            f"{indent}// Removed bogus NTAG0001 NFC0 device; it conflicts with "
            "I2C HID touchpad enumeration on Linux.\n"
        )
        text = text[:line_start] + replacement + text[next_line:]
        offset = line_start + len(replacement)
        count += 1

    return text, count


def bump_oem_revision(text: str) -> tuple[str, bool]:
    pattern = re.compile(
        r'(DefinitionBlock\s*\(\s*"[^"]*"\s*,\s*"[^"]*"\s*,\s*[^,]+\s*,\s*"[^"]*"\s*,\s*"[^"]*"\s*,\s*)'
        r'(0x[0-9A-Fa-f]+|\d+)'
        r'(\s*\))',
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return text, False

    raw_revision = match.group(2)
    revision = int(raw_revision, 0) + 1
    if raw_revision.lower().startswith("0x"):
        new_revision = f"0x{revision:08X}"
    else:
        new_revision = str(revision)

    return text[: match.start(2)] + new_revision + text[match.end(2) :], True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch a dumped Honor DSDT by removing the bad NTAG0001 NFC0 node."
    )
    parser.add_argument("input", type=Path, help="input dsdt.dsl")
    parser.add_argument("output", type=Path, help="output patched dsdt.dsl")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="write the input unchanged when no NTAG0001 NFC0 node is present",
    )
    args = parser.parse_args()

    source = args.input.read_text(encoding="utf-8")
    patched, cleanup_count = strip_known_compile_errors(source)
    patched, count = remove_bad_nfc(patched)
    if count == 0:
        if not args.allow_missing:
            raise SystemExit(
                "No Device (NFC0) block with _HID \"NTAG0001\" was found. "
                "This firmware may need a different patch; inspect the dumped ACPI tables."
            )
        patched = source
    else:
        patched, bumped = bump_oem_revision(patched)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(patched, encoding="utf-8")
    print(f"Removed {count} NTAG0001 NFC0 device block(s).")
    if count and bumped:
        print("Bumped ACPI OEM revision for table upgrade.")
    if cleanup_count:
        print(f"Removed {cleanup_count} known iasl compile-error line(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
