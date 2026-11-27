"""End-to-end Unicorn ARMv7-A Thumb-2 emulation test suite for Auto ISO Min. SS (CFW v3.30).

Executes the REAL patched Sony ILCE-7R `av-cam.bin` machine code AND the REAL Zig-compiled
PIC `src/interval_engine.c` binary (`+0x40000000` load bias) in Unicorn:
  1. Real `av-cam.bin` LIRO `0x40CE` handler (`0x43781c00`) -> `[LiroParam + 0x204]`
  2. Real `av-cam.bin` Patch #1 ShutLowLimit consumer (`0x437a762e..0x437a7670`) ->
     proves `[CamState + 0x43C]` stores full 32-bit payload & `[CamState + 0x440] == 0x0AAA`
     for custom `0x80xxxxxx` while keeping `0`, `-1`, and positive microseconds 100% stock!
  3. Real `av-cam.bin` Patch #2 `CalcHandShakeTv` (`0x438abba8`) vs unmodified stock `av-cam.bin`:
     - Bit-for-bit stock equivalence matrix across all modes, focal lengths, OSS flags, and
       stock `ShutLowLimit` values (`0`, `-1`, positive us)
     - Mode isolation: S / M / iAuto / Superior Auto / Scene ignore custom setting 100%
     - P & A modes: all 5 Auto relative levels (`-2 .. +2 EV` around 1/f across 16mm..1000mm
       plus manual lens `0mm -> 60mm = 1/60s` base)
     - P & A modes: all 19 full-stop fixed speeds (`1/8000s .. 30"`)
  4. Real `av-cam.bin` Still AE Program Line Builder (`BuildStillProgramLine_Default @ 0x438ac330`):
     - Proves ISO ramps smoothly at `Tv_handover` (`1/250s` or `1/15s`), AND once ISO hits
       `ISO_MAX`, shutter drops down to `30"` (`0x0B00`) with ZERO underexposure!
     - Proves Flash ON (`[1/60s, 1/160s]`) safely clamps `Tv_handover` within sync bounds.
  5. Real compiled `interval_engine.c` (`+0x40000000` PIC bias) -> `av-cam.bin` loop:
     - All 24 `PID_MIN_SS (0xEB)` menu/Fn changes emit exact `@M00B` Event `0x7E` key `0x39F`
       matching `menu_spec.MIN_SS_SPEC`, fed directly into Unicorn `av-cam.bin`!
     - Cold boot / standby wakeup (`spinal_cmd_hook`, both Interval Off & On) + Factory Reset
       default (`BK_MIN_SS == 0 -> Standard`) + `ACT_DriveMode` one-shot belt-and-suspenders sync.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_R4,
    UC_ARM_REG_R5,
    UC_ARM_REG_SP,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import menu_spec as MS  # noqa: E402
import patch_avcam as PA  # noqa: E402
import patch_libobj as PL  # noqa: E402
import test_interval_engine as TIE  # noqa: E402

AVCAM_PATH = (
    ROOT
    / "firmware/unpacked/fw320/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked_unpacked/av-cam.bin"
)

# Synthetic RAM regions outside av-cam.bin VA space (0x43541000..0x43f72444)
EMU_STACK = 0x50000000
EMU_RAM   = 0x51000000
EMU_RET   = 0x52000000
CAM_STATE = EMU_RAM + 0x0000
LIRO_PKT  = EMU_RAM + 0x2000
SYNC_CTX  = EMU_RAM + 0x3000  # r5 struct: [r5+0]=LiroParam*
BOUNDS_LO = EMU_RAM + 0x4000  # (Av_min, ND_min, Tv_min, Sv_min=ISO_MAX)
BOUNDS_HI = EMU_RAM + 0x4020  # (Av_max, ND_max, Tv_max, Sv_max=ISO_MIN)
PROG_LINE = EMU_RAM + 0x4100  # output program line struct

LIRO_PARAM_VA = 0x43E1041C  # real global LiroParam struct in av-cam.bin (.bss/data)


class AvCamHarness:
    """Runs REAL Sony ILCE-7R `av-cam.bin` ARMv7-A Thumb-2 code in Unicorn."""

    def __init__(self, avcam_bytes: bytes) -> None:
        self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        # Map 16 MiB covering 0x43540000..0x44540000 (code + rodata + got + bss)
        self.mu.mem_map(0x43540000, 0x01000000)
        self.mu.mem_write(PA.AVCAM_BASE_VA, avcam_bytes)
        self.mu.mem_map(EMU_STACK, 0x10000)
        self.mu.mem_map(EMU_RAM, 0x10000)
        self.mu.mem_map(EMU_RET, 0x1000)

    def _reset_regs(self) -> None:
        self.mu.reg_write(UC_ARM_REG_SP, EMU_STACK + 0xE000)
        self.mu.reg_write(UC_ARM_REG_LR, EMU_RET | 1)

    def run_liro_40ce(self, raw_u32: int) -> int:
        """Execute REAL av-cam.bin LIRO 0x40CE handler @ 0x43781c00."""
        self.mu.mem_write(LIRO_PKT, bytes(16))
        pkt = bytearray(16)
        pkt[0] = 5
        pkt[2] = 0xCE
        pkt[3] = 0x40
        struct.pack_into("<I", pkt, 8, raw_u32 & 0xFFFFFFFF)
        self.mu.mem_write(LIRO_PKT, bytes(pkt))
        self._reset_regs()
        self.mu.reg_write(UC_ARM_REG_R0, LIRO_PKT)
        self.mu.emu_start(0x43781C00 | 1, EMU_RET, count=100)
        return struct.unpack("<I", self.mu.mem_read(LIRO_PARAM_VA + 0x204, 4))[0]

    def run_shutlow_consumer(self, raw_u32: int) -> tuple[int, int]:
        """Execute REAL av-cam.bin ShutLowLimit consumer snippet @ 0x437a762e..0x437a7670."""
        self.mu.mem_write(LIRO_PARAM_VA + 0x204, struct.pack("<I", raw_u32 & 0xFFFFFFFF))
        self.mu.mem_write(LIRO_PARAM_VA + 0x324, struct.pack("<I", 0))
        self.mu.mem_write(SYNC_CTX, struct.pack("<I", LIRO_PARAM_VA))
        self.mu.mem_write(CAM_STATE, bytes(0x900))
        self._reset_regs()
        self.mu.reg_write(UC_ARM_REG_R4, CAM_STATE)
        self.mu.reg_write(UC_ARM_REG_R5, SYNC_CTX)
        self.mu.emu_start(0x437A762E | 1, 0x437A7670, count=500)
        stored_raw = struct.unpack("<I", self.mu.mem_read(CAM_STATE + 0x43C, 4))[0]
        stored_tv = struct.unpack("<H", self.mu.mem_read(CAM_STATE + 0x440, 2))[0]
        return stored_raw, stored_tv

    def setup_cam_state(
        self,
        *,
        mode: int,
        fl_mm: int,
        raw_shutlow: int,
        oss_state: int = 0,
        flag_100: int = 0,
        mode8_flag: int = 0,
        sub_mode: int = 0,
    ) -> None:
        self.mu.mem_write(CAM_STATE, bytes(0x900))
        self.mu.mem_write(CAM_STATE + 0x2B4, struct.pack("<I", mode))
        self.mu.mem_write(CAM_STATE + 0x668, struct.pack("<I", sub_mode))
        self.mu.mem_write(CAM_STATE + 0x43C, struct.pack("<I", raw_shutlow & 0xFFFFFFFF))
        self.mu.mem_write(CAM_STATE + 0x51C, struct.pack("<H", fl_mm))
        self.mu.mem_write(CAM_STATE + 0x63C, struct.pack("<I", oss_state))
        self.mu.mem_write(CAM_STATE + 0x69F, bytes([mode8_flag]))
        self.mu.mem_write(PA.STATIC_FLAG_VA, struct.pack("<H", flag_100))

    def call_calc_handshake_tv(self, **kwargs) -> int:
        self.setup_cam_state(**kwargs)
        self._reset_regs()
        self.mu.reg_write(UC_ARM_REG_R0, CAM_STATE)
        self.mu.emu_start(PA.SITE2_VA | 1, EMU_RET, count=1000)
        return self.mu.reg_read(UC_ARM_REG_R0) & 0xFFFF

    def call_build_still_program_line_default(
        self,
        *,
        mode: int,
        fl_mm: int,
        raw_shutlow: int,
        av_min: int = 0x1300,   # F2.8
        av_max: int = 0x1300,   # F2.8 (A mode fixed aperture)
        tv_min: int = 0x0B00,   # 30"
        tv_max: int = 0x1D00,   # 1/8000s
        sv_min: int = 0x0A00,   # ISO 6400 (remember Sony APEX Sv: 0x1000=ISO100, -0x100=+1EV ISO)
        sv_max: int = 0x1000,   # ISO 100
    ) -> list[tuple[int, int, int, int]]:
        """Execute REAL Sony `BuildStillProgramLine_Default` (0x438ac330) and return vertices (Av, Tv, Sv, Bv)."""
        self.setup_cam_state(mode=mode, fl_mm=fl_mm, raw_shutlow=raw_shutlow)
        # [CamState + 0x4de] = Sv_handover (0x1000 = ISO 100)
        self.mu.mem_write(CAM_STATE + 0x4DE, struct.pack("<H", sv_max))
        # Bounds structs: (Av, ND, Tv, Sv)
        self.mu.mem_write(BOUNDS_LO, struct.pack("<4H", av_min, 0x1000, tv_min, sv_min))
        self.mu.mem_write(BOUNDS_HI, struct.pack("<4H", av_max, 0x1000, tv_max, sv_max))
        self.mu.mem_write(PROG_LINE, bytes(0x200))

        self._reset_regs()
        # 5th argument `PROG_LINE` passed at [sp + 0] (read at 0x438ac350 after pushing 10 regs = 40B -> [sp+0x28])
        sp = EMU_STACK + 0xE000
        self.mu.mem_write(sp, struct.pack("<I", PROG_LINE))
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        self.mu.reg_write(UC_ARM_REG_R0, 0)
        self.mu.reg_write(UC_ARM_REG_R1, CAM_STATE)
        self.mu.reg_write(UC_ARM_REG_R2, BOUNDS_LO)
        self.mu.reg_write(UC_ARM_REG_R3, BOUNDS_HI)
        self.mu.emu_start(0x438AC330 | 1, EMU_RET, count=2000)

        n_pts = self.mu.mem_read(PROG_LINE, 1)[0]
        assert 2 <= n_pts <= 16, f"Unexpected vertex count: {n_pts}"
        verts = []
        for i in range(n_pts):
            av = struct.unpack("<H", self.mu.mem_read(PROG_LINE + 0x02 + 2 * i, 2))[0]
            tv = struct.unpack("<H", self.mu.mem_read(PROG_LINE + 0x52 + 2 * i, 2))[0]
            sv = struct.unpack("<H", self.mu.mem_read(PROG_LINE + 0x7A + 2 * i, 2))[0]
            bv = struct.unpack("<H", self.mu.mem_read(PROG_LINE + 0xA2 + 2 * i, 2))[0]
            verts.append((av, tv, sv, bv))
        return verts


def run_all_tests() -> None:
    stock_av = AVCAM_PATH.read_bytes()
    patched_av, av_rep = PA.patch_avcam_bytes(stock_av)
    h_stock = AvCamHarness(stock_av)
    h_patch = AvCamHarness(patched_av)
    print(f"Loaded stock & patched av-cam.bin ({av_rep['total_changed_bytes']} B changed)")

    # --- Test 1: LIRO 0x40CE handler + Patch #1 ShutLowLimit consumer ---
    for payload in (0, 0xFFFFFFFF, 1_000_000, 16_667, 0x80000000, 0x8000FE00, 0x80000200, 0x80011800, 0x80010B00):
        got_liro = h_patch.run_liro_40ce(payload)
        assert got_liro == payload, f"LIRO 0x40CE round-trip mismatch for 0x{payload:08x}"

    # Verify Patch #1 vs stock for all non-custom values AND custom values!
    for stock_val in (0, 0xFFFFFFFF, 1_000_000, 16_667, 500_000, 1000):
        s_raw, s_tv = h_stock.run_shutlow_consumer(stock_val)
        p_raw, p_tv = h_patch.run_shutlow_consumer(stock_val)
        assert (p_raw, p_tv) == (s_raw, s_tv), (
            f"Patch #1 stock mismatch for 0x{stock_val:x}: patched={(hex(p_raw), hex(p_tv))} vs stock={(hex(s_raw), hex(s_tv))}"
        )
    # 1,000,000 us -> 1.0s -> 0x1000 APEX
    _, tv_1s = h_patch.run_shutlow_consumer(1_000_000)
    assert tv_1s == 0x1000, f"Expected 1s -> 0x1000, got 0x{tv_1s:04x}"

    for custom_val in (0x80000000, 0x8000FE00, 0x80000200, 0x80011D00, 0x80010B00):
        p_raw, p_tv = h_patch.run_shutlow_consumer(custom_val)
        assert p_raw == custom_val, f"[CamState+0x43C] corrupted: 0x{p_raw:08x} != 0x{custom_val:08x}"
        assert p_tv == 0x0AAA, f"Expected [CamState+0x440]==0x0AAA (no hard Tv_min clamp), got 0x{p_tv:04x}"
    print("[PASS] Test 1: LIRO 0x40CE + Patch #1 ShutLowLimit consumer (100% stock compat + raw pass-through)")

    # --- Test 2: Exhaustive Stock Equivalence Matrix (patched == stock bit-for-bit!) ---
    eq_count = 0
    for mode in (0, 1, 2, 3, 4, 5, 7, 8, 0x1D, 0x2C):
        for fl in (0, 16, 24, 30, 50, 85, 200, 600, 1000):
            for oss in (0, 1):
                for f100 in (0, 1):
                    for m8 in (0, 1):
                        if mode != 8 and m8 == 1:
                            continue
                        if mode not in (4, 5, 7, 0x2C) and (oss == 1 or f100 == 1):
                            continue
                        for sl in (0, 0xFFFFFFFF, 16_667):
                            s_tv = h_stock.call_calc_handshake_tv(
                                mode=mode, fl_mm=fl, raw_shutlow=sl, oss_state=oss, flag_100=f100, mode8_flag=m8
                            )
                            p_tv = h_patch.call_calc_handshake_tv(
                                mode=mode, fl_mm=fl, raw_shutlow=sl, oss_state=oss, flag_100=f100, mode8_flag=m8
                            )
                            assert p_tv == s_tv, (
                                f"Stock mismatch! mode={mode} fl={fl} oss={oss} f100={f100} m8={m8} sl=0x{sl:x}: "
                                f"patched=0x{p_tv:04x} vs stock=0x{s_tv:04x}"
                            )
                            eq_count += 1
    print(f"[PASS] Test 2: Exhaustive stock equivalence matrix ({eq_count} cases 100% bit-for-bit identical)")

    # --- Test 3: Mode Isolation (S, M, iAuto, Superior Auto, Scene ignore custom setting!) ---
    for mode in (2, 3, 4, 5, 7, 8, 0x1D, 0x2C):
        for fl in (24, 50, 200):
            s_tv = h_stock.call_calc_handshake_tv(mode=mode, fl_mm=fl, raw_shutlow=0, oss_state=0, flag_100=1)
            for custom in (PA.MIN_SS_AUTO_SLOWEST, PA.MIN_SS_AUTO_FASTEST, 0x80011D00, 0x80010B00):
                p_tv = h_patch.call_calc_handshake_tv(mode=mode, fl_mm=fl, raw_shutlow=custom, oss_state=0, flag_100=1)
                assert p_tv == s_tv, (
                    f"Mode {mode} leaked custom 0x{custom:08x}: got 0x{p_tv:04x}, want stock 0x{s_tv:04x}"
                )
    print("[PASS] Test 3: Non-P/A modes (S, M, iAuto, Superior Auto, Scene) 100% isolated from custom setting")

    # --- Test 4: P & A Modes — 5 Auto Relative Levels across 16mm..1000mm + Manual 0mm ---
    expected_std_by_fl = {
        0:    0x1600,  # Manual lens -> 60mm -> 1/60s (Sony A7R2/A7M3 standard!)
        15:   0x1400,  # 15mm -> 1/16s
        20:   0x1455,  # 20mm -> 1/20s
        25:   0x14AA,  # 25mm -> 1/25s
        30:   0x1500,  # 30mm -> 1/32s
        50:   0x15AA,  # 50mm -> 1/50s
        60:   0x1600,  # 60mm -> 1/64s
        100:  0x16AA,  # 100mm -> 1/100s
        200:  0x17AA,  # 200mm -> 1/200s
        500:  0x1900,  # 500mm -> 1/512s
        1000: 0x1A00,  # 1000mm -> 1/1024s
    }
    auto_shifts = [
        (PA.MIN_SS_AUTO_SLOWEST,  -0x200),
        (PA.MIN_SS_AUTO_SLOWER,   -0x100),
        (PA.MIN_SS_AUTO_STANDARD,  0x000),
        (PA.MIN_SS_AUTO_FASTER,   +0x100),
        (PA.MIN_SS_AUTO_FASTEST,  +0x200),
    ]
    for mode in (0, 1):  # P and A modes
        for fl, base_tv in expected_std_by_fl.items():
            for raw_code, shift in auto_shifts:
                got = h_patch.call_calc_handshake_tv(mode=mode, fl_mm=fl, raw_shutlow=raw_code)
                want = base_tv + shift
                assert got == want, (
                    f"Auto-rel mismatch! mode={mode} fl={fl}mm code=0x{raw_code:08x}: "
                    f"got 0x{got:04x}, want 0x{want:04x}"
                )
    print("[PASS] Test 4: P & A modes 5 Auto relative levels (-2..+2 EV x 1/f, 0mm..1000mm) verified exact")

    # --- Test 5: P & A Modes — All 19 Fixed Full-Stop Shutter Speeds (1/8000s .. 30") ---
    for mode in (0, 1):
        for opt_key, raw_code in MS.MIN_SS_SPEC.options[5:]:
            want_tv = raw_code & 0xFFFF
            for fl in (0, 24, 50, 300):
                got = h_patch.call_calc_handshake_tv(mode=mode, fl_mm=fl, raw_shutlow=raw_code)
                assert got == want_tv, (
                    f"Fixed mismatch! mode={mode} {opt_key} fl={fl}mm: got 0x{got:04x}, want 0x{want_tv:04x}"
                )
    print("[PASS] Test 5: P & A modes all 19 Fixed Shutter Speeds (1/8000s .. 30\") verified exact")

    # --- Test 6: FULL Sony Still AE Program Line Builder (`BuildStillProgramLine_Default @ 0x438ac330`) ---
    # Case 6a: A mode (F2.8, ISO 100..6400), Min. SS = 1/250s (0x1800) with 50mm lens!
    verts_250 = h_patch.call_build_still_program_line_default(
        mode=1, fl_mm=50, raw_shutlow=0x80011800,
        av_min=0x1300, av_max=0x1300, tv_min=0x0B00, tv_max=0x1D00, sv_min=0x0A00, sv_max=0x1000,
    )
    # Vertex 0 (darkest): Tv MUST be 30" (0x0B00) at ISO 6400 (0x0A00) -> ZERO UNDEREXPOSURE!
    assert verts_250[0][1] == 0x0B00 and verts_250[0][2] == 0x0A00, f"Vertex 0 wrong: {verts_250[0]}"
    # ISO handover vertices: Tv == 1/250s (0x1800) while Sv sweeps 0x0A00 (6400) -> 0x1000 (100)!
    handover_250 = [v for v in verts_250 if v[1] == 0x1800]
    assert any(v[2] == 0x0A00 for v in handover_250) and any(v[2] == 0x1000 for v in handover_250), (
        f"Expected ISO 6400->100 ramp pinned at 1/250s (0x1800), got {[(hex(a),hex(t),hex(s)) for a,t,s,_ in verts_250]}"
    )
    # Brightest vertex: Tv == 1/8000s (0x1D00) at ISO 100 (0x1000)!
    assert verts_250[-1][1] == 0x1D00 and verts_250[-1][2] == 0x1000, f"Last vertex wrong: {verts_250[-1]}"

    # Case 6b: A mode (F2.8, ISO 100..6400), Min. SS = 1/15s (0x1400, slower than stock 1/60s floor!)
    verts_15 = h_patch.call_build_still_program_line_default(
        mode=1, fl_mm=50, raw_shutlow=0x80011400,
        av_min=0x1300, av_max=0x1300, tv_min=0x0B00, tv_max=0x1D00, sv_min=0x0A00, sv_max=0x1000,
    )
    handover_15 = [v for v in verts_15 if v[1] == 0x1400]
    assert any(v[2] == 0x0A00 for v in handover_15) and any(v[2] == 0x1000 for v in handover_15), (
        f"Expected ISO 6400->100 ramp pinned at 1/15s (0x1400), got {[(hex(a),hex(t),hex(s)) for a,t,s,_ in verts_15]}"
    )
    assert verts_15[0][1] == 0x0B00, "Darkest vertex must still reach 30\" (0x0B00)"

    # Case 6c: Flash ON (Tv_min = 1/60s = 0x1600, Tv_max = 1/160s = 0x1755) clamps both 1/1000s & 1/4s safely!
    for raw_code, exp_clamped in ((0x80011A00, 0x1755), (0x80011200, 0x1600)):
        verts_fl = h_patch.call_build_still_program_line_default(
            mode=1, fl_mm=50, raw_shutlow=raw_code,
            av_min=0x1300, av_max=0x1300, tv_min=0x1600, tv_max=0x1755, sv_min=0x0A00, sv_max=0x1000,
        )
        assert all(0x1600 <= v[1] <= 0x1755 for v in verts_fl), f"Flash sync violated: {verts_fl}"
        assert any(v[1] == exp_clamped and v[2] == 0x1000 for v in verts_fl), (
            f"Flash handover mismatch for 0x{raw_code:08x}: {[(hex(t), hex(s)) for _,t,s,_ in verts_fl]}"
        )
    print("[PASS] Test 6: REAL BIONZ BuildStillProgramLine_Default (1/250s, 1/15s, 30\" zero-underexposure, Flash sync)")

    # --- Test 7: Compiled PIC `interval_engine.c` (+0x40000000 bias) -> REAL `av-cam.bin` Loop! ---
    zig_exe = PL.find_zig(ROOT)
    code_bytes, syms = PL.compile_engine(ROOT / "src/interval_engine.c", zig_exe, TIE.LINK_CODE_VA, TIE.LINK_STATE_VA)
    eh = TIE.IntervalHarness(code_bytes, syms)
    eh.backup[MS.BK_MIN_SS] = 0  # Fresh / Factory Reset default!

    # 7a. Cold boot spinal_cmd_hook(model, 1) -> syncs Factory Reset default (bk=0 -> Standard 0x80000000)!
    eh.events.clear()
    eh.call_sym("spinal_cmd_hook", TIE.MODEL_ADDR, 1)
    minss_ev = [e for e in eh.events if e[1] == "@M00B" and e[2] == 0x7E]
    assert len(minss_ev) == 1 and minss_ev[0][3] == [(0x39F, 0x80000000)], f"Boot sync wrong: {minss_ev}"

    # 7b. Cold boot when Interval Shoot is ALSO Enabled (bk_enable=1) -> MUST STILL sync Auto ISO Min. SS!
    eh.reset_world()
    eh.backup[TIE.BK_ENABLE] = 1
    eh.backup[MS.BK_MIN_SS] = 10  # 1/250s -> 0x80011800
    eh.call_sym("spinal_cmd_hook", TIE.MODEL_ADDR, 1)
    minss_ev = [e for e in eh.events if e[1] == "@M00B" and e[2] == 0x7E]
    assert len(minss_ev) == 1 and minss_ev[0][3] == [(0x39F, 0x80011800)], (
        f"Boot sync skipped when Interval On! {minss_ev}"
    )

    # 7c. ACT_DriveMode belt-and-suspenders one-shot sync on initial Live View!
    eh.reset_world()
    eh.backup[MS.BK_MIN_SS] = 4  # Fastest (+2 EV) -> 0x80000200
    eh.call_sym("interval_act_drivemode_icon", 0x0205C10B, 0, TIE.ACT_OBJ_ADDR)
    eh.call_sym("interval_act_drivemode_icon", 0x0205C10B, 0, TIE.ACT_OBJ_ADDR)  # 2nd call must NOT spam!
    minss_ev = [e for e in eh.events if e[1] == "@M00B" and e[2] == 0x7E]
    assert len(minss_ev) == 1 and minss_ev[0][3] == [(0x39F, 0x80000200)], f"ACT_DriveMode sync wrong: {minss_ev}"

    # 7d. All 24 Menu / Fn selections via `pdm_setValue_hook(PID_MIN_SS, 0..23)` fed live into Unicorn av-cam.bin!
    for opt_idx, (opt_key, want_liro) in enumerate(MS.MIN_SS_SPEC.options):
        eh.events.clear()
        rc = eh.call_sym("pdm_setValue_hook", TIE.SCRATCH_ADDR, MS.PID_MIN_SS, opt_idx)
        assert rc == 1, f"pdm_setValue_hook failed for opt {opt_idx}"
        minss_ev = [e for e in eh.events if e[1] == "@M00B" and e[2] == 0x7E]
        assert len(minss_ev) == 1, f"Missing @M00B 0x7E event for opt {opt_idx}: {eh.events}"
        key, emitted_u32 = minss_ev[0][3][0]
        assert key == 0x39F and (emitted_u32 & 0xFFFFFFFF) == want_liro, (
            f"opt {opt_idx} ({opt_key}): emitted 0x{emitted_u32 & 0xFFFFFFFF:08x}, want 0x{want_liro:08x}"
        )
        # Feed emitted Linux IPC payload directly through REAL av-cam.bin LIRO -> Consumer -> CalcHandShakeTv!
        liro_val = h_patch.run_liro_40ce(emitted_u32)
        c_raw, c_tv = h_patch.run_shutlow_consumer(liro_val)
        assert c_raw == want_liro and c_tv == 0x0AAA
        tv_out = h_patch.call_calc_handshake_tv(mode=1, fl_mm=50, raw_shutlow=c_raw)
        if opt_idx < 5:
            shift = struct.unpack("<h", struct.pack("<H", want_liro & 0xFFFF))[0]
            assert tv_out == 0x15AA + shift, f"{opt_key} on 50mm: got 0x{tv_out:04x}, want 0x{0x15AA + shift:04x}"
        else:
            assert tv_out == (want_liro & 0xFFFF), f"{opt_key}: got 0x{tv_out:04x}, want 0x{want_liro & 0xFFFF:04x}"
        # Also verify BK_MIN_SS cold-boot path reproduces the EXACT same payload!
        bk_byte = MS.MIN_SS_SPEC.backup_bytes[opt_idx]
        eh.reset_world()
        eh.backup[MS.BK_MIN_SS] = bk_byte
        eh.call_sym("spinal_cmd_hook", TIE.MODEL_ADDR, 1)
        boot_ev = [e for e in eh.events if e[1] == "@M00B" and e[2] == 0x7E]
        assert len(boot_ev) == 1 and (boot_ev[0][3][0][1] & 0xFFFFFFFF) == want_liro, (
            f"Boot sync mismatch for opt {opt_idx} (bk={bk_byte}): {boot_ev}"
        )

    print("[PASS] Test 7: Full Linux PIC (+0x40000000) -> @M00B 0x7E -> LIRO 0x40CE -> BIONZ AE loop (all 24 options!)")
    print("\nALL 7 AUTO ISO MIN. SS END-TO-END UNICORN ARMv7-A THUMB-2 TESTS PASSED!")


if __name__ == "__main__":
    run_all_tests()
