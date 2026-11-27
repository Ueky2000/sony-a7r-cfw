"""Patch Sony ILCE-7R v3.20 `lib/CautionConfig.so` with native Interval Shooting nodes:

1. Tab 2 Page 3 Folder (`Interval Shoot Func.` -> 6 native Backup settings PIDs 0xE4..0xE9).
2. Fn Menu Drive Mode Option (`cmnViewSettingNodesDriveModeTypeEmntMid`, 9 -> 10 entries):
   Inserts an `Interval Shoot` Drive Mode option node (`0x0f = 0x24`, `0x13 = 0x24`,
   untouched stock Sony icon `0x0205D3E9` stopwatch + stacked frames) directly after
   `Selftimer` (index 4), matching the exact order of `ViewRecDriveMode`.
3. Single-Slot Fn Menu Shortcut (`Interval Shoot Func.` -> opens Tab 2 P3 folder directly):
   - Inserts ONE `Interval Shoot Func.` option into ALL 12 ILCE-7R Function Menu Set.
     arrays (`cmnViewSettingNodesCustomFunction{1..12}Type03`, 28 -> 29 entries, placed
     right before `NotSet`), reusing the unused ND Filter Backup code (`0x25`):
       * Slot 1 (`CF1`): `0x0e = 0x21, 0x0f = 0x21` -> Backup byte `0x25`
       * Slots 2..12 (`CF2..12`): `0x0e = 0x22, 0x0f = 0x22` -> Backup byte `0x25`
   - Builds an `.init_array`-immune `fn_intv_root_node` (`0x0b = 4`, `PID = 0xEA`, icon
     `0x0205D3E9` / `0x0205D67E`) anchored via `cmnViewSettingNodeRootDriveMode + fn_root_delta`
     so `viewUnified4.so` (`ViewFnMenu` slot 35 / `fn_id = 0x24`) renders a single Fn tile
     that launches `view/SETTINGMENUX` (`bootElement = 0x77`) on Center/OK press.

ILCE-7R v3.20 verified CmnViewSettingNode / CmnViewSettingProperties layout:
- CmnViewSettingNode (40 bytes = 10 uint32 words):
    [vtable (_ZTV18CmnViewSettingNode + 8), kids**, count, props*, 0, 0, 0, 0, 0, 0]
- CmnViewSettingProperties (8 bytes = 2 uint32 words):
    [triples*, n_triples]
  Binary search in CautionConfig.so (0x000f4682) requires triples sorted strictly
  ascending by key; we also append a sentinel (0x7FFFFFFF, 0, 0) guard triple
  right after each list in memory (outside n_triples) for extra safety.
- A7R1 property keys (shifted -1 vs A6000, verified against CustomLensComp* & DriveMode*):
    0x00 (type 0): Title string resource (0x02040000 | uxc_id)
    0x01 (type 0): Icon resource         (0x0205xxxx)
    0x02 (type 0): Alt icon resource     (0x0205xxxx)
    0x04 (type 0): Selected icon res     (0x0205xxxx)
    0x05 (type 0): Help string resource  (0x02040000 | uxc_id)
    0x0a (type 1): Style (0 = folder/option, 2 = page/setting/root)
    0x0b (type 1): Kind  (0 = setting, 1 = option leaf, 2 = custom fn opt, 3 = folder, 4 = fn root)
    0x0d (type 1): ProcessId
    0x0e (type 1): Option index
    0x0f (type 1): Option value
    0x10 (type 1): DriveMode UI layout/index
    0x13 (type 1): Icon-set index
"""
from __future__ import annotations

import struct

from menu_spec import (
    DM_HELP_KEY,
    DM_TITLE_KEY,
    DM_VAL_INTERVAL,
    EXT_SHUTTER_SPEC,
    FOLDER_HELP_KEY,
    FOLDER_TITLE_KEY,
    MIN_SS_SPEC,
    PID_EXT_SHUTTER,
    PID_FN_INTV,
    PID_MIN_SS,
    SETTINGS,
)

ALIGN = 0x8000
R_ARM_ABS32 = 2
R_ARM_RELATIVE = 23
RES_STR = 0x0204_0000
REF_SYMBOL = b"cmnViewSettingNodeRootAelWShutter"
FN_REF_SYMBOL = b"cmnViewSettingNodeRootDriveMode"
VTABLE_SYMBOL = b"_ZTV18CmnViewSettingNode"

# Verified ILCE-7R v3.20 Drive Mode Fn-menu symbols in CautionConfig.so
DM_NODES_SYM = b"cmnViewSettingNodesDriveModeTypeEmntMid"
DM_NUM_SYM = b"cmnViewSettingNodesNumOfDriveModeTypeEmntMid"
DM_ORIG_COUNT = 9
DM_INSERT_IDX = 4  # Directly after Selftimer (index 3), matching ViewRecDriveMode!
# Option A: 100% untouched stock Sony icons (ZERO image_*.uxc modifications!)
DM_ICON_RES = 0x0205D3E9     # 70x60 stopwatch + stacked photo frames (distinct from self-timer!)
FN_ICON_NORM = 0x0205D3E9    # 70x60 normal tile icon in Fn Menu (Interval Shoot)
FN_ICON_SEL = 0x0205D67E     # 116x100 orange stacked frames selected icon (Interval Shoot)
DM_ICON_SET_IDX = 0x24

# Stock Sony icons for Auto ISO Min. SS Fn Menu tile (0 image_*.uxc modifications!)
MIN_SS_ICON_AUTO = 0x0205C42B      # ISO AUTO icon (for Slowest/Slower/Standard/Faster/Fastest)
MIN_SS_ICON_AUTO_SET = 0x7C
MIN_SS_ICON_FIXED = 0x0205C1CB     # Shutter Priority "S" icon (for fixed 1/8000..30" speeds)
MIN_SS_ICON_FIXED_SET = 0x1B

# Verified ILCE-7R (model 0x1b) Function Menu Set. arrays (12 slots x 28 stock items -> 30)
FN_CF_SLOTS = 12
FN_CF_ORIG_COUNT = 28
FN_CF_NEW_COUNT = 30
FN_CF_INSERT_IDX = 27  # Directly before NotSet (index 27 -> becomes index 29)
FN_CF1_VAL = 0x21      # PID 0x55 val_map[0x21] = 0x25 -> ViewFnMenu fn_id 0x24 (Interval Shoot)
FN_CF2_12_VAL = 0x22   # PIDs 0x56..0x60 val_map[0x22] = 0x25 -> ViewFnMenu fn_id 0x24
FN_MINSS_CF1_VAL = 0x18    # PID 0x55 val_map[0x18] = 0x2A -> ViewFnMenu fn_id 0x1E (slot 29 Cinematone)
FN_MINSS_CF2_12_VAL = 0x19 # PIDs 0x56..0x60 val_map[0x19] = 0x2A -> ViewFnMenu fn_id 0x1E


def cf_nodes_sym(slot: int) -> bytes:
    return f"cmnViewSettingNodesCustomFunction{slot}Type03".encode()


def cf_num_sym(slot: int) -> bytes:
    return f"cmnViewSettingNodesNumOfCustomFunction{slot}Type03".encode()


def _segments(d: bytes) -> list[tuple[int, ...]]:
    phoff = struct.unpack_from("<I", d, 28)[0]
    phnum = struct.unpack_from("<H", d, 44)[0]
    return [struct.unpack_from("<8I", d, phoff + 32 * i) for i in range(phnum)]


def _va_to_off(segs: list[tuple[int, ...]], va: int) -> int:
    for t, o, v, _, fs, _, _, _ in segs:
        if t == 1 and v <= va < v + fs:
            return o + va - v
    raise KeyError(f"VA 0x{va:08x} not in any PT_LOAD file range")


def _dynamic(d: bytes, segs: list[tuple[int, ...]]) -> tuple[int, list[tuple[int, int]]]:
    _, off, _, _, size, *_ = next(s for s in segs if s[0] == 2)
    return off, [struct.unpack_from("<II", d, off + 8 * i) for i in range(size // 8)]


def _sym_entry(
    d: bytes, segs: list[tuple[int, ...]], dyn: list[tuple[int, int]], name: bytes
) -> tuple[int, int, int]:
    """Return (sym_file_offset, st_value, st_size) for a dynamic symbol."""
    tags = {t: v for t, v in dyn if t}
    symtab = _va_to_off(segs, tags[6])
    strtab = _va_to_off(segs, tags[5])
    for i in range((strtab - symtab) // 16):
        ent_off = symtab + 16 * i
        st_name, st_value, st_size = struct.unpack_from("<III", d, ent_off)
        if d[strtab + st_name:strtab + st_name + len(name) + 1] == name + b"\0":
            return ent_off, st_value, st_size
    raise KeyError(name)


def _sym_va(d: bytes, segs: list[tuple[int, ...]], dyn: list[tuple[int, int]], name: bytes) -> int:
    return _sym_entry(d, segs, dyn, name)[1]


class _Arena:
    def __init__(self, base_va: int) -> None:
        self.base_va = base_va
        self.words: list[int] = []
        self.rels: list[tuple[int, int]] = []  # (r_offset, r_info)

    def alloc(
        self,
        words: list[int],
        rel_indices: tuple[int, ...] = (),
        rel_map: dict[int, int] | None = None,
    ) -> int:
        va = self.base_va + 4 * len(self.words)
        for idx in rel_indices:
            self.rels.append((va + 4 * idx, R_ARM_RELATIVE))
        for idx, rinfo in (rel_map or {}).items():
            self.rels.append((va + 4 * idx, rinfo))
        self.words.extend(w & 0xFFFFFFFF for w in words)
        return va


def _build_nodes(
    arena: _Arena,
    vtable: int,
    ids: dict[str, int],
    dm_orig_words: list[int],
    dm_orig_rels: list[int],
    cf_orig_list: list[tuple[list[int], list[int]]],
) -> tuple[int, int, int, int, int, int, int, int, list[int]]:
    """Build Tab 2 P3 (Interval + Min. SS), Tab 2 P5 (Extended Shutter & BULB), Fn Drive Mode & 12 Fn arrays.

    Returns (folder_va, min_ss_va, ext_shutter_va, dm_opt_va, dm_arr_va, fn_root_va, fn_opt_cf1_va, fn_opt_cf2_va, cf_arr_vas).
    """
    def make_node(triples: list[tuple[int, int, int]], children: list[int]) -> int:
        for a, b in zip(triples, triples[1:]):
            assert a[0] < b[0], f"Unsorted property keys: {triples}"
        flat = [x for t in triples for x in t] + [0x7FFFFFFF, 0, 0]  # sentinel guard
        lst_va = arena.alloc(flat)
        if children:
            kids_va = arena.alloc(children, tuple(range(len(children))))
        else:
            kids_va = arena.alloc([0])
        props_va = arena.alloc([lst_va, len(triples)], (0,))
        return arena.alloc([vtable, kids_va, len(children), props_va, 0, 0, 0, 0, 0, 0], (0, 1, 3))

    res = lambda k: RES_STR | ids[k]

    # 1. Tab 2 Page 3 settings folder (Interval Shoot Func.)
    setting_nodes: list[int] = []
    for s in SETTINGS:
        opt_nodes: list[int] = []
        for idx, (opt_key, _) in enumerate(s.options):
            opt_triples = [
                (0x00, 0, res(opt_key)),
                (0x05, 0, res(s.help_key)),
                (0x0A, 1, 0),
                (0x0B, 1, 1),
                (0x0E, 1, idx),
                (0x0F, 1, idx),
            ]
            opt_nodes.append(make_node(opt_triples, []))
        setting_triples = [
            (0x00, 0, res(s.title_key)),
            (0x05, 0, res(s.help_key)),
            (0x0A, 1, 2),
            (0x0B, 1, 0),
            (0x0D, 1, s.pid),
        ]
        setting_nodes.append(make_node(setting_triples, opt_nodes))

    page_node = make_node([(0x0A, 1, 2)], setting_nodes)
    folder_node = make_node(
        [
            (0x00, 0, res(FOLDER_TITLE_KEY)),
            (0x05, 0, res(FOLDER_HELP_KEY)),
            (0x0A, 1, 0),
            (0x0B, 1, 3),
        ],
        [page_node],
    )

    # 1b. Tab 2 Page 3 Item 6 + Fn Menu Slot 29 shared node: ISO AUTO Min. SS (24 options, PID 0xEB)
    # Identical to Sony's cmnViewSettingNodeRootZebra (0x0b = 0): works natively in BOTH
    # ViewSettingMenu (Tab 2 P3) AND ViewFnMenu (Fn slot 29 inline dial/picker)!
    min_ss_opts: list[int] = []
    for idx, (opt_key, opt_val) in enumerate(MIN_SS_SPEC.options):
        is_fixed = bool(opt_val & 0x00010000)
        icon = MIN_SS_ICON_FIXED if is_fixed else MIN_SS_ICON_AUTO
        icon_set = MIN_SS_ICON_FIXED_SET if is_fixed else MIN_SS_ICON_AUTO_SET
        min_ss_opts.append(
            make_node(
                [
                    (0x00, 0, res(opt_key)),
                    (0x01, 0, icon),
                    (0x02, 0, icon),
                    (0x05, 0, res(MIN_SS_SPEC.help_key)),
                    (0x0A, 1, 0),
                    (0x0B, 1, 1),
                    (0x0E, 1, idx),
                    (0x0F, 1, idx),
                    (0x13, 1, icon_set),
                ],
                [],
            )
        )
    min_ss_node = make_node(
        [
            (0x00, 0, res(MIN_SS_SPEC.title_key)),
            (0x05, 0, res(MIN_SS_SPEC.help_key)),
            (0x0A, 1, 2),
            (0x0B, 1, 0),
            (0x0D, 1, PID_MIN_SS),
        ],
        min_ss_opts,
    )

    # 1c. Tab 2 Page 5 Item 5 toggle: Extended Shutter Speed & BULB (Off/On, PID 0xEC)
    ext_ss_opts: list[int] = []
    for idx, (opt_key, _) in enumerate(EXT_SHUTTER_SPEC.options):
        ext_ss_opts.append(
            make_node(
                [
                    (0x00, 0, res(opt_key)),
                    (0x05, 0, res(EXT_SHUTTER_SPEC.help_key)),
                    (0x0A, 1, 0),
                    (0x0B, 1, 1),
                    (0x0E, 1, idx),
                    (0x0F, 1, idx),
                ],
                [],
            )
        )
    ext_shutter_node = make_node(
        [
            (0x00, 0, res(EXT_SHUTTER_SPEC.title_key)),
            (0x05, 0, res(EXT_SHUTTER_SPEC.help_key)),
            (0x0A, 1, 2),
            (0x0B, 1, 0),
            (0x0D, 1, PID_EXT_SHUTTER),
        ],
        ext_ss_opts,
    )

    # 2. Fn Menu Drive Mode option node (Interval Shoot -> PID 0x07 value 0x24)
    dm_opt_node = make_node(
        [
            (0x00, 0, res(DM_TITLE_KEY)),
            (0x01, 0, DM_ICON_RES),
            (0x05, 0, res(DM_HELP_KEY)),
            (0x0A, 1, 0),
            (0x0B, 1, 1),
            (0x0F, 1, DM_VAL_INTERVAL),
            (0x10, 1, DM_ICON_SET_IDX),
            (0x13, 1, DM_ICON_SET_IDX),
        ],
        [],
    )

    # 3. Expanded 10-entry cmnViewSettingNodesDriveModeTypeEmntMid array
    dm_words = (
        dm_orig_words[:DM_INSERT_IDX]
        + [dm_opt_node]
        + dm_orig_words[DM_INSERT_IDX:]
    )
    dm_rel_map: dict[int, int] = {}
    for i in range(DM_INSERT_IDX):
        dm_rel_map[i] = dm_orig_rels[i]
    dm_rel_map[DM_INSERT_IDX] = R_ARM_RELATIVE
    for i in range(DM_INSERT_IDX, len(dm_orig_words)):
        dm_rel_map[i + 1] = dm_orig_rels[i]
    dm_arr_va = arena.alloc(dm_words, rel_map=dm_rel_map)

    # 4. Function Menu Set. options (28 -> 30 entries: +Interval Shoot Func. & +ISO AUTO Min. SS before NotSet)
    fn_opt_cf1 = make_node(
        [
            (0x00, 0, res(FOLDER_TITLE_KEY)),
            (0x05, 0, res(FOLDER_HELP_KEY)),
            (0x0A, 1, 0),
            (0x0B, 1, 2),
            (0x0E, 1, FN_CF1_VAL),
            (0x0F, 1, FN_CF1_VAL),
        ],
        [],
    )
    fn_opt_cf2_12 = make_node(
        [
            (0x00, 0, res(FOLDER_TITLE_KEY)),
            (0x05, 0, res(FOLDER_HELP_KEY)),
            (0x0A, 1, 0),
            (0x0B, 1, 2),
            (0x0E, 1, FN_CF2_12_VAL),
            (0x0F, 1, FN_CF2_12_VAL),
        ],
        [],
    )
    fn_minss_cf1 = make_node(
        [
            (0x00, 0, res(MIN_SS_SPEC.title_key)),
            (0x05, 0, res(MIN_SS_SPEC.help_key)),
            (0x0A, 1, 0),
            (0x0B, 1, 2),
            (0x0E, 1, FN_MINSS_CF1_VAL),
            (0x0F, 1, FN_MINSS_CF1_VAL),
        ],
        [],
    )
    fn_minss_cf2_12 = make_node(
        [
            (0x00, 0, res(MIN_SS_SPEC.title_key)),
            (0x05, 0, res(MIN_SS_SPEC.help_key)),
            (0x0A, 1, 0),
            (0x0B, 1, 2),
            (0x0E, 1, FN_MINSS_CF2_12_VAL),
            (0x0F, 1, FN_MINSS_CF2_12_VAL),
        ],
        [],
    )

    cf_arr_vas: list[int] = []
    for idx_zero, (orig_words, orig_rels) in enumerate(cf_orig_list):
        opt_intv = fn_opt_cf1 if idx_zero == 0 else fn_opt_cf2_12
        opt_minss = fn_minss_cf1 if idx_zero == 0 else fn_minss_cf2_12
        words = orig_words[:FN_CF_INSERT_IDX] + [opt_intv, opt_minss] + orig_words[FN_CF_INSERT_IDX:]
        rel_map: dict[int, int] = {}
        for i in range(FN_CF_INSERT_IDX):
            rel_map[i] = orig_rels[i]
        rel_map[FN_CF_INSERT_IDX] = R_ARM_RELATIVE
        rel_map[FN_CF_INSERT_IDX + 1] = R_ARM_RELATIVE
        for i in range(FN_CF_INSERT_IDX, len(orig_words)):
            rel_map[i + 2] = orig_rels[i]
        cf_arr_vas.append(arena.alloc(words, rel_map=rel_map))

    # 5. ViewFnMenu single-slot root node (replaces unused NDFilter slot 35 / fn_id 0x24)
    fn_intv_leaf = make_node(
        [
            (0x00, 0, res(FOLDER_TITLE_KEY)),
            (0x01, 0, FN_ICON_NORM),
            (0x02, 0, FN_ICON_NORM),
            (0x04, 0, FN_ICON_SEL),
            (0x05, 0, res(FOLDER_HELP_KEY)),
            (0x0A, 1, 0),
            (0x0B, 1, 1),
            (0x0F, 1, 0),
            (0x13, 1, DM_ICON_SET_IDX),
        ],
        [],
    )
    fn_intv_root = make_node(
        [
            (0x00, 0, res(FOLDER_TITLE_KEY)),
            (0x05, 0, res(FOLDER_HELP_KEY)),
            (0x0A, 1, 2),
            (0x0B, 1, 4),
            (0x0D, 1, PID_FN_INTV),
        ],
        [fn_intv_leaf],
    )

    return (
        folder_node,
        min_ss_node,
        ext_shutter_node,
        dm_opt_node,
        dm_arr_va,
        fn_intv_root,
        fn_opt_cf1,
        fn_opt_cf2_12,
        cf_arr_vas,
    )


def patch_caution(data: bytes, key_to_id: dict[str, int]) -> tuple[bytes, int, int, int, int, int, dict]:
    """Append RW PT_LOAD segment to CautionConfig.so with menu, Drive Mode, and Fn nodes.

    Returns (patched_bytes, folder_delta, min_ss_delta, ext_shutter_delta, fn_root_delta, fn_min_ss_delta, report).
    """
    segs = _segments(data)
    dyn_off, dyn = _dynamic(data, segs)
    tags = {t: v for t, v in dyn if t}
    rel_va, rel_sz = tags[0x11], tags[0x12]
    orig_rel = data[_va_to_off(segs, rel_va):_va_to_off(segs, rel_va) + rel_sz]
    by_off = {
        struct.unpack_from("<I", orig_rel, i)[0]: struct.unpack_from("<I", orig_rel, i + 4)[0]
        for i in range(0, rel_sz, 8)
    }

    vtable = _sym_va(data, segs, dyn, VTABLE_SYMBOL) + 8
    ref_va = _sym_va(data, segs, dyn, REF_SYMBOL)
    fn_ref_va = _sym_va(data, segs, dyn, FN_REF_SYMBOL)

    # Verify stock Fn Menu Drive Mode symbols & relocations
    dm_sym_off, dm_orig_va, dm_orig_sz = _sym_entry(data, segs, dyn, DM_NODES_SYM)
    _, dm_num_va, _ = _sym_entry(data, segs, dyn, DM_NUM_SYM)
    assert dm_orig_sz == 4 * DM_ORIG_COUNT, f"Unexpected EmntMid size: {dm_orig_sz}"
    dm_num_off = _va_to_off(segs, dm_num_va)
    orig_dm_num = struct.unpack_from("<I", data, dm_num_off)[0]
    assert orig_dm_num == DM_ORIG_COUNT, f"Unexpected EmntMid count: {orig_dm_num}"

    dm_orig_off = _va_to_off(segs, dm_orig_va)
    dm_orig_words = list(struct.unpack_from(f"<{DM_ORIG_COUNT}I", data, dm_orig_off))
    dm_orig_rels = [by_off[dm_orig_va + 4 * i] for i in range(DM_ORIG_COUNT)]
    assert all((r & 0xFF) == R_ARM_ABS32 for r in dm_orig_rels), "Expected R_ARM_ABS32 on stock EmntMid"

    # Verify all 12 stock Function Menu Set. CustomFunction{1..12}Type03 arrays & counts
    cf_meta: list[tuple[int, int, int]] = []  # (sym_off, num_off, num_va)
    cf_orig_list: list[tuple[list[int], list[int]]] = []
    for slot in range(1, FN_CF_SLOTS + 1):
        sym_off, orig_va, orig_sz = _sym_entry(data, segs, dyn, cf_nodes_sym(slot))
        _, num_va, _ = _sym_entry(data, segs, dyn, cf_num_sym(slot))
        assert orig_sz == 4 * FN_CF_ORIG_COUNT, f"CF{slot} unexpected size: {orig_sz}"
        num_off = _va_to_off(segs, num_va)
        orig_num = struct.unpack_from("<I", data, num_off)[0]
        assert orig_num == FN_CF_ORIG_COUNT, f"CF{slot} unexpected count: {orig_num}"
        arr_off = _va_to_off(segs, orig_va)
        words = list(struct.unpack_from(f"<{FN_CF_ORIG_COUNT}I", data, arr_off))
        rels = [by_off[orig_va + 4 * i] for i in range(FN_CF_ORIG_COUNT)]
        assert all((r & 0xFF) == R_ARM_ABS32 for r in rels), f"Expected R_ARM_ABS32 on stock CF{slot}"
        cf_meta.append((sym_off, num_off, num_va))
        cf_orig_list.append((words, rels))

    top = max(v + ms for t, _, v, _, _, ms, _, _ in segs if t == 1)
    new_va = (top + ALIGN - 1) // ALIGN * ALIGN
    new_off = (len(data) + ALIGN - 1) // ALIGN * ALIGN

    # Pass 1: determine exact relocation count
    probe = _Arena(new_va)
    _build_nodes(probe, vtable, key_to_id, dm_orig_words, dm_orig_rels, cf_orig_list)
    n_extra_rel = len(probe.rels)

    # Pass 2: place arena right after relocated .rel.dyn (16-byte aligned)
    arena_va = new_va + ((rel_sz + 8 * n_extra_rel + 15) // 16 * 16)
    arena = _Arena(arena_va)
    (
        folder_va,
        min_ss_va,
        ext_shutter_va,
        dm_opt_va,
        dm_arr_va,
        fn_root_va,
        fn_opt_cf1_va,
        fn_opt_cf2_va,
        cf_arr_vas,
    ) = _build_nodes(
        arena, vtable, key_to_id, dm_orig_words, dm_orig_rels, cf_orig_list
    )
    assert len(arena.rels) == n_extra_rel

    rel_new = bytearray(orig_rel)
    for rva, rinfo in arena.rels:
        rel_new += struct.pack("<II", rva, rinfo)
    assert len(rel_new) == rel_sz + 8 * n_extra_rel

    blob = struct.pack(f"<{len(arena.words)}I", *arena.words)
    body = bytes(rel_new).ljust(arena_va - new_va, b"\0") + blob
    body = body.ljust((len(body) + 15) // 16 * 16, b"\0")

    phdr_off = new_off + len(body)
    new_ph = [list(s) for s in segs] + [[1, new_off, new_va, new_va, 0, 0, 6, ALIGN]]
    seg_total = len(body) + 32 * len(new_ph)
    new_ph[-1][4] = new_ph[-1][5] = seg_total
    body += b"".join(struct.pack("<8I", *s) for s in new_ph)

    out = bytearray(data.ljust(new_off, b"\0") + body)
    struct.pack_into("<I", out, 28, phdr_off)
    struct.pack_into("<H", out, 44, len(new_ph))

    # Update Fn Menu Drive Mode count (9 -> 10) and repoint EmntMid symbol to new 10-slot array
    struct.pack_into("<I", out, dm_num_off, DM_ORIG_COUNT + 1)
    struct.pack_into("<II", out, dm_sym_off + 4, dm_arr_va, 4 * (DM_ORIG_COUNT + 1))

    # Update all 12 Function Menu Set. counts (28 -> 30) and repoint symbols to new 30-slot arrays
    for (sym_off, num_off, _), new_cf_arr_va in zip(cf_meta, cf_arr_vas):
        struct.pack_into("<I", out, num_off, FN_CF_NEW_COUNT)
        struct.pack_into("<II", out, sym_off + 4, new_cf_arr_va, 4 * FN_CF_NEW_COUNT)

    # Point DT_REL / DT_RELSZ to the extended relocation table
    for i, (t, _) in enumerate(dyn):
        if t == 0x11:  # DT_REL
            struct.pack_into("<I", out, dyn_off + 8 * i + 4, new_va)
        elif t == 0x12:  # DT_RELSZ
            struct.pack_into("<I", out, dyn_off + 8 * i + 4, len(rel_new))

    folder_delta = (folder_va - ref_va) & 0xFFFFFFFF
    min_ss_delta = (min_ss_va - ref_va) & 0xFFFFFFFF
    ext_shutter_delta = (ext_shutter_va - ref_va) & 0xFFFFFFFF
    fn_root_delta = (fn_root_va - fn_ref_va) & 0xFFFFFFFF
    fn_min_ss_delta = (min_ss_va - fn_ref_va) & 0xFFFFFFFF
    report = {
        "vtable_va": hex(vtable),
        "ref_va": hex(ref_va),
        "folder_va": hex(folder_va),
        "delta": hex(folder_delta),
        "min_ss_va": hex(min_ss_va),
        "min_ss_delta": hex(min_ss_delta),
        "ext_shutter_va": hex(ext_shutter_va),
        "ext_shutter_delta": hex(ext_shutter_delta),
        "fn_ref_va": hex(fn_ref_va),
        "fn_root_va": hex(fn_root_va),
        "fn_root_delta": hex(fn_root_delta),
        "fn_min_ss_delta": hex(fn_min_ss_delta),
        "fn_opt_cf1_va": hex(fn_opt_cf1_va),
        "fn_opt_cf2_12_va": hex(fn_opt_cf2_va),
        "fn_cf_count": FN_CF_NEW_COUNT,
        "fn_cf_arr_vas": [hex(v) for v in cf_arr_vas],
        "dm_opt_va": hex(dm_opt_va),
        "dm_arr_va": hex(dm_arr_va),
        "dm_count": DM_ORIG_COUNT + 1,
        "dm_num_va": hex(dm_num_va),
        "dm_icon_res": hex(DM_ICON_RES),
        "new_seg_va": hex(new_va),
        "new_seg_size": seg_total,
        "extra_relocs": n_extra_rel,
    }
    return bytes(out), folder_delta, min_ss_delta, ext_shutter_delta, fn_root_delta, fn_min_ss_delta, report
