"""Unicorn ARMv7-A Thumb-2 real-binary emulation tests for v3.36 UI patches:
1. `lib/viewUnified3.so` — `ViewZoomPb` State 3 (`0x00018e61`) & State 4 (`0x0001a191`)
   exhaustive key translation comparison (stock vs patched, real-hardware polarity verified):
   - Control Wheel CCW (`0x150a0b00`) -> `0x21` (Zoom Out, stops cleanly at 1.0x!) in BOTH State 3 & State 4
   - Control Wheel CW  (`0x150a0b10`) -> `0x03` (Zoom In) in BOTH State 3 & State 4
   - Top Dial (`0x150a0b40` / `0x150a0b50`) and EVERY other Sony hardware key
     remain 100% identical to stock!
2. `lib/viewUnified3.so` — `ViewZoomPb::onTransition` (`0x0001da15`) real-code FSM verification
   for D-pad Left (`ev=0x07`) & Right (`ev=0x08`) in State 3:
   - `Guard 4 == 0` (magnification > 1.0x): Left -> `Action 0x08` (Pan Left), Right -> `Action 0x09` (Pan Right)
   - `Guard 4 == 1` (minimum magnification 1.0x): Left -> `Action 0x0a` (Prev Photo), Right -> `Action 0x0b` (Next Photo)
   - Respects `Guard 2` (zoom ready) & `Guard 6` (can switch photo) in all cases; Up/Down (`0x05`/`0x06`) & `0x09`/`0x0a` intact!
3. `lib/viewUnified4.so` — `ViewFnMenu` cursor-recall BST (`0x000b7211`)
   exhaustive PID `0x00..0xFF` sweep (stock vs patched):
   - `0xce -> 0x20` (preserved)
   - `0xd0 -> 0x22` (Zebra — fixed!)
   - `0xea -> 0x25` (Fn Interval Shoot — fixed!)
   - `0xeb -> 0x1f` (Fn ISO AUTO Min. SS)
   - EVERY other PID outside `{0xcf, 0xe1, 0xea, 0xeb}` matches stock 100% identically!
"""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_THUMB, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_SP,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import menu_spec  # noqa: E402
import patch_view_drivemode as PVD  # noqa: E402
import patch_view_zoompb as PVZ  # noqa: E402

USR_LIB = (
    ROOT
    / "firmware/unpacked/fw320/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked_unpacked/lib"
)

STACK_BASE = 0x70000000
RET_SENTINEL = 0x60000F00
STATE3_FALLBACK_VA = 0x0000F0C4
VU4_BK_WRITE_PLT = 0x000375E4

KEY_WHEEL_CCW = 0x150A0B00  # Real-hardware verified A7R1 rear Control Wheel CCW (逆时针)
KEY_WHEEL_CW = 0x150A0B10   # Real-hardware verified A7R1 rear Control Wheel CW  (顺时针)
KEY_TOP_DIAL_OUT = 0x150A0B40
KEY_TOP_DIAL_IN = 0x150A0B50


def _load_elf_into_uc(elf_bytes: bytes) -> Uc:
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    with io.BytesIO(elf_bytes) as f:
        ef = ELFFile(f)
        loads = [s.header for s in ef.iter_segments() if s.header["p_type"] == "PT_LOAD"]
    min_va = min(s["p_vaddr"] for s in loads) & ~0xFFF
    max_va = (max(s["p_vaddr"] + s["p_memsz"] for s in loads) + 0xFFF) & ~0xFFF
    mu.mem_map(min_va, max_va - min_va)
    for s in loads:
        off, va, fsz = s["p_offset"], s["p_vaddr"], s["p_filesz"]
        mu.mem_write(va, elf_bytes[off:off + fsz])
    mu.mem_map(STACK_BASE, 0x10000)
    mu.mem_map(RET_SENTINEL & ~0xFFF, 0x2000)
    return mu


def _eval_zoompb_key(mu: Uc, fn_va: int, keycode: int) -> tuple[str, int]:
    """Run ViewZoomPb state key translator (`0x18e60` or `0x1a190`) on `keycode`."""
    sp = STACK_BASE + 0x8000
    mu.reg_write(UC_ARM_REG_SP, sp)
    mu.reg_write(UC_ARM_REG_R0, keycode)
    mu.reg_write(UC_ARM_REG_LR, RET_SENTINEL | 1)
    # Stop if execution reaches RET_SENTINEL or base-class tail call STATE3_FALLBACK_VA
    mu.emu_start(fn_va | 1, RET_SENTINEL, count=20_000)
    pc = mu.reg_read(UC_ARM_REG_PC) & ~1
    if pc == STATE3_FALLBACK_VA:
        assert mu.reg_read(UC_ARM_REG_SP) == sp
        return ("FALLBACK", mu.reg_read(UC_ARM_REG_R0))
    assert pc == RET_SENTINEL, f"Unexpected stop PC 0x{pc:08x} for key 0x{keycode:08x}"
    assert mu.reg_read(UC_ARM_REG_SP) == sp, f"SP imbalance for key 0x{keycode:08x}"
    return ("EV", mu.reg_read(UC_ARM_REG_R0))


def _make_zoompb_uc(elf_bytes: bytes) -> Uc:
    mu = _load_elf_into_uc(elf_bytes)

    def stop_at_fallback(uc: Uc, addr: int, _size: int, _user) -> None:
        if addr == STATE3_FALLBACK_VA:
            uc.emu_stop()

    mu.hook_add(UC_HOOK_CODE, stop_at_fallback, begin=STATE3_FALLBACK_VA, end=STATE3_FALLBACK_VA + 2)
    return mu


def test_view_zoompb() -> None:
    stock_vu3 = (USR_LIB / "viewUnified3.so").read_bytes()
    patched_vu3, rep = PVZ.patch_view_zoompb(stock_vu3)
    assert rep["file_size_unchanged"] == len(stock_vu3)
    assert rep["patch_site_count"] == 4

    mu_stock = _make_zoompb_uc(stock_vu3)
    mu_patch = _make_zoompb_uc(patched_vu3)

    # Comprehensive key sweep covering all dial/wheel codes, buttons, and surrounding ranges
    keys: set[int] = {
        0x00000000,
        0xFFFFFFFF,
        KEY_WHEEL_CCW,
        KEY_WHEEL_CW,
        KEY_TOP_DIAL_OUT,
        KEY_TOP_DIAL_IN,
        0x15020001, 0x15020002, 0x15020011, 0x15020012,
        0x150200A1, 0x150200A2, 0x150200B1, 0x150200B2,
        0x15020101, 0x15020102, 0x15020111, 0x15020112,
        0x15020121, 0x15020122, 0x15020131, 0x15020132,
        0x15020141, 0x15020142, 0x15020151, 0x15020152,
    }
    for base in (0x150A0AE0, 0x150A0B00, 0x150A0B20, 0x150A0B40, 0x150A0B60, 0x150A0B80):
        for delta in range(0x24):
            keys.add(base + delta)

    # --- State 3 (0x00018e60): normal zoomed playback ---
    assert _eval_zoompb_key(mu_stock, 0x00018E60, KEY_WHEEL_CCW) == ("EV", 0x09)
    assert _eval_zoompb_key(mu_stock, 0x00018E60, KEY_WHEEL_CW) == ("EV", 0x0A)
    assert _eval_zoompb_key(mu_patch, 0x00018E60, KEY_WHEEL_CCW) == ("EV", 0x21)  # Zoom Out (stops at 1.0x!)
    assert _eval_zoompb_key(mu_patch, 0x00018E60, KEY_WHEEL_CW) == ("EV", 0x03)   # Zoom In
    assert _eval_zoompb_key(mu_patch, 0x00018E60, KEY_TOP_DIAL_IN) == ("EV", 0x03)
    assert _eval_zoompb_key(mu_patch, 0x00018E60, KEY_TOP_DIAL_OUT) == ("EV", 0x21)
    assert _eval_zoompb_key(mu_patch, 0x00018E60, 0x150100A2) == ("EV", 0x07)     # D-pad Left
    assert _eval_zoompb_key(mu_patch, 0x00018E60, 0x15010092) == ("EV", 0x08)     # D-pad Right

    for k in sorted(keys):
        if k in (KEY_WHEEL_CCW, KEY_WHEEL_CW):
            continue
        r_stock = _eval_zoompb_key(mu_stock, 0x00018E60, k)
        r_patch = _eval_zoompb_key(mu_patch, 0x00018E60, k)
        assert r_patch == r_stock, f"State 3 unintended change for key 0x{k:08x}: {r_stock} -> {r_patch}"
    print(f"[PASS] Test 1: ViewZoomPb State 3 (0x18e60) verified across {len(keys)} keys (CCW->0x21 stop@1.0x, CW->0x03 ZoomIn, 0 side effects)")

    # --- State 4 (0x0001a190): single-photo / no-switch zoomed playback ---
    assert _eval_zoompb_key(mu_stock, 0x0001A190, KEY_WHEEL_CCW) == ("EV", 0x2C)
    assert _eval_zoompb_key(mu_stock, 0x0001A190, KEY_WHEEL_CW) == ("EV", 0x2C)
    assert _eval_zoompb_key(mu_patch, 0x0001A190, KEY_WHEEL_CCW) == ("EV", 0x21)  # Zoom Out (stops at 1.0x!)
    assert _eval_zoompb_key(mu_patch, 0x0001A190, KEY_WHEEL_CW) == ("EV", 0x03)   # Zoom In
    assert _eval_zoompb_key(mu_patch, 0x0001A190, KEY_TOP_DIAL_IN) == ("EV", 0x03)
    assert _eval_zoompb_key(mu_patch, 0x0001A190, KEY_TOP_DIAL_OUT) == ("EV", 0x21)

    for k in sorted(keys):
        if k in (KEY_WHEEL_CCW, KEY_WHEEL_CW):
            continue
        r_stock = _eval_zoompb_key(mu_stock, 0x0001A190, k)
        r_patch = _eval_zoompb_key(mu_patch, 0x0001A190, k)
        assert r_patch == r_stock, f"State 4 unintended change for key 0x{k:08x}: {r_stock} -> {r_patch}"
    print(f"[PASS] Test 2: ViewZoomPb State 4 (0x1a190) verified across {len(keys)} keys (CCW->0x21 stop@1.0x, CW->0x03 ZoomIn, 0 side effects)")

    # --- Test 3 (FSM): ViewZoomPb::onTransition (0x1da14) State 3 D-pad Left/Right & Wheel events ---
    mu_fsm = _load_elf_into_uc(patched_vu3)
    fsm_state = {"state": 3, "ev": 0x07, "g2": 1, "g4": 0, "g6": 1}
    SCRATCH = 0x61000000
    mu_fsm.mem_map(SCRATCH, 0x4000)

    def on_fsm_stub(uc: Uc, addr: int, _size: int, _user) -> None:
        if addr == 0x0000F0AC:  # ViewBase::GetEventId()
            uc.reg_write(UC_ARM_REG_R0, fsm_state["ev"])
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)
        elif addr == 0x00014440:  # getCurrentState()
            uc.reg_write(UC_ARM_REG_R0, fsm_state["state"])
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)
        elif addr == 0x0000F62C:  # debug log stub
            uc.reg_write(UC_ARM_REG_R0, 0)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)
        elif addr == 0x0001D98C:  # checkGuard(this, guard_id)
            gid = uc.reg_read(UC_ARM_REG_R1)
            res = {2: fsm_state["g2"], 4: fsm_state["g4"], 6: fsm_state["g6"]}.get(gid, 0)
            uc.reg_write(UC_ARM_REG_R0, res)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)

    for stub_va in (0x0000F0AC, 0x00014440, 0x0000F62C, 0x0001D98C):
        mu_fsm.hook_add(UC_HOOK_CODE, on_fsm_stub, begin=stub_va, end=stub_va + 2)

    def eval_fsm(st: int, ev: int, g2: int = 1, g4: int = 0, g6: int = 1) -> int:
        fsm_state.update({"state": st, "ev": ev, "g2": g2, "g4": g4, "g6": g6})
        ns_ptr, act_ptr = SCRATCH + 0x100, SCRATCH + 0x104
        mu_fsm.mem_write(ns_ptr, struct.pack("<I", 0xFFFFFFFF))
        mu_fsm.mem_write(act_ptr, struct.pack("<I", 0xFFFFFFFF))
        sp = STACK_BASE + 0x8000
        mu_fsm.reg_write(UC_ARM_REG_SP, sp)
        mu_fsm.reg_write(UC_ARM_REG_R0, SCRATCH)
        mu_fsm.reg_write(UC_ARM_REG_R1, ns_ptr)
        mu_fsm.reg_write(UC_ARM_REG_R2, act_ptr)
        mu_fsm.reg_write(UC_ARM_REG_LR, RET_SENTINEL | 1)
        mu_fsm.emu_start(0x0001DA14 | 1, RET_SENTINEL, count=20_000)
        assert mu_fsm.reg_read(UC_ARM_REG_SP) == sp
        return struct.unpack("<I", mu_fsm.mem_read(act_ptr, 4))[0]

    # Zoomed in (>1.0x, g4=0): D-pad Left/Right pan normally!
    assert eval_fsm(3, 0x07, g2=1, g4=0, g6=1) == 0x08          # Left -> Pan Left
    assert eval_fsm(3, 0x07, g2=0, g4=0, g6=1) == 0xFFFFFFFF    # Guard 2 blocked
    assert eval_fsm(3, 0x08, g2=1, g4=0, g6=1) == 0x09          # Right -> Pan Right
    assert eval_fsm(3, 0x08, g2=0, g4=0, g6=1) == 0xFFFFFFFF    # Guard 2 blocked

    # Minimum magnification (1.0x, g4=1): D-pad Left/Right switch Prev/Next photo via Guard 6!
    assert eval_fsm(3, 0x07, g2=1, g4=1, g6=1) == 0x0A          # Left @ 1.0x -> Prev Photo
    assert eval_fsm(3, 0x07, g2=1, g4=1, g6=0) == 0xFFFFFFFF    # Guard 6 blocked
    assert eval_fsm(3, 0x08, g2=1, g4=1, g6=1) == 0x0B          # Right @ 1.0x -> Next Photo
    assert eval_fsm(3, 0x08, g2=1, g4=1, g6=0) == 0xFFFFFFFF    # Guard 6 blocked

    # Direct ev=0x09/0x0a, Up/Down (0x05/0x06), Wheel Zoom In/Out (0x03/0x21), & State 4 intact!
    assert eval_fsm(3, 0x09, g6=1) == 0x0A and eval_fsm(3, 0x09, g6=0) == 0xFFFFFFFF
    assert eval_fsm(3, 0x0A, g6=1) == 0x0B and eval_fsm(3, 0x0A, g6=0) == 0xFFFFFFFF
    assert eval_fsm(3, 0x05, g2=1, g4=1) == 0x06 and eval_fsm(3, 0x06, g2=1, g4=1) == 0x07
    assert eval_fsm(3, 0x21, g2=1, g4=1) == 0x25 and eval_fsm(3, 0x03, g2=1, g4=1) == 0x04
    assert eval_fsm(4, 0x21, g2=1, g4=1) == 0x25 and eval_fsm(4, 0x03, g2=1, g4=1) == 0x04
    assert eval_fsm(4, 0x07, g2=1, g4=1) == 0x08 and eval_fsm(4, 0x08, g2=1, g4=1) == 0x09
    print("[PASS] Test 3: ViewZoomPb::onTransition (0x1da14) verified (State 3 Left/Right -> Pan @ >1.0x, Prev/Next @ 1.0x, 0 regressions)")


def _run_vu4_cursor_recall(elf_bytes: bytes) -> dict[int, int]:
    mu = _load_elf_into_uc(elf_bytes)
    captured: list[int] = []

    def on_bk_write(uc: Uc, _addr: int, _size: int, _user) -> None:
        buf_ptr = uc.reg_read(UC_ARM_REG_R1)
        captured.append(uc.mem_read(buf_ptr, 1)[0])
        uc.reg_write(UC_ARM_REG_R0, 0)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)

    mu.hook_add(UC_HOOK_CODE, on_bk_write, begin=VU4_BK_WRITE_PLT, end=VU4_BK_WRITE_PLT + 4)

    out: dict[int, int] = {}
    sp = STACK_BASE + 0x8000
    for pid in range(256):
        captured.clear()
        mu.reg_write(UC_ARM_REG_SP, sp)
        mu.reg_write(UC_ARM_REG_R0, 0x12345678)
        mu.reg_write(UC_ARM_REG_R1, pid)
        mu.reg_write(UC_ARM_REG_LR, RET_SENTINEL | 1)
        mu.emu_start(0x000B7210 | 1, RET_SENTINEL, count=10_000)
        assert mu.reg_read(UC_ARM_REG_SP) == sp, f"SP imbalance at PID 0x{pid:02x}"
        assert len(captured) == 1, f"Expected 1 Backup write for PID 0x{pid:02x}, got {len(captured)}"
        out[pid] = captured[0]
    return out


def test_vu4_cursor_bst() -> None:
    stock_vu4 = (USR_LIB / "viewUnified4.so").read_bytes()
    dummy_keys = {menu_spec.DM_TITLE_KEY: 0x1001, menu_spec.DM_HELP_KEY: 0x1002}
    patched_vu4, rep = PVD.patch_view_drivemode(stock_vu4, dummy_keys, 0x1000, 0x2000)
    assert rep["file_size_unchanged"] == len(stock_vu4)

    stock_map = _run_vu4_cursor_recall(stock_vu4)
    patch_map = _run_vu4_cursor_recall(patched_vu4)

    # Verify stock baseline
    assert stock_map[0xCE] == 0x20
    assert stock_map[0xCF] == 0x1F
    assert stock_map[0xD0] == 0x22
    assert stock_map[0xE1] == 0x25

    # Verify patched strictly-sorted BST results
    assert patch_map[0xCE] == 0x20, f"0xce broken: 0x{patch_map[0xCE]:x}"
    assert patch_map[0xD0] == 0x22, f"0xd0 (Zebra) broken: 0x{patch_map[0xD0]:x}"
    assert patch_map[0xEA] == 0x25, f"0xea (Fn Interval) broken: 0x{patch_map[0xEA]:x}"
    assert patch_map[0xEB] == 0x1F, f"0xeb (Fn Min.SS) broken: 0x{patch_map[0xEB]:x}"
    assert patch_map[0xCF] == 0x00
    assert patch_map[0xE1] == 0x00

    # Verify ZERO regressions on all other 252 PIDs!
    for pid in range(256):
        if pid in (0xCF, 0xE1, 0xEA, 0xEB):
            continue
        assert patch_map[pid] == stock_map[pid], (
            f"Unintended BST change at PID 0x{pid:02x}: stock=0x{stock_map[pid]:x}, patch=0x{patch_map[pid]:x}"
        )
    print("[PASS] Test 4: viewUnified4.so cursor-recall BST (0xb7210) verified across all 256 PIDs (0xce/0xd0/0xea/0xeb exact, 0 regressions)")


def run_all_tests() -> None:
    test_view_zoompb()
    test_vu4_cursor_bst()
    print("\nALL 4 REAL-BINARY UI UNICORN ARMv7-A THUMB-2 TESTS PASSED (v3.36)!")


if __name__ == "__main__":
    run_all_tests()
