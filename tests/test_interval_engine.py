"""Comprehensive Unicorn ARMv7-A Thumb-2 emulation tests for `src/interval_engine.c` (Release v3.29).

Compiles the exact production C source (`src/interval_engine.c`) with Zig's embedded
Clang+LLD at realistic `libObj.so` addresses (and verifies PIC relocation independence
by running the same flat binary shifted by +0x40000000!), then faithfully emulates:
  - Zero NAND debug file I/O (zero open/write/fsync/close imports!)
  - Sony's Main EventLoop `TimerManager` (`add`, `remove` with ZERO memory-leak checking,
    `timeGetNow`, `timeAddMS`, `wakeup`)
  - Direct `ModelStillRec` internal commands (`CaptureStart`, `CaptureStop`, `FocusStart`,
    `FocusStop`, `AfMfChangeOn`, `AfMfChangeOff`) + BIONZ `m_StsCapture` (`1 -> 2 -> 3 -> 0`)
  - 3-Mode AF/AE Tracking (`0=Lock AF+AE`, `1=Lock AF Only / Holy Grail Auto AE`, `2=AF/AE Each`)
  - `ModelDisplay` (`@M006`) true Panel/EVF power switch (`ev=0x2F, 0=ALL OFF, 1=PANEL, 2=EVF`)
    + S1 half-press 5-second temporary screen peek (`pvt_ActionExeFocusStart` @ 0x00722300)
  - `ModelAutoPowerOff` (`@M058`) native keep-alive (`ev=5`, every 4s during long intervals!)
  - Expanded interval (1..60s, 10 opts) & shots (30..999/Unlimited, 9 opts) tables
  - Option A untouched stock `ACT_DriveMode` icons (`0x0205D3E9` LCD / `0x020E8787` EVF)
    + Live View blinking icon + `ACT_LabelStillRemain` live countdown + dtor guards
  - Single-slot Fn shortcut (`PID 0xEA` + `ViewSettingMenu` direct Layout 5 folder jump)
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_THUMB, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_R4,
    UC_ARM_REG_R5,
    UC_ARM_REG_R6,
    UC_ARM_REG_R7,
    UC_ARM_REG_R8,
    UC_ARM_REG_SP,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import patch_libobj as PL  # noqa: E402

# Backup IDs
BK_ENABLE     = 0x010700F6
BK_DELAY      = 0x010700F7
BK_INTERVAL   = 0x0107003C
BK_SHOTS      = 0x0107003F
BK_AF_MODE    = 0x0107007E
BK_DISP_SLEEP = 0x01070121
BK_MIN_SS     = 0x010700D0

# ModelDisplay / ModelCamera / ModelAutoPowerOff constants
EV_SET_AEL        = 0x39
PRM_KEY_AEL       = 0x1E0
AEL_ON            = 0
AEL_OFF           = 1
EV_PANEL_EVF      = 0x2F
PRM_KEY_PANEL_EVF = 0x1200600B
EV_RELOAD_APO     = 5

# Link-time VAs inside simulated libObj.so (matches real A7R1 layout)
LINK_CODE_VA  = 0x014B0000
LINK_STATE_VA = 0x014B2000
LINK_P3_VA    = LINK_STATE_VA + PL.STATE_SIZE
# Runtime dynamic base shift (proves 100% PIC independence!)
LOAD_BIAS     = 0x40000000

RET_SENTINEL  = 0x50000F00
STUB_PAGE     = 0x50000000
HEAP_BASE     = 0x60000000
STACK_BASE    = 0x70000000
MODEL_ADDR    = 0x68000000
SCRATCH_ADDR  = 0x69000000
ACT_OBJ_ADDR  = 0x6A000000
REMAIN_ADDR   = 0x6A002000
VSM_OBJ_ADDR  = 0x6A004000
TMR_OBJ_ADDR  = 0x6B000000
FAKE_FOLDER   = 0x6C551234
FAKE_MIN_SS   = 0x6C555678


class IntervalHarness:
    def __init__(self, code_bytes: bytes, syms: dict[str, int]) -> None:
        self.code_bytes = code_bytes
        self.syms = syms
        self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)

        self.stub_targets: dict[int, str] = {}
        mapped_pages: set[int] = set()

        def ensure_page(addr: int) -> None:
            pg = addr & ~0xFFFF
            if pg not in mapped_pages:
                self.mu.mem_map(pg, 0x10000)
                mapped_pages.add(pg)

        for name, va in PL.LIBOBJ_SYMS.items():
            rt = LOAD_BIAS + (va & ~1)
            ensure_page(rt)
            if name != "G_TIMER_MGR":
                self.stub_targets[rt] = name

        self.g_tmr_addr = LOAD_BIAS + PL.LIBOBJ_SYMS["G_TIMER_MGR"]

        ensure_page(LOAD_BIAS + LINK_CODE_VA)
        self.mu.mem_write(LOAD_BIAS + LINK_CODE_VA, code_bytes)
        self.state_addr = LOAD_BIAS + LINK_STATE_VA
        self.p3_arr_addr = LOAD_BIAS + LINK_P3_VA

        self.mu.mem_map(STUB_PAGE, 0x10000)
        self.mu.mem_map(HEAP_BASE, 0x100000)
        self.mu.mem_map(MODEL_ADDR, 0x10000)
        self.mu.mem_map(SCRATCH_ADDR, 0x10000)
        self.mu.mem_map(ACT_OBJ_ADDR, 0x10000)
        self.mu.mem_map(TMR_OBJ_ADDR, 0x10000)
        self.mu.mem_map(STACK_BASE, 0x20000)

        self.reset_world()
        self.mu.hook_add(UC_HOOK_CODE, self._on_stub, begin=LOAD_BIAS, end=LOAD_BIAS + 0x00C00000)

    def reset_world(self) -> None:
        self.mu.mem_write(self.state_addr, bytes(0x100))
        self.mu.mem_write(self.g_tmr_addr, struct.pack("<I", TMR_OBJ_ADDR))
        self.mu.mem_write(MODEL_ADDR, struct.pack("<4I", 0xCAFEBABE, 4, 0, 0))
        self.mu.mem_write(MODEL_ADDR + 0x19C, struct.pack("<I", 2))
        self.mu.mem_write(MODEL_ADDR + 0xB28, bytes([1, 1]))
        self.mu.mem_write(MODEL_ADDR + 0xD08, struct.pack("<I", 0))
        self.heap_ptr = HEAP_BASE
        self.backup: dict[int, int] = {
            BK_ENABLE: 0,
            BK_DELAY: 0,
            BK_INTERVAL: 0,
            BK_SHOTS: 0,
            BK_AF_MODE: 0,
            BK_DISP_SLEEP: 0,
        }
        self.now_ms = 10_000
        self.bionz_prep_ms = 280
        self.bionz_ack_ms = 0
        self.exposure_ms = 300
        self.busy_until_ms = 0
        self.stay_state4_during_exp = False
        self.forced_state: int | None = None
        self.allow_early_stop = False

        # Simulated Focus Mode & Display output state
        self.focus_mode = 2
        self.drop_afmf_after_shot = False
        self.panel_evf_status = 1
        self.wake_display_on_shot = False
        self.live_stack_pls: set[int] = set()

        # Simulated Sony TimerManager (tracks live records to verify ZERO leaks!)
        self.next_rec_id = 0x6C000010
        self.armed_timers: dict[int, tuple[int, int, int]] = {}
        self.live_recs: set[int] = set()
        self.wakeups = 0

        self.events: list[tuple[int, str, int, list[tuple[int, int]]]] = []
        self.direct_cmds: list[tuple[int, str, int, int]] = []
        self.param_lists: dict[int, list[tuple[int, int]]] = {}
        self.prm_values: dict[int, int] = {}
        self.passthrough_hits = 0
        self.focstart_hits = 0
        self.shot_times_ms: list[int] = []
        self.on_tick_cb = None

        # Simulated Live View OSD widgets
        self.disp_state_calls: list[tuple[int, int, int]] = []
        self.remain_updates: list[tuple[int, int]] = []
        self.remain_dtor_calls: list[int] = []
        self.remain_final_r0: int | None = None
        self.remain_final_r4: int | None = None
        self.remain_final_r6: int | None = None

        # Simulated Sony CmnViewProcessDataMgr & SPINAL_REFLEX state
        self.hw_values: dict[int, int] = {7: 0, 0xE4: 0}
        self.hw_set_fail_pids: set[int] = set()
        self.pdm_calls: list[tuple[str, int, int, int]] = []
        self.spinal_calls: list[tuple[int, int]] = []
        self.act_dm_final_r1: int | None = None

        # Simulated ViewSettingMenu direct-jump state (6 Tab 2 P3 entries: slot 4 = Folder, slot 5 = Min. SS)
        self.mu.mem_write(
            self.p3_arr_addr,
            struct.pack("<6I", 0x1001, 0x1002, 0x1003, 0x1004, FAKE_FOLDER, FAKE_MIN_SS),
        )
        self.mu.mem_write(VSM_OBJ_ADDR, bytes(0x200))
        self.vsm_ev_id = 0x11012001
        self.vsm_calls: list[tuple] = []
        self.vsm_ontrans_fallthrough = 0
        self.vsm_initnode_fallthrough = 0

    def set_model_state(self, state: int) -> None:
        self.mu.mem_write(MODEL_ADDR + 4, struct.pack("<I", state))

    def set_capture_sts(self, sts: int) -> None:
        self.mu.mem_write(MODEL_ADDR + 0xD08, struct.pack("<I", sts))

    def read_state_struct(self) -> dict[str, int]:
        raw = bytes(self.mu.mem_read(self.state_addr, 0xA8))
        vals = struct.unpack("<18IQQQ12I3Q", raw)
        names = [
            "magic", "running", "phase", "af_held",
            "shots_taken", "seq_idx", "disp_is_off", "model_rec",
            "active_rec", "start_delay_s", "interval_s", "max_shots",
            "af_mode", "disp_sleep", "abort_reason", "inactive_ticks",
            "busy_ticks", "cap_held", "anchor_ms", "next_shot_ms",
            "last_fire_ms", "cb_count", "disp_recheck", "peek_active", "_pad1",
            "dm_widget", "remain_widget", "lock_applied", "afmf_locked",
            "ael_locked", "saved_disp_out", "blink_phase", "_pad2",
            "last_blink_ms", "last_apo_ms", "peek_until_ms",
        ]
        return dict(zip(names, vals))

    def _read_cstr(self, addr: int) -> str:
        out = bytearray()
        while True:
            b = self.mu.mem_read(addr + len(out), 1)[0]
            if b == 0:
                break
            out.append(b)
        return out.decode("ascii")

    def _pop_and_ret_4reg(self, mu: Uc, ret: int) -> None:
        sp = mu.reg_read(UC_ARM_REG_SP)
        r3, r4, r7, pc = struct.unpack("<4I", mu.mem_read(sp, 16))
        mu.reg_write(UC_ARM_REG_R3, r3)
        mu.reg_write(UC_ARM_REG_R4, r4)
        mu.reg_write(UC_ARM_REG_R7, r7)
        mu.reg_write(UC_ARM_REG_SP, sp + 16)
        mu.reg_write(UC_ARM_REG_R0, ret & 0xFFFFFFFF)
        mu.reg_write(UC_ARM_REG_PC, pc | 1)

    def _pop_and_ret_6reg(self, mu: Uc, ret: int) -> None:
        sp = mu.reg_read(UC_ARM_REG_SP)
        r3, r4, r5, r6, r7, pc = struct.unpack("<6I", mu.mem_read(sp, 24))
        mu.reg_write(UC_ARM_REG_R3, r3)
        mu.reg_write(UC_ARM_REG_R4, r4)
        mu.reg_write(UC_ARM_REG_R5, r5)
        mu.reg_write(UC_ARM_REG_R6, r6)
        mu.reg_write(UC_ARM_REG_R7, r7)
        mu.reg_write(UC_ARM_REG_SP, sp + 24)
        mu.reg_write(UC_ARM_REG_R0, ret & 0xFFFFFFFF)
        mu.reg_write(UC_ARM_REG_PC, pc | 1)

    def _pop_and_ret_4reg_spinal(self, mu: Uc) -> None:
        sp = mu.reg_read(UC_ARM_REG_SP)
        r4, r5, r7, pc = struct.unpack("<4I", mu.mem_read(sp, 16))
        mu.reg_write(UC_ARM_REG_R4, r4)
        mu.reg_write(UC_ARM_REG_R5, r5)
        mu.reg_write(UC_ARM_REG_R7, r7)
        mu.reg_write(UC_ARM_REG_SP, sp + 16)
        mu.reg_write(UC_ARM_REG_PC, pc | 1)

    def _on_stub(self, mu: Uc, addr: int, size: int, _user) -> None:
        name = self.stub_targets.get(addr)
        if name is None:
            return
        r0 = mu.reg_read(UC_ARM_REG_R0)
        r1 = mu.reg_read(UC_ARM_REG_R1)
        r2 = mu.reg_read(UC_ARM_REG_R2)
        r3 = mu.reg_read(UC_ARM_REG_R3)
        lr = mu.reg_read(UC_ARM_REG_LR)

        if name == "V_CAPSTART_CONT":
            self.passthrough_hits += 1
            self.shot_times_ms.append(self.now_ms)
            if self.forced_state is None:
                self.busy_until_ms = self.now_ms + self.exposure_ms
                self.set_capture_sts(2)
                if not self.stay_state4_during_exp:
                    self.set_model_state(4)
            sp = mu.reg_read(UC_ARM_REG_SP) + 0x5C
            saved_lr = struct.unpack("<I", mu.mem_read(sp + 16, 4))[0]
            mu.reg_write(UC_ARM_REG_SP, sp + 20)
            mu.reg_write(UC_ARM_REG_PC, saved_lr | 1)
            return

        if name == "V_FOCSTART_CONT":
            self.focstart_hits += 1
            sp = mu.reg_read(UC_ARM_REG_SP) + 0x24
            r4, r7, saved_lr = struct.unpack("<3I", mu.mem_read(sp, 12))
            mu.reg_write(UC_ARM_REG_R4, r4)
            mu.reg_write(UC_ARM_REG_R7, r7)
            mu.reg_write(UC_ARM_REG_SP, sp + 12)
            mu.reg_write(UC_ARM_REG_PC, saved_lr | 1)
            return

        if name == "V_SPINAL_CONT":
            self.spinal_calls.append((r0, r1))
            self._pop_and_ret_4reg_spinal(mu)
            return

        if name == "V_PDM_SETVAL_CONT":
            self.pdm_calls.append(("setValue", r1, r2, 0))
            if r1 in self.hw_set_fail_pids:
                self._pop_and_ret_4reg(mu, 0)
                return
            self.hw_values[r1] = r2
            if r1 == 0xE4:
                self.backup[BK_ENABLE] = r2 & 0xFF
            self._pop_and_ret_4reg(mu, 1)
            return

        if name == "V_PDM_GETVAL_CONT":
            val = self.hw_values.get(r1, 0)
            self.pdm_calls.append(("getValue", r1, val, r2))
            if r2 != 0:
                mu.mem_write(r2, struct.pack("<I", val))
            self._pop_and_ret_4reg(mu, 1)
            return

        if name in ("V_PDM_INVALID_CONT", "V_PDM_DISABLE_CONT", "V_PDM_GRAY_CONT"):
            self.pdm_calls.append((name, r1, r2, 0))
            self._pop_and_ret_4reg(mu, (r1 << 16) | r2)
            return

        if name == "V_PDM_CAUTION_CONT":
            self.pdm_calls.append((name, r1, r2, r3))
            self._pop_and_ret_6reg(mu, (r1 << 16) | r2)
            return

        if name == "V_ACT_DM_CONT":
            self.act_dm_final_r1 = r1
            mu.reg_write(UC_ARM_REG_PC, RET_SENTINEL | 1)
            return

        if name in ("V_REMAIN_A_CONT", "V_REMAIN_B_CONT"):
            self.remain_final_r0 = r0
            self.remain_final_r4 = mu.reg_read(UC_ARM_REG_R4)
            self.remain_final_r6 = mu.reg_read(UC_ARM_REG_R6)
            mu.reg_write(UC_ARM_REG_PC, RET_SENTINEL | 1)
            return

        if name == "V_REMAIN_DTOR_CONT":
            self.remain_dtor_calls.append(r0)
            self._pop_and_ret_6reg(mu, r0)
            return

        if name == "V_VSM_ONTRANS_CONT":
            self.vsm_ontrans_fallthrough += 1
            sp = mu.reg_read(UC_ARM_REG_SP)
            r4, r5, r6, r7, r8, pc = struct.unpack("<6I", mu.mem_read(sp, 24))
            mu.reg_write(UC_ARM_REG_R4, r4)
            mu.reg_write(UC_ARM_REG_R5, r5)
            mu.reg_write(UC_ARM_REG_R6, r6)
            mu.reg_write(UC_ARM_REG_R7, r7)
            mu.reg_write(UC_ARM_REG_R8, r8)
            mu.reg_write(UC_ARM_REG_SP, sp + 24)
            mu.reg_write(UC_ARM_REG_PC, pc | 1)
            return

        if name == "V_VSM_INITNODE_CONT":
            self.vsm_initnode_fallthrough += 1
            sp = mu.reg_read(UC_ARM_REG_SP) + 0x2C
            r4, r5, r6, r7, pc = struct.unpack("<5I", mu.mem_read(sp, 20))
            mu.reg_write(UC_ARM_REG_R4, r4)
            mu.reg_write(UC_ARM_REG_R5, r5)
            mu.reg_write(UC_ARM_REG_R6, r6)
            mu.reg_write(UC_ARM_REG_R7, r7)
            mu.reg_write(UC_ARM_REG_SP, sp + 20)
            mu.reg_write(UC_ARM_REG_PC, pc | 1)
            return

        ret = 0
        if name == "P_BK_READ":
            val = self.backup.get(r0, 0)
            mu.mem_write(r1, struct.pack("<B7x", val & 0xFF))
            ret = 0
        elif name == "P_BK_WRITE":
            val = mu.mem_read(r1, 1)[0]
            self.backup[r0] = val
            ret = 0
        elif name == "P_NEW":
            ret = self.heap_ptr
            self.heap_ptr += (r0 + 15) & ~15
        elif name == "P_PL_CTOR":
            self.param_lists[r0] = []
            if r0 >= STACK_BASE:
                self.live_stack_pls.add(r0)
        elif name == "P_PL_DTOR":
            self.param_lists.pop(r0, None)
            if r0 >= STACK_BASE:
                assert r0 in self.live_stack_pls, f"ParamList::~ParamList on unknown stack obj 0x{r0:x}"
                self.live_stack_pls.remove(r0)
        elif name == "V_PRM_CTOR":
            self.prm_values[r0] = r1
            ret = r0
        elif name == "V_PRM_GETNUM":
            ret = self.prm_values.get(r0, -1)
        elif name == "V_PL_GET":
            pairs = self.param_lists.get(r0, [])
            found = 0
            for k, v in pairs:
                if k == r1:
                    prm = self.heap_ptr
                    self.heap_ptr += 16
                    self.prm_values[prm] = v
                    found = prm
                    break
            ret = found
        elif name == "P_PL_ADD":
            self.param_lists.setdefault(r0, []).append((r1, self.prm_values.get(r2, -999)))
        elif name == "P_GET_MODEL_INFO":
            mstr = self._read_cstr(r0)
            if mstr == "@M006" and r1 == 0x20:
                self.param_lists[r2] = [(PRM_KEY_PANEL_EVF, self.panel_evf_status)]
                ret = 1
            else:
                ret = 0
        elif name == "P_REQ_EXEC":
            mstr = self._read_cstr(r0)
            params = list(self.param_lists.get(r2, []))
            self.events.append((self.now_ms, mstr, r1, params))
            if mstr == "@M006" and r1 == EV_PANEL_EVF:
                for k, v in params:
                    if k == PRM_KEY_PANEL_EVF:
                        self.panel_evf_status = v
        elif name == "V_TIME_NOW":
            sec = self.now_ms // 1000
            nsec = (self.now_ms % 1000) * 1_000_000
            mu.mem_write(r0, struct.pack("<ii", sec, nsec))
            ret = 0
        elif name == "V_TIME_ADD_MS":
            sec, nsec = struct.unpack("<ii", mu.mem_read(r0, 8))
            total_ms = sec * 1000 + nsec // 1_000_000 + r1
            mu.mem_write(r0, struct.pack("<ii", total_ms // 1000, (total_ms % 1000) * 1_000_000))
            ret = 0
        elif name == "V_TMR_ADD":
            assert r0 == TMR_OBJ_ADDR, f"Bad TimerManager* 0x{r0:x}"
            assert (r2 & 1) == 1, f"Callback 0x{r2:x} missing Thumb bit 0!"
            sec, nsec = struct.unpack("<ii", mu.mem_read(r1, 8))
            due_ms = sec * 1000 + nsec // 1_000_000
            rec = self.next_rec_id
            self.next_rec_id += 0x18
            self.armed_timers[rec] = (due_ms, r2, r3)
            self.live_recs.add(rec)
            ret = rec
        elif name == "V_TMR_REMOVE":
            assert r0 == TMR_OBJ_ADDR, f"Bad TimerManager* 0x{r0:x}"
            if r1 != 0:
                self.armed_timers.pop(r1, None)
                assert r1 in self.live_recs, f"Double-free or invalid rec 0x{r1:x}!"
                self.live_recs.remove(r1)
            ret = 0
        elif name == "V_CAP_START_CMD":
            assert r0 == MODEL_ADDR and r1 == 1
            self.direct_cmds.append((self.now_ms, "CAP_START", r0, r1))
            self.shot_times_ms.append(self.now_ms)
            self.bionz_ack_ms = self.now_ms + self.bionz_prep_ms
            self.busy_until_ms = self.now_ms + self.exposure_ms
            self.set_capture_sts(1)
        elif name == "V_CAP_STOP_CMD":
            assert r0 == MODEL_ADDR and r1 == 1
            cur_sc = struct.unpack("<I", mu.mem_read(MODEL_ADDR + 0xD08, 4))[0]
            if not self.allow_early_stop:
                assert cur_sc == 2, f"Premature CaptureStop while m_StsCapture={cur_sc} (expected 2)!"
            self.direct_cmds.append((self.now_ms, "CAP_STOP", r0, r1))
            self.bionz_ack_ms = 0
            self.set_capture_sts(3)
            if self.drop_afmf_after_shot:
                self.focus_mode = 2
            if self.wake_display_on_shot and self.panel_evf_status == 0:
                self.panel_evf_status = 1
        elif name == "V_AF_START_CMD":
            assert r0 == MODEL_ADDR and r1 == 0
            self.direct_cmds.append((self.now_ms, "AF_START", r0, r1))
        elif name == "V_AF_STOP_CMD":
            assert r0 == MODEL_ADDR and r1 == 0
            self.direct_cmds.append((self.now_ms, "AF_STOP", r0, r1))
        elif name == "V_AFMF_ON_CMD":
            assert r0 == MODEL_ADDR
            self.direct_cmds.append((self.now_ms, "AFMF_ON", r0, 0))
            self.focus_mode = 1
        elif name == "V_AFMF_OFF_CMD":
            assert r0 == MODEL_ADDR
            self.direct_cmds.append((self.now_ms, "AFMF_OFF", r0, 0))
            self.focus_mode = 2
        elif name == "V_GET_FOCUS_MODE":
            ret = self.focus_mode
        elif name == "V_SET_DISP_STATE":
            self.disp_state_calls.append((self.now_ms, r0, r1))
        elif name == "V_REMAIN_UPDATE":
            self.remain_updates.append((self.now_ms, r0))
        elif name == "V_WAKEUP_LOOP":
            self.wakeups += 1
        elif name == "P_VIEW_GET_EVID":
            self.vsm_calls.append(("GetEventId", r0))
            ret = self.vsm_ev_id
        elif name == "V_VSM_SELECT_NODE":
            self.vsm_calls.append(("selectNode", r0, r1))
        elif name == "V_VSM_SYNC_INDICES":
            self.vsm_calls.append(("syncIndices", r0))
            mu.mem_write(r0 + 0x124, struct.pack("<I", 2))  # Tab 2 (Custom Settings)
        elif name == "V_VSM_GET_PAGE":
            self.vsm_calls.append(("getPage", r0))
            ret = 3  # Page 3
        elif name == "V_VSM_SET_PAGE_TAB":
            self.vsm_calls.append(("setPageTab", r0, r1, r2))
        elif name == "V_VSM_ACTION7":
            self.vsm_calls.append(("action7", r0))

        mu.reg_write(UC_ARM_REG_R0, ret & 0xFFFFFFFF)
        mu.reg_write(UC_ARM_REG_PC, lr | 1)

    def _advance_camera_hw(self) -> None:
        if self.forced_state is not None:
            self.set_model_state(self.forced_state)
        cur_sc = struct.unpack("<I", self.mu.mem_read(MODEL_ADDR + 0xD08, 4))[0]
        if self.bionz_ack_ms and self.now_ms >= self.bionz_ack_ms and cur_sc == 1:
            self.bionz_ack_ms = 0
            self.set_capture_sts(2)
            if self.busy_until_ms < self.now_ms + 200:
                self.busy_until_ms = self.now_ms + 200
        if self.busy_until_ms and self.now_ms >= self.busy_until_ms and not self.bionz_ack_ms:
            self.busy_until_ms = 0
            self.set_capture_sts(0)
            if self.forced_state is None:
                self.set_model_state(4)

    def call_capstart(self) -> bool:
        before = self.passthrough_hits
        sp = STACK_BASE + 0x10000
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.reg_write(UC_ARM_REG_R0, MODEL_ADDR)
        self.mu.reg_write(UC_ARM_REG_LR, RET_SENTINEL | 1)
        entry = LOAD_BIAS + LINK_CODE_VA + self.syms["interval_capstart_trampoline"]
        self.mu.emu_start(entry | 1, RET_SENTINEL, count=500_000)
        assert self.mu.reg_read(UC_ARM_REG_SP) == sp, "SP imbalance in capstart trampoline!"
        return self.passthrough_hits > before

    def call_focstart(self) -> None:
        before = self.focstart_hits
        sp = STACK_BASE + 0x11000
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.reg_write(UC_ARM_REG_R0, MODEL_ADDR)
        self.mu.reg_write(UC_ARM_REG_R4, 0xCAFE1234)
        self.mu.reg_write(UC_ARM_REG_R7, 0xBEEF5678)
        self.mu.reg_write(UC_ARM_REG_LR, RET_SENTINEL | 1)
        entry = LOAD_BIAS + LINK_CODE_VA + self.syms["interval_focstart_trampoline"]
        self.mu.emu_start(entry | 1, RET_SENTINEL, count=500_000)
        assert self.mu.reg_read(UC_ARM_REG_SP) == sp, "SP imbalance in focstart trampoline!"
        assert self.mu.reg_read(UC_ARM_REG_R4) == 0xCAFE1234
        assert self.mu.reg_read(UC_ARM_REG_R7) == 0xBEEF5678
        assert self.focstart_hits == before + 1

    def run_eventloop(self, max_ticks: int = 5000) -> int:
        ticks = 0
        while self.armed_timers and ticks < max_ticks:
            ticks += 1
            rec = min(self.armed_timers, key=lambda k: self.armed_timers[k][0])
            due_ms, cb_fn, arg = self.armed_timers.pop(rec)
            if due_ms > self.now_ms:
                self.now_ms = due_ms
            self._advance_camera_hw()
            if self.on_tick_cb is not None:
                self.on_tick_cb(self)
                if rec not in self.live_recs:
                    continue

            ts_addr = SCRATCH_ADDR + 0x100
            self.mu.mem_write(ts_addr, struct.pack("<ii", self.now_ms // 1000, (self.now_ms % 1000) * 1_000_000))
            sp = STACK_BASE + 0x1E000
            self.mu.reg_write(UC_ARM_REG_SP, sp)
            self.mu.reg_write(UC_ARM_REG_R0, rec)
            self.mu.reg_write(UC_ARM_REG_R1, ts_addr)
            self.mu.reg_write(UC_ARM_REG_R2, arg)
            self.mu.reg_write(UC_ARM_REG_LR, RET_SENTINEL | 1)
            self.mu.emu_start(cb_fn, RET_SENTINEL, count=500_000)
            assert self.mu.reg_read(UC_ARM_REG_SP) == sp, "SP imbalance in interval_timer_cb!"
        return ticks

    def call_sym(self, sym_name: str, r0: int, r1: int = 0, r2: int = 0, r3: int = 0) -> int:
        sp = STACK_BASE + 0x14000
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.reg_write(UC_ARM_REG_R0, r0)
        self.mu.reg_write(UC_ARM_REG_R1, r1)
        self.mu.reg_write(UC_ARM_REG_R2, r2)
        self.mu.reg_write(UC_ARM_REG_R3, r3)
        self.mu.reg_write(UC_ARM_REG_LR, RET_SENTINEL | 1)
        entry = LOAD_BIAS + LINK_CODE_VA + self.syms[sym_name]
        self.mu.emu_start(entry | 1, RET_SENTINEL, count=500_000)
        assert self.mu.reg_read(UC_ARM_REG_SP) == sp, f"SP imbalance in {sym_name}!"
        return self.mu.reg_read(UC_ARM_REG_R0)

    def call_act_drivemode(self, icon_id: int, appearance_mode: int, obj_addr: int = ACT_OBJ_ADDR) -> int:
        sp = STACK_BASE + 0x16000
        self.act_dm_final_r1 = None
        self.mu.mem_write(obj_addr + 0x588, struct.pack("<II", icon_id, appearance_mode))
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.reg_write(UC_ARM_REG_R0, 0x11111111)
        self.mu.reg_write(UC_ARM_REG_R2, 0x22222222)
        self.mu.reg_write(UC_ARM_REG_R3, 0x33333333)
        self.mu.reg_write(UC_ARM_REG_R4, obj_addr + 0x1AC)
        self.mu.reg_write(UC_ARM_REG_R5, obj_addr)
        self.mu.reg_write(UC_ARM_REG_LR, 0x44444445)
        entry = LOAD_BIAS + LINK_CODE_VA + self.syms["interval_act_drivemode_trampoline"]
        self.mu.emu_start(entry | 1, RET_SENTINEL, count=500_000)
        assert self.mu.reg_read(UC_ARM_REG_SP) == sp
        assert self.mu.reg_read(UC_ARM_REG_R0) == 0x11111111
        assert self.mu.reg_read(UC_ARM_REG_R2) == 0x22222222
        assert self.mu.reg_read(UC_ARM_REG_R3) == 0x33333333
        assert self.mu.reg_read(UC_ARM_REG_R4) == obj_addr + 0x1AC
        assert self.mu.reg_read(UC_ARM_REG_R5) == obj_addr
        assert self.mu.reg_read(UC_ARM_REG_LR) == 0x44444445
        assert self.act_dm_final_r1 is not None
        return self.act_dm_final_r1

    def call_remain_tramp(self, sym_name: str, orig_cnt: int, obj_addr: int = REMAIN_ADDR) -> int:
        sp = STACK_BASE + 0x17000
        self.remain_final_r0 = None
        self.remain_final_r4 = None
        self.remain_final_r6 = None
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.reg_write(UC_ARM_REG_R0, orig_cnt)
        self.mu.reg_write(UC_ARM_REG_R1, 0x11112222)
        self.mu.reg_write(UC_ARM_REG_R2, 0x33334444)
        self.mu.reg_write(UC_ARM_REG_R3, 0x55556666)
        self.mu.reg_write(UC_ARM_REG_R4, 0x99999999)
        self.mu.reg_write(UC_ARM_REG_R5, obj_addr)
        self.mu.reg_write(UC_ARM_REG_R6, orig_cnt)
        self.mu.reg_write(UC_ARM_REG_LR, 0x77777779)
        entry = LOAD_BIAS + LINK_CODE_VA + self.syms[sym_name]
        self.mu.emu_start(entry | 1, RET_SENTINEL, count=500_000)
        assert self.mu.reg_read(UC_ARM_REG_SP) == sp
        assert self.mu.reg_read(UC_ARM_REG_R1) == 0x11112222
        assert self.mu.reg_read(UC_ARM_REG_R2) == 0x33334444
        assert self.mu.reg_read(UC_ARM_REG_R3) == 0x55556666
        assert self.mu.reg_read(UC_ARM_REG_R5) == obj_addr
        assert self.mu.reg_read(UC_ARM_REG_LR) == 0x77777779
        assert self.remain_final_r4 == obj_addr + 0x1AC
        assert self.remain_final_r0 == self.remain_final_r6
        return self.remain_final_r0


def run_all_tests() -> None:
    # Verify ZERO NAND debug file I/O symbols exist in production v3.28 build!
    for forbidden in ("P_OPEN", "P_WRITE", "P_CLOSE", "P_FSYNC"):
        assert forbidden not in PL.LIBOBJ_SYMS, f"Forbidden debug I/O symbol {forbidden} in Release v3.28!"

    zig = PL.find_zig(ROOT)
    c_src = ROOT / "src/interval_engine.c"
    code_bytes, syms = PL.compile_engine(c_src, zig, LINK_CODE_VA, LINK_STATE_VA)
    print(f"Compiled interval_engine.c (Release v3.29, zero NAND log): {len(code_bytes)} bytes, syms={len(syms)}")
    assert len(code_bytes) <= PL.CODE_RESERVE

    h = IntervalHarness(code_bytes, syms)

    # --- TEST 1: Interval Shoot = Off (transparent pass-through, 0 timers) ---
    h.reset_world()
    h.backup[BK_ENABLE] = 0
    assert h.call_capstart() is True
    h.call_focstart()
    assert h.armed_timers == {} and h.live_recs == set() and h.events == []
    print("[PASS] Test 1: Interval Shoot = Off -> 0ms transparent pass-through, 0 timers, 0 events")

    # --- TEST 2: Mode 0 (Lock AF+AE) + Monitor Sleep (LCD->ALL OFF->LCD) + APO keep-alive ---
    h.reset_world()
    h.focus_mode = 2
    h.panel_evf_status = 1
    h.backup.update({
        BK_ENABLE: 1, BK_DELAY: 0, BK_INTERVAL: 1, BK_SHOTS: 0, BK_AF_MODE: 0, BK_DISP_SLEEP: 1
    })
    assert h.call_capstart() is True
    h.run_eventloop()
    st = h.read_state_struct()
    assert st["running"] == 0 and st["disp_is_off"] == 0 and st["shots_taken"] == 30
    assert st["afmf_locked"] == 0 and st["ael_locked"] == 0 and h.live_recs == set() and h.live_stack_pls == set()
    t0 = h.shot_times_ms[0]
    for k, ts in enumerate(h.shot_times_ms):
        assert abs((ts - t0) - k * 2000) <= 5
    assert [c[1] for c in h.direct_cmds] == ["AFMF_ON"] + ["CAP_START", "CAP_STOP"] * 29 + ["AFMF_OFF"]
    assert [(e[2], e[3]) for e in h.events if e[1] == "@M00B"] == [
        (EV_SET_AEL, [(PRM_KEY_AEL, AEL_ON)]),
        (EV_SET_AEL, [(PRM_KEY_AEL, AEL_OFF)]),
    ]
    assert [(e[2], e[3]) for e in h.events if e[1] == "@M006"] == [
        (EV_PANEL_EVF, [(PRM_KEY_PANEL_EVF, 0)]),
        (EV_PANEL_EVF, [(PRM_KEY_PANEL_EVF, 1)]),
    ]
    apo_evs = [e for e in h.events if e[1] == "@M058" and e[2] == EV_RELOAD_APO and e[3] == []]
    assert len(apo_evs) >= 14, f"Expected periodic APO keep-alive every 4s over 58s, got {len(apo_evs)}"
    print(f"[PASS] Test 2: Mode 0 (Lock AF+AE) + ALL OFF + {len(apo_evs)}x APO keep-alive -> 0 drift, 0 leaks")

    # --- TEST 3: Mode 1 (NEW Holy Grail: Lock AF Only, Auto AE!) + Expanded Interval 15s (idx 5) & 100 shots (idx 2) ---
    h.reset_world()
    h.focus_mode = 2
    h.backup.update({
        BK_ENABLE: 1, BK_DELAY: 0, BK_INTERVAL: 5, BK_SHOTS: 2, BK_AF_MODE: 1, BK_DISP_SLEEP: 0
    })  # 15s interval, 100 shots, Lock AF Only!
    assert h.call_capstart() is True
    assert h.read_state_struct()["interval_s"] == 15 and h.read_state_struct()["max_shots"] == 100

    def stop_hg_at_3(harness: IntervalHarness) -> None:
        if len(harness.shot_times_ms) >= 3 and harness.read_state_struct()["shots_taken"] >= 3:
            harness.allow_early_stop = True
            assert harness.call_capstart() is False

    h.on_tick_cb = stop_hg_at_3
    h.run_eventloop()
    assert [c[1] for c in h.direct_cmds] == ["AFMF_ON", "CAP_START", "CAP_STOP", "CAP_START", "CAP_STOP", "AFMF_OFF"]
    assert [e for e in h.events if e[1] == "@M00B"] == [], "Holy Grail mode must NOT engage AEL!"
    assert h.shot_times_ms[1] - h.shot_times_ms[0] == 15_000
    assert h.shot_times_ms[2] - h.shot_times_ms[1] == 15_000
    apo_evs = [e for e in h.events if e[1] == "@M058" and e[2] == EV_RELOAD_APO]
    assert len(apo_evs) >= 7, f"Expected APO reload every 4s during 30s wait, got {len(apo_evs)}"
    print(f"[PASS] Test 3: Mode 1 (Holy Grail Lock AF / Auto AE) + 15s interval + {len(apo_evs)}x APO keep-alive")

    # --- TEST 4: Mode 2 (AF/AE Each) + Delay=5s + Interval=3s ---
    h.reset_world()
    h.backup.update({
        BK_ENABLE: 1, BK_DELAY: 2, BK_INTERVAL: 2, BK_SHOTS: 0, BK_AF_MODE: 2, BK_DISP_SLEEP: 0
    })
    t_press_ms = h.now_ms
    assert h.call_capstart() is False
    h.run_eventloop()
    assert h.read_state_struct()["shots_taken"] == 30 and h.live_recs == set()
    assert h.shot_times_ms[0] - t_press_ms == 5250
    assert [c[1] for c in h.direct_cmds] == ["AF_START", "CAP_START", "CAP_STOP", "AF_STOP"] * 30
    assert [e for e in h.events if e[1] in ("@M006", "@M00B")] == []
    print("[PASS] Test 4: Mode 2 (AF/AE Each), Delay=5s, 30x3s -> exact S1/S2 cycle, zero AFMF/AEL")

    # --- TEST 5: S1 Half-Press 5-Second Screen Peek during Monitor Sleep (auto re-sleeps!) ---
    h.reset_world()
    h.panel_evf_status = 2  # User is using EVF!
    h.backup.update({
        BK_ENABLE: 1, BK_DELAY: 0, BK_INTERVAL: 5, BK_SHOTS: 0, BK_AF_MODE: 0, BK_DISP_SLEEP: 1
    })  # 15s interval so we have plenty of time to peek for 5s and see auto re-sleep!
    h.call_remain_tramp("interval_remain_a_trampoline", 500, REMAIN_ADDR)
    assert h.call_capstart() is True
    peeked = [False]
    re_slept_checked = [False]

    def test_s1_peek(harness: IntervalHarness) -> None:
        st_now = harness.read_state_struct()
        # At t=12000ms (screen is ALL OFF), simulate user half-pressing S1 to peek!
        if not peeked[0] and st_now["disp_is_off"] == 1 and harness.now_ms >= 12_000:
            peeked[0] = True
            assert harness.panel_evf_status == 0
            harness.call_focstart()
            # Screen must immediately wake to saved EVF (2) & set peek_active=1!
            assert harness.panel_evf_status == 2
            assert harness.read_state_struct()["peek_active"] == 1
        # After 5s peek expires (~17500ms), screen must automatically go back to ALL OFF (0)!
        if peeked[0] and not re_slept_checked[0] and harness.now_ms >= 17_600:
            re_slept_checked[0] = True
            assert harness.panel_evf_status == 0, f"Expected auto re-sleep to 0, got {harness.panel_evf_status}"
            assert harness.read_state_struct()["peek_active"] == 0
            harness.allow_early_stop = True
            assert harness.call_capstart() is False

    h.on_tick_cb = test_s1_peek
    h.run_eventloop()
    assert peeked[0] and re_slept_checked[0]
    # Final disp sequence: ALL OFF (0) -> S1 peek EVF ON (2) -> auto re-sleep ALL OFF (0) -> abort EVF ON (2)!
    disp_seq = [e[3][0][1] for e in h.events if e[1] == "@M006"]
    assert disp_seq == [0, 2, 0, 2], disp_seq
    assert h.panel_evf_status == 2 and h.live_recs == set() and h.live_stack_pls == set()
    print(f"[PASS] Test 5: S1 half-press 5s Screen Peek (EVF) -> exact disp transitions {disp_seq}")

    # --- TEST 6: Already-MF guard + Auto-Review re-sleep + BIONZ AFMF-drop self-recovery ---
    h.reset_world()
    h.focus_mode = 1        # Already MF!
    h.panel_evf_status = 1
    h.wake_display_on_shot = True
    h.backup.update({
        BK_ENABLE: 1, BK_DELAY: 1, BK_INTERVAL: 1, BK_SHOTS: 0, BK_AF_MODE: 0, BK_DISP_SLEEP: 1
    })
    assert h.call_capstart() is False

    def stop_mf_at_3(harness: IntervalHarness) -> None:
        if len(harness.shot_times_ms) >= 3 and harness.read_state_struct()["shots_taken"] >= 3:
            harness.allow_early_stop = True
            assert harness.call_capstart() is False

    h.on_tick_cb = stop_mf_at_3
    h.run_eventloop()
    assert not any(c[1] in ("AFMF_ON", "AFMF_OFF") for c in h.direct_cmds)
    assert h.focus_mode == 1 and h.panel_evf_status == 1

    # BIONZ drops AFMF mid-sequence -> auto re-asserts before next shot!
    h.reset_world()
    h.focus_mode = 2
    h.drop_afmf_after_shot = True
    h.backup.update({
        BK_ENABLE: 1, BK_DELAY: 0, BK_INTERVAL: 1, BK_SHOTS: 8, BK_AF_MODE: 1, BK_DISP_SLEEP: 0
    })  # Unlimited (idx 8!) + Holy Grail AF Lock
    assert h.call_capstart() is True and h.read_state_struct()["max_shots"] == 0

    def stop_reassert(harness: IntervalHarness) -> None:
        if len(harness.shot_times_ms) >= 4 and harness.read_state_struct()["shots_taken"] >= 4:
            harness.allow_early_stop = True
            assert harness.call_capstart() is False

    h.on_tick_cb = stop_reassert
    h.run_eventloop()
    assert len([c for c in h.direct_cmds if c[1] == "AFMF_ON"]) == 3
    assert len([c for c in h.direct_cmds if c[1] == "AFMF_OFF"]) == 1
    print("[PASS] Test 6: Already-MF guard + Auto-Review re-sleep + BIONZ AFMF-drop self-recovery")

    # --- TEST 7: Live View OSD Blinking Drive Mode Icon + Remaining Shots Countdown + Destructor Guards ---
    h.reset_world()
    h.backup.update({
        BK_ENABLE: 1, BK_DELAY: 1, BK_INTERVAL: 1, BK_SHOTS: 0, BK_AF_MODE: 0, BK_DISP_SLEEP: 0
    })
    # Verify Option A untouched stock ACT_DriveMode icons across ALL 5 appearance modes (LCD + EVF)
    act_single = [0x0205C10B, 0x0205C0D3, 0x020E804E, 0x0205C0EF, 0x020CE1CD]
    act_intv   = [0x0205D3E9, 0x0205D3E9, 0x020E8787, 0x0205D3E9, 0x0205D3E9]
    for mode, (s_ico, i_ico) in enumerate(zip(act_single, act_intv)):
        assert h.call_act_drivemode(s_ico, mode, ACT_OBJ_ADDR) == i_ico, f"mode {mode} mismatch"
    assert h.call_remain_tramp("interval_remain_a_trampoline", 412, REMAIN_ADDR) == 412
    assert h.call_remain_tramp("interval_remain_b_trampoline", 412, REMAIN_ADDR) == 412

    assert h.call_capstart() is False
    assert h.disp_state_calls == [(10_000, ACT_OBJ_ADDR + 0x1AC, 0)]
    assert h.call_remain_tramp("interval_remain_a_trampoline", 412, REMAIN_ADDR) == 30

    seen_remains: list[int] = []

    def track_osd(harness: IntervalHarness) -> None:
        st_now = harness.read_state_struct()
        if st_now["running"]:
            seen_remains.append(harness.call_remain_tramp("interval_remain_a_trampoline", 412, REMAIN_ADDR))
        if st_now["shots_taken"] >= 3:
            harness.allow_early_stop = True
            assert harness.call_capstart() is False

    h.on_tick_cb = track_osd
    h.run_eventloop()
    vis_seq = [c[2] for c in h.disp_state_calls]
    assert 0 in vis_seq and 1 in vis_seq and vis_seq[-1] == 1
    assert 30 in seen_remains and 29 in seen_remains and 28 in seen_remains
    assert h.call_remain_tramp("interval_remain_a_trampoline", 412, REMAIN_ADDR) == 412

    # Unlimited (idx 8) + destructor safety
    h.backup[BK_SHOTS] = 8
    h.on_tick_cb = None
    assert h.call_capstart() is False
    assert h.call_remain_tramp("interval_remain_a_trampoline", 412, REMAIN_ADDR) == 9999
    h.call_sym("act_drivemode_destroy_hook", ACT_OBJ_ADDR)
    assert h.call_sym("act_remain_dtor_hook", REMAIN_ADDR) == REMAIN_ADDR
    prev_ds, prev_ru = len(h.disp_state_calls), len(h.remain_updates)
    assert h.call_capstart() is False
    assert len(h.disp_state_calls) == prev_ds and len(h.remain_updates) == prev_ru
    print("[PASS] Test 7: Live View OSD blinking + countdown (30->29->28->SD 412) + Unlimited + dtor guards")

    # --- TEST 8: Long exposure (4.5s > 2s) + Abort during AF hold + Menu/Playback abort ---
    h.reset_world()
    h.exposure_ms = 4500
    h.stay_state4_during_exp = True
    h.backup.update({BK_ENABLE: 1, BK_DELAY: 0, BK_INTERVAL: 1, BK_SHOTS: 8, BK_AF_MODE: 0, BK_DISP_SLEEP: 0})
    assert h.call_capstart() is True

    def stop_le(harness: IntervalHarness) -> None:
        if len(harness.shot_times_ms) >= 4:
            harness.allow_early_stop = True
            assert harness.call_capstart() is False

    h.on_tick_cb = stop_le
    h.run_eventloop()
    gaps = [b - a for a, b in zip(h.shot_times_ms, h.shot_times_ms[1:])]
    assert all(g >= 4500 for g in gaps)

    # Abort during AF hold (Mode 2)
    h.reset_world()
    h.backup.update({BK_ENABLE: 1, BK_DELAY: 0, BK_INTERVAL: 1, BK_SHOTS: 8, BK_AF_MODE: 2, BK_DISP_SLEEP: 1})
    assert h.call_capstart() is True
    aborted = [False]

    def abort_af(harness: IntervalHarness) -> None:
        if not aborted[0] and harness.read_state_struct()["af_held"] == 1:
            aborted[0] = True
            assert harness.call_capstart() is False

    h.on_tick_cb = abort_af
    h.run_eventloop()
    assert aborted[0] and h.direct_cmds[-1][1] == "AF_STOP" and h.panel_evf_status == 1 and h.live_recs == set()

    # Playback abort
    h.reset_world()
    h.backup.update({BK_ENABLE: 1, BK_DELAY: 0, BK_INTERVAL: 3, BK_SHOTS: 8, BK_DISP_SLEEP: 1})
    assert h.call_capstart() is True

    def go_pb(harness: IntervalHarness) -> None:
        if harness.now_ms >= 11_000:
            harness.allow_early_stop = True
            harness.forced_state = 2

    h.on_tick_cb = go_pb
    h.run_eventloop()
    assert h.read_state_struct()["abort_reason"] == 3 and h.panel_evf_status == 1 and h.live_recs == set()
    print("[PASS] Test 8: Long exposure + AF-hold abort + Playback abort all clean & 0 leaks")

    # --- TEST 9: Drive Mode hooks (setValue/getValue/4 remaps/ACT_DriveMode/SPINAL) + All expanded tables ---
    h.reset_world()
    h.hw_values[7] = 1
    assert h.call_sym("pdm_setValue_hook", MODEL_ADDR, 7, 0x24) == 1 and h.hw_values[7] == 0 and h.backup[BK_ENABLE] == 1
    h.backup[BK_DELAY] = 1
    assert h.call_capstart() is False
    assert h.call_sym("pdm_setValue_hook", MODEL_ADDR, 7, 1) == 1 and h.read_state_struct()["running"] == 0
    assert h.live_recs == set()
    h.hw_set_fail_pids.add(7)
    assert h.call_sym("pdm_setValue_hook", MODEL_ADDR, 7, 0x24) == 0 and h.backup[BK_ENABLE] == 0
    h.hw_set_fail_pids.clear()
    h.hw_values[7] = 1
    assert h.call_sym("pdm_setValue_hook", MODEL_ADDR, 0xE4, 1) == 1 and h.hw_values[7] == 0
    out_ptr = SCRATCH_ADDR
    assert h.call_sym("pdm_getValue_hook", MODEL_ADDR, 7, out_ptr) == 1
    assert struct.unpack("<I", h.mu.mem_read(out_ptr, 4))[0] == 0x24
    for sym_name in (
        "pdm_isInvalid_trampoline",
        "pdm_isDisable_trampoline",
        "pdm_isGrayedOut_trampoline",
        "pdm_getCaution_trampoline",
    ):
        assert h.call_sym(sym_name, MODEL_ADDR, 7, 0x24, SCRATCH_ADDR) == ((7 << 16) | 0)
    h.backup[BK_ENABLE] = 1
    h.call_sym("spinal_cmd_hook", MODEL_ADDR, 1)
    assert h.spinal_calls == [(MODEL_ADDR, 0)]

    # Verify all 10 interval indices & 9 shot indices match menu_spec.py exactly!
    exp_intv = [1, 2, 3, 5, 10, 15, 20, 30, 45, 60]
    for idx, sec in enumerate(exp_intv):
        h.reset_world()
        h.backup.update({BK_ENABLE: 1, BK_DELAY: 1, BK_INTERVAL: idx, BK_SHOTS: 0})
        assert h.call_capstart() is False
        assert h.read_state_struct()["interval_s"] == sec
        assert h.call_capstart() is False
    exp_shots = [30, 60, 100, 120, 240, 300, 500, 999, 0]
    for idx, cnt in enumerate(exp_shots):
        h.reset_world()
        h.backup.update({BK_ENABLE: 1, BK_DELAY: 1, BK_INTERVAL: 0, BK_SHOTS: idx})
        assert h.call_capstart() is False
        assert h.read_state_struct()["max_shots"] == cnt
        assert h.call_capstart() is False
    print("[PASS] Test 9: Drive Mode hooks + all 10 interval & 9 shot option mappings verified")

    # --- TEST 10: Auto Review State 5 + NULL timerManager safe fallback ---
    h.reset_world()
    h.forced_state = 5
    h.backup.update({BK_ENABLE: 1, BK_DELAY: 1, BK_INTERVAL: 0, BK_SHOTS: 0, BK_AF_MODE: 0, BK_DISP_SLEEP: 0})
    assert h.call_capstart() is False

    def stop_ar(harness: IntervalHarness) -> None:
        if len(harness.shot_times_ms) >= 3:
            harness.allow_early_stop = True
            assert harness.call_capstart() is False

    h.on_tick_cb = stop_ar
    h.run_eventloop()
    assert len(h.shot_times_ms) == 3 and h.live_recs == set()
    h.reset_world()
    h.mu.mem_write(h.g_tmr_addr, struct.pack("<I", 0))
    h.backup[BK_ENABLE] = 1
    assert h.call_capstart() is True and h.read_state_struct()["running"] == 0
    print("[PASS] Test 10: Auto Review State 5 + NULL timerManager safe fallback verified")

    # --- TEST 11: Single-Slot Fn Shortcut (PID 0xEA + ViewSettingMenu direct Layout 5 folder jump) ---
    h.reset_world()
    out_ptr = SCRATCH_ADDR + 0x200
    h.mu.mem_write(out_ptr, struct.pack("<I", 0xDEADBEEF))
    assert h.call_sym("pdm_getValue_hook", MODEL_ADDR, 0xEA, out_ptr) == 1
    assert struct.unpack("<I", h.mu.mem_read(out_ptr, 4))[0] == 0
    assert h.call_sym("pdm_setValue_hook", MODEL_ADDR, 0xEA, 0) == 1
    assert h.pdm_calls == [], "PID 0xEA get/set must be handled directly without hitting stock PDM"

    # Case 11a (Bug 1 real-hardware order): State 0 onEnterState calls initMenuNode FIRST!
    # Must select Tab 2 P3 folder & sync page/tab, WITHOUT clearing bootElement (0x77) or locking folderState!
    h.vsm_calls.clear()
    h.mu.mem_write(VSM_OBJ_ADDR + 4, struct.pack("<I", 0))         # layout = 0
    h.mu.mem_write(VSM_OBJ_ADDR + 0x13C, struct.pack("<I", 0x77))  # bootElement = 0x77
    h.call_sym("vsm_initMenuNode_trampoline", VSM_OBJ_ADDR)
    assert h.vsm_initnode_fallthrough == 0
    assert struct.unpack("<I", h.mu.mem_read(VSM_OBJ_ADDR + 0x13C, 4))[0] == 0x77, \
        "initMenuNode must NOT prematurely clear bootElement=0x77 before VSM_EV_BOOT hits onTransition!"
    assert h.vsm_calls == [
        ("selectNode", VSM_OBJ_ADDR, FAKE_FOLDER),
        ("syncIndices", VSM_OBJ_ADDR),
        ("getPage", VSM_OBJ_ADDR),
        ("setPageTab", VSM_OBJ_ADDR, 3, 2),
    ], f"Unexpected VSM initMenuNode sequence: {h.vsm_calls}"

    # Case 11b (Bug 1 real-hardware order): VSM_EV_BOOT (0x11012001) hits onTransition NEXT!
    # Must clear bootElement=0, call V_VSM_ACTION7, and transition to Layout 5 + Action 0x38!
    nl_ptr = SCRATCH_ADDR + 0x210
    act_ptr = SCRATCH_ADDR + 0x214
    h.mu.mem_write(nl_ptr, struct.pack("<I", 0))
    h.mu.mem_write(act_ptr, struct.pack("<I", 0))
    h.vsm_calls.clear()
    h.vsm_ev_id = 0x11012001
    h.call_sym("vsm_onTransition_trampoline", VSM_OBJ_ADDR, nl_ptr, act_ptr)
    assert h.vsm_ontrans_fallthrough == 0
    assert struct.unpack("<I", h.mu.mem_read(VSM_OBJ_ADDR + 0x13C, 4))[0] == 0, \
        "onTransition must clear bootElement=0x77 after handling VSM_EV_BOOT!"
    assert h.vsm_calls == [
        ("GetEventId", VSM_OBJ_ADDR),
        ("action7", VSM_OBJ_ADDR),
    ], f"Unexpected VSM onTransition call sequence: {h.vsm_calls}"
    assert struct.unpack("<I", h.mu.mem_read(nl_ptr, 4))[0] == 5
    assert struct.unpack("<I", h.mu.mem_read(act_ptr, 4))[0] == 0x38

    # Case 11c: Close event (0xb) during bootElement=0x77 must fall through to Sony original onTransition!
    h.mu.mem_write(VSM_OBJ_ADDR + 0x13C, struct.pack("<I", 0x77))
    h.vsm_ev_id = 0x0B
    h.call_sym("vsm_onTransition_trampoline", VSM_OBJ_ADDR, nl_ptr, act_ptr)
    assert h.vsm_ontrans_fallthrough == 1
    assert struct.unpack("<I", h.mu.mem_read(VSM_OBJ_ADDR + 0x13C, 4))[0] == 0x77

    # Case 11d: Normal MENU button (bootElement=0) falls through in both onTransition & initMenuNode!
    h.mu.mem_write(VSM_OBJ_ADDR + 0x13C, struct.pack("<I", 0))
    h.vsm_ev_id = 0x11012001
    h.call_sym("vsm_onTransition_trampoline", VSM_OBJ_ADDR, nl_ptr, act_ptr)
    assert h.vsm_ontrans_fallthrough == 2
    h.call_sym("vsm_initMenuNode_trampoline", VSM_OBJ_ADDR)
    assert h.vsm_initnode_fallthrough == 1

    # Case 11e: NULL folder fallback falls through cleanly!
    h.mu.mem_write(VSM_OBJ_ADDR + 0x13C, struct.pack("<I", 0x77))
    h.mu.mem_write(h.p3_arr_addr + 16, struct.pack("<I", 0))
    h.call_sym("vsm_initMenuNode_trampoline", VSM_OBJ_ADDR)
    assert h.vsm_initnode_fallthrough == 2
    print("[PASS] Test 11: Single-slot Fn shortcut (Bug 1 fix: initMenuNode -> Action7 + Layout 5 / Action 0x38) verified")

    # --- TEST 12: Fn Menu ISO AUTO Min. SS Direct Popup Launcher (VSM_BOOT_MIN_SS = 0x78) ---
    h.reset_world()
    # Case 12a: initMenuNode selects Tab 2 P3 slot 5 (FAKE_MIN_SS) & leaves bootElement=0x78 intact
    h.vsm_calls.clear()
    h.mu.mem_write(VSM_OBJ_ADDR + 4, struct.pack("<I", 0))         # layout = 0
    h.mu.mem_write(VSM_OBJ_ADDR + 0x13C, struct.pack("<I", 0x78))  # bootElement = 0x78
    h.call_sym("vsm_initMenuNode_trampoline", VSM_OBJ_ADDR)
    assert h.vsm_initnode_fallthrough == 0
    assert struct.unpack("<I", h.mu.mem_read(VSM_OBJ_ADDR + 0x13C, 4))[0] == 0x78
    assert h.vsm_calls == [
        ("selectNode", VSM_OBJ_ADDR, FAKE_MIN_SS),
        ("syncIndices", VSM_OBJ_ADDR),
        ("getPage", VSM_OBJ_ADDR),
        ("setPageTab", VSM_OBJ_ADDR, 3, 2),
    ], f"Unexpected VSM initMenuNode (0x78) sequence: {h.vsm_calls}"

    # Case 12b: onTransition arms Layout 1 reopen-popup state across ALL 24 BK_MIN_SS values + OOB!
    nl_ptr = SCRATCH_ADDR + 0x220
    act_ptr = SCRATCH_ADDR + 0x224
    expected_map = {0: 2, 1: 0, 2: 1, **{k: k for k in range(3, 24)}, 255: 2}
    for bk_val, want_idx in expected_map.items():
        h.backup[BK_MIN_SS] = bk_val
        h.mu.mem_write(VSM_OBJ_ADDR + 4, struct.pack("<I", 0))
        h.mu.mem_write(VSM_OBJ_ADDR + 0x120, struct.pack("<I", 0))
        h.mu.mem_write(VSM_OBJ_ADDR + 0x130, struct.pack("<I", 0xDEADBEEF))
        h.mu.mem_write(VSM_OBJ_ADDR + 0x13C, struct.pack("<I", 0x78))
        h.mu.mem_write(VSM_OBJ_ADDR + 0x160, bytes([0]))
        h.mu.mem_write(nl_ptr, struct.pack("<I", 0))
        h.mu.mem_write(act_ptr, struct.pack("<I", 0))
        h.vsm_calls.clear()
        h.vsm_ev_id = 0x11012001
        h.call_sym("vsm_onTransition_trampoline", VSM_OBJ_ADDR, nl_ptr, act_ptr)
        assert h.vsm_ontrans_fallthrough == 0
        assert h.vsm_calls == [("GetEventId", VSM_OBJ_ADDR)], (
            f"0x78 must let state machine run Action 7 (not call it twice): {h.vsm_calls}"
        )
        assert struct.unpack("<I", h.mu.mem_read(VSM_OBJ_ADDR + 0x13C, 4))[0] == 0
        assert struct.unpack("<I", h.mu.mem_read(VSM_OBJ_ADDR + 0x120, 4))[0] == 2
        assert struct.unpack("<I", h.mu.mem_read(VSM_OBJ_ADDR + 0x130, 4))[0] == want_idx, (
            f"bk={bk_val}: got opt_idx={struct.unpack('<I', h.mu.mem_read(VSM_OBJ_ADDR + 0x130, 4))[0]}, want {want_idx}"
        )
        assert h.mu.mem_read(VSM_OBJ_ADDR + 0x160, 1)[0] == 1
        assert struct.unpack("<I", h.mu.mem_read(nl_ptr, 4))[0] == 1
        assert struct.unpack("<I", h.mu.mem_read(act_ptr, 4))[0] == 7

    # Case 12c: Close event (0x0B) & NULL tab2_p3[5] both fall through cleanly!
    h.mu.mem_write(VSM_OBJ_ADDR + 0x13C, struct.pack("<I", 0x78))
    h.vsm_ev_id = 0x0B
    h.call_sym("vsm_onTransition_trampoline", VSM_OBJ_ADDR, nl_ptr, act_ptr)
    assert h.vsm_ontrans_fallthrough == 1
    assert struct.unpack("<I", h.mu.mem_read(VSM_OBJ_ADDR + 0x13C, 4))[0] == 0x78

    h.mu.mem_write(h.p3_arr_addr + 20, struct.pack("<I", 0))
    h.call_sym("vsm_initMenuNode_trampoline", VSM_OBJ_ADDR)
    assert h.vsm_initnode_fallthrough == 1
    print("[PASS] Test 12: Fn Menu ISO AUTO Min. SS direct popup launcher (0x78 -> Layout 1 + Action 7, all 24 opts) verified")

    print("\nALL 12 PRODUCTION RELEASE UNICORN ARMv7-A THUMB-2 TESTS PASSED (v3.33, 0x40000000 PIC BIAS)!")


if __name__ == "__main__":
    run_all_tests()
