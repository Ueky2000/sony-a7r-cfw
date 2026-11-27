"""Unified, audited patcher for Sony ILCE-7R v3.20 `/usr/lib/libObj.so` (CFW v3.33).

Performs three clean, verified modifications in a SINGLE appended RWX PT_LOAD segment:
1. Menu Page Insertions (zero page overflow):
   - Root[2] Tab 2 (Custom Settings) Page 3 (`node=0x0147e864`, `arr=0x013f4090`, items 4 -> 6):
     Slot 4 = `Interval Shoot Func.` folder (`folder_delta`)
     Slot 5 = `ISO AUTO Min. SS` 24-option setting (`min_ss_delta`)
   - Root[2] Tab 2 (Custom Settings) Page 5 (`node=0x0147dca8`, `arr=0x013f3a98`, items 4 -> 5):
     Slot 4 = `Extended Shutter & BULB` Off/On toggle (`ext_shutter_delta`)
2. `CmnViewProcessDataMgr` PID Space Expansion (`0x00..0xE3` -> `0x00..0xEF`):
   - Updates the three 2-byte `cmp rN, #0xe3` bounds checks in-place to `#0xef`.
   - Copies and extends `ELEM` (`0x0104290c`) and `D` (`0x01336dac`) tables and
     repoints their three PC-relative literals.
   - Emits native A7R1 12-word (48-byte) descriptors for:
     * `PID 0xE4..0xE9`: Interval Shooting (6 Backup settings, `disp_sleep` migrated to `0x01070121`)
     * `PID 0xEA`: Single-slot Fn shortcut launcher (`view/SETTINGMENUX`, `bootElement=0x77`)
     * `PID 0xEB`: Auto ISO Min. SS (`Backup 0x010700D0`, 24 options + Fn launcher `view/SETTINGMENUX`, `bootElement=0x78`)
     * `PID 0xEC`: Extended Shutter & BULB (`Backup 0x01070147`, 2 options)
3. Unified Native Engine (`src/interval_engine.c`, 21 verified Thumb-2 `b.w` hooks + 24 fingerprints):
   - Compiles freestanding `-fropi` Thumb-2 C with Zig's embedded Clang + LLD.
   - Reserves a 256-byte zeroed `INTERVAL_STATE` scratch block in the same RWX segment.
"""
from __future__ import annotations

import struct
import subprocess
import tempfile
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
from elftools.elf.elffile import ELFFile
from keystone import KS_ARCH_ARM, KS_MODE_THUMB, Ks

from menu_spec import (
    ALL_BACKUP_SETTINGS,
    N_ORIG_PIDS,
    PID_EXT_SHUTTER,
    PID_FN_INTV,
    PID_MAX,
    PID_MIN_SS,
    VSM_BOOT_INTV,
    VSM_BOOT_MIN_SS,
)

ALIGN = 0x8000
R_ARM_ABS32 = 2
R_ARM_RELATIVE = 23

# Verified ILCE-7R v3.20 "view/SETTINGMENUX" string inside libObj.so .rodata
SETTINGMENUX_STR_VA = 0x010577D2
SETTINGMENUX_STR_BYTES = b"view/SETTINGMENUX\x00"

# Verified ILCE-7R v3.20 Tab 2 (Custom Settings) Page 3 constructor sites (4 -> 6)
TAB2_P3_ARR_VA = 0x013F4090
TAB2_P3_ORIG_CNT = 4
TAB2_P3_NEW_CNT = 6
TAB2_P3_MOVS_VA = 0x00811040  # `04 22` -> movs r2, #4
TAB2_P3_LIT_VA = 0x00811AD0
TAB2_P3_ADD_VA = 0x0081104C   # `add r1, pc` -> pc = 0x00811050

# Verified ILCE-7R v3.20 Tab 2 (Custom Settings) Page 5 constructor sites (4 -> 5)
TAB2_P5_ARR_VA = 0x013F3A98
TAB2_P5_ORIG_CNT = 4
TAB2_P5_NEW_CNT = 5
TAB2_P5_MOVS_VA = 0x00811088  # `04 22` -> movs r2, #4
TAB2_P5_LIT_VA = 0x00811AE8
TAB2_P5_ADD_VA = 0x00811094   # `add r1, pc` -> pc = 0x00811098

REF_SYMBOL = b"cmnViewSettingNodeRootAelWShutter"

# Verified ILCE-7R v3.20 CmnViewProcessDataMgr tables & bounds checks
ELEM_VA = 0x0104290C
D_VA = 0x01336DAC
PID_BACKUP_TPL = 0x55
NADA_VAL = 0x0A45
DESC_WORDS = 12  # Verified A7R1 descriptor size = 48 bytes (12 uint32 words)

# (cmp_va, orig_2bytes, new_2bytes, lit_va, add_pc_va, orig_table_va)
PID_SITES = (
    (0x00841698, bytes.fromhex("e329"), bytes.fromhex(f"{PID_MAX:02x}29"), 0x008416C8, 0x008416AE, ELEM_VA),
    (0x00910462, bytes.fromhex("e32a"), bytes.fromhex(f"{PID_MAX:02x}2a"), 0x00910480, 0x0091046E, D_VA),
    (0x0091048C, bytes.fromhex("e329"), bytes.fromhex(f"{PID_MAX:02x}29"), 0x009104A8, 0x00910498, D_VA),
)

# Verified ILCE-7R v3.20 4-byte Thumb-2 hook sites: (name, va, orig_4bytes, target_sym)
CAPSTART_VA = 0x00723C24
CAPSTART_ORIG_BYTES = bytes.fromhex("f0b597b0")  # push {r4,r5,r6,r7,lr}; sub sp, #0x5c

HOOK_SITES: tuple[tuple[str, int, bytes, str], ...] = (
    ("capstart",       0x00723C24, bytes.fromhex("f0b597b0"), "interval_capstart_trampoline"),
    ("capstop",        0x00721B90, bytes.fromhex("90b589b0"), "interval_capstop_trampoline"),
    ("focstart",       0x00722300, bytes.fromhex("90b589b0"), "interval_focstart_trampoline"),
    ("spinal_cmd",     0x00718D14, bytes.fromhex("b0b500af"), "spinal_cmd_hook"),
    ("shift_ss",         0x00551474, bytes.fromhex("b0b50c46"), "ext_ss_shift_trampoline"),
    ("info35_num",       0x0053ECC6, bytes.fromhex("d5f80012"), "ext_ss_num_trampoline"),
    ("info35_den",       0x0053ECD6, bytes.fromhex("d5f80412"), "ext_ss_den_trampoline"),
    ("fast_disp_memcpy", 0x0050E9C4, bytes.fromhex("87f498ed"), "fast_disp_get_trampoline"),
    ("pdm_setValue",     0x0084173E, bytes.fromhex("98b500af"), "pdm_setValue_hook"),
    ("pdm_getValue",   0x0084188A, bytes.fromhex("98b500af"), "pdm_getValue_hook"),
    ("pdm_isInvalid",  0x00841756, bytes.fromhex("98b500af"), "pdm_isInvalid_trampoline"),
    ("pdm_isDisable",  0x00841782, bytes.fromhex("98b500af"), "pdm_isDisable_trampoline"),
    ("pdm_isGrayed",   0x008417AE, bytes.fromhex("98b500af"), "pdm_isGrayedOut_trampoline"),
    ("pdm_getCaution", 0x008417DA, bytes.fromhex("f8b500af"), "pdm_getCaution_trampoline"),
    ("act_drivemode",  0x009651EE, bytes.fromhex("d5f88815"), "interval_act_drivemode_trampoline"),
    ("act_dm_destroy", 0x00964734, bytes.fromhex("80b500af"), "act_drivemode_destroy_hook"),
    ("remain_fmt_a",   0x00924D9A, bytes.fromhex("05f5d674"), "interval_remain_a_trampoline"),
    ("remain_fmt_b",   0x00924826, bytes.fromhex("05f5d674"), "interval_remain_b_trampoline"),
    ("remain_dtor",    0x00918A84, bytes.fromhex("f8b500af"), "act_remain_dtor_hook"),
    ("vsm_onTrans",    0x0080DA7C, bytes.fromhex("2de9f041"), "vsm_onTransition_trampoline"),
    ("vsm_initNode",   0x0080CF84, bytes.fromhex("f0b58bb0"), "vsm_initMenuNode_trampoline"),
)

LIBOBJ_SYMS = {
    "G_TIMER_MGR":        0x014A61C0,  # BSS global: TimerManager* timerManager
    "V_CAPSTART_CONT":    0x00723C29,  # Thumb: 0x00723C24 + 4 | 1
    "V_CAPSTOP_CONT":     0x00721B95,  # Thumb: 0x00721B90 + 4 | 1
    "V_FOCSTART_CONT":    0x00722305,  # Thumb: 0x00722300 + 4 | 1
    "V_SPINAL_CONT":      0x00718D19,  # Thumb: 0x00718D14 + 4 | 1
    "V_SHIFT_SS_CONT":    0x00551479,  # Thumb: 0x00551474 + 4 | 1
    "V_INFO35_NUM_CONT":  0x0053ECCB,  # Thumb: 0x0053ECC6 + 4 | 1
    "V_INFO35_DEN_CONT":  0x0053ECDB,  # Thumb: 0x0053ECD6 + 4 | 1
    "V_FAST_DISP_CONT":   0x0050E9C9,  # Thumb: 0x0050E9C4 + 4 | 1
    "V_NOTIFY_UPDATE":    0x004C4B11,  # Thumb: ModelBase::notifyUpdate(this, infoId)
    "V_GET_UI_DISP":      0x0050E8E5,  # Thumb: UI EventDispatcher singleton getter
    "V_POST_UI_EVENT":    0x00AF4A21,  # Thumb: UI EventDispatcher postEvent(disp, ev)
    "P_MEMCPY":           0x001964F8,  # ARM PLT: memcpy
    "P_EVENT_CTOR":       0x0018F6F4,  # ARM PLT: Event::Event(ulong, uchar, uchar)
    "V_PDM_SETVAL_CONT":  0x00841743,  # Thumb: 0x0084173E + 4 | 1
    "V_PDM_GETVAL_CONT":  0x0084188F,  # Thumb: 0x0084188A + 4 | 1
    "V_PDM_INVALID_CONT": 0x0084175B,  # Thumb: 0x00841756 + 4 | 1
    "V_PDM_DISABLE_CONT": 0x00841787,  # Thumb: 0x00841782 + 4 | 1
    "V_PDM_GRAY_CONT":    0x008417B3,  # Thumb: 0x008417AE + 4 | 1
    "V_PDM_CAUTION_CONT": 0x008417DF,  # Thumb: 0x008417DA + 4 | 1
    "V_ACT_DM_CONT":      0x009651F3,  # Thumb: 0x009651EE + 4 | 1
    "V_REMAIN_A_CONT":    0x00924D9F,  # Thumb: 0x00924D9A + 4 | 1
    "V_REMAIN_B_CONT":    0x0092482B,  # Thumb: 0x00924826 + 4 | 1
    "V_REMAIN_DTOR_CONT": 0x00918A89,  # Thumb: 0x00918A84 + 4 | 1
    "V_VSM_ONTRANS_CONT": 0x0080DA81,  # Thumb: 0x0080DA7C + 4 | 1
    "V_VSM_INITNODE_CONT":0x0080CF89,  # Thumb: 0x0080CF84 + 4 | 1
    "V_VSM_SELECT_NODE":  0x0080C13F,  # Thumb: ViewSettingMenu::selectNode(node*)
    "V_VSM_SYNC_INDICES": 0x0080A2AB,  # Thumb: ViewSettingMenu::syncTabPageItem()
    "V_VSM_GET_PAGE":     0x0080B5CB,  # Thumb: ViewSettingMenu::getCurrentPage()
    "V_VSM_SET_PAGE_TAB": 0x0080B2E1,  # Thumb: ViewSettingMenu::setPageTab(page, tab)
    "V_VSM_ACTION7":      0x00809F65,  # Thumb: ViewSettingMenu Action 7 (init sub-menu keys)
    "P_VIEW_GET_EVID":    0x00195BAC,  # ARM PLT: ViewBase::GetEventId() const
    "V_PRM_CTOR":         0x004553D5,  # Thumb: PrmNumber::PrmNumber(int)
    "V_PRM_GETNUM":       0x0044FC01,  # Thumb: PrmNumber::getNumber()
    "V_PL_GET":           0x0044FC09,  # Thumb: ParamList::get(key) -> ParamBase*
    "V_TIME_NOW":         0x00AF57AB,  # Thumb: timeGetNow(timespec*)
    "V_TIME_ADD_MS":      0x00AF5A41,  # Thumb: timeAddMS(timespec*, uint)
    "V_TMR_ADD":          0x00AF19B3,  # Thumb: TimerManager::add(...)
    "V_TMR_REMOVE":       0x00AF1A0D,  # Thumb: TimerManager::remove(...)
    "V_WAKEUP_LOOP":      0x00AF22E1,  # Thumb: EventLoop wakeup pipe write
    "V_CAP_START_CMD":    0x007214E5,  # Thumb: ModelStillRec::pvt_ExeStillCaptureStartCmd(model, type)
    "V_CAP_STOP_CMD":     0x00721645,  # Thumb: ModelStillRec::pvt_ExeStillCaptureStopCmd(model, type)
    "V_AF_START_CMD":     0x00722165,  # Thumb: ModelStillRec::pvt_ExeForcusStartCmd(model, type)
    "V_AF_STOP_CMD":      0x007218E9,  # Thumb: ModelStillRec::pvt_ExeForcusStopCmd(model, type)
    "V_AFMF_ON_CMD":      0x00720B85,  # Thumb: ModelStillRec::pvt_ExeAfMfChangeOnCmd(model)
    "V_AFMF_OFF_CMD":     0x00720C55,  # Thumb: ModelStillRec::pvt_ExeAfMfChangeOffCmd(model)
    "V_GET_FOCUS_MODE":   0x0086E3F5,  # Thumb: CmnViewModelWrpCameraFocus::getFocusMode() (1=MF,2=AF)
    "V_SET_DISP_STATE":   0x0050047F,  # Thumb: LayoutableWidgetBase::setDispState(bool)
    "V_REMAIN_UPDATE":    0x00925325,  # Thumb: ACT_LabelStillRemain::updateDisplay()
    "P_REQ_EXEC":         0x00504BE9,  # Thumb: viewManagerIf::requestModelExecute (direct export)
    "P_GET_MODEL_INFO":   0x0019716C,  # ARM PLT: CmnViewModelIfWrapper::getModelInfo
    "P_PL_CTOR":          0x00AF64A3,  # Thumb: ParamList::ParamList() (direct export)
    "P_PL_DTOR":          0x00195980,  # ARM PLT: ParamList::~ParamList()
    "P_PL_ADD":           0x00AF694B,  # Thumb: ParamList::add(ulong, ParamBase*) (direct export)
    "P_NEW":              0x00190454,  # ARM PLT: operator new(unsigned int)
    "P_BK_READ":          0x00196880,  # ARM PLT: Backup_read
    "P_BK_WRITE":         0x001922EC,  # ARM PLT: BackupManager::Bkup_Write(int, const void*)
}

DIRECT_TARGET_FINGERPRINTS: tuple[tuple[str, int, bytes], ...] = (
    ("V_PRM_GETNUM",     0x0044FC00, bytes.fromhex("80b500af")),
    ("V_PL_GET",         0x0044FC08, bytes.fromhex("80b50122")),
    ("V_SET_DISP_STATE", 0x0050047E, bytes.fromhex("b0b500af")),
    ("V_TIME_NOW",       0x00AF57AA, bytes.fromhex("80b50146")),
    ("V_TIME_ADD_MS",    0x00AF5A40, bytes.fromhex("b0b50d46")),
    ("V_TMR_ADD",        0x00AF19B2, bytes.fromhex("2de9f843")),
    ("V_TMR_REMOVE",     0x00AF1A0C, bytes.fromhex("f8b500af")),
    ("V_WAKEUP_LOOP",    0x00AF22E0, bytes.fromhex("98b50122")),
    ("V_CAP_START_CMD",  0x007214E4, bytes.fromhex("f0b50d46")),
    ("V_CAP_STOP_CMD",   0x00721644, bytes.fromhex("f0b50c46")),
    ("V_AF_START_CMD",   0x00722164, bytes.fromhex("f0b50c46")),
    ("V_AF_STOP_CMD",    0x007218E8, bytes.fromhex("f0b50c46")),
    ("V_AFMF_ON_CMD",    0x00720B84, bytes.fromhex("b0b588b0")),
    ("V_AFMF_OFF_CMD",   0x00720C54, bytes.fromhex("b0b588b0")),
    ("V_GET_FOCUS_MODE", 0x0086E3F4, bytes.fromhex("90b583b0")),
    ("V_REMAIN_UPDATE",  0x00925324, bytes.fromhex("b0b500af")),
    ("V_VSM_SELECT_NODE",      0x0080C13E, bytes.fromhex("b0b582b0")),
    ("V_VSM_SYNC_INDICES",     0x0080A2AA, bytes.fromhex("90b585b0")),
    ("V_VSM_GET_PAGE",         0x0080B5CA, bytes.fromhex("80b582b0")),
    ("V_VSM_SET_PAGE_TAB",     0x0080B2E0, bytes.fromhex("b2f1ff3f")),
    ("V_VSM_ACTION7",          0x00809F64, bytes.fromhex("b0b52221")),
    ("V_NOTIFY_UPDATE",        0x004C4B10, bytes.fromhex("80b584b0")),
    ("V_GET_UI_DISP",          0x0050E8E4, bytes.fromhex("f8b500af")),
    ("V_POST_UI_EVENT",        0x00AF4A20, bytes.fromhex("b0b50446")),
)

CODE_RESERVE = 0x2000   # 8 KiB reserved for compiled Thumb-2 unified engine
STATE_SIZE = 0x100      # 256 bytes zeroed RW state block


def find_zig(root: Path) -> Path:
    cands = sorted((root / "tools").glob("zig-*/zig.exe"))
    if not cands:
        raise FileNotFoundError("Portable zig.exe not found under tools/zig-*")
    return cands[-1]


def compile_engine(
    c_src: Path,
    zig_exe: Path,
    code_va: int,
    state_va: int,
    sym_overrides: dict[str, int] | None = None,
) -> tuple[bytes, dict[str, int]]:
    """Compile `interval_engine.c` to a flat Thumb-2 binary linked at `code_va`.

    Returns (flat_bytes, {symbol_name: offset_from_code_va}).
    """
    syms = dict(LIBOBJ_SYMS)
    syms["INTERVAL_STATE"] = state_va
    syms["G_TAB2_P3_NEW_ARR"] = state_va + STATE_SIZE
    if sym_overrides:
        syms.update(sym_overrides)

    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        obj = td / "engine.o"
        elf_out = td / "engine.elf"

        subprocess.run(
            [
                str(zig_exe), "cc",
                "-target", "thumb-freestanding-eabi",
                "-mcpu=cortex_a5",
                "-mthumb",
                "-mfloat-abi=soft",
                "-Os",
                "-ffreestanding",
                "-fno-builtin",
                "-fno-stack-protector",
                "-fropi",
                "-fno-exceptions",
                "-fno-asynchronous-unwind-tables",
                "-nostdlib",
                "-Wall", "-Wextra", "-Werror",
                "-c", str(c_src),
                "-o", str(obj),
            ],
            check=True,
        )

        defsyms = [f"--defsym={k}={v:#x}" for k, v in syms.items()]
        subprocess.run(
            [
                str(zig_exe), "ld.lld",
                "-m", "armelf",
                "--omagic",
                f"-Ttext={code_va:#x}",
                f"--entry={code_va:#x}",
                *defsyms,
                "-o", str(elf_out),
                str(obj),
            ],
            check=True,
        )

        with open(elf_out, "rb") as f:
            ef = ELFFile(f)
            alloc_secs = [
                s for s in ef.iter_sections()
                if (s["sh_flags"] & 0x2) and s["sh_size"] > 0
            ]
            for s in alloc_secs:
                if s["sh_flags"] & 0x1:  # SHF_WRITE
                    raise AssertionError(
                        f"Writable section {s.name!r} found in -fropi engine! Must use INTERVAL_STATE."
                    )
                if s["sh_type"] == "SHT_NOBITS":
                    raise AssertionError(f"BSS section {s.name!r} found in -fropi engine!")

            min_va = min(s["sh_addr"] for s in alloc_secs)
            max_va = max(s["sh_addr"] + s["sh_size"] for s in alloc_secs)
            assert min_va == code_va, f"Expected base 0x{code_va:x}, got 0x{min_va:x}"
            flat = bytearray(max_va - min_va)
            for s in alloc_secs:
                off = s["sh_addr"] - min_va
                flat[off:off + s["sh_size"]] = s.data()

            symtab = ef.get_section_by_name(".symtab")
            sym_offsets: dict[str, int] = {}
            for s in symtab.iter_symbols():
                if s.name and s["st_shndx"] != "SHN_UNDEF" and s["st_shndx"] != "SHN_ABS":
                    if not s.name.startswith("$"):
                        sym_offsets[s.name] = (s["st_value"] & ~1) - code_va

        return bytes(flat), sym_offsets


def _segments(d: bytes) -> list[tuple[int, ...]]:
    phoff = struct.unpack_from("<I", d, 28)[0]
    phnum = struct.unpack_from("<H", d, 44)[0]
    return [struct.unpack_from("<8I", d, phoff + 32 * i) for i in range(phnum)]


def _va_to_off(segs: list[tuple[int, ...]], va: int) -> int:
    for t, o, v, _, fs, _, _, _ in segs:
        if t == 1 and v <= va < v + fs:
            return o + va - v
    raise KeyError(f"VA 0x{va:08x} not mapped in file")


def _dynamic(d: bytes, segs: list[tuple[int, ...]]) -> tuple[int, list[tuple[int, int]]]:
    _, off, _, _, size, *_ = next(s for s in segs if s[0] == 2)
    return off, [struct.unpack_from("<II", d, off + 8 * i) for i in range(size // 8)]


def _sym_index(d: bytes, segs: list[tuple[int, ...]], dyn: list[tuple[int, int]], name: bytes) -> int:
    tags = {t: v for t, v in dyn if t}
    symtab = _va_to_off(segs, tags[6])
    strtab = _va_to_off(segs, tags[5])
    for i in range((strtab - symtab) // 16):
        st_name = struct.unpack_from("<I", d, symtab + 16 * i)[0]
        if d[strtab + st_name:strtab + st_name + len(name) + 1] == name + b"\0":
            return i
    raise KeyError(name)


def _asm_thumb(src: str, va: int) -> bytes:
    enc, _ = Ks(KS_ARCH_ARM, KS_MODE_THUMB).asm(src, va)
    return bytes(enc)


def patch_libobj(
    data: bytes,
    folder_delta: int,
    min_ss_delta: int,
    ext_shutter_delta: int,
    c_src: Path,
    zig_exe: Path,
) -> tuple[bytes, dict]:
    """Apply Tab 2 P3/P5 menu insertions + PID expansion + unified 20-hook Thumb-2 engine."""
    segs = _segments(data)
    dyn_off, dyn = _dynamic(data, segs)
    tags = {t: v for t, v in dyn if t}
    rel_va, rel_sz = tags[0x11], tags[0x12]
    orig_rel = data[_va_to_off(segs, rel_va):_va_to_off(segs, rel_va) + rel_sz]
    by_off = {
        struct.unpack_from("<I", orig_rel, i)[0]: struct.unpack_from("<I", orig_rel, i + 4)[0]
        for i in range(0, rel_sz, 8)
    }

    # --- Pre-flight byte-exact sanity assertions on stock libObj.so ---
    for h_name, h_va, h_orig, _ in HOOK_SITES:
        got = data[_va_to_off(segs, h_va):_va_to_off(segs, h_va) + 4]
        assert got == h_orig, (
            f"Unexpected bytes at hook {h_name} 0x{h_va:08x}: got {got.hex()}, want {h_orig.hex()}"
        )
    for t_name, t_va, t_orig in DIRECT_TARGET_FINGERPRINTS:
        got = data[_va_to_off(segs, t_va):_va_to_off(segs, t_va) + 4]
        assert got == t_orig, (
            f"Unexpected bytes at {t_name} 0x{t_va:08x}: got {got.hex()}, want {t_orig.hex()}"
        )
    smenu_off = _va_to_off(segs, SETTINGMENUX_STR_VA)
    assert data[smenu_off:smenu_off + len(SETTINGMENUX_STR_BYTES)] == SETTINGMENUX_STR_BYTES, (
        f"Expected 'view/SETTINGMENUX' at 0x{SETTINGMENUX_STR_VA:08x}"
    )

    assert struct.unpack_from("<H", data, _va_to_off(segs, TAB2_P3_MOVS_VA))[0] == (0x2200 | TAB2_P3_ORIG_CNT), \
        "Unexpected movs r2,#4 at Tab 2 P3"
    orig_p3_lit = struct.unpack_from("<I", data, _va_to_off(segs, TAB2_P3_LIT_VA))[0]
    assert ((TAB2_P3_ADD_VA + 4 + orig_p3_lit) & 0xFFFFFFFF) == TAB2_P3_ARR_VA, \
        "Tab 2 P3 literal mismatch"

    assert struct.unpack_from("<H", data, _va_to_off(segs, TAB2_P5_MOVS_VA))[0] == (0x2200 | TAB2_P5_ORIG_CNT), \
        "Unexpected movs r2,#4 at Tab 2 P5"
    orig_p5_lit = struct.unpack_from("<I", data, _va_to_off(segs, TAB2_P5_LIT_VA))[0]
    assert ((TAB2_P5_ADD_VA + 4 + orig_p5_lit) & 0xFFFFFFFF) == TAB2_P5_ARR_VA, \
        "Tab 2 P5 literal mismatch"

    for cmp_va, orig_b, _, lit_va, add_pc, tbl_va in PID_SITES:
        assert data[_va_to_off(segs, cmp_va):_va_to_off(segs, cmp_va) + 2] == orig_b, \
            f"Unexpected cmp bytes at 0x{cmp_va:08x}"
        got_lit = struct.unpack_from("<I", data, _va_to_off(segs, lit_va))[0]
        assert ((add_pc + got_lit) & 0xFFFFFFFF) == tbl_va, \
            f"Unexpected literal at 0x{lit_va:08x}"

    ref_sym_idx = _sym_index(data, segs, dyn, REF_SYMBOL)

    elem_orig = list(struct.unpack_from(f"<{N_ORIG_PIDS}I", data, _va_to_off(segs, ELEM_VA)))
    d_orig = list(struct.unpack_from(f"<{N_ORIG_PIDS}I", data, _va_to_off(segs, D_VA)))
    assert elem_orig[PID_BACKUP_TPL] == 0, "Expected elem[0x55] == 0 (Normal)"
    tpl_desc = list(struct.unpack_from(f"<{DESC_WORDS}I", data, _va_to_off(segs, d_orig[PID_BACKUP_TPL])))
    assert tpl_desc[0] == NADA_VAL and tpl_desc[6] == 0 and tpl_desc[9] == 1, "Unexpected PID 0x55 template"

    # --- Layout of single appended RWX PT_LOAD segment ---
    top = max(v + ms for t, _, v, _, _, ms, _, _ in segs if t == 1)
    new_va = (top + ALIGN - 1) // ALIGN * ALIGN
    new_off = (len(data) + ALIGN - 1) // ALIGN * ALIGN

    code_va = new_va
    state_va = code_va + CODE_RESERVE
    data_va = state_va + STATE_SIZE

    code_bytes, syms = compile_engine(c_src, zig_exe, code_va, state_va)
    assert len(code_bytes) <= CODE_RESERVE, f"Engine too large: {len(code_bytes)} > {CODE_RESERVE}"

    # Assemble & round-trip verify ALL 8 Thumb-2 b.w hooks!
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    hook_patches: list[tuple[str, int, int, bytes]] = []
    for h_name, h_va, _, sym_name in HOOK_SITES:
        target_va = code_va + syms[sym_name]
        bw_bytes = _asm_thumb(f"b.w {target_va:#x}", h_va)
        assert len(bw_bytes) == 4
        ins = next(md.disasm(bw_bytes, h_va))
        assert ins.mnemonic == "b.w" and int(ins.op_str.lstrip("#"), 16) == target_va, (
            f"b.w verify failed for {h_name}: {ins.mnemonic} {ins.op_str} vs 0x{target_va:x}"
        )
        hook_patches.append((h_name, h_va, target_va, bw_bytes))

    tramp_va = code_va + syms["interval_capstart_trampoline"]

    # Build data blob (menu array + ELEM + D + Backup descriptors) & extra relocations
    blob = bytearray()
    extra_rel = bytearray()

    def alloc_words(words: list[int], rel_map: dict[int, int] | None = None) -> int:
        pad = (-len(blob)) % 16
        if pad:
            blob.extend(b"\0" * pad)
        va = data_va + len(blob)
        blob.extend(struct.pack(f"<{len(words)}I", *(w & 0xFFFFFFFF for w in words)))
        for idx, rinfo in (rel_map or {}).items():
            extra_rel.extend(struct.pack("<II", va + 4 * idx, rinfo))
        return va

    # 1. New Tab 2 P3 children array (6 slots: original 4 + Interval Shoot folder + ISO AUTO Min. SS)
    p3_off = _va_to_off(segs, TAB2_P3_ARR_VA)
    p3_words = list(struct.unpack_from(f"<{TAB2_P3_ORIG_CNT}I", data, p3_off))
    p3_rels = {
        i: by_off[TAB2_P3_ARR_VA + 4 * i]
        for i in range(TAB2_P3_ORIG_CNT)
        if (TAB2_P3_ARR_VA + 4 * i) in by_off
    }
    p3_words.append(folder_delta & 0xFFFFFFFF)
    p3_rels[TAB2_P3_ORIG_CNT] = (ref_sym_idx << 8) | R_ARM_ABS32
    p3_words.append(min_ss_delta & 0xFFFFFFFF)
    p3_rels[TAB2_P3_ORIG_CNT + 1] = (ref_sym_idx << 8) | R_ARM_ABS32
    assert len(p3_words) == TAB2_P3_NEW_CNT
    new_p3_arr_va = alloc_words(p3_words, p3_rels)
    assert new_p3_arr_va == data_va, (
        f"G_TAB2_P3_NEW_ARR mismatch: got 0x{new_p3_arr_va:x}, expected 0x{data_va:x}"
    )

    # 1b. New Tab 2 P5 children array (5 slots: original 4 + Extended Shutter & BULB)
    p5_off = _va_to_off(segs, TAB2_P5_ARR_VA)
    p5_words = list(struct.unpack_from(f"<{TAB2_P5_ORIG_CNT}I", data, p5_off))
    p5_rels = {
        i: by_off[TAB2_P5_ARR_VA + 4 * i]
        for i in range(TAB2_P5_ORIG_CNT)
        if (TAB2_P5_ARR_VA + 4 * i) in by_off
    }
    p5_words.append(ext_shutter_delta & 0xFFFFFFFF)
    p5_rels[TAB2_P5_ORIG_CNT] = (ref_sym_idx << 8) | R_ARM_ABS32
    assert len(p5_words) == TAB2_P5_NEW_CNT
    new_p5_arr_va = alloc_words(p5_words, p5_rels)

    # 2. Expanded ELEM & D tables + native A7R1 12-word Backup-only descriptors (PIDs 0xE4..0xE9 & 0xEB)
    n_new = PID_MAX + 1
    elem_new = elem_orig + [0] * (n_new - N_ORIG_PIDS)
    d_new = d_orig + [0] * (n_new - N_ORIG_PIDS)
    d_rels = {
        i: by_off[D_VA + 4 * i]
        for i in range(N_ORIG_PIDS)
        if (D_VA + 4 * i) in by_off
    }
    assert len(d_rels) == N_ORIG_PIDS, "Expected relocation on every stock D entry"

    for s in ALL_BACKUP_SETTINGS:
        assert N_ORIG_PIDS <= s.pid <= PID_MAX
        n_opt = len(s.options)
        assert 1 <= n_opt <= 64
        bk_vals = list(s.backup_bytes) if s.backup_bytes is not None else list(range(n_opt))
        assert len(bk_vals) == n_opt, f"backup_bytes len mismatch for {s.key}"
        nada_va = alloc_words([NADA_VAL] * n_opt)
        vals_va = alloc_words(bk_vals)
        bk_info_va = alloc_words([s.backup_id, vals_va, 0, 1], {1: R_ARM_RELATIVE})
        desc_w10 = SETTINGMENUX_STR_VA if s.pid == PID_MIN_SS else 0
        desc_w11 = VSM_BOOT_MIN_SS if s.pid == PID_MIN_SS else 0xFFFFFFFF
        desc_words = [
            NADA_VAL,
            nada_va,
            0, 0, 0,
            n_opt << 8,
            0, 0,
            bk_info_va,
            1,
            desc_w10,
            desc_w11,
        ]
        assert len(desc_words) == DESC_WORDS
        desc_rels = {1: R_ARM_RELATIVE, 8: R_ARM_RELATIVE}
        if desc_w10 != 0:
            desc_rels[10] = R_ARM_RELATIVE
        desc_va = alloc_words(desc_words, desc_rels)
        elem_new[s.pid] = 0
        d_new[s.pid] = desc_va
        d_rels[s.pid] = R_ARM_RELATIVE

    # 2b. View-launcher descriptor for PID 0xEA (single-slot Fn shortcut -> view/SETTINGMENUX, 0x77)
    assert N_ORIG_PIDS <= PID_FN_INTV <= PID_MAX
    assert d_new[PID_FN_INTV] == 0, f"PID 0x{PID_FN_INTV:x} collision!"
    fn_nada_va = alloc_words([NADA_VAL])
    fn_desc_words = [
        NADA_VAL,
        fn_nada_va,
        0, 0, 0,
        1 << 8,
        0, 0, 0, 0,
        SETTINGMENUX_STR_VA,
        VSM_BOOT_INTV,
    ]
    assert len(fn_desc_words) == DESC_WORDS
    fn_desc_va = alloc_words(fn_desc_words, {1: R_ARM_RELATIVE, 10: R_ARM_RELATIVE})
    elem_new[PID_FN_INTV] = 0
    d_new[PID_FN_INTV] = fn_desc_va
    d_rels[PID_FN_INTV] = R_ARM_RELATIVE

    new_elem_va = alloc_words(elem_new)
    new_d_va = alloc_words(d_new, d_rels)

    # Assemble segment body: code + state + data + rel_new + phdr
    seg = bytearray(code_bytes.ljust(CODE_RESERVE, b"\0"))
    seg.extend(b"\0" * STATE_SIZE)
    seg.extend(blob)
    seg.extend(b"\0" * (-len(seg) % 16))

    rel_new_va = new_va + len(seg)
    rel_new = bytes(orig_rel) + bytes(extra_rel)
    seg.extend(rel_new)
    seg.extend(b"\0" * (-len(seg) % 16))

    phdr_off = new_off + len(seg)
    new_ph = [list(s) for s in segs] + [[1, new_off, new_va, new_va, 0, 0, 7, ALIGN]]  # RWX
    seg_total = len(seg) + 32 * len(new_ph)
    new_ph[-1][4] = new_ph[-1][5] = seg_total
    seg.extend(b"".join(struct.pack("<8I", *s) for s in new_ph))

    # Apply in-place patches to libObj.so text & dynamic table
    out = bytearray(data)

    # Patch Tab 2 P3 count (4 -> 6) & array literal
    struct.pack_into("<H", out, _va_to_off(segs, TAB2_P3_MOVS_VA), 0x2200 | TAB2_P3_NEW_CNT)
    struct.pack_into(
        "<I", out, _va_to_off(segs, TAB2_P3_LIT_VA),
        (new_p3_arr_va - (TAB2_P3_ADD_VA + 4)) & 0xFFFFFFFF,
    )

    # Patch Tab 2 P5 count (4 -> 5) & array literal
    struct.pack_into("<H", out, _va_to_off(segs, TAB2_P5_MOVS_VA), 0x2200 | TAB2_P5_NEW_CNT)
    struct.pack_into(
        "<I", out, _va_to_off(segs, TAB2_P5_LIT_VA),
        (new_p5_arr_va - (TAB2_P5_ADD_VA + 4)) & 0xFFFFFFFF,
    )

    # Patch PID bounds checks & table literals
    for idx, (cmp_va, _, new_b, lit_va, add_pc, _) in enumerate(PID_SITES):
        cmp_off = _va_to_off(segs, cmp_va)
        out[cmp_off:cmp_off + 2] = new_b
        target_tbl_va = new_elem_va if idx == 0 else new_d_va
        struct.pack_into(
            "<I", out, _va_to_off(segs, lit_va),
            (target_tbl_va - add_pc) & 0xFFFFFFFF,
        )

    # Patch all 8 Thumb-2 b.w hooks
    for _, h_va, _, bw_bytes in hook_patches:
        h_off = _va_to_off(segs, h_va)
        out[h_off:h_off + 4] = bw_bytes

    # Append new RWX segment & update ELF header + .dynamic
    out = out.ljust(new_off, b"\0") + seg
    struct.pack_into("<I", out, 28, phdr_off)
    struct.pack_into("<H", out, 44, len(new_ph))

    for i, (t, _) in enumerate(dyn):
        if t == 0x11:  # DT_REL
            struct.pack_into("<I", out, dyn_off + 8 * i + 4, rel_new_va)
        elif t == 0x12:  # DT_RELSZ
            struct.pack_into("<I", out, dyn_off + 8 * i + 4, len(rel_new))

    report = {
        "new_seg_va": hex(new_va),
        "new_seg_size": seg_total,
        "code_va": hex(code_va),
        "code_size": len(code_bytes),
        "state_va": hex(state_va),
        "trampoline_va": hex(tramp_va),
        "bw_distance_bytes": tramp_va - CAPSTART_VA,
        "hooks": {
            h_name: {"site": hex(h_va), "target": hex(t_va), "dist": t_va - h_va}
            for h_name, h_va, t_va, _ in hook_patches
        },
        "new_tab2_p3_arr_va": hex(new_p3_arr_va),
        "new_tab2_p5_arr_va": hex(new_p5_arr_va),
        "new_elem_va": hex(new_elem_va),
        "new_d_va": hex(new_d_va),
        "fn_intv_desc_va": hex(fn_desc_va),
        "min_ss_desc_va": hex(d_new[PID_MIN_SS]),
        "ext_shutter_desc_va": hex(d_new[PID_EXT_SHUTTER]),
        "rel_new_va": hex(rel_new_va),
        "extra_relocs": len(extra_rel) // 8,
        "engine_syms": {k: hex(code_va + v) for k, v in sorted(syms.items())},
    }
    return bytes(out), report
