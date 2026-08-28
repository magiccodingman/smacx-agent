#!/usr/bin/env python3
"""Prepare the disposable second LAN client for in-process bridge loading.

The script parses the PE import directory instead of replacing arbitrary text.
It changes exactly the DDRAW.dll import descriptor to agent.dll (same length),
then installs the current thinker bridge under that imported filename.  It will
never modify the canonical runtime/game executable.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import struct


PROJECT = Path(__file__).resolve().parents[1]
CANONICAL_GAME = (PROJECT / "runtime" / "game").resolve()


def u16(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def rva_to_offset(data: bytes | bytearray, pe_offset: int, rva: int) -> int:
    section_count = u16(data, pe_offset + 6)
    optional_size = u16(data, pe_offset + 20)
    sections = pe_offset + 24 + optional_size
    for index in range(section_count):
        header = sections + index * 40
        virtual_size = u32(data, header + 8)
        virtual_address = u32(data, header + 12)
        raw_size = u32(data, header + 16)
        raw_offset = u32(data, header + 20)
        span = max(virtual_size, raw_size)
        if virtual_address <= rva < virtual_address + span:
            return raw_offset + rva - virtual_address
    raise ValueError(f"RVA 0x{rva:x} is outside every PE section")


def patch_import(executable: Path) -> tuple[str, str]:
    data = bytearray(executable.read_bytes())
    if data[:2] != b"MZ":
        raise ValueError(f"{executable} is not a PE executable")
    pe_offset = u32(data, 0x3C)
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError(f"{executable} has no valid PE header")
    optional = pe_offset + 24
    if u16(data, optional) != 0x10B:
        raise ValueError("only the 32-bit SMACX executable is supported")
    import_rva = u32(data, optional + 104)
    descriptor = rva_to_offset(data, pe_offset, import_rva)
    ddraw_matches: list[int] = []
    agent_matches: list[int] = []
    while any(data[descriptor:descriptor + 20]):
        name_rva = u32(data, descriptor + 12)
        name_offset = rva_to_offset(data, pe_offset, name_rva)
        end = data.index(0, name_offset)
        name = bytes(data[name_offset:end]).lower()
        if name == b"ddraw.dll":
            ddraw_matches.append(name_offset)
        elif name == b"agent.dll":
            agent_matches.append(name_offset)
        descriptor += 20
    if len(agent_matches) == 1 and not ddraw_matches:
        digest = hashlib.sha256(data).hexdigest()
        return digest, digest
    if len(ddraw_matches) != 1 or agent_matches:
        raise ValueError(
            "expected exactly one DDRAW.dll or already-patched agent.dll "
            f"import descriptor; found {len(ddraw_matches)} and {len(agent_matches)}"
        )
    before = hashlib.sha256(data).hexdigest()
    data[ddraw_matches[0]:ddraw_matches[0] + 9] = b"agent.dll"
    executable.write_bytes(data)
    after = hashlib.sha256(data).hexdigest()
    return before, after


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=PROJECT / "runtime" / "game-lan-join",
    )
    args = parser.parse_args()
    game_dir = args.game_dir.resolve()
    if game_dir == CANONICAL_GAME:
        raise SystemExit("refusing to patch the canonical runtime/game directory")
    executable = game_dir / "terranx.exe"
    bridge = PROJECT / "runtime" / "game" / "thinker.dll"
    if not executable.is_file() or not bridge.is_file():
        raise SystemExit("join executable or built thinker.dll is missing")

    before, after = patch_import(executable)
    shutil.copy2(bridge, game_dir / "agent.dll")
    print(f"join_executable_sha256_before={before}")
    print(f"join_executable_sha256_after={after}")
    print(f"agent_dll_sha256={hashlib.sha256(bridge.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
