"""Comprehensive Unicorn ARMv7-A Thumb-2 emulation tests for unified `src/interval_engine.c` (CFW v3.31).

Verifies 100% PIC independence (+0x40000000 load bias), zero TimerManager/ParamList leaks, and:
  1. Toggle OFF (0): 100% stock `30" <-> BULB` dial & shutter behavior
  2. Toggle ON  (1): Full native dial walk `30" -> 45" -> 60" -> 80" -> 100" -> 120" -> BULB`
                     and back `BULB -> 120" -> 100" -> 80" -> 60" -> 45" -> 30"` with live UI notify
                     (including byte-truncated signed char `0xFF` delta!)
  3. Timed Extended Shutter (`80"`): single shutter click & release runs exact 80s timed Bulb,
                     live seconds countdown OSD, blinking icon, APO keep-alive, auto-close
  4. Manual `BULB` step (past `120"`): press & release passes straight through (normal hold BULB!)
  5. Early Cancel by second shutter click during `120"` exposure (+ trailing S2 release swallowed)
  6. Combo Mode: Interval Shooting + `45"` Extended Shutter (3 consecutive 45s timed Bulb shots)
  7. Menu Toggle OFF (`PID 0xEC`) resets `ext_ss_idx -> 0`, notifies UI, restores SPINAL_REFLEX
  8. Three-feature coexistence: Extended Shutter (`0xEC`/`0x01070147`) + Auto ISO Min. SS
     (`0xEB`/`0x010700D0` boot sync) + Interval Shoot (`0xE4`) SPINAL_REFLEX interplay & Option A icons
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
    UC_ARM_REG_SP,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import patch_libobj as PL  # noqa: E402

BK_ENABLE      = 0x010700F6
BK_DELAY       = 0x010700F7
BK_INTERVAL    = 0x0107003C
BK_SHOTS       = 0x0107003F
BK_AF_MODE     = 0x0107007E
BK_DISP_SLEEP  = 0x01070121
BK_MIN_SS      = 0x010700D0
BK_EXT_SHUTTER = 0x01070147

PID_ENABLE      = 0xE4
PID_MIN_SS      = 0xEB
PID_EXT_SHUTTER = 0xEC

EV_SET_SPINAL        = 0x1A
EV_SET_MIN_SS        = 0x7E
PRM_KEY_MIN_SS       = 0x39F
EV_PANEL_EVF         = 0x2F
PRM_KEY_PANEL_EVF    = 0x1200600B
EV_RELOAD_APO        = 5
EV_FAST_DISP_SHUTTER = 0x18000001
INFO_SHUTTER_SPD     = 0x35
SS_BULB_NUM          = 0xFFFF
UI_DISP_ADDR         = 0x6B008000

LINK_CODE_VA  = 0x014B0000
LINK_STATE_VA = 0x014B2000
LOAD_BIAS     = 0x40000000

RET_SENTINEL  = 0x50000F00
STUB_PAGE     = 0x50000000
HEAP_BASE     = 0x60000000
STACK_BASE    = 0x70000000
MODEL_ADDR    = 0x68000000
CAM_ADDR      = 0x68010000
SCRATCH_ADDR  = 0x69000000
ACT_OBJ_ADDR  = 0x6A000000
REMAIN_ADDR   = 0x6A002000
TMR_OBJ_ADDR  = 0x6B000000


class ExtShutterHarness:
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

        self.mu.mem_map(STUB_PAGE, 0x10000)
        self.mu.mem_map(HEAP_BASE, 0x100000)
        self.mu.mem_map(MODEL_ADDR, 0x20000)
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
        self.set_hw_shutter(30, 1)

        self.heap_ptr = HEAP_BASE
        self.backup: dict[int, int] = {
            BK_ENABLE: 0,
            BK_DELAY: 0,
            BK_INTERVAL: 0,
            BK_SHOTS: 0,
            BK_AF_MODE: 0,
            BK_DISP_SLEEP: 0,
            BK_MIN_SS: 0,
            BK_EXT_SHUTTER: 0,
        }
        self.now_ms = 10_000
        self.bionz_prep_ms = 280
        self.bionz_ack_ms = 0
        self.lenr_write_ms = 400
        self.busy_until_ms = 0
        self.focus_mode = 2
        self.panel_evf_status = 1
        self.live_stack_pls: set[int] = set()

        self.next_rec_id = 0x6C000010
        self.armed_timers: dict[int, tuple[int, int, int]] = {}
        self.live_recs: set[int] = set()
        self.wakeups = 0

        self.events: list[tuple[int, str, int, list[tuple[int, int]]]] = []
        self.direct_cmds: list[tuple[int, str, int, int]] = []
        self.param_lists: dict[int, list[tuple[int, int]]] = {}
        self.prm_values: dict[int, int] = {}
        self.capstart_passthroughs = 0
        self.capstop_passthroughs = 0
        self.shift_passthroughs: list[int] = []
        self.notify_calls: list[tuple[int, int]] = []
        self.disp_state_calls: list[tuple[int, int, int]] = []
        self.remain_updates: list[tuple[int, int]] = []
        self.hw_values: dict[int, int] = {7: 0, PID_ENABLE: 0, PID_MIN_SS: 2, PID_EXT_SHUTTER: 0}
        self.spinal_calls: list[tuple[int, int]] = []
        self.info35_r1_result: int = 0
        self.ui_events: list[tuple[int, int, int, int]] = []  # (disp, ev_id, a2, a3)
        self.ev_ctors: dict[int, tuple[int, int, int]] = {}
        self.async_bionz_delay: bool = False
        self.fast_disp_r4_after: int = 0

    def set_hw_shutter(self, num: int, den: int) -> None:
        self.mu.mem_write(CAM_ADDR + 0x200, struct.pack("<II", num, den))

    def get_hw_shutter(self) -> tuple[int, int]:
        return struct.unpack("<II", bytes(self.mu.mem_read(CAM_ADDR + 0x200, 8)))

    def set_capture_sts(self, sts: int) -> None:
        self.mu.mem_write(MODEL_ADDR + 0xD08, struct.pack("<I", sts))

    def read_state_struct(self) -> dict[str, int]:
        raw = bytes(self.mu.mem_read(self.state_addr, 0xE0))
        vals = struct.unpack("<18IQQQ12I3Q2I2Q6IQ", raw)
        names = [
            "magic", "running", "phase", "af_held",
            "shots_taken", "seq_idx", "disp_is_off", "model_rec",
            "active_rec", "start_delay_s", "interval_s", "max_shots",
            "af_mode", "disp_sleep", "abort_reason", "inactive_ticks",
            "busy_ticks", "cap_held", "anchor_ms", "next_shot_ms",
            "last_fire_ms", "cb_count", "disp_recheck", "peek_active", "bulb_active",
            "dm_widget", "remain_widget", "lock_applied", "afmf_locked",
            "ael_locked", "saved_disp_out", "blink_phase", "bulb_mode_only",
            "last_blink_ms", "last_apo_ms", "peek_until_ms",
            "bulb_duration_s", "swallow_next_capstop",
            "bulb_start_ms", "bulb_end_ms",
            "bulb_last_osd_sec", "ext_ss_idx", "model_cam",
            "min_ss_synced", "min_ss_last_liro", "ext_ss_entering", "ext_ss_enter_ms",
        ]
        return dict(zip(names, vals))

    def _read_cstr(self, addr: int) -> str:
        out = bytearray()
        while True:
            b = bytes(self.mu.mem_read(addr + len(out), 1))[0]
            if b == 0:
                return out.decode("ascii", "replace")
            out.append(b)

    def _ret_from_stub(self, r0: int | None = None) -> None:
        if r0 is not None:
            self.mu.reg_write(UC_ARM_REG_R0, r0 & 0xFFFFFFFF)
        lr = self.mu.reg_read(UC_ARM_REG_LR)
        self.mu.reg_write(UC_ARM_REG_PC, lr)

    def _on_stub(self, uc: Uc, address: int, size: int, user_data) -> None:
        name = self.stub_targets.get(address)
        if name is None:
            return
        r0 = uc.reg_read(UC_ARM_REG_R0)
        r1 = uc.reg_read(UC_ARM_REG_R1)
        r2 = uc.reg_read(UC_ARM_REG_R2)
        r3 = uc.reg_read(UC_ARM_REG_R3)

        if name == "V_CAPSTART_CONT":
            self.capstart_passthroughs += 1
            if self.get_hw_shutter()[0] == SS_BULB_NUM:
                self.set_capture_sts(2)
            sp = uc.reg_read(UC_ARM_REG_SP)
            uc.reg_write(UC_ARM_REG_SP, sp + 0x5C + 20)
            uc.reg_write(UC_ARM_REG_PC, RET_SENTINEL | 1)
            return

        if name == "V_CAPSTOP_CONT":
            self.capstop_passthroughs += 1
            self.set_capture_sts(0)
            sp = uc.reg_read(UC_ARM_REG_SP)
            uc.reg_write(UC_ARM_REG_SP, sp + 0x24 + 12)
            uc.reg_write(UC_ARM_REG_PC, RET_SENTINEL | 1)
            return

        if name == "V_SHIFT_SS_CONT":
            # Sony's real 0x005514aa executes `sxtb r1, r4` (signed 8-bit delta)
            b = r1 & 0xFF
            delta = b - 0x100 if (b & 0x80) else b
            self.shift_passthroughs.append(delta)
            if not self.async_bionz_delay:
                hw_num, hw_den = self.get_hw_shutter()
                # Simulate BIONZ hardware 30" <-> BULB transition (handles both 30/1 and 300/10!)
                if hw_den != 0 and hw_num == 30 * hw_den and delta < 0:
                    self.set_hw_shutter(SS_BULB_NUM, 1)
                elif hw_num == SS_BULB_NUM and delta > 0:
                    self.set_hw_shutter(30, 1)
                elif hw_den != 0 and hw_num == 30 * hw_den and delta > 0:
                    self.set_hw_shutter(25, 1)
            sp = uc.reg_read(UC_ARM_REG_SP)
            _, _, _, saved_lr = struct.unpack("<4I", bytes(uc.mem_read(sp, 16)))
            uc.reg_write(UC_ARM_REG_SP, sp + 16)
            uc.reg_write(UC_ARM_REG_PC, saved_lr)
            return

        if name in ("V_INFO35_NUM_CONT", "V_INFO35_DEN_CONT"):
            self.info35_r1_result = r1
            uc.reg_write(UC_ARM_REG_PC, RET_SENTINEL | 1)
            return

        if name == "V_FAST_DISP_CONT":
            self.fast_disp_r4_after = uc.reg_read(UC_ARM_REG_R4)
            uc.reg_write(UC_ARM_REG_PC, RET_SENTINEL | 1)
            return

        if name == "P_MEMCPY":
            data_cp = bytes(uc.mem_read(r1, r2))
            uc.mem_write(r0, data_cp)
            self._ret_from_stub(r0)
            return

        if name == "V_GET_UI_DISP":
            self._ret_from_stub(UI_DISP_ADDR)
            return

        if name == "P_EVENT_CTOR":
            self.ev_ctors[r0] = (r1, r2, r3)
            self._ret_from_stub(r0)
            return

        if name == "V_POST_UI_EVENT":
            ev_id, a2, a3 = self.ev_ctors.get(r1, (0, 0, 0))
            self.ui_events.append((r0, ev_id, a2, a3))
            self._ret_from_stub(0)
            return

        if name == "V_NOTIFY_UPDATE":
            self.notify_calls.append((r0, r1))
            self._ret_from_stub(0)
            return

        if name == "V_SPINAL_CONT":
            self.spinal_calls.append((r0, r1))
            sp = uc.reg_read(UC_ARM_REG_SP)
            _, _, _, saved_lr = struct.unpack("<4I", bytes(uc.mem_read(sp, 16)))
            uc.reg_write(UC_ARM_REG_SP, sp + 16)
            uc.reg_write(UC_ARM_REG_PC, saved_lr)
            return

        if name == "V_PDM_SETVAL_CONT":
            self.hw_values[r1] = r2
            if r1 == PID_ENABLE:
                self.backup[BK_ENABLE] = r2 & 0xFF
            elif r1 == PID_EXT_SHUTTER:
                self.backup[BK_EXT_SHUTTER] = r2 & 0xFF
            sp = uc.reg_read(UC_ARM_REG_SP)
            _, _, _, saved_lr = struct.unpack("<4I", bytes(uc.mem_read(sp, 16)))
            uc.reg_write(UC_ARM_REG_SP, sp + 16)
            uc.reg_write(UC_ARM_REG_R0, 1)
            uc.reg_write(UC_ARM_REG_PC, saved_lr)
            return

        if name == "V_ACT_DM_CONT":
            uc.reg_write(UC_ARM_REG_PC, RET_SENTINEL | 1)
            return

        if name == "P_NEW":
            ptr = self.heap_ptr
            self.heap_ptr += (r0 + 15) & ~15
            self._ret_from_stub(ptr)
            return

        if name == "P_PL_CTOR":
            self.param_lists[r0] = []
            if r0 >= STACK_BASE:
                self.live_stack_pls.add(r0)
            self._ret_from_stub(r0)
            return

        if name == "P_PL_DTOR":
            self.live_stack_pls.discard(r0)
            self.param_lists.pop(r0, None)
            self._ret_from_stub(r0)
            return

        if name == "V_PRM_CTOR":
            self.prm_values[r0] = r1
            self._ret_from_stub(r0)
            return

        if name == "V_PRM_GETNUM":
            self._ret_from_stub(self.prm_values.get(r0, 0))
            return

        if name == "V_PL_GET":
            items = self.param_lists.get(r0, [])
            found = next((ptr for k, ptr in items if k == r1), 0)
            self._ret_from_stub(found)
            return

        if name == "P_PL_ADD":
            self.param_lists.setdefault(r0, []).append((r1, r2))
            self._ret_from_stub(0)
            return

        if name == "P_GET_MODEL_INFO":
            model = self._read_cstr(r0)
            if model == "@M006" and r1 == 0x20:
                prm = self.heap_ptr
                self.heap_ptr += 16
                self.prm_values[prm] = self.panel_evf_status
                self.param_lists.setdefault(r2, []).append((PRM_KEY_PANEL_EVF, prm))
                self._ret_from_stub(1)
                return
            self._ret_from_stub(0)
            return

        if name == "P_REQ_EXEC":
            model = self._read_cstr(r0)
            items = [(k, self.prm_values.get(p, 0)) for k, p in self.param_lists.get(r2, [])]
            self.events.append((self.now_ms, model, r1, items))
            if model == "@M006" and r1 == EV_PANEL_EVF:
                for k, v in items:
                    if k == PRM_KEY_PANEL_EVF:
                        self.panel_evf_status = v
            self._ret_from_stub(0)
            return

        if name == "P_BK_READ":
            uc.mem_write(r1, bytes([self.backup.get(r0, 0) & 0xFF]))
            self._ret_from_stub(0)
            return

        if name == "P_BK_WRITE":
            self.backup[r0] = bytes(uc.mem_read(r1, 1))[0]
            self._ret_from_stub(0)
            return

        if name == "V_TIME_NOW":
            uc.mem_write(r0, struct.pack("<ii", self.now_ms // 1000, (self.now_ms % 1000) * 1_000_000))
            self._ret_from_stub(0)
            return

        if name == "V_TIME_ADD_MS":
            sec, nsec = struct.unpack("<ii", bytes(uc.mem_read(r0, 8)))
            tot_ms = sec * 1000 + nsec // 1_000_000 + r1
            uc.mem_write(r0, struct.pack("<ii", tot_ms // 1000, (tot_ms % 1000) * 1_000_000))
            self._ret_from_stub(0)
            return

        if name == "V_TMR_ADD":
            sec, nsec = struct.unpack("<ii", bytes(uc.mem_read(r1, 8)))
            fire_ms = sec * 1000 + nsec // 1_000_000
            rec = self.next_rec_id
            self.next_rec_id += 0x20
            self.armed_timers[rec] = (fire_ms, r2, r3)
            self.live_recs.add(rec)
            self._ret_from_stub(rec)
            return

        if name == "V_TMR_REMOVE":
            self.armed_timers.pop(r1, None)
            self.live_recs.discard(r1)
            self._ret_from_stub(0)
            return

        if name == "V_WAKEUP_LOOP":
            self.wakeups += 1
            self._ret_from_stub(0)
            return

        if name == "V_CAP_START_CMD":
            self.direct_cmds.append((self.now_ms, "CAP_START", r0, r1))
            self.set_capture_sts(1)
            self.bionz_ack_ms = self.now_ms + self.bionz_prep_ms
            self._ret_from_stub(0)
            return

        if name == "V_CAP_STOP_CMD":
            self.direct_cmds.append((self.now_ms, "CAP_STOP", r0, r1))
            self.set_capture_sts(3)
            self.busy_until_ms = self.now_ms + self.lenr_write_ms
            self._ret_from_stub(0)
            return

        if name == "V_AF_START_CMD":
            self.direct_cmds.append((self.now_ms, "AF_START", r0, r1))
            self._ret_from_stub(0)
            return

        if name == "V_AF_STOP_CMD":
            self.direct_cmds.append((self.now_ms, "AF_STOP", r0, r1))
            self._ret_from_stub(0)
            return

        if name == "V_AFMF_ON_CMD":
            self.direct_cmds.append((self.now_ms, "AFMF_ON", r0, 0))
            self.focus_mode = 1
            self._ret_from_stub(0)
            return

        if name == "V_AFMF_OFF_CMD":
            self.direct_cmds.append((self.now_ms, "AFMF_OFF", r0, 0))
            self.focus_mode = 2
            self._ret_from_stub(0)
            return

        if name == "V_GET_FOCUS_MODE":
            self._ret_from_stub(self.focus_mode)
            return

        if name == "V_SET_DISP_STATE":
            self.disp_state_calls.append((self.now_ms, r0, r1))
            self._ret_from_stub(0)
            return

        if name == "V_REMAIN_UPDATE":
            self.remain_updates.append((self.now_ms, r0))
            self._ret_from_stub(0)
            return

        raise AssertionError(f"Unhandled stub: {name} at 0x{address:08x}")

    def _call(self, sym_name: str, r0: int = 0, r1: int = 0, r2: int = 0, r3: int = 0) -> int:
        addr = LOAD_BIAS + LINK_CODE_VA + self.syms[sym_name]
        self.mu.reg_write(UC_ARM_REG_SP, STACK_BASE + 0x18000)
        self.mu.reg_write(UC_ARM_REG_R0, r0)
        self.mu.reg_write(UC_ARM_REG_R1, r1 & 0xFFFFFFFF)
        self.mu.reg_write(UC_ARM_REG_R2, r2)
        self.mu.reg_write(UC_ARM_REG_R3, r3)
        self.mu.reg_write(UC_ARM_REG_LR, RET_SENTINEL | 1)
        self.mu.emu_start(addr | 1, RET_SENTINEL)
        return self.mu.reg_read(UC_ARM_REG_R0)

    def query_displayed_shutter(self) -> tuple[int, int]:
        self.mu.reg_write(UC_ARM_REG_R5, CAM_ADDR)
        self._call("ext_ss_num_trampoline")
        num = self.info35_r1_result
        self.mu.reg_write(UC_ARM_REG_R5, CAM_ADDR)
        self._call("ext_ss_den_trampoline")
        den = self.info35_r1_result
        return num, den

    def query_fast_disp(self, slot: int, hw_num: int, hw_den: int, valid: int = 1, lock_flag: int = 1) -> tuple[int, int]:
        """Emulate `fast_disp_get(slot, dst)` hitting `fast_disp_get_trampoline` @ 0x0050E9C4."""
        src = SCRATCH_ADDR + 0x400
        dst = SCRATCH_ADDR + 0x500
        raw_slot = bytearray(0x20)
        struct.pack_into("<HH", raw_slot, 0x00, hw_num & 0xFFFF, hw_den & 0xFFFF)
        struct.pack_into("<H", raw_slot, 0x0C, valid & 0xFFFF)
        self.mu.mem_write(src, bytes(raw_slot))
        self.mu.mem_write(dst, bytes(0x20))
        self.mu.reg_write(UC_ARM_REG_R4, lock_flag)
        self.mu.reg_write(UC_ARM_REG_R5, slot)
        self.mu.reg_write(UC_ARM_REG_R6, dst)
        self._call("fast_disp_get_trampoline", dst, src, 0x20)
        assert self.fast_disp_r4_after == lock_flag, "fast_disp_get_trampoline must preserve r4 lock flag!"
        out = bytes(self.mu.mem_read(dst, 0x20))
        return struct.unpack_from("<HH", out, 0)

    def dial_shutter(self, delta: int) -> tuple[int, int]:
        """Turn shutter dial (-1 = slower, +1 = faster) and return displayed (num, den)."""
        self._call("ext_ss_shift_trampoline", CAM_ADDR, delta)
        return self.query_displayed_shutter()

    def register_widgets(self) -> None:
        self.mu.mem_write(ACT_OBJ_ADDR + 0x588, struct.pack("<II", 0x0205C10B, 0))
        self.mu.reg_write(UC_ARM_REG_R5, ACT_OBJ_ADDR)
        self._call("interval_act_drivemode_trampoline")
        self.sample_remain(999)

    def sample_remain(self, orig_count: int = 888) -> int:
        return self._call("interval_remain_override", orig_count, REMAIN_ADDR)

    def press_shutter(self) -> bool:
        before = self.capstart_passthroughs
        self._call("interval_capstart_trampoline", MODEL_ADDR)
        return self.capstart_passthroughs > before

    def release_shutter(self) -> bool:
        before = self.capstop_passthroughs
        self._call("interval_capstop_trampoline", MODEL_ADDR)
        return self.capstop_passthroughs > before

    def run_event_loop(self, max_time_ms: int = 250_000, on_step=None) -> None:
        while self.armed_timers:
            rec, (fire_ms, cb_addr, arg) = min(self.armed_timers.items(), key=lambda kv: kv[1][0])
            if fire_ms > max_time_ms:
                break
            self.armed_timers.pop(rec)
            self.now_ms = max(self.now_ms, fire_ms)

            sc = struct.unpack("<I", bytes(self.mu.mem_read(MODEL_ADDR + 0xD08, 4)))[0]
            if sc == 1 and self.bionz_ack_ms and self.now_ms >= self.bionz_ack_ms:
                self.set_capture_sts(2)
                self.bionz_ack_ms = 0
            elif sc == 3 and self.busy_until_ms and self.now_ms >= self.busy_until_ms:
                self.set_capture_sts(0)
                self.busy_until_ms = 0

            if on_step:
                on_step(self)

            ts_addr = SCRATCH_ADDR
            self.mu.mem_write(ts_addr, struct.pack("<ii", self.now_ms // 1000, (self.now_ms % 1000) * 1_000_000))
            self.mu.reg_write(UC_ARM_REG_SP, STACK_BASE + 0x18000)
            self.mu.reg_write(UC_ARM_REG_R0, rec)
            self.mu.reg_write(UC_ARM_REG_R1, ts_addr)
            self.mu.reg_write(UC_ARM_REG_R2, arg)
            self.mu.reg_write(UC_ARM_REG_LR, RET_SENTINEL | 1)
            self.mu.emu_start(cb_addr | 1, RET_SENTINEL)


def run_all_tests() -> None:
    zig = PL.find_zig(ROOT)
    code_bytes, syms = PL.compile_engine(ROOT / "src/interval_engine.c", zig, LINK_CODE_VA, LINK_STATE_VA)
    print(f"Compiled unified src/interval_engine.c: {len(code_bytes)} bytes (limit {PL.CODE_RESERVE})")
    h = ExtShutterHarness(code_bytes, syms)

    # 1. Toggle OFF: 100% stock 30" -> BULB -> 30"
    h.reset_world()
    h.backup[BK_EXT_SHUTTER] = 0
    assert h.query_displayed_shutter() == (30, 1)
    assert h.dial_shutter(-1) == (SS_BULB_NUM, 1), "With toggle OFF, 30\" slower must jump straight to BULB"
    assert h.dial_shutter(+1) == (30, 1), "With toggle OFF, BULB faster must jump straight to 30\""
    print("  [PASS] 1. Toggle OFF -> 100% stock 30\" <-> BULB")

    # 2. Toggle ON: Full native dial walk 30" -> 45" -> 60" -> 80" -> 100" -> 120" -> BULB and back!
    h.reset_world()
    h.backup[BK_EXT_SHUTTER] = 1
    seq_down = [h.dial_shutter(-1) for _ in range(7)]
    assert seq_down == [
        (45, 1), (60, 1), (80, 1), (100, 1), (120, 1), (SS_BULB_NUM, 1), (SS_BULB_NUM, 1)
    ], f"Unexpected slower dial sequence: {seq_down}"
    assert h.get_hw_shutter() == (SS_BULB_NUM, 1)
    assert h.shift_passthroughs == [-1], "Only 30\"->45\" should hit BIONZ; 45\"..BULB stay in hardware BULB!"

    seq_up = [h.dial_shutter(+1) for _ in range(7)]
    assert seq_up == [
        (120, 1), (100, 1), (80, 1), (60, 1), (45, 1), (30, 1), (25, 1)
    ], f"Unexpected faster dial sequence: {seq_up}"
    assert h.get_hw_shutter() == (25, 1)
    assert len(h.notify_calls) == 10, f"Expected 10 UI redraw notifies across virtual steps, got {len(h.notify_calls)}"
    print("  [PASS] 2. Native dial walk 30\" -> 45\" -> 60\" -> 80\" -> 100\" -> 120\" -> BULB -> ... -> 30\" -> 25\"")

    # 3. Single-click timed exposure on 80"
    h.reset_world()
    h.register_widgets()
    h.backup[BK_EXT_SHUTTER] = 1
    assert h.dial_shutter(-1) == (45, 1)
    assert h.dial_shutter(-1) == (60, 1)
    assert h.dial_shutter(-1) == (80, 1)

    assert h.press_shutter() is True, "80\" should open Bulb synchronously (0ms latency)"
    assert h.sample_remain(888) == 80, f"Expected initial OSD 80s, got {h.sample_remain(888)}"
    h.now_ms += 100
    assert h.release_shutter() is False, "S2 release must be swallowed during 80\" exposure"

    h.run_event_loop(max_time_ms=150_000)
    st = h.read_state_struct()
    assert st["running"] == 0 and st["abort_reason"] == 0
    cap_stops = [t for t, cmd, _, _ in h.direct_cmds if cmd == "CAP_STOP"]
    assert cap_stops == [90_000], f"Expected exact 80s stop at 90000ms, got {cap_stops}"
    apo_cnt = sum(1 for _, m, ev, _ in h.events if m == "@M058" and ev == EV_RELOAD_APO)
    assert apo_cnt >= 20, f"Expected >= 20 APO keepalives during 80s exposure, got {apo_cnt}"
    assert h.sample_remain(888) == 888
    assert not h.live_recs and not h.live_stack_pls
    print(f"  [PASS] 3. Single-click 80\" timed exposure: exact CAP_STOP @ {cap_stops[0]}ms, APO={apo_cnt}x, 0 leaks")

    # 4. Manual BULB step (past 120"): 100% normal press-and-hold BULB!
    h.reset_world()
    h.backup[BK_EXT_SHUTTER] = 1
    for _ in range(6):
        disp = h.dial_shutter(-1)
    assert disp == (SS_BULB_NUM, 1), "Step 6 must be Manual BULB"
    assert h.press_shutter() is True, "Manual BULB press must pass through"
    assert h.read_state_struct()["running"] == 0, "Manual BULB must NOT start timer"
    assert h.release_shutter() is True, "Manual BULB release must pass through to close shutter!"
    print("  [PASS] 4. Manual BULB (past 120\") -> normal hold-to-expose / release-to-close")

    # 5. Early Cancel on 120" exposure
    h.reset_world()
    h.register_widgets()
    h.backup[BK_EXT_SHUTTER] = 1
    for _ in range(5):
        disp = h.dial_shutter(-1)
    assert disp == (120, 1)
    assert h.press_shutter() is True
    h.now_ms += 100
    assert h.release_shutter() is False

    cancel_t = [0]
    def on_cancel(har: ExtShutterHarness) -> None:
        if not cancel_t[0] and har.now_ms >= 30_000:
            cancel_t[0] = har.now_ms
            assert har.press_shutter() is False
            assert har.release_shutter() is False

    h.run_event_loop(max_time_ms=150_000, on_step=on_cancel)
    st = h.read_state_struct()
    assert st["running"] == 0 and st["abort_reason"] == 1
    cap_stops = [t for t, cmd, _, _ in h.direct_cmds if cmd == "CAP_STOP"]
    assert cap_stops == [cancel_t[0]]
    assert not h.live_recs and not h.live_stack_pls
    print(f"  [PASS] 5. Early cancel on 120\" exposure: immediate CAP_STOP @ {cap_stops[0]}ms")

    # 6. Combo: Interval Shooting + 45" Extended Shutter (3 consecutive 45s shots)
    h.reset_world()
    h.register_widgets()
    h.backup[BK_EXT_SHUTTER] = 1
    h.backup[BK_ENABLE] = 1
    h.backup[BK_INTERVAL] = 3  # 5s
    h.backup[BK_AF_MODE] = 0   # Lock AF+AE
    assert h.dial_shutter(-1) == (45, 1)
    assert h.press_shutter() is True
    h.now_ms += 100
    assert h.release_shutter() is False

    stopped = [False]
    def on_combo(har: ExtShutterHarness) -> None:
        if not stopped[0] and har.read_state_struct()["shots_taken"] >= 3:
            stopped[0] = True
            har.press_shutter()

    h.run_event_loop(max_time_ms=250_000, on_step=on_combo)
    st = h.read_state_struct()
    assert st["running"] == 0 and st["shots_taken"] == 3
    cap_stops = [t for t, cmd, _, _ in h.direct_cmds if cmd == "CAP_STOP"]
    assert len(cap_stops) == 3, f"Expected 3 timed 45s Bulb stops, got {cap_stops}"
    assert not h.live_recs and not h.live_stack_pls
    print(f"  [PASS] 6. Combo Interval + 45\" Extended Shutter (3 shots): stops={cap_stops}")

    # 7. Menu Toggle OFF (PID 0xEC) resets ext_ss_idx -> 0, notifies UI, restores SPINAL_REFLEX
    h.reset_world()
    h.backup[BK_EXT_SHUTTER] = 1
    assert h.dial_shutter(-1) == (45, 1)
    h._call("pdm_setValue_hook", SCRATCH_ADDR, PID_EXT_SHUTTER, 0)
    assert h.read_state_struct()["ext_ss_idx"] == 0
    assert h.query_displayed_shutter() == (SS_BULB_NUM, 1), "Turning toggle OFF must immediately revert display to BULB"
    assert h.events[-1][1:] == ("@M00F", EV_SET_SPINAL, [(0x109, 1)])
    print("  [PASS] 7. Menu Toggle OFF (PID 0xEC) resets virtual step, notifies UI, restores SPINAL_REFLEX")

    # 8. Three-feature coexistence: Extended Shutter + Auto ISO Min. SS boot sync + Interval SPINAL_REFLEX interplay
    h.reset_world()
    h.backup[BK_EXT_SHUTTER] = 1
    h.backup[BK_ENABLE] = 0
    h.backup[BK_MIN_SS] = 10  # 1/250s -> 0x80011800
    h._call("spinal_cmd_hook", MODEL_ADDR, 1)
    # SPINAL_REFLEX must be forced to 0 (because Extended Shutter is ON), AND Auto ISO Min. SS MUST STILL sync!
    assert h.spinal_calls == [(MODEL_ADDR, 0)], f"Expected spinal=0 when ExtShutter ON: {h.spinal_calls}"
    minss_evs = [e for e in h.events if e[1] == "@M00B" and e[2] == EV_SET_MIN_SS]
    assert len(minss_evs) == 1 and minss_evs[0][3] == [(PRM_KEY_MIN_SS, 0x80011800)], f"Boot sync failed: {minss_evs}"

    # Turning Interval OFF while Extended Shutter is ON must keep SPINAL_REFLEX = 0!
    h.events.clear()
    h.backup[BK_ENABLE] = 1
    h._call("pdm_setValue_hook", SCRATCH_ADDR, PID_ENABLE, 0)
    spinal_evs = [e for e in h.events if e[1] == "@M00F" and e[2] == EV_SET_SPINAL]
    assert spinal_evs and spinal_evs[-1][3] == [(0x109, 0)], f"SPINAL wrongly re-enabled while ExtShutter ON: {spinal_evs}"

    # Byte-truncated unsigned 0xFF (-1 as int8) must ALSO work in ext_ss_on_shift!
    h.set_hw_shutter(30, 1)
    assert h.dial_shutter(0xFF) == (45, 1), "Byte-truncated 0xFF (-1) must step 30\" -> 45\"!"
    print("  [PASS] 8. 3-feature coexistence: ExtShutter + Min. SS boot sync + Interval SPINAL interplay + int8 delta")

    # 9. Real-Hardware Bug 2 Regression: Live View fast_disp Slot 1 + Event 0x18000001 + 300/10 & async BIONZ race!
    h.reset_world()
    h.backup[BK_EXT_SHUTTER] = 1
    h.set_hw_shutter(300, 10)  # Sony den=10 encoding for 30.0"!
    h.async_bionz_delay = True
    # Spin slower from 300/10 while BIONZ has NOT yet replied 0xFFFF!
    assert h.dial_shutter(-1) == (300, 10), "While awaiting BIONZ, info35 should still report 300/10 without clearing idx"
    assert h.read_state_struct()["ext_ss_idx"] == 1 and h.read_state_struct()["ext_ss_entering"] == 1
    # Fast-disp polls 300/10 at +200ms -> MUST NOT clear ext_ss_idx during 1500ms grace!
    h.now_ms += 200
    assert h.query_fast_disp(1, 300, 10, valid=1) == (300, 10)
    assert h.read_state_struct()["ext_ss_idx"] == 1, "Async 30\" poll wrongly wiped ext_ss_idx!"
    # Fast double-spin during async grace (30" -> 45" -> 60") before BIONZ replies!
    h._call("ext_ss_shift_trampoline", CAM_ADDR, -1)
    assert h.read_state_struct()["ext_ss_idx"] == 2
    assert len(h.shift_passthroughs) == 1, "Second spin during async grace must NOT duplicate BIONZ shift"
    # BIONZ finally delivers BULB (0xFFFF)! Both fast_disp Slot 1 & info35 MUST show 60/1 and clear entering!
    h.set_hw_shutter(SS_BULB_NUM, 1)
    assert h.query_fast_disp(1, SS_BULB_NUM, 1, valid=1) == (60, 1)
    assert h.query_displayed_shutter() == (60, 1)
    assert h.read_state_struct()["ext_ss_entering"] == 0
    # Non-slot-1 (e.g. slot 0) and invalid slot 1 (valid=0) must stay untouched!
    assert h.query_fast_disp(0, SS_BULB_NUM, 1, valid=1) == (SS_BULB_NUM, 1)
    assert h.query_fast_disp(1, SS_BULB_NUM, 1, valid=0) == (SS_BULB_NUM, 1)
    # Spin to 80" -> verify Event 0x18000001 posted to UI dispatcher!
    h.ui_events.clear()
    h.async_bionz_delay = False
    assert h.dial_shutter(-1) == (80, 1)
    assert h.query_fast_disp(1, SS_BULB_NUM, 1, valid=1) == (80, 1)
    assert h.ui_events == [(UI_DISP_ADDR, EV_FAST_DISP_SHUTTER, 4, 0)], f"Missing 0x18000001 redraw: {h.ui_events}"
    # S-mode safety: if BIONZ never enters BULB (stays 30/1) past 1500ms grace, ext_ss_idx clears cleanly!
    h.reset_world()
    h.backup[BK_EXT_SHUTTER] = 1
    h.set_hw_shutter(30, 1)
    h.async_bionz_delay = True
    h._call("ext_ss_shift_trampoline", CAM_ADDR, -1)
    assert h.read_state_struct()["ext_ss_idx"] == 1
    h.now_ms += 1600
    assert h.query_fast_disp(1, 30, 1, valid=1) == (30, 1)
    assert h.read_state_struct()["ext_ss_idx"] == 0 and h.read_state_struct()["ext_ss_entering"] == 0
    print("  [PASS] 9. Bug 2 regression: fast_disp Slot 1 + Event 0x18000001 + 300/10 + async race & S-mode expiry")

    print("\nALL 9 UNICORN UNIFIED EXTENDED SHUTTER TESTS PASSED (100% PIC +0x40000000, 0 leaks)!")


if __name__ == "__main__":
    run_all_tests()
