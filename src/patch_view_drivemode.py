"""Surgical in-place patcher for Sony ILCE-7R v3.20 `/usr/lib/viewUnified4.so`.

Integrates two clean native features with ZERO new PT_LOAD segments & ZERO file size change:
1. Full-Screen Drive Mode UI (`ViewRecDriveMode` — Rear Wheel Left & Camera Settings Tab 1):
   - Re-enables hidden leaf `menu_id = 0x0c` (`WRAPPER_DRIVE_MODE = 0x2e` -> `PID 0x07 = 0x24`)
     via a 1-byte `.text` patch (`0x000e6974`: `movs r2, #0` -> `movs r2, #1`).
   - Swaps Slot `[06]` (`0x00197bfc`, hidden Self-portrait, right after `Self-timer`) and
     Slot `[12]` (`0x00197cec`, leaf `id=0x0c`), updating the moved `R_ARM_RELATIVE` relocation.
   - Uses 100% untouched stock Sony icons (Option A, ZERO `image_*.uxc` edits):
       * Selected (116x100 orange): `0x0205D67E` (orange stacked frames)
       * Normal   (70x60 white):    `0x0205D3E9` (stopwatch + stacked frames, no self-timer digits!)
2. Fn Menu Shortcuts (`ViewFnMenu` — Slot 35 Interval Shoot & Slot 29 Auto ISO Min. SS):
   - Retargets `R_ARM_ABS32` at `0x0018e6f4` (Slot 35, `PID 0xEA`) and `0x0018e6dc` (Slot 29, `PID 0xEB`).
   - Rebuilds the 22-byte cursor-recall BST at `0x000b72bc..0x000b72d2` in strictly sorted order
     (pivot `0xd0` -> `0xb7352`, left `0xce` -> `0xb734a`, right `0xea` -> `0xb735e` & `0xeb` -> `0xb7346`)
     so Zebra (`0xd0`), Interval Shoot (`0xea`), and ISO AUTO Min. SS (`0xeb`) all recall cleanly!
"""
from __future__ import annotations

import struct

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs

from menu_spec import DM_HELP_KEY, DM_TITLE_KEY, PID_FN_INTV, PID_MIN_SS

RES_STR = 0x0204_0000
R_ARM_ABS32 = 2
R_ARM_RELATIVE = 23

# Verified ILCE-7R v3.20 ViewRecDriveMode addresses in viewUnified4.so
SETVALID_0C_VA = 0x000E6972
SETVALID_0C_ORIG = bytes.fromhex("0c210022")  # movs r1, #0xc; movs r2, #0
SETVALID_0C_NEW  = bytes.fromhex("0c210122")  # movs r1, #0xc; movs r2, #1

TOP_ARRAY_VA = 0x00197B0C
ITEM_SIZE = 40  # 10 uint32 words per CmnViewMenuItemStruct
SLOT_SELFPORTRAIT_IDX = 6   # @ 0x00197bfc (hidden, sits directly after Self-timer!)
SLOT_REMOTECDR_IDX    = 12  # @ 0x00197cec (hidden leaf id=0x0c)
SLOT_06_VA = TOP_ARRAY_VA + SLOT_SELFPORTRAIT_IDX * ITEM_SIZE  # 0x00197bfc
SLOT_12_VA = TOP_ARRAY_VA + SLOT_REMOTECDR_IDX * ITEM_SIZE     # 0x00197cec

SLOT_06_ORIG_CHILDREN = 0x00197A1C
SLOT_12_ORIG_ID = 0x0C
SLOT_12_ORIG_TITLE = 0x0204E100
SLOT_12_ORIG_ICON = 0x0205C128

# Option A: 100% untouched stock Sony icons (distinct from Self-timer 10/2/C3!)
ICON_SEL_INTV = 0x0205D67E   # 116x100 orange stacked frames
ICON_NORM_INTV = 0x0205D3E9  # 70x60 white stopwatch + stacked frames

# Verified ILCE-7R v3.20 ViewFnMenu slot 35 (fn_id 0x24 = unused ND Filter -> Interval Shoot)
FN_SLOT35_VA = 0x0018E6F4
FN_ORIG_SYM = b"cmnViewSettingNodeRootNDFilter"
FN_ANCHOR_SYM = b"cmnViewSettingNodeRootDriveMode"
FN_PID_TBL_36_VA = 0x001551DC
FN_PID_TBL_36_ORIG = 0xE1

# Verified ILCE-7R v3.20 ViewFnMenu slot 29 (fn_id 0x1E = unused Cinematone -> Auto ISO Min. SS)
FN_SLOT29_VA = 0x0018E6DC
FN_MINSS_ORIG_SYM = b"cmnViewSettingNodeRootCinematone"
FN_PID_TBL_30_VA = 0x001551C4
FN_PID_TBL_30_ORIG = 0xCF

# Verified ILCE-7R v3.20 ViewFnMenu cursor-recall BST right subtree @ 0x000b72bc..0x000b72d2 (22 bytes).
# Stock sorted BST: pivot 0xcf (-> 0xb7346, r3=0x1f), left 0xce (-> 0xb734a, r3=0x20),
#                   right 0xd0 (-> 0xb7352, r3=0x22 Zebra), 0xe1 (-> 0xb735e, r3=0x25).
# Patched strictly-sorted BST (0xce < 0xd0 < 0xea < 0xeb):
#   0x000b72bc: cmp r1, #0xd0  (d0 29)  | beq #0xb7352 (48 d0 -> r3=0x22 Zebra)
#   0x000b72c0: bgt #0xb72c8   (02 dc)  | cmp r1, #0xce (ce 29) | bne #0xb7362 (4d d1) | b #0xb734a (40 e0 -> r3=0x20)
#   0x000b72c8: cmp r1, #0xea  (ea 29)  | beq #0xb735e (48 d0 -> r3=0x25 Interval Shoot)
#   0x000b72cc: cmp r1, #0xeb  (eb 29)  | bne #0xb7362 (48 d1) | b #0xb7346 (39 e0 -> r3=0x1f ISO AUTO Min. SS)
FN_CURSOR_BST_VA = 0x000B72BC
FN_CURSOR_BST_ORIG = bytes.fromhex(
    "cf2942d002dcce294dd140e0d02942d0e12948d145e0"
)
FN_CURSOR_BST_NEW = bytes.fromhex(
    f"d02948d002dcce294dd140e0{PID_FN_INTV:02x}2948d0{PID_MIN_SS:02x}2948d139e0"
)
FN_CURSOR_BST_EXPECTED_DISASM: tuple[tuple[int, str, str], ...] = (
    (0x000B72BC, "cmp", "r1, #0xd0"),
    (0x000B72BE, "beq", "#0xb7352"),
    (0x000B72C0, "bgt", "#0xb72c8"),
    (0x000B72C2, "cmp", "r1, #0xce"),
    (0x000B72C4, "bne", "#0xb7362"),
    (0x000B72C6, "b",   "#0xb734a"),
    (0x000B72C8, "cmp", f"r1, #0x{PID_FN_INTV:x}"),
    (0x000B72CA, "beq", "#0xb735e"),
    (0x000B72CC, "cmp", f"r1, #0x{PID_MIN_SS:x}"),
    (0x000B72CE, "bne", "#0xb7362"),
    (0x000B72D0, "b",   "#0xb7346"),
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


def _dynamic(d: bytes, segs: list[tuple[int, ...]]) -> list[tuple[int, int]]:
    _, off, _, _, size, *_ = next(s for s in segs if s[0] == 2)
    return [struct.unpack_from("<II", d, off + 8 * i) for i in range(size // 8)]


def _sym_index(d: bytes, segs: list[tuple[int, ...]], dyn: list[tuple[int, int]], name: bytes) -> int:
    tags = {t: v for t, v in dyn if t}
    symtab = _va_to_off(segs, tags[6])
    strtab = _va_to_off(segs, tags[5])
    for i in range((strtab - symtab) // 16):
        st_name = struct.unpack_from("<I", d, symtab + 16 * i)[0]
        if d[strtab + st_name:strtab + st_name + len(name) + 1] == name + b"\0":
            return i
    raise KeyError(name)


def patch_view_drivemode(
    data: bytes,
    key_to_id: dict[str, int],
    fn_root_delta: int,
    fn_min_ss_delta: int,
) -> tuple[bytes, dict]:
    """Surgically patch `lib/viewUnified4.so` in-place for Drive Mode + Fn slots 29 & 35."""
    segs = _segments(data)
    dyn = _dynamic(data, segs)
    tags = {t: v for t, v in dyn if t}
    rel_va, rel_sz = tags[0x11], tags[0x12]
    rel_off = _va_to_off(segs, rel_va)

    # --- Pre-flight byte-exact assertions on stock viewUnified4.so ---
    sv_off = _va_to_off(segs, SETVALID_0C_VA)
    assert data[sv_off:sv_off + 4] == SETVALID_0C_ORIG, (
        f"Unexpected bytes at setValid(0x0c) 0x{SETVALID_0C_VA:08x}: "
        f"{data[sv_off:sv_off + 4].hex()}"
    )

    s06_off = _va_to_off(segs, SLOT_06_VA)
    s12_off = _va_to_off(segs, SLOT_12_VA)
    w06 = struct.unpack_from("<10I", data, s06_off)
    w12 = struct.unpack_from("<10I", data, s12_off)

    assert w06[0] == SLOT_06_ORIG_CHILDREN and w06[1] == 2 and w06[2] == 0x06, (
        f"Unexpected Slot [06] @ 0x{SLOT_06_VA:08x}: {[hex(x) for x in w06]}"
    )
    assert (
        w12[0] == 0
        and w12[1] == 0
        and w12[2] == SLOT_12_ORIG_ID
        and w12[3] == SLOT_12_ORIG_TITLE
        and w12[6] == SLOT_12_ORIG_ICON
        and w12[7] == 0x200
    ), f"Unexpected Slot [12] @ 0x{SLOT_12_VA:08x}: {[hex(x) for x in w12]}"

    # Pre-flight assertions on ViewFnMenu slot 35 (Interval Shoot) & slot 29 (Auto ISO Min. SS)
    fn_orig_sym_idx = _sym_index(data, segs, dyn, FN_ORIG_SYM)
    fn_minss_orig_sym_idx = _sym_index(data, segs, dyn, FN_MINSS_ORIG_SYM)
    fn_anchor_sym_idx = _sym_index(data, segs, dyn, FN_ANCHOR_SYM)

    fn_slot35_off = _va_to_off(segs, FN_SLOT35_VA)
    assert struct.unpack_from("<I", data, fn_slot35_off)[0] == 0, (
        f"Expected 0 addend at Fn slot 35 0x{FN_SLOT35_VA:08x}"
    )
    fn_pid_off = _va_to_off(segs, FN_PID_TBL_36_VA)
    assert struct.unpack_from("<I", data, fn_pid_off)[0] == FN_PID_TBL_36_ORIG, (
        f"Unexpected PID table[36] @ 0x{FN_PID_TBL_36_VA:08x}"
    )

    fn_slot29_off = _va_to_off(segs, FN_SLOT29_VA)
    assert struct.unpack_from("<I", data, fn_slot29_off)[0] == 0, (
        f"Expected 0 addend at Fn slot 29 0x{FN_SLOT29_VA:08x}"
    )
    fn_minss_pid_off = _va_to_off(segs, FN_PID_TBL_30_VA)
    assert struct.unpack_from("<I", data, fn_minss_pid_off)[0] == FN_PID_TBL_30_ORIG, (
        f"Unexpected PID table[30] @ 0x{FN_PID_TBL_30_VA:08x}"
    )

    fn_bst_off = _va_to_off(segs, FN_CURSOR_BST_VA)
    assert data[fn_bst_off:fn_bst_off + len(FN_CURSOR_BST_ORIG)] == FN_CURSOR_BST_ORIG, (
        f"Unexpected cursor BST @ 0x{FN_CURSOR_BST_VA:08x}: "
        f"{data[fn_bst_off:fn_bst_off + len(FN_CURSOR_BST_ORIG)].hex()}"
    )
    # Capstone round-trip verification of our strictly-sorted 22-byte cursor BST
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    dis = [(i.address, i.mnemonic, i.op_str) for i in md.disasm(FN_CURSOR_BST_NEW, FN_CURSOR_BST_VA)]
    assert tuple(dis) == FN_CURSOR_BST_EXPECTED_DISASM, f"BST disassembly mismatch: {dis}"

    # Locate exact relocations for Slot [06] children pointer AND ViewFnMenu slots 29 & 35
    slot06_rel_pos = None
    fn_slot35_rel_pos = None
    fn_slot29_rel_pos = None
    for i in range(0, rel_sz, 8):
        r_off, r_info = struct.unpack_from("<II", data, rel_off + i)
        if SLOT_06_VA <= r_off < SLOT_06_VA + ITEM_SIZE:
            assert r_off == SLOT_06_VA and r_info == R_ARM_RELATIVE, (
                f"Unexpected reloc in Slot [06]: off=0x{r_off:08x} info={r_info}"
            )
            assert slot06_rel_pos is None, "Duplicate reloc at Slot [06]"
            slot06_rel_pos = rel_off + i
        if SLOT_12_VA <= r_off < SLOT_12_VA + ITEM_SIZE:
            raise AssertionError(f"Unexpected reloc in leaf Slot [12]: off=0x{r_off:08x}")
        if r_off == FN_SLOT35_VA:
            assert r_info == ((fn_orig_sym_idx << 8) | R_ARM_ABS32), (
                f"Unexpected reloc at Fn slot 35 0x{FN_SLOT35_VA:08x}: info=0x{r_info:x}"
            )
            assert fn_slot35_rel_pos is None, "Duplicate reloc at Fn slot 35"
            fn_slot35_rel_pos = rel_off + i
        if r_off == FN_SLOT29_VA:
            assert r_info == ((fn_minss_orig_sym_idx << 8) | R_ARM_ABS32), (
                f"Unexpected reloc at Fn slot 29 0x{FN_SLOT29_VA:08x}: info=0x{r_info:x}"
            )
            assert fn_slot29_rel_pos is None, "Duplicate reloc at Fn slot 29"
            fn_slot29_rel_pos = rel_off + i

    assert slot06_rel_pos is not None, f"Missing R_ARM_RELATIVE at 0x{SLOT_06_VA:08x}"
    assert fn_slot35_rel_pos is not None, f"Missing R_ARM_ABS32 at 0x{FN_SLOT35_VA:08x}"
    assert fn_slot29_rel_pos is not None, f"Missing R_ARM_ABS32 at 0x{FN_SLOT29_VA:08x}"

    # Build patched output (exact same size!)
    out = bytearray(data)

    # 1. Enable menu_id = 0x0c in ViewRecDriveMode::initMenu
    out[sv_off:sv_off + 4] = SETVALID_0C_NEW

    # 2. Place Interval Shoot (id=0x0c) into Slot [06] (right after Self-timer!)
    title_res = RES_STR | key_to_id[DM_TITLE_KEY]
    help_res = RES_STR | key_to_id[DM_HELP_KEY]
    new_s06 = struct.pack(
        "<10I",
        0,               # children = NULL (leaf)
        0,               # child_count = 0
        0x0C,            # menu_id = 0x0c -> WRAPPER_DRIVE_MODE 0x2e -> PID 7 val 0x24
        title_res,       # localized "Interval Shoot" / "间隔拍摄"
        ICON_SEL_INTV,   # selected icon (116x100 orange stacked frames)
        help_res,        # localized help text
        ICON_NORM_INTV,  # normal icon (70x60 stopwatch + stacked frames)
        0x200,           # selectable leaf flag
        0,
        0,
    )
    out[s06_off:s06_off + ITEM_SIZE] = new_s06

    # 3. Move original hidden Slot [06] (Self-portrait, id=0x06) into Slot [12]
    out[s12_off:s12_off + ITEM_SIZE] = data[s06_off:s06_off + ITEM_SIZE]

    # 4. Update moved children pointer's R_ARM_RELATIVE relocation 0x00197bfc -> 0x00197cec
    struct.pack_into("<II", out, slot06_rel_pos, SLOT_12_VA, R_ARM_RELATIVE)

    # 5. Wire ViewFnMenu slot 35 (0x0018e6f4) -> cmnViewSettingNodeRootDriveMode + fn_root_delta
    struct.pack_into("<I", out, fn_slot35_off, fn_root_delta & 0xFFFFFFFF)
    struct.pack_into("<II", out, fn_slot35_rel_pos, FN_SLOT35_VA, (fn_anchor_sym_idx << 8) | R_ARM_ABS32)
    struct.pack_into("<I", out, fn_pid_off, PID_FN_INTV)

    # 6. Wire ViewFnMenu slot 29 (0x0018e6dc) -> cmnViewSettingNodeRootDriveMode + fn_min_ss_delta
    struct.pack_into("<I", out, fn_slot29_off, fn_min_ss_delta & 0xFFFFFFFF)
    struct.pack_into("<II", out, fn_slot29_rel_pos, FN_SLOT29_VA, (fn_anchor_sym_idx << 8) | R_ARM_ABS32)
    struct.pack_into("<I", out, fn_minss_pid_off, PID_MIN_SS)

    # 7. Write strictly-sorted 22-byte cursor-recall BST (0xce < 0xd0 < 0xea < 0xeb)
    out[fn_bst_off:fn_bst_off + len(FN_CURSOR_BST_NEW)] = FN_CURSOR_BST_NEW

    assert len(out) == len(data), "viewUnified4.so file size must remain unchanged"

    report = {
        "file_size_unchanged": len(out),
        "setvalid_0c_va": hex(SETVALID_0C_VA),
        "interval_slot_va": hex(SLOT_06_VA),
        "moved_hidden_slot_va": hex(SLOT_12_VA),
        "moved_reloc_file_off": hex(slot06_rel_pos),
        "title_res": hex(title_res),
        "help_res": hex(help_res),
        "icon_sel_res": hex(ICON_SEL_INTV),
        "icon_res": hex(ICON_NORM_INTV),
        "fn_slot35_va": hex(FN_SLOT35_VA),
        "fn_anchor_sym_idx": fn_anchor_sym_idx,
        "fn_root_delta": hex(fn_root_delta & 0xFFFFFFFF),
        "fn_cursor_pid": hex(PID_FN_INTV),
        "fn_slot29_va": hex(FN_SLOT29_VA),
        "fn_min_ss_delta": hex(fn_min_ss_delta & 0xFFFFFFFF),
        "fn_min_ss_cursor_pid": hex(PID_MIN_SS),
    }
    return bytes(out), report
