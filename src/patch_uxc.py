"""Patch Sony ILCE-7R v3.20 `share/app/string_*.uxc` files safely.

- Allocates universally free IDs within the existing 16-bit slot table (0xE000..0xF68F)
  so ResourceManager never hits an out-of-range assert.
- Verifies byte-identical round-trip on every stock `.uxc` before patching.
- Injects localized English, Simplified Chinese, Traditional Chinese, and Japanese strings.
"""
from __future__ import annotations

import struct
from pathlib import Path

from menu_spec import STRINGS, all_string_keys, lang_for_uxc_filename

FIRST_ID = 0xE000
EMPTY_SLOT = 0xFFFF


def parse_uxc(data: bytes) -> tuple[bytes, int, dict[int, str]]:
    if data[:4] != b"uxc\x07":
        raise ValueError("Invalid UXC header")
    count = struct.unpack_from("<I", data, 12)[0] & 0x1FFF
    base = 0x10 + 2 * count
    recs: dict[int, str] = {}
    pos = base
    while pos + 4 <= len(data):
        rid, typ = struct.unpack_from("<HH", data, pos)
        if typ != 0x1E04:
            raise ValueError(f"Unsupported record type 0x{typ:04x} at 0x{pos:x}")
        end = data.index(b"\0", pos + 4)
        recs[rid] = data[pos + 4:end].decode("utf-8")
        pos += (end - pos + 1 + 3) & ~3
    return data[:base], base, recs


def build_uxc(head: bytes, recs: dict[int, str]) -> bytes:
    head_buf = bytearray(head)
    slots = (len(head_buf) - 0x10) // 2
    if not all(0 <= rid - FIRST_ID < slots for rid in recs):
        raise ValueError("UXC string ID outside slot table bounds")
    body = bytearray()
    table: dict[int, int] = {}
    for rid in sorted(recs):
        table[rid - FIRST_ID] = len(body) // 4
        raw = recs[rid].encode("utf-8") + b"\0"
        body += struct.pack("<HH", rid, 0x1E04) + raw + b"\0" * (-len(raw) % 4)
    if len(body) // 4 >= EMPTY_SLOT:
        raise ValueError("UXC body exceeds 16-bit slot index")
    for k in range(slots):
        struct.pack_into("<H", head_buf, 0x10 + 2 * k, table.get(k, EMPTY_SLOT))
    return bytes(head_buf) + bytes(body)


def patch_all_uxc(usr_dir: Path) -> tuple[dict[str, int], dict[str, bytes]]:
    """Allocate free IDs and build patched UTF-8 `share/app/string_*.uxc` files.

    Returns:
      key_to_id: {string_key: u16_rid} (e.g. 0xE017; CautionConfig uses 0x02040000 | rid)
      patched_files: {"share/app/string_english.uxc": bytes, ...}
    """
    app_dir = usr_dir / "share/app"
    parsed: list[tuple[Path, bytes, dict[int, str], int]] = []
    used_all: set[int] = set()
    min_slots = 1 << 30

    for f in sorted(app_dir.glob("string_*.uxc")):
        if f.name.endswith("_guide.uxc"):
            continue
        raw = f.read_bytes()
        try:
            head, _, recs = parse_uxc(raw)
        except ValueError:
            # Skip non-UTF8 (e.g. Arabic/Russian/Thai) string tables cleanly.
            continue
        if build_uxc(head, recs) != raw:
            raise AssertionError(f"Stock UXC failed byte-identical round-trip: {f.name}")
        slots = (len(head) - 0x10) // 2
        min_slots = min(min_slots, slots)
        used_all |= set(recs.keys())
        parsed.append((f, head, recs, slots))

    keys = all_string_keys()
    free_ids = [FIRST_ID + k for k in range(min_slots) if (FIRST_ID + k) not in used_all]
    if len(free_ids) < len(keys):
        raise RuntimeError(f"Need {len(keys)} free UXC slots, only {len(free_ids)} available")

    key_to_id = dict(zip(keys, free_ids[:len(keys)]))
    patched_files: dict[str, bytes] = {}

    for f, head, recs, slots in parsed:
        lang = lang_for_uxc_filename(f.name)
        new_recs = dict(recs)
        for k, rid in key_to_id.items():
            assert rid not in new_recs, f"Slot collision 0x{rid:x} in {f.name}"
            assert 0 <= rid - FIRST_ID < slots
            entry = STRINGS[k]
            new_recs[rid] = entry.get(lang, entry["en"])
        rebuilt = build_uxc(head, new_recs)
        # Verify clean re-parse of patched output
        _, _, check_recs = parse_uxc(rebuilt)
        assert check_recs == new_recs, f"Patched UXC verify failed: {f.name}"
        patched_files[f"share/app/{f.name}"] = rebuilt

    return key_to_id, patched_files
