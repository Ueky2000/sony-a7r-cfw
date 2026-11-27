"""Surgical in-place patcher for Sony ILCE-7R v3.20 `/usr/lib/viewUnified3.so`.

Modernizes zoomed playback (`ViewZoomPb`, States 3 & 4) with ZERO new PT_LOAD segments,
ZERO relocation changes, and ZERO file size change:
1. Rear Control Wheel (4 branch sites in States 3 & 4, 4 changed bytes):
   - Control Wheel CCW (`0x150a0b00`) -> Zoom Out (`ev=0x21` -> `Action 0x25`):
     smoothly zooms out and STOPS cleanly at minimum 1.0x (never exits zoom or flips
     multiple photos on a fast spin!).
   - Control Wheel CW  (`0x150a0b10`) -> Zoom In  (`ev=0x03` -> `Action 0x04`).
2. D-pad Left/Right at Minimum Magnification 1.0x (80-byte in-place block at
   `ViewZoomPb::onTransition` State 3 `0x0001dcfc..0x0001dd4c`, keeping all 4 original
   entry VAs `0x1dcfc`/`0x1dd10`/`0x1dd24`/`0x1dd38` intact!):
   - When zoomed in (`> 1.0x`, `Guard 4 == 0`):
       * D-pad Left  (`ev=0x07`) -> Guard 2 -> `Action 0x08` (Pan Left, 100% stock)
       * D-pad Right (`ev=0x08`) -> Guard 2 -> `Action 0x09` (Pan Right, 100% stock)
   - When zoomed out to minimum (`1.0x`, `ratio == 100`, `Guard 4 == 1`):
       * D-pad Left  (`ev=0x07`) -> Guard 6 -> `Action 0x0a` (Previous Photo at 1.0x!)
       * D-pad Right (`ev=0x08`) -> Guard 6 -> `Action 0x0b` (Next Photo at 1.0x!)
"""
from __future__ import annotations

import struct

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

# 1. Verified ILCE-7R v3.20 ViewZoomPb Control Wheel patch sites:
# (name, va, orig_bytes, new_bytes, expected_mnemonic, expected_target_va)
PATCH_SITES: tuple[tuple[str, int, bytes, bytes, str, int], ...] = (
    ("state3_wheel_ccw_zoom_out", 0x00019FF6, bytes.fromhex("00f0bb80"), bytes.fromhex("00f0bf80"), "beq.w", 0x0001A178),
    ("state3_wheel_cw_zoom_in",   0x0001A002, bytes.fromhex("b7e0"),     bytes.fromhex("bbe0"),     "b",     0x0001A17C),
    ("state4_wheel_cw_zoom_in",   0x0001B8AA, bytes.fromhex("63d0"),     bytes.fromhex("67d0"),     "beq",   0x0001B97C),
    ("state4_wheel_ccw_zoom_out", 0x0001B8B8, bytes.fromhex("5ce0"),     bytes.fromhex("5ee0"),     "b",     0x0001B978),
)

# Read-only return stub fingerprints confirming exact target events (`0x21`=Zoom Out, `0x03`=Zoom In)
TARGET_STUB_FINGERPRINTS: tuple[tuple[str, int, bytes], ...] = (
    ("state3_ret_zoom_out_0x21", 0x0001A178, bytes.fromhex("212080bd")),  # movs r0, #0x21; pop {r7, pc}
    ("state3_ret_zoom_in_0x03",  0x0001A17C, bytes.fromhex("032080bd")),  # movs r0, #3;    pop {r7, pc}
    ("state4_ret_zoom_out_0x21", 0x0001B978, bytes.fromhex("212080bd")),  # movs r0, #0x21; pop {r7, pc}
    ("state4_ret_zoom_in_0x03",  0x0001B97C, bytes.fromhex("032080bd")),  # movs r0, #3;    pop {r7, pc}
)

# Read-only key-constant literal fingerprints confirming exact hardware dial codes
KEY_LITERAL_FINGERPRINTS: tuple[tuple[str, int, int], ...] = (
    ("state3_top_dial_out", 0x0001A10C, 0x150A0B40),
    ("state4_wheel_anchor", 0x0001B99C, 0x150A0B72),
    ("state4_top_dial_in",  0x0001B9A0, 0x150A0B50),
)

# 2. Verified ILCE-7R v3.20 ViewZoomPb::onTransition State 3 ev=0x07..0x0a block (80 bytes)
ONTRANS_DPAD_VA = 0x0001DCFC
ONTRANS_DPAD_SIZE = 0x50  # 0x0001dcfc .. 0x0001dd4c
ONTRANS_GUARD_FN = 0x0001D98C
ONTRANS_EXIT_VA = 0x0001E280

ONTRANS_DPAD_ORIG = bytes.fromhex(
    "30460221fff744fe002800f0bb8208232360b7e2"  # 0x1dcfc: ev=0x07 (Pan Left)
    "30460221fff73afe002800f0b18209232360ade2"  # 0x1dd10: ev=0x08 (Pan Right)
    "30460621fff730fe002800f0a7820a232360a3e2"  # 0x1dd24: ev=0x09 (Prev Photo)
    "30460621fff726fe002800f09d820b23236099e2"  # 0x1dd38: ev=0x0a (Next Photo)
)
assert len(ONTRANS_DPAD_ORIG) == ONTRANS_DPAD_SIZE


def _enc_bl(va: int, target: int) -> bytes:
    disp = target - (va + 4)
    assert disp % 2 == 0 and -(1 << 24) <= disp < (1 << 24)
    s = (disp >> 24) & 1
    i1 = (disp >> 23) & 1
    i2 = (disp >> 22) & 1
    j1 = (~(i1 ^ s)) & 1
    j2 = (~(i2 ^ s)) & 1
    imm10 = (disp >> 12) & 0x3FF
    imm11 = (disp >> 1) & 0x7FF
    hw1 = 0xF000 | (s << 10) | imm10
    hw2 = 0xD000 | (j1 << 13) | (j2 << 11) | imm11
    return struct.pack("<HH", hw1, hw2)


def _enc_cbz(va: int, target: int, reg: int = 0, nz: bool = False) -> bytes:
    u = target - (va + 4)
    assert 0 <= u <= 126 and u % 2 == 0 and 0 <= reg <= 7
    base = 0xB900 if nz else 0xB100
    hw = base | (((u >> 6) & 1) << 9) | (((u >> 1) & 0x1F) << 3) | reg
    return struct.pack("<H", hw)


def _enc_b16(va: int, target: int) -> bytes:
    disp = target - (va + 4)
    assert disp % 2 == 0 and -2048 <= disp <= 2046
    return struct.pack("<H", 0xE000 | ((disp >> 1) & 0x7FF))


def build_ontrans_dpad_block() -> bytes:
    """Assemble and Capstone-verify the 80-byte State 3 D-pad Left/Right block."""
    buf = bytearray()

    def emit(b: bytes) -> None:
        buf.extend(b)

    def cur_va() -> int:
        return ONTRANS_DPAD_VA + len(buf)

    # 0x0001dcfc: ev=0x07 (D-pad Left) -> if Guard 4 (1.0x) goto 0x1dd24 (Prev Photo), else Action 0x08 via Guard 2
    assert cur_va() == 0x0001DCFC
    emit(bytes.fromhex("3046"))                                  # mov r0, r6
    emit(bytes.fromhex("0421"))                                  # movs r1, #4
    emit(_enc_bl(cur_va(), ONTRANS_GUARD_FN))                    # bl #0x1d98c
    emit(_enc_cbz(cur_va(), 0x0001DD24, reg=0, nz=True))         # cbnz r0, #0x1dd24
    emit(bytes.fromhex("0825"))                                  # movs r5, #8
    emit(_enc_b16(cur_va(), 0x0001DD28))                         # b #0x1dd28
    emit(bytes.fromhex("00bf00bf00bf"))                          # 3x nop padding -> 0x1dd10

    # 0x0001dd10: ev=0x08 (D-pad Right) -> if Guard 4 (1.0x) goto 0x1dd38 (Next Photo), else Action 0x09 via Guard 2
    assert cur_va() == 0x0001DD10
    emit(bytes.fromhex("3046"))                                  # mov r0, r6
    emit(bytes.fromhex("0421"))                                  # movs r1, #4
    emit(_enc_bl(cur_va(), ONTRANS_GUARD_FN))                    # bl #0x1d98c
    emit(_enc_cbz(cur_va(), 0x0001DD38, reg=0, nz=True))         # cbnz r0, #0x1dd38
    emit(bytes.fromhex("0925"))                                  # movs r5, #9
    emit(_enc_b16(cur_va(), 0x0001DD28))                         # b #0x1dd28
    emit(bytes.fromhex("00bf00bf00bf"))                          # 3x nop padding -> 0x1dd24

    # 0x0001dd24: ev=0x09 (Prev Photo) -> Action 0x0a via Guard 6
    assert cur_va() == 0x0001DD24
    emit(bytes.fromhex("0a25"))                                  # movs r5, #0xa
    emit(_enc_b16(cur_va(), 0x0001DD3A))                         # b #0x1dd3a

    # 0x0001dd28: shared Guard 2 helper (r5 = action_id)
    assert cur_va() == 0x0001DD28
    emit(bytes.fromhex("3046"))                                  # mov r0, r6
    emit(bytes.fromhex("0221"))                                  # movs r1, #2
    emit(_enc_bl(cur_va(), ONTRANS_GUARD_FN))                    # bl #0x1d98c
    emit(_enc_cbz(cur_va(), 0x0001DD48, reg=0, nz=False))        # cbz r0, #0x1dd48
    emit(bytes.fromhex("2560"))                                  # str r5, [r4]
    emit(_enc_b16(cur_va(), 0x0001DD48))                         # b #0x1dd48
    emit(bytes.fromhex("00bf"))                                  # nop -> 0x1dd38

    # 0x0001dd38: ev=0x0a (Next Photo) -> Action 0x0b, falls through into Guard 6 helper
    assert cur_va() == 0x0001DD38
    emit(bytes.fromhex("0b25"))                                  # movs r5, #0xb

    # 0x0001dd3a: shared Guard 6 helper (r5 = action_id)
    assert cur_va() == 0x0001DD3A
    emit(bytes.fromhex("3046"))                                  # mov r0, r6
    emit(bytes.fromhex("0621"))                                  # movs r1, #6
    emit(_enc_bl(cur_va(), ONTRANS_GUARD_FN))                    # bl #0x1d98c
    emit(_enc_cbz(cur_va(), 0x0001DD48, reg=0, nz=False))        # cbz r0, #0x1dd48
    emit(bytes.fromhex("2560"))                                  # str r5, [r4]
    emit(bytes.fromhex("00bf"))                                  # nop -> 0x1dd48

    # 0x0001dd48: shared exit to ViewZoomPb::onTransition epilogue @ 0x1e280
    assert cur_va() == 0x0001DD48
    emit(_enc_b16(cur_va(), ONTRANS_EXIT_VA))                    # b #0x1e280
    emit(bytes.fromhex("00bf"))                                  # nop -> 0x1dd4c

    assert len(buf) == ONTRANS_DPAD_SIZE, f"Expected {ONTRANS_DPAD_SIZE} B, got {len(buf)}"
    return bytes(buf)


ONTRANS_DPAD_NEW = build_ontrans_dpad_block()

ONTRANS_DPAD_EXPECTED_DISASM: tuple[tuple[int, str, str], ...] = (
    (0x0001DCFC, "mov",  "r0, r6"),
    (0x0001DCFE, "movs", "r1, #4"),
    (0x0001DD00, "bl",   "#0x1d98c"),
    (0x0001DD04, "cbnz", "r0, #0x1dd24"),
    (0x0001DD06, "movs", "r5, #8"),
    (0x0001DD08, "b",    "#0x1dd28"),
    (0x0001DD0A, "nop",  ""),
    (0x0001DD0C, "nop",  ""),
    (0x0001DD0E, "nop",  ""),
    (0x0001DD10, "mov",  "r0, r6"),
    (0x0001DD12, "movs", "r1, #4"),
    (0x0001DD14, "bl",   "#0x1d98c"),
    (0x0001DD18, "cbnz", "r0, #0x1dd38"),
    (0x0001DD1A, "movs", "r5, #9"),
    (0x0001DD1C, "b",    "#0x1dd28"),
    (0x0001DD1E, "nop",  ""),
    (0x0001DD20, "nop",  ""),
    (0x0001DD22, "nop",  ""),
    (0x0001DD24, "movs", "r5, #0xa"),
    (0x0001DD26, "b",    "#0x1dd3a"),
    (0x0001DD28, "mov",  "r0, r6"),
    (0x0001DD2A, "movs", "r1, #2"),
    (0x0001DD2C, "bl",   "#0x1d98c"),
    (0x0001DD30, "cbz",  "r0, #0x1dd48"),
    (0x0001DD32, "str",  "r5, [r4]"),
    (0x0001DD34, "b",    "#0x1dd48"),
    (0x0001DD36, "nop",  ""),
    (0x0001DD38, "movs", "r5, #0xb"),
    (0x0001DD3A, "mov",  "r0, r6"),
    (0x0001DD3C, "movs", "r1, #6"),
    (0x0001DD3E, "bl",   "#0x1d98c"),
    (0x0001DD42, "cbz",  "r0, #0x1dd48"),
    (0x0001DD44, "str",  "r5, [r4]"),
    (0x0001DD46, "nop",  ""),
    (0x0001DD48, "b",    "#0x1e280"),
    (0x0001DD4A, "nop",  ""),
)


def _segments(d: bytes) -> list[tuple[int, ...]]:
    phoff = struct.unpack_from("<I", d, 28)[0]
    phnum = struct.unpack_from("<H", d, 44)[0]
    return [struct.unpack_from("<8I", d, phoff + 32 * i) for i in range(phnum)]


def _va_to_off(segs: list[tuple[int, ...]], va: int) -> int:
    for t, o, v, _, fs, _, _, _ in segs:
        if t == 1 and v <= va < v + fs:
            return o + va - v
    raise KeyError(f"VA 0x{va:08x} not in any PT_LOAD file range")


def verify_site(raw: bytes, va: int, expected_len: int, expected_mnem: str, expected_target: int) -> bytes:
    """Round-trip verify an assembled Thumb/Thumb-2 branch at `va` with Capstone."""
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    assert len(raw) == expected_len, f"@ 0x{va:08x}: len {len(raw)} != {expected_len}"
    insns = list(md.disasm(raw, va))
    assert len(insns) == 1, f"@ 0x{va:08x}: decoded {len(insns)} insns"
    ins = insns[0]
    got_target = int(ins.op_str.lstrip("#"), 16)
    assert ins.mnemonic == expected_mnem and got_target == expected_target, (
        f"Verify failed @ 0x{va:08x}: got `{ins.mnemonic} {ins.op_str}`, "
        f"want `{expected_mnem} #0x{expected_target:x}`"
    )
    return raw


def patch_view_zoompb(data: bytes) -> tuple[bytes, dict]:
    """Surgically patch `lib/viewUnified3.so` in-place for Playback Zoom wheel & 1.0x D-pad switch."""
    segs = _segments(data)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

    # Pre-flight fingerprints on return stubs and hardware key literals
    for name, va, want in TARGET_STUB_FINGERPRINTS:
        off = _va_to_off(segs, va)
        got = data[off:off + len(want)]
        assert got == want, f"Unexpected stub {name} @ 0x{va:08x}: got {got.hex()}, want {want.hex()}"

    for name, va, want_val in KEY_LITERAL_FINGERPRINTS:
        off = _va_to_off(segs, va)
        got_val = struct.unpack_from("<I", data, off)[0]
        assert got_val == want_val, (
            f"Unexpected literal {name} @ 0x{va:08x}: got 0x{got_val:08x}, want 0x{want_val:08x}"
        )

    # Pre-flight assertion & Capstone verification of the 80-byte State 3 onTransition D-pad block
    dpad_off = _va_to_off(segs, ONTRANS_DPAD_VA)
    assert data[dpad_off:dpad_off + ONTRANS_DPAD_SIZE] == ONTRANS_DPAD_ORIG, (
        f"Unexpected bytes at onTransition D-pad block @ 0x{ONTRANS_DPAD_VA:08x}"
    )
    dpad_dis = tuple(
        (i.address, i.mnemonic, i.op_str)
        for i in md.disasm(ONTRANS_DPAD_NEW, ONTRANS_DPAD_VA)
    )
    assert dpad_dis == ONTRANS_DPAD_EXPECTED_DISASM, f"D-pad block disassembly mismatch: {dpad_dis}"

    out = bytearray(data)
    site_reports: dict[str, dict] = {}
    allowed_offsets: set[int] = set(range(dpad_off, dpad_off + ONTRANS_DPAD_SIZE))

    # 1. Patch the 4 Control Wheel branches (CCW -> 0x21 Zoom Out stop@1.0x, CW -> 0x03 Zoom In)
    for name, va, orig_b, new_b_raw, exp_mnem, exp_target in PATCH_SITES:
        off = _va_to_off(segs, va)
        got = data[off:off + len(orig_b)]
        assert got == orig_b, (
            f"Unexpected bytes at {name} 0x{va:08x}: got {got.hex()}, want {orig_b.hex()}"
        )
        new_b = verify_site(new_b_raw, va, len(orig_b), exp_mnem, exp_target)
        assert new_b != orig_b, f"Patch {name} @ 0x{va:08x} is a no-op!"
        out[off:off + len(new_b)] = new_b
        allowed_offsets.update(range(off, off + len(orig_b)))
        site_reports[name] = {
            "va": hex(va),
            "orig": orig_b.hex(),
            "new": new_b.hex(),
            "insn": f"{exp_mnem} #{hex(exp_target)}",
        }

    # 2. Patch the 80-byte State 3 onTransition block (D-pad Left/Right switches photo at 1.0x!)
    out[dpad_off:dpad_off + ONTRANS_DPAD_SIZE] = ONTRANS_DPAD_NEW

    assert len(out) == len(data), "viewUnified3.so file size must remain unchanged"
    changed_offsets = [i for i in range(len(data)) if out[i] != data[i]]
    assert set(changed_offsets).issubset(allowed_offsets), (
        f"Unexpected byte modifications outside patch sites: {changed_offsets}"
    )

    report = {
        "file_size_unchanged": len(out),
        "patch_site_count": len(PATCH_SITES),
        "ontrans_dpad_va": hex(ONTRANS_DPAD_VA),
        "ontrans_dpad_size": ONTRANS_DPAD_SIZE,
        "changed_bytes": len(changed_offsets),
        "sites": site_reports,
    }
    return bytes(out), report
