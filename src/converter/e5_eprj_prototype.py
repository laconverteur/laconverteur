#!/usr/bin/env python3
"""Experimental EASE 5 EPRJ decoder and geometry inspector.

The decoder intentionally lives outside the production converter until both
the current encrypted format and the older Bond format have been validated.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import shutil


DEFAULT_EASE_DIR = Path(os.environ.get("EASE5_DIR", r"C:\Program Files\AFMG\EASE 5"))
HELPER_DIR = Path(__file__).with_name("e5-prototype")
HELPER_DLL = HELPER_DIR / "EprjDecode10.dll"


def decode_number(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and len(value) == 16:
        try:
            return struct.unpack(">d", bytes.fromhex(value))[0]
        except (ValueError, struct.error):
            pass
    return value


def decode_eprj(path: Path, ease_dir: Path = DEFAULT_EASE_DIR) -> dict:
    if not (ease_dir / "S4.EASE.Core.dll").exists():
        raise RuntimeError(
            "EASE 5 is required for .eprj conversion. Install EASE 5 or set EASE5_DIR to its installation directory."
        )
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        standard_dotnet = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "dotnet" / "dotnet.exe"
        dotnet = str(standard_dotnet) if standard_dotnet.exists() else "dotnet"
    command = [
        dotnet, str(HELPER_DLL),
        str(path.resolve()), str(ease_dir.resolve()),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(f"EASE 5 project decoding failed: {detail}")
    return json.loads(completed.stdout)


def summarize(project: dict) -> dict:
    room = project.get("SingleRoom") or {}
    surfaces = room.get("Surfaces") or []
    audiences = room.get("AudienceAreas") or []
    return {
        "room": room.get("Name"),
        "surfaces": len(surfaces),
        "audience_areas": len(audiences),
        "selection_sets": len(room.get("SelectionSets") or []),
        "components": len(room.get("Components") or []),
        "system_definitions": len(project.get("SystemDefinitions") or []),
        "surface_names": [surface.get("Name") for surface in surfaces[:10]],
        "audience_names": [area.get("Name") for area in audiences[:10]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--ease-dir", type=Path, default=DEFAULT_EASE_DIR)
    parser.add_argument("--json", type=Path, help="write decoded project JSON")
    args = parser.parse_args()
    project = decode_eprj(args.input, args.ease_dir)
    if args.json:
        args.json.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summarize(project), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
