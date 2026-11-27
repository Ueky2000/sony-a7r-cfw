"""Surgical 2-block in-place patcher for Sony ILCE-7R v3.20 `/dev/nflasha3` (`av-cam.bin`).

Adds native hardware 60fps BIONZ "Auto ISO Min. Shutter Speed" (ISO AUTO 最低快门速度)
behavior identical to Sony A7R II / A7 III in P and A modes, while keeping:
- TPZL Block 0 of `nflasha3` 100% byte-identical to official Sony v3.20
- FAT12 boot sector, FAT tables, and root directory 100% byte-identical (ZERO metadata edits!)
- 353 of 355 TPZL blocks in `nflasha3` 100% byte-identical (ONLY Block #38 & Block #55 touched!)
- ZERO code caves, ZERO relocated pointers, ZERO file-size changes in `av-cam.bin`

Wire protocol sent via Sony's hidden stock IPC channel:
  Linux `ModelCamera (@M00B)` Event 0x7E (key 0x39F) -> `ObjCamera 0x7664` ->
  `LIRO 0x40CE` -> `av-cam.bin 0x43781c00` -> `[LiroParam + 0x204]` -> `[CamState + 0x43C]`:
  - `0x8000FE00` : Auto Slowest  (-2 EV around 1/f)
  - `0x8000FF00` : Auto Slower   (-1 EV around 1/f)
  - `0x80000000` : Auto Standard ( 0 EV around 1/f, full 1mm..1000mm table; manual lens -> 1/60s)
  - `0x80000100` : Auto Faster   (+1 EV around 1/f)
  - `0x80000200` : Auto Fastest  (+2 EV around 1/f)
  - `0x8001xxxx` : Fixed Sony APEX Tv `xxxx` (`0x0B00` = 30" .. `0x1D00` = 1/8000s)
  - `0` / `-1` / positive us : 100% unmodified stock Sony behavior!

Two surgical in-place sites inside contiguous `av-cam.bin` (loaded at VA `0x43541000`,
located at FAT12 byte offset `0x9600` inside decompressed `nflasha3`):
1. Block #38 (`0x437a7638..0x437a7645`, 5 bytes changed):
   Stock `0x437a7638: cmp.w r0, #-1; str.w r0, [r4, #0x43c]; str.w r2, [r4, #0x304]; bne 0x437a7650`
   Patched to:
   `0x437a7638: cmp.w r0, #0; ...; bpl 0x437a7650`
   Effect:
     * Raw 32-bit `r0` is still stored verbatim into `[CamState + 0x43C]`.
     * For any `r0` with bit 31 set (`-1` or our `0x8000xxxx`/`0x8001xxxx`), `N=1` skips the
       microsecond log2 converter and stores `0x0AAA` into `[CamState + 0x440]`, disabling
       hard `Tv_min` clamping in BOTH Still (`0x438aa5ce`) and Movie/EE (`0x437aa2d2`) so the
       camera never underexposes when ISO hits `ISO_MAX`!
     * For `r0 == 0` and all positive microsecond values (`1..0x7FFFFFFF`), behavior is 100% stock.
2. Block #55 (`CalcHandShakeTv @ 0x438abba8..0x438abc27`, 128 bytes in-place):
   Replaces `CalcHandShakeTv` + its private 8-byte literal pool (`0x438abc20..0x438abc27`)
   with a 124-byte Thumb-2 implementation + 4-byte literal (`0x43ed016e`, verified identical to
   `*(0x43f706d0) + 0x100`):
     * In P (`mode == 0`) or A (`mode == 1`) with custom `r5` (`(int32_t)(r5 + 1) < 0`):
       - Bit 16 == 1 -> Fixed APEX Tv `r5 & 0xFFFF`
       - Bit 16 == 0 -> `FocalLengthToApexTv(fl ? fl : 60) + (int16_t)(r5 & 0xFFFF)`
     * In all other modes (S, M, iAuto, Superior Auto, Scene, Panorama, etc.) or when `r5` is
       `0` / `-1` / positive us -> 100% bit-for-bit identical stock Sony `CalcHandShakeTv` logic
       (30mm floor, exact `0x1600`/`0x1755` mode/OSS floor, `0x1900` cap).
"""
from __future__ import annotations

import struct

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
from keystone import KS_ARCH_ARM, KS_MODE_THUMB, Ks

AVCAM_BASE_VA = 0x43541000
AVCAM_FAT12_OFF = 0x9600
AVCAM_SIZE = 0x00A31444
N3_PARTITION_LIMIT = 24 * 1024 * 1024  # 24 MiB (/dev/nflasha3; stock is ~11.7 MiB)

# Exact TPZL blocks allowed to change in nflasha3 (out of 355 total blocks)
EXPECTED_CHANGED_BLOCKS = (38, 55)

# Site 1 (Block #38): ShutLowLimit LIRO consumer sign-bit check
SITE1_CMP_VA = 0x437A7638
SITE1_CMP_ORIG = bytes.fromhex("b0f1ff3f")  # cmp.w r0, #-1
SITE1_CMP_NEW = bytes.fromhex("b0f1000f")   # cmp.w r0, #0
SITE1_B_VA = 0x437A7644
SITE1_B_ORIG = bytes.fromhex("04d1")        # bne #0x437a7650
SITE1_B_NEW = bytes.fromhex("04d5")         # bpl #0x437a7650

# Site 2 (Block #55): CalcHandShakeTv in-place replacement (128 bytes including private literal pool)
SITE2_VA = 0x438ABBA8
SITE2_SIZE = 0x80  # 0x438abba8..0x438abc27 (128 bytes)
SITE2_ORIG_HEAD = bytes.fromhex("f7b5064632f2c7f8")  # push {r0-r2,r4-r7,lr}; mov r6,r0; bl 0x43addd3e
SITE2_ORIG_TAIL = bytes.fromhex("bdf80600febd00bf") + struct.pack("<II", 0x006BF0F2, 0x00005A24)

# Verified stock av-cam.bin targets referenced by CalcHandShakeTv
FN_GET_EXP_MODE = 0x43ADDD3E    # returns 0=P, 1=A, 2=S, 3=M, 4=iAuto, 5=SupAuto, 7/8/0x1d/0x2c=Scene...
FN_FL_TO_APEX_TV = 0x43829358   # FocalLengthToApexTv(fl_mm) -> [0x1000 (1s) .. 0x1A00 (1/1024s)]
FN_MIN_U16 = 0x43AADB3E         # cmp r1, r0; it lo; movlo r0, r1; bx lr -> unsigned min(r0, r1)
GOT_SLOT_VA = 0x43F706D0        # stock GOT entry containing static VA 0x43ed006e
STATIC_FLAG_BASE = 0x43ED006E
STATIC_FLAG_VA = STATIC_FLAG_BASE + 0x100  # 0x43ed016e: u16 OSS/mode 1/160s floor flag

# Stock instruction fingerprints at external targets (safety check)
TARGET_FINGERPRINTS: tuple[tuple[str, int, bytes], ...] = (
    ("FN_GET_EXP_MODE",  FN_GET_EXP_MODE,  bytes.fromhex("d0f86826")),  # ldr.w r2, [r0, #0x668]
    ("FN_FL_TO_APEX_TV", FN_FL_TO_APEX_TV, bytes.fromhex("134b07b5")),  # ldr r3,[pc,#0x4c]; push {r0-r2,lr}
    ("FN_MIN_U16",       FN_MIN_U16,       bytes.fromhex("814238bf08467047")),  # full 8-byte leaf fn
    ("LIRO_40CE_HDL",    0x43781C00,       bytes.fromhex("417a027a")),  # ldrb r1,[r0,#9]; ldrb r2,[r0,#8]
    ("STILL_AE_CLAMP",   0x438AA5CE,       bytes.fromhex("b5f84034")),  # ldrh.w r3, [r5, #0x440]
)

# Wire encoding helper constants (shared with menu_spec.py, interval_engine.c, and tests)
MIN_SS_AUTO_SLOWEST  = 0x8000FE00  # -2 EV
MIN_SS_AUTO_SLOWER   = 0x8000FF00  # -1 EV
MIN_SS_AUTO_STANDARD = 0x80000000  #  0 EV (1/f)
MIN_SS_AUTO_FASTER   = 0x80000100  # +1 EV
MIN_SS_AUTO_FASTEST  = 0x80000200  # +2 EV
MIN_SS_FIXED_PREFIX  = 0x80010000  # | apex_tv (0x0B00..0x1D00)


def _asm_calc_handshake_tv() -> bytes:
    """Assemble the 128-byte in-place `CalcHandShakeTv` replacement at `0x438abba8`."""
    # Keystone doesn't accept `.word` mixed cleanly with PC-rel label distance checks
    # if alignment drifts, so we assemble the Thumb-2 instructions cleanly with a known
    # literal slot at offset +0x7C (0x438abc24) and verify every instruction via Capstone!
    asm = f"""
        push   {{r4, r5, r6, lr}}
        mov    r6, r0
        bl     #{FN_GET_EXP_MODE:#x}
        mov    r4, r0
        ldr.w  r5, [r6, #0x43c]
        ldrh.w r0, [r6, #0x51c]
        cmp    r4, #1
        bhi    stock_fl
        adds   r3, r5, #1
        bge    stock_fl
        lsls   r3, r5, #15
        bpl    custom_auto
        uxth   r0, r5
        pop    {{r4, r5, r6, pc}}

    custom_auto:
        cbnz   r0, custom_fl_ok
        movs   r0, #60
    custom_fl_ok:
        bl     #{FN_FL_TO_APEX_TV:#x}
        sxth   r5, r5
        adds   r0, r0, r5
        pop    {{r4, r5, r6, pc}}

    stock_fl:
        cmp    r0, #0x1e
        it     lo
        movlo  r0, #0x1e
        bl     #{FN_FL_TO_APEX_TV:#x}
        mov.w  r1, #0x1600
        cmp    r4, #0x1d
        beq    use_1755
        subs   r3, r4, #4
        cmp    r3, #1
        bls    chk_oss
        cmp    r4, #7
        beq    chk_oss
        cmp    r4, #0x2c
        beq    chk_oss
        cmp    r4, #8
        bne    stock_clamp
        ldrb.w r3, [r6, #0x69f]
        cbnz   r3, stock_clamp
        b      use_1755

    chk_oss:
        ldr.w  r3, [r6, #0x63c]
        cbnz   r3, stock_clamp
        ldr    r3, lit_flag
        ldrh   r3, [r3]
        cbz    r3, stock_clamp
    use_1755:
        movw   r1, #0x1755
    stock_clamp:
        cmp    r0, r1
        it     lo
        movlo  r0, r1
        mov.w  r1, #0x1900
        bl     #{FN_MIN_U16:#x}
        pop    {{r4, r5, r6, pc}}
    .balign 4
    lit_flag:
        .word  {STATIC_FLAG_VA:#x}
    """
    ks = Ks(KS_ARCH_ARM, KS_MODE_THUMB)
    enc, _ = ks.asm(asm, SITE2_VA)
    blob = bytes(enc)
    assert len(blob) == SITE2_SIZE, f"CalcHandShakeTv patch size {len(blob)} != {SITE2_SIZE}"

    # Independent Capstone round-trip disassembly check (verify BL targets & literal load!)
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    lit_off = blob.rfind(struct.pack("<I", STATIC_FLAG_VA))
    assert lit_off == SITE2_SIZE - 4, f"Expected literal at +0x7c, got +0x{lit_off:x}"
    insns = list(md.disasm(blob[:lit_off], SITE2_VA))
    bl_targets = [
        int(ins.op_str.lstrip("#"), 16)
        for ins in insns
        if ins.mnemonic == "bl"
    ]
    assert bl_targets == [FN_GET_EXP_MODE, FN_FL_TO_APEX_TV, FN_FL_TO_APEX_TV, FN_MIN_U16], (
        f"Unexpected BL targets in CalcHandShakeTv: {[hex(x) for x in bl_targets]}"
    )
    ldr_lit_hits = []
    for ins in insns:
        if ins.mnemonic == "ldr" and ins.op_str.startswith("r3, [pc,"):
            imm = int(ins.op_str.split("#")[1].rstrip("]"), 0)
            target_lit_va = ((ins.address + 4) & ~3) + imm
            ldr_lit_hits.append(target_lit_va)
    assert ldr_lit_hits == [SITE2_VA + lit_off], (
        f"Literal pool PC-rel mismatch: got {[hex(x) for x in ldr_lit_hits]}, want 0x{SITE2_VA + lit_off:x}"
    )
    return blob


def patch_avcam_bytes(avcam: bytes) -> tuple[bytes, dict]:
    """Apply the 2 surgical in-place patches to `av-cam.bin` bytes (exact same size!)."""
    assert len(avcam) == AVCAM_SIZE, f"Unexpected av-cam.bin size: 0x{len(avcam):x} != 0x{AVCAM_SIZE:x}"

    # Pre-flight byte-exact sanity assertions on stock av-cam.bin
    for name, va, want in TARGET_FINGERPRINTS:
        off = va - AVCAM_BASE_VA
        got = avcam[off:off + len(want)]
        assert got == want, f"av-cam.bin fingerprint mismatch at {name} (0x{va:08x}): {got.hex()} != {want.hex()}"

    got_val = struct.unpack_from("<I", avcam, GOT_SLOT_VA - AVCAM_BASE_VA)[0]
    assert got_val == STATIC_FLAG_BASE, (
        f"Unexpected GOT value at 0x{GOT_SLOT_VA:08x}: 0x{got_val:08x} != 0x{STATIC_FLAG_BASE:08x}"
    )

    s1_cmp_off = SITE1_CMP_VA - AVCAM_BASE_VA
    s1_b_off = SITE1_B_VA - AVCAM_BASE_VA
    assert avcam[s1_cmp_off:s1_cmp_off + 4] == SITE1_CMP_ORIG, "Site 1 cmp.w mismatch"
    assert avcam[s1_b_off:s1_b_off + 2] == SITE1_B_ORIG, "Site 1 bne mismatch"

    s2_off = SITE2_VA - AVCAM_BASE_VA
    assert avcam[s2_off:s2_off + len(SITE2_ORIG_HEAD)] == SITE2_ORIG_HEAD, "Site 2 head mismatch"
    assert avcam[s2_off + SITE2_SIZE - len(SITE2_ORIG_TAIL):s2_off + SITE2_SIZE] == SITE2_ORIG_TAIL, (
        "Site 2 tail/literal mismatch"
    )

    site2_new = _asm_calc_handshake_tv()
    out = bytearray(avcam)
    out[s1_cmp_off:s1_cmp_off + 4] = SITE1_CMP_NEW
    out[s1_b_off:s1_b_off + 2] = SITE1_B_NEW
    out[s2_off:s2_off + SITE2_SIZE] = site2_new

    assert len(out) == len(avcam)
    changed_bytes = sum(1 for a, b in zip(avcam, out) if a != b)
    report = {
        "avcam_size": len(out),
        "site1_cmp_va": hex(SITE1_CMP_VA),
        "site1_b_va": hex(SITE1_B_VA),
        "site2_va": hex(SITE2_VA),
        "site2_size": SITE2_SIZE,
        "static_flag_va": hex(STATIC_FLAG_VA),
        "total_changed_bytes": changed_bytes,
    }
    return bytes(out), report


def patch_nflasha3_decompressed(n3_dec: bytes) -> tuple[bytes, dict]:
    """Patch `av-cam.bin` strictly in-place inside decompressed `nflasha3` FAT12 image.

    Touches ZERO FAT12 metadata bytes (boot sector, FATs, root dir, and all other files
    remain 100% byte-identical) and ONLY changes bytes within TPZL blocks #38 and #55.
    """
    av_orig = n3_dec[AVCAM_FAT12_OFF:AVCAM_FAT12_OFF + AVCAM_SIZE]
    av_new, rep = patch_avcam_bytes(av_orig)

    out = bytearray(n3_dec)
    out[AVCAM_FAT12_OFF:AVCAM_FAT12_OFF + AVCAM_SIZE] = av_new

    # Verify ONLY expected 64 KiB TPZL blocks changed (and NEVER Block 0!)
    changed_blocks = [
        blk
        for blk in range(len(n3_dec) // 0x10000)
        if out[blk * 0x10000:(blk + 1) * 0x10000] != n3_dec[blk * 0x10000:(blk + 1) * 0x10000]
    ]
    assert tuple(changed_blocks) == EXPECTED_CHANGED_BLOCKS, (
        f"Expected changed nflasha3 TPZL blocks {EXPECTED_CHANGED_BLOCKS}, got {changed_blocks}"
    )
    assert out[:AVCAM_FAT12_OFF] == n3_dec[:AVCAM_FAT12_OFF], "FAT12 metadata modified!"
    assert out[AVCAM_FAT12_OFF + AVCAM_SIZE:] == n3_dec[AVCAM_FAT12_OFF + AVCAM_SIZE:], (
        "Bytes after av-cam.bin modified!"
    )

    rep["changed_tpzl_blocks"] = changed_blocks
    rep["avcam_fat12_offset"] = hex(AVCAM_FAT12_OFF)
    return bytes(out), rep


if __name__ == "__main__":
    from pathlib import Path
    import tpzl_mod

    root = Path(__file__).resolve().parents[1]
    n3_path = root / "firmware/unpacked/fw320/firmware.tar_unpacked/0700_part_image/dev/nflasha3"
    n3_tpzl = tpzl_mod.TpzlImage.parse(n3_path.read_bytes())
    n3_dec = b"".join(tpzl_mod.decode_block(n3_tpzl.block(i), n3_tpzl.block_size) for i in range(len(n3_tpzl.entries)))
    patched_dec, rep = patch_nflasha3_decompressed(n3_dec)
    print("patch_avcam self-test PASS:", rep)
    # Disassemble the assembled CalcHandShakeTv for visual inspection!
    av_new = patched_dec[AVCAM_FAT12_OFF:AVCAM_FAT12_OFF + AVCAM_SIZE]
    s2 = av_new[SITE2_VA - AVCAM_BASE_VA:SITE2_VA - AVCAM_BASE_VA + SITE2_SIZE]
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    print("\nAssembled CalcHandShakeTv (0x438abba8..0x438abc27):")
    lit_off = s2.rfind(struct.pack("<I", STATIC_FLAG_VA))
    for ins in md.disasm(s2[:lit_off], SITE2_VA):
        print(f"  0x{ins.address:08x}: {ins.bytes.hex():8s} {ins.mnemonic:8s} {ins.op_str}")
    print(f"  0x{SITE2_VA + lit_off:08x}: .word 0x{struct.unpack_from('<I', s2, lit_off)[0]:08x} (total {len(s2)} B)")
