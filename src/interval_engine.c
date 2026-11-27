/* Sony ILCE-7R (A7R1) v3.20 — Unified Native CFW Engine (Thumb-2 PIC, Release v3.33)
 *
 * Integrates all three production features into `/usr/lib/libObj.so` (single RWX PT_LOAD):
 *   1. Native Interval Shooting (real-hardware verified v3.28/v3.29 + v3.32 Fn fix):
 *      - Zero NAND debug I/O, Main-EventLoop TimerManager, 3-mode AF/AE (incl. Holy Grail),
 *        true hardware Monitor Sleep (`Backup.bin 0x01070121`, 0-ref) + 5s S1 peek, 4s @M058 APO
 *        keep-alive, Option A untouched stock Drive Mode icons (`0x0205D3E9` LCD / `0x020E8787` EVF),
 *        Live View blinking icon & remaining shots countdown, and single-slot Fn Menu shortcut
 *        (`PID 0xEA` -> `ViewSettingMenu` Layout 5 via Action 7 + Action 0x38).
 *   2. Hardware 60fps Auto ISO Min. Shutter Speed (v3.30, real-hardware verified):
 *      - Persisted in `Backup.bin 0x010700D0` (`PID 0xEB`, 24 options, Tab 2 P3 + Fn slot 29),
 *        synced to BIONZ `av-cam.bin` (`LIRO 0x40CE`) via `@M00B Event 0x7E (key 0x39F)` on
 *        cold boot, standby wakeup, Still mode entry, Live View init, and instant menu/Fn edits.
 *   3. Native Extended Shutter Speed & Timed BULB (v3.32 Live View fast_disp + race fix):
 *      - Persisted in `Backup.bin 0x01070147` (`PID 0xEC`, Off/On toggle on Tab 2 P5 Item 5).
 *      - Off: 100% stock `30" <-> BULB`.
 *      - On: M-mode dial extends `30" <-> 45" <-> 60" <-> 80" <-> 100" <-> 120" <-> BULB`
 *        across BOTH `fast_disp_get` Slot 1 (Live View LCD/EVF + `Event 0x18000001`) AND
 *        `ModelCamera::getModelInfo(0x35)` (Quick Navi); single click on `45"..120"` runs
 *        hands-free timed Bulb exposure; `BULB` stays 100% normal manual press-and-hold!
 *
 * Compiled with:
 *   zig cc -target thumb-freestanding-eabi -mcpu=cortex_a5 -mthumb \
 *          -mfloat-abi=soft -Os -ffreestanding -fno-builtin \
 *          -fno-stack-protector -fropi -fno-exceptions \
 *          -fno-asynchronous-unwind-tables -nostdlib
 */

typedef unsigned int u32;
typedef int i32;
typedef unsigned long long u64;
typedef unsigned short u16;
typedef unsigned char u8;

/* Link-time symbols resolved relative to PC via --defsym in patch_libobj.py.
 * Thumb functions carry bit 0 = 1; ARM PLT stubs carry bit 0 = 0. */
extern const char INTERVAL_STATE[];
extern const char G_TIMER_MGR[];
extern const char G_TAB2_P3_NEW_ARR[];
extern const char V_CAPSTART_CONT[];
extern const char V_CAPSTOP_CONT[];
extern const char V_FOCSTART_CONT[];
extern const char V_SPINAL_CONT[];
extern const char V_SHIFT_SS_CONT[];
extern const char V_INFO35_NUM_CONT[];
extern const char V_INFO35_DEN_CONT[];
extern const char V_FAST_DISP_CONT[];
extern const char V_NOTIFY_UPDATE[];
extern const char V_GET_UI_DISP[];
extern const char V_POST_UI_EVENT[];
extern const char P_MEMCPY[];
extern const char P_EVENT_CTOR[];
extern const char V_PDM_SETVAL_CONT[];
extern const char V_PDM_GETVAL_CONT[];
extern const char V_PDM_INVALID_CONT[];
extern const char V_PDM_DISABLE_CONT[];
extern const char V_PDM_GRAY_CONT[];
extern const char V_PDM_CAUTION_CONT[];
extern const char V_ACT_DM_CONT[];
extern const char V_REMAIN_A_CONT[];
extern const char V_REMAIN_B_CONT[];
extern const char V_REMAIN_DTOR_CONT[];
extern const char V_VSM_ONTRANS_CONT[];
extern const char V_VSM_INITNODE_CONT[];
extern const char V_VSM_SELECT_NODE[];
extern const char V_VSM_SYNC_INDICES[];
extern const char V_VSM_GET_PAGE[];
extern const char V_VSM_SET_PAGE_TAB[];
extern const char V_VSM_ACTION7[];
extern const char P_VIEW_GET_EVID[];
extern const char V_PRM_CTOR[];
extern const char V_PRM_GETNUM[];
extern const char V_PL_GET[];
extern const char V_TIME_NOW[];
extern const char V_TIME_ADD_MS[];
extern const char V_TMR_ADD[];
extern const char V_TMR_REMOVE[];
extern const char V_WAKEUP_LOOP[];
extern const char V_CAP_START_CMD[];
extern const char V_CAP_STOP_CMD[];
extern const char V_AF_START_CMD[];
extern const char V_AF_STOP_CMD[];
extern const char V_AFMF_ON_CMD[];
extern const char V_AFMF_OFF_CMD[];
extern const char V_GET_FOCUS_MODE[];
extern const char V_SET_DISP_STATE[];
extern const char V_REMAIN_UPDATE[];
extern const char P_REQ_EXEC[];
extern const char P_GET_MODEL_INFO[];
extern const char P_PL_CTOR[];
extern const char P_PL_DTOR[];
extern const char P_PL_ADD[];
extern const char P_NEW[];
extern const char P_BK_READ[];
extern const char P_BK_WRITE[];

/* Asm continuation wrappers for Sony's original methods */
extern i32 orig_pdm_setValue(void *mgr, u32 pid, u32 val);
extern i32 orig_pdm_getValue(void *mgr, u32 pid, u32 *out);
extern void orig_set_spinal_reflex(void *model, u32 enable);
extern void *orig_remain_dtor(void *self);

/* Opaque PC-relative address helper: forces indirect call via `blx rN` so
 * ARM PLT stubs (bit 0 = 0) and Thumb targets (bit 0 = 1) interwork cleanly. */
static inline const void *pic_ptr(const char *sym) {
    __asm__("" : "+r"(sym));
    return sym;
}

#define CALL(fn_type, sym) ((fn_type)pic_ptr(sym))

/* Backup.bin IDs (verified unused 1-byte writable slots in subsystem 0x0107) */
#define BK_ENABLE      0x010700F6u
#define BK_DELAY       0x010700F7u
#define BK_INTERVAL    0x0107003Cu
#define BK_SHOTS       0x0107003Fu
#define BK_AF_MODE     0x0107007Eu
#define BK_DISP_SLEEP  0x01070121u  /* attr=0x50, resetData=0, 0 refs in fw320 (migrated off 0x010700C2) */
#define BK_MIN_SS      0x010700D0u  /* attr=0x50, resetData=0 -> Auto ISO Min. SS (0=Standard) */
#define BK_EXT_SHUTTER 0x01070147u  /* attr=0x50, resetData=0 -> Extended Shutter & BULB (0=Off) */

/* Drive Mode & custom ProcessIds */
#define PID_DRIVEMODE   7u
#define PID_ENABLE      0xE4u
#define PID_FN_INTV     0xEAu
#define PID_MIN_SS      0xEBu
#define PID_EXT_SHUTTER 0xECu
#define DM_VAL_SINGLE   0u
#define DM_VAL_INTERVAL 0x24u

/* ModelCamera ("@M00B") hidden ShutterSpeedLowLimit IPC & Extended Shutter constants */
#define EV_SET_SHUT_LOW_LIMIT  0x7Eu
#define PRM_KEY_SHUT_LOW_LIMIT 0x39Fu
#define INFO_SHUTTER_SPEED     0x35u
#define EV_FAST_DISP_SHUTTER   0x18000001u
#define EXT_SS_ENTER_GRACE_MS  1500u
#define CAM_OFF_SS_NUM         0x200u
#define CAM_OFF_SS_DEN         0x204u
#define SS_BULB_NUM            0xFFFFu
/* Extended Shutter Speed virtual indices (when hardware is BULB 0xFFFF):
 * 1=45s, 2=60s, 3=80s, 4=100s, 5=120s, 6=Manual BULB */
#define EXT_SS_MAX_TIMED       5u
#define EXT_SS_MANUAL_BULB     6u

/* ViewSettingMenu direct-jump constants (byte-verified on ILCE-7R v3.20) */
#define VSM_BOOT_INTV          0x77u
#define VSM_BOOT_MIN_SS        0x78u
#define VSM_EV_BOOT            0x11012001u
#define VSM_LAYOUT_MAIN        1u
#define VSM_LAYOUT_FOLDER      5u
#define VSM_ACTION_BOOT_MAIN   7u
#define VSM_ACTION_FOLDER_OPEN 0x38u
#define VSM_OFF_LAYOUT         4u
#define VSM_OFF_SUBSTATE       0x120u
#define VSM_OFF_TAB            0x124u
#define VSM_OFF_POPUP_IDX      0x130u
#define VSM_OFF_BOOT_EL        0x13Cu
#define VSM_OFF_REOPEN_POPUP   0x160u

/* AF/AE tracking modes (matches menu_spec.py PID 0xE8) */
#define AF_MODE_LOCK_AFAE  0u  /* Lock AF + AE on 1st shot */
#define AF_MODE_LOCK_AF    1u  /* Lock AF only, Auto AE each shot (Holy Grail) */
#define AF_MODE_EACH       2u  /* Re-focus + re-meter every shot */

/* ModelStillRec ("@M00F") local event IDs & verified offsets */
#define EV_SET_SPINAL_REFLEX 0x1Au  /* 0x0071e0be -> pvt_ActionSetSpinalReflex */
#define PRM_KEY_SHUTTER      0x109u

#define MODEL_OFF_STATE      4u     /* ModelStillRec::getCurrentState @ 0x00491c9c */
#define MODEL_OFF_S1_MODE    0x19Cu /* SelfTimer/S1 status checked at 0x00723d66 */
#define MODEL_OFF_DEV_RDY1   0xB28u /* verified in pvt_ActionExeCaptureStart @ 0x00723d52 */
#define MODEL_OFF_DEV_RDY2   0xB29u /* verified in pvt_ActionExeCaptureStart @ 0x00723d5c */
#define MODEL_OFF_STS_CAP    0xD08u /* m_StsCapture (0 = idle, 1/2/3 = capturing/stopping) */

/* ModelCamera ("@M00B") AE Lock & Focus Mode constants (byte-verified on ILCE-7R v3.20)
 * pvt_ChangeAelParamApp2Obj @ 0x00568ed4:
 *   0 -> 0x2d6 (VALUEID_NORMAL_AELOCK_ON)
 *   1 -> 0x2d8 (VALUEID_AELOCK_OFF)
 * CmnViewModelWrpCameraFocus::getFocusMode @ 0x0086e3f5:
 *   1 -> MF (VALUEID_FOCUS_MANUAL), 2 -> AF (AF-S / AF-C / DMF / AF-A) */
#define EV_SET_AEL       0x39u
#define PRM_KEY_AEL      0x1E0u
#define AEL_VAL_ON       0
#define AEL_VAL_OFF      1
#define FOCUS_MODE_MF    1u

/* ModelDisplay ("@M006") true hardware Panel/EVF power switch (byte-verified @ 0x005c19e0) */
#define EV_PANEL_EVF_SWITCH  0x2Fu
#define INFO_PANEL_EVF_STS   0x20u
#define PRM_KEY_PANEL_EVF    0x1200600Bu
#define DISP_ALL_OFF         0
#define DISP_PANEL_ON        1
#define DISP_EVF_ON          2

/* ModelAutoPowerOff ("@M058") native Power Save timer reload (byte-verified @ 0x004a8598 -> 0x004a8cb0) */
#define EV_RELOAD_APO        5u
#define APO_KEEPALIVE_MS     4000u  /* Reload Power Save timer every 4s (safe even with 10s Power Save!) */
#define PEEK_DURATION_MS     5000u  /* S1 half-press temporarily wakes display for 5s during Monitor Sleep */

/* ACT_DriveMode LayoutableWidgetBase image member offset (verified @ 0x009651ea) */
#define ACT_DM_IMG_OFFSET    0x1ACu

#define STATE_READY_MIN  4u
#define STATE_READY_MAX  6u
#define POLL_SLICE_MS    500u       /* Max single timer slice so menu/playback abort reacts <= 0.5s */
#define BLINK_HALF_MS    450u       /* ~1 Hz Live View Drive Mode waiting icon heartbeat */
#define READY_POLL_MS    50u        /* Long exposure / BIONZ sc==2 poll cadence */
#define CAP_ACK_POLL_MS  50u        /* Wait for BIONZ 0x8503 (0xF000 -> m_StsCapture==2) before CaptureStop */
#define MAX_CAP_ACK_TICKS   60u     /* 3000ms safety timeout if BIONZ never leaves sc==1 (e.g. AF fail) */
#define MAX_BUSY_TICKS      12000u  /* 600s safety timeout for single exposure + LENR + write */
#define BULB_EARLY_GUARD_MS 3000u   /* Ignore transient sc==0 for first 3s after BULB open */
#define MAGIC_INTV          0x494E5456u

enum interval_phase {
    PHASE_IDLE          = 0u,
    PHASE_FIRST_SLEEP   = 1u,
    PHASE_WAIT_SLOT     = 2u,
    PHASE_WAIT_READY    = 3u,
    PHASE_WAIT_AF       = 4u,
    PHASE_CAP_STOP      = 5u,
    PHASE_FINAL_CLEANUP = 6u,
    PHASE_BULB_HOLD     = 7u,
};

struct timespec32 {
    i32 tv_sec;
    i32 tv_nsec;
};

struct interval_state {
    u32 magic;                /* +0x00 */
    u32 running;              /* +0x04 */
    u32 phase;                /* +0x08 */
    u32 af_held;              /* +0x0C */
    u32 shots_taken;          /* +0x10 */
    u32 seq_idx;              /* +0x14 */
    u32 disp_is_off;          /* +0x18 */
    void *model_rec;          /* +0x1C */
    void *active_rec;         /* +0x20: armed TimerManager timeCallBackRecord* */
    u32 start_delay_s;        /* +0x24 */
    u32 interval_s;           /* +0x28 */
    u32 max_shots;            /* +0x2C: 0 = unlimited */
    u32 af_mode;              /* +0x30: 0 = Lock AF+AE, 1 = Lock AF (Auto AE), 2 = AF/AE Each */
    u32 disp_sleep;           /* +0x34: 0 = Off, 1 = On */
    u32 abort_reason;         /* +0x38: 0=done, 1=user/mode, 2=menu off, 3=playback, 4=timeout */
    u32 inactive_ticks;       /* +0x3C */
    u32 busy_ticks;           /* +0x40 */
    u32 cap_held;             /* +0x44: 1 between CaptureStart and CaptureStop */
    u64 anchor_ms;            /* +0x48 */
    u64 next_shot_ms;         /* +0x50 */
    u64 last_fire_ms;         /* +0x58: timestamp when CaptureStart fired */
    u32 cb_count;             /* +0x60: total timer callback invocations in current sequence */
    u32 disp_recheck;         /* +0x64: 1 = re-verify ALL OFF once ready after shot */
    u32 peek_active;          /* +0x68: 1 = S1 half-press temporarily woke screen during sleep */
    u32 bulb_active;          /* +0x6C: 1 = current sequence uses timed Extended Shutter Bulb hold */
    void *dm_widget;          /* +0x70: live ACT_DriveMode* (cleared by destructor hook) */
    void *remain_widget;      /* +0x74: live ACT_LabelStillRemain* (cleared by destructor hook) */
    u32 lock_applied;         /* +0x78: 1 = post-Shot-#1 AF/MF(+AEL) lock applied */
    u32 afmf_locked;          /* +0x7C: 1 = we issued AF_MF_CHANGE_ON_CMD (must release on stop) */
    u32 ael_locked;           /* +0x80: 1 = we issued AEL ON (must release on stop) */
    u32 saved_disp_out;       /* +0x84: saved output before ALL OFF (1=PANEL, 2=EVF) */
    u32 blink_phase;          /* +0x88: 0=hidden, 1=visible */
    u32 bulb_mode_only;       /* +0x8C: 1 = standalone timed Extended Shutter (0 = Interval or Combo) */
    u64 last_blink_ms;        /* +0x90: timestamp of last Drive Mode icon blink toggle */
    u64 last_apo_ms;          /* +0x98: timestamp of last Auto Power Off timer reload */
    u64 peek_until_ms;        /* +0xA0: timestamp when S1 peek expires and display re-sleeps */
    u32 bulb_duration_s;      /* +0xA8: active timed exposure duration (45..120s) */
    u32 swallow_next_capstop; /* +0xAC: 1 = swallow trailing S2 release after user cancel */
    u64 bulb_start_ms;        /* +0xB0 */
    u64 bulb_end_ms;          /* +0xB8 */
    u32 bulb_last_osd_sec;    /* +0xC0 */
    u32 ext_ss_idx;           /* +0xC4: 0=normal, 1..5=45/60/80/100/120s, 6=Manual BULB */
    void *model_cam;          /* +0xC8: live ModelCamera* captured by info35/shift hooks */
    u32 min_ss_synced;        /* +0xCC: 1 = Auto ISO Min. SS synced to BIONZ via @M00B 0x7E */
    u32 min_ss_last_liro;     /* +0xD0: last encoded 32-bit payload pushed to BIONZ LIRO 0x40CE */
    u32 ext_ss_entering;      /* +0xD4: 1 = awaiting BIONZ 30" -> BULB (0xFFFF) async handoff */
    u64 ext_ss_enter_ms;      /* +0xD8: timestamp when 30" -> 45" transition began */
};                            /* total = 0xE0 (224 bytes <= 256-byte STATE_SIZE) */

_Static_assert(sizeof(struct interval_state) <= 0x100u, "interval_state exceeds STATE_SIZE");

/* Option lookup tables matching src/menu_spec.py (const -> .rodata, PIC-safe) */
static const u32 DELAY_SEC[6]     = {0, 2, 5, 10, 30, 60};
static const u32 INTERVAL_SEC[10] = {1, 2, 3, 5, 10, 15, 20, 30, 45, 60};
static const u32 SHOTS_CNT[9]     = {30, 60, 100, 120, 240, 300, 500, 999, 0};
static const u32 EXT_SS_SEC[6]    = {0, 45, 60, 80, 100, 120};

/* Verified ACT_DriveMode icon table @ 0x0105f824 (stride 0xbc) for appearance modes 0..4:
 * Option A: 100% untouched stock Sony icons — 0x0205D3E9 (70x60 stopwatch+frames) on LCD/QuickNavi,
 * and 0x020E8787 (58x50 stacked frames) on EVF (matches Sony's col[38..46] layout). */
static const u32 ACT_SINGLE_ICON[5] = {
    0x0205C10Bu, 0x0205C0D3u, 0x020E804Eu, 0x0205C0EFu, 0x020CE1CDu
};
static const u32 ACT_INTV_ICON[5] = {
    0x0205D3E9u, 0x0205D3E9u, 0x020E8787u, 0x0205D3E9u, 0x0205D3E9u
};

typedef void *(*op_new_t)(u32);
typedef void (*pl_ctor_t)(void *);
typedef void (*pl_dtor_t)(void *);
typedef void (*prm_ctor_t)(void *, i32);
typedef i32 (*prm_getnum_t)(void *);
typedef void *(*pl_get_t)(void *, u32);
typedef void (*pl_add_t)(void *, u32, void *);
typedef void (*req_exec_t)(const char *, u32, void *);
typedef i32 (*get_model_info_t)(const char *, u32, void *, const void *);
typedef i32 (*bk_read_t)(u32, void *);
typedef i32 (*bk_write_t)(u32, const void *);
typedef i32 (*time_now_t)(struct timespec32 *);
typedef i32 (*time_add_ms_t)(struct timespec32 *, u32);
typedef void (*tmr_cb_t)(void *, struct timespec32 *, void *);
typedef void *(*tmr_add_t)(void *, struct timespec32 *, tmr_cb_t, void *);
typedef i32 (*tmr_rem_t)(void *, void *);
typedef void (*wakeup_t)(void);
typedef void (*model_cmd_t)(void *, u32);
typedef void (*model_void_cmd_t)(void *);
typedef u32 (*get_focus_mode_t)(void);
typedef void (*set_disp_state_t)(void *, u32);
typedef void (*widget_void_t)(void *);
typedef u32 (*vsm_get_u32_t)(void *);
typedef void (*vsm_void_ptr_t)(void *, void *);
typedef void (*vsm_set_page_tab_t)(void *, u32, u32);
typedef void *(*get_ui_disp_t)(void);
typedef void (*event_ctor_t)(void *, u32, u32, u32);
typedef void (*post_ui_event_t)(void *, void *);

static inline struct interval_state *get_state(void) {
    return (struct interval_state *)(void *)pic_ptr(INTERVAL_STATE);
}

static inline void *get_timer_mgr(void) {
    return *(void *const *)pic_ptr(G_TIMER_MGR);
}

static u32 bk_read_u8(u32 id) {
    u8 buf[8];
    buf[0] = 0;
    if (CALL(bk_read_t, P_BK_READ)(id, buf) < 0) {
        return 0;
    }
    return (u32)buf[0];
}

static void bk_write_u8(u32 id, u8 val) {
    u32 word = (u32)val;
    CALL(bk_write_t, P_BK_WRITE)(id, &word);
}

static u32 need_sw_shutter(void) {
    return (bk_read_u8(BK_ENABLE) == 1u || bk_read_u8(BK_EXT_SHUTTER) == 1u) ? 1u : 0u;
}

/* Forward declarations */
static u64 now_ms(void);
static void send_model_u32(const char *model, u32 ev, u32 key, i32 val);
static void send_model_empty(const char *model, u32 ev);
static void interval_stop(struct interval_state *st, u32 reason);
void interval_timer_cb(void *rec, struct timespec32 *fired_ts, void *arg);

/* ================= Native Extended Shutter Speed Dial & OSD Hooks ================= */

static inline u32 is_hw_30s(u32 num, u32 den) {
    return (den != 0u && num == 30u * den) ? 1u : 0u;
}

/* Post Sony's native fast-display shutter speed redraw event (0x18000001) to UI dispatcher,
 * matching libObj.so @ 0x0050ea54..0x0050ea70 so ACT_IconShutterSpeed redraws LCD & EVF! */
static void post_osd_shutter_refresh(void) {
    void *disp = CALL(get_ui_disp_t, V_GET_UI_DISP)();
    if (!disp) {
        return;
    }
    void *ev = CALL(op_new_t, P_NEW)(0x10u);
    if (!ev) {
        return;
    }
    CALL(event_ctor_t, P_EVENT_CTOR)(ev, EV_FAST_DISP_SHUTTER, 4u, 0u);
    CALL(post_ui_event_t, V_POST_UI_EVENT)(disp, ev);
}

static void notify_shutter_ui(void *model_cam) {
    if (model_cam) {
        CALL(model_cmd_t, V_NOTIFY_UPDATE)(model_cam, INFO_SHUTTER_SPEED);
    }
    post_osd_shutter_refresh();
}

/* Returns configured timed duration (45..120s) if Extended Shutter is ON, hardware is in
 * BULB (0xFFFF), and dial is positioned on virtual step 1..5; otherwise returns 0. */
static u32 active_ext_shutter_sec(const struct interval_state *st) {
    if (bk_read_u8(BK_EXT_SHUTTER) != 1u) {
        return 0u;
    }
    if (!st->model_cam) {
        return 0u;
    }
    u32 hw_num = *(const volatile u32 *)((const u8 *)st->model_cam + CAM_OFF_SS_NUM);
    if (hw_num != SS_BULB_NUM) {
        return 0u;
    }
    u32 idx = st->ext_ss_idx;
    if (idx >= 1u && idx <= EXT_SS_MAX_TIMED) {
        return EXT_SS_SEC[idx];
    }
    return 0u;
}

/* Hooked on `ModelCamera::pvt_ActionShiftShutterSpeed` @ 0x00551474(model_cam, delta).
 * Sony's function sign-extends delta via `sxtb`, so we do the same here for ABI safety.
 * delta < 0: spin slower (30" -> 45" -> 60" -> 80" -> 100" -> 120" -> BULB)
 * delta > 0: spin faster (BULB -> 120" -> 100" -> 80" -> 60" -> 45" -> 30")
 * Returns 1 to swallow hardware shift (keeping BIONZ in BULB), 0 to pass through. */
i32 ext_ss_on_shift(void *model_cam, i32 delta) {
    struct interval_state *st = get_state();
    st->model_cam = model_cam;
    i32 d = (i32)(signed char)delta;

    if (bk_read_u8(BK_EXT_SHUTTER) != 1u) {
        st->ext_ss_idx = 0u;
        st->ext_ss_entering = 0u;
        return 0;
    }

    u32 hw_num = *(const volatile u32 *)((const u8 *)model_cam + CAM_OFF_SS_NUM);
    u32 hw_den = *(const volatile u32 *)((const u8 *)model_cam + CAM_OFF_SS_DEN);

    /* Stepping slower from 30" (30/1 or 300/10) -> enter virtual 45" (idx=1) and let BIONZ enter BULB!
     * Hold ext_ss_idx across the brief async BIONZ handoff via ext_ss_entering (1500ms grace). */
    if (is_hw_30s(hw_num, hw_den)) {
        if (d < 0) {
            u64 t_now = now_ms();
            if (st->ext_ss_entering && (t_now - st->ext_ss_enter_ms) < (u64)EXT_SS_ENTER_GRACE_MS) {
                u32 cur = st->ext_ss_idx;
                if (cur < EXT_SS_MANUAL_BULB) {
                    st->ext_ss_idx = (cur == 0u) ? 1u : (cur + 1u);
                    notify_shutter_ui(model_cam);
                }
                return 1;
            }
            st->ext_ss_idx = 1u;
            st->ext_ss_entering = 1u;
            st->ext_ss_enter_ms = t_now;
            return 0;
        }
        st->ext_ss_idx = 0u;
        st->ext_ss_entering = 0u;
        return 0;
    }

    /* Currently in hardware BULB (0xFFFF): step through 45"/60"/80"/100"/120"/BULB! */
    if (hw_num == SS_BULB_NUM) {
        st->ext_ss_entering = 0u;
        u32 cur = st->ext_ss_idx;
        if (cur == 0u || cur > EXT_SS_MANUAL_BULB) {
            cur = EXT_SS_MANUAL_BULB;
        }
        if (d < 0) {
            if (cur < EXT_SS_MANUAL_BULB) {
                st->ext_ss_idx = cur + 1u;
                notify_shutter_ui(model_cam);
            }
            return 1;
        }
        if (d > 0) {
            if (cur > 1u) {
                st->ext_ss_idx = cur - 1u;
                notify_shutter_ui(model_cam);
                return 1;
            }
            /* On 45" (cur == 1) and spinning faster -> exit virtual steps, BIONZ steps BULB -> 30"! */
            st->ext_ss_idx = 0u;
            return 0;
        }
        return 1;
    }

    /* Any other shutter speed -> clear virtual index and pass through normally */
    st->ext_ss_idx = 0u;
    st->ext_ss_entering = 0u;
    return 0;
}

/* Hooked on `ModelCamera::getModelInfo(0x35)` @ 0x0053ECC6 (replaces ldr.w r1, [r5, #0x200]). */
u32 ext_ss_override_num(void *model_cam) {
    struct interval_state *st = get_state();
    st->model_cam = model_cam;
    u32 hw_num = *(const volatile u32 *)((const u8 *)model_cam + CAM_OFF_SS_NUM);
    if (hw_num != SS_BULB_NUM) {
        if (st->ext_ss_entering) {
            u32 hw_den = *(const volatile u32 *)((const u8 *)model_cam + CAM_OFF_SS_DEN);
            if (is_hw_30s(hw_num, hw_den) &&
                (now_ms() - st->ext_ss_enter_ms) < (u64)EXT_SS_ENTER_GRACE_MS) {
                return hw_num;
            }
            st->ext_ss_entering = 0u;
        }
        if (st->ext_ss_idx != 0u) {
            st->ext_ss_idx = 0u;
        }
        return hw_num;
    }
    st->ext_ss_entering = 0u;
    if (bk_read_u8(BK_EXT_SHUTTER) == 1u) {
        u32 idx = st->ext_ss_idx;
        if (idx >= 1u && idx <= EXT_SS_MAX_TIMED) {
            return EXT_SS_SEC[idx];
        }
    }
    return hw_num;
}

/* Hooked on `ModelCamera::getModelInfo(0x35)` @ 0x0053ECD6 (replaces ldr.w r1, [r5, #0x204]). */
u32 ext_ss_override_den(void *model_cam) {
    struct interval_state *st = get_state();
    u32 hw_num = *(const volatile u32 *)((const u8 *)model_cam + CAM_OFF_SS_NUM);
    u32 hw_den = *(const volatile u32 *)((const u8 *)model_cam + CAM_OFF_SS_DEN);
    if (hw_num == SS_BULB_NUM && bk_read_u8(BK_EXT_SHUTTER) == 1u) {
        u32 idx = st->ext_ss_idx;
        if (idx >= 1u && idx <= EXT_SS_MAX_TIMED) {
            return 1u;
        }
    }
    return hw_den;
}

/* Hooked on `fast_disp_get` @ 0x0050E9C4 (after memcpy when slot == 1).
 * Live View LCD & EVF `ACT_IconShutterSpeed` reads Slot 1:
 *   +0x00: u16 num (0xFFFF = BULB), +0x02: u16 den, +0x0C: u16 valid (== 1). */
void ext_ss_override_fast_disp(u8 *buf) {
    if (!buf) {
        return;
    }
    u16 valid = *(const volatile u16 *)(buf + 0x0Cu);
    if (valid != 1u) {
        return;
    }
    struct interval_state *st = get_state();
    u16 num = *(const volatile u16 *)(buf + 0x00u);
    if (num != (u16)SS_BULB_NUM) {
        if (st->ext_ss_entering) {
            u16 den = *(const volatile u16 *)(buf + 0x02u);
            if (is_hw_30s((u32)num, (u32)den) &&
                (now_ms() - st->ext_ss_enter_ms) < (u64)EXT_SS_ENTER_GRACE_MS) {
                return;
            }
            st->ext_ss_entering = 0u;
        }
        if (st->ext_ss_idx != 0u) {
            st->ext_ss_idx = 0u;
        }
        return;
    }
    st->ext_ss_entering = 0u;
    if (bk_read_u8(BK_EXT_SHUTTER) == 1u) {
        u32 idx = st->ext_ss_idx;
        if (idx >= 1u && idx <= EXT_SS_MAX_TIMED) {
            *(volatile u16 *)(buf + 0x00u) = (u16)EXT_SS_SEC[idx];
            *(volatile u16 *)(buf + 0x02u) = 1u;
        }
    }
}

/* ================= Live View OSD Indicator & Remaining Shots Helpers ================= */

static void set_dm_icon_visible(struct interval_state *st, u32 visible) {
    void *w = st->dm_widget;
    if (w) {
        CALL(set_disp_state_t, V_SET_DISP_STATE)((u8 *)w + ACT_DM_IMG_OFFSET, visible ? 1u : 0u);
    }
}

static void refresh_remain_osd(struct interval_state *st) {
    void *w = st->remain_widget;
    if (w) {
        CALL(widget_void_t, V_REMAIN_UPDATE)(w);
    }
}

static u32 bulb_remaining_sec(const struct interval_state *st, u64 cur_t) {
    u64 target = (st->phase == PHASE_BULB_HOLD) ? st->bulb_end_ms : st->next_shot_ms;
    if (target <= cur_t) {
        return 0u;
    }
    u64 rem_ms = target - cur_t;
    if (rem_ms > 9999000ULL) {
        return 9999u;
    }
    return ((u32)rem_ms + 999u) / 1000u;
}

static void update_waiting_blink(struct interval_state *st, u64 now_t) {
    if (!st->running || !st->dm_widget) {
        return;
    }
    if (now_t >= st->last_blink_ms && (now_t - st->last_blink_ms) >= (u64)BLINK_HALF_MS) {
        st->blink_phase = st->blink_phase ? 0u : 1u;
        st->last_blink_ms = now_t;
        set_dm_icon_visible(st, st->blink_phase);
    }
}

static void keepalive_apo(struct interval_state *st, u64 now_t, u32 force) {
    if (force || (now_t >= st->last_apo_ms && (now_t - st->last_apo_ms) >= (u64)APO_KEEPALIVE_MS)) {
        st->last_apo_ms = now_t;
        send_model_empty("@M058", EV_RELOAD_APO);
    }
}

/* ================= Auto ISO Min. SS (BIONZ LIRO 0x40CE) Sync ================= */

/* Map persisted Backup.bin byte (BK_MIN_SS = 0x010700D0, 0..23) to BIONZ LIRO 0x40CE payload:
 *   0 -> Standard (0 EV * 1/f)   = 0x80000000  (Factory Reset default!)
 *   1 -> Slowest  (-2 EV * 1/f)  = 0x8000FE00
 *   2 -> Slower   (-1 EV * 1/f)  = 0x8000FF00
 *   3 -> Faster   (+1 EV * 1/f)  = 0x80000100
 *   4 -> Fastest  (+2 EV * 1/f)  = 0x80000200
 *   5..23 -> Fixed APEX Tv 1/8000s (0x1D00) .. 30" (0x0B00) = 0x8001xxxx
 */
static u32 min_ss_bk_to_liro(u32 bk) {
    if (bk == 0u) {
        return 0x80000000u;
    }
    if (bk == 1u) {
        return 0x8000FE00u;
    }
    if (bk == 2u) {
        return 0x8000FF00u;
    }
    if (bk == 3u) {
        return 0x80000100u;
    }
    if (bk == 4u) {
        return 0x80000200u;
    }
    if (bk >= 5u && bk <= 23u) {
        return 0x80010000u | (0x1D00u - ((bk - 5u) << 8));
    }
    return 0x80000000u;
}

static void push_auto_iso_min_ss_bk(struct interval_state *st, u32 bk) {
    u32 payload = min_ss_bk_to_liro(bk);
    st->min_ss_synced = 1u;
    st->min_ss_last_liro = payload;
    send_model_u32("@M00B", EV_SET_SHUT_LOW_LIMIT, PRM_KEY_SHUT_LOW_LIMIT, (i32)payload);
}

static void sync_auto_iso_min_ss(struct interval_state *st) {
    push_auto_iso_min_ss_bk(st, bk_read_u8(BK_MIN_SS));
}

/* ================= SPINAL_REFLEX, Drive Mode (PID 0x07), S1 Peek & OSD Hooks ================= */

void spinal_cmd_hook(void *model, u32 enable) {
    struct interval_state *st = get_state();
    st->model_rec = model;
    u32 orig_enable = enable;
    if (enable != 0u && need_sw_shutter()) {
        enable = 0u;
    }
    orig_set_spinal_reflex(model, enable);
    /* Re-sync persisted Auto ISO Min. SS to BIONZ on every boot / standby wakeup / Still mode entry! */
    if (orig_enable != 0u) {
        sync_auto_iso_min_ss(st);
    }
}

/* Called on S1 half-press (`ModelStillRec::pvt_ActionExeFocusStart` @ 0x00722300).
 * If Monitor Sleep is currently active, temporarily wakes the saved LCD/EVF for 5s
 * so user can peek at framing, blinking icon, and remaining shots, then auto-sleeps! */
void interval_on_focus_start(void *model) {
    struct interval_state *st = get_state();
    st->model_rec = model;
    if (st->running && st->disp_is_off) {
        u64 cur = now_ms();
        st->peek_until_ms = cur + (u64)PEEK_DURATION_MS;
        if (!st->peek_active) {
            i32 target = (st->saved_disp_out == (u32)DISP_EVF_ON) ? DISP_EVF_ON : DISP_PANEL_ON;
            send_model_u32("@M006", EV_PANEL_EVF_SWITCH, PRM_KEY_PANEL_EVF, target);
            st->peek_active = 1u;
            st->disp_recheck = 0u;
            refresh_remain_osd(st);
        }
    }
}

/* Called on S2 release (`ModelStillRec::pvt_ActionExeCaptureStop` @ 0x00721B90).
 * Swallows S2 release while a timed Extended Shutter Bulb exposure is holding shutter open,
 * or once right after an early-cancel press. */
i32 interval_on_capstop(void *model) {
    struct interval_state *st = get_state();
    st->model_rec = model;
    if (st->running && st->bulb_active) {
        return 1;
    }
    if (st->swallow_next_capstop) {
        st->swallow_next_capstop = 0u;
        return 1;
    }
    return 0;
}

i32 pdm_setValue_hook(void *mgr, u32 pid, u32 val) {
    struct interval_state *st = get_state();
    if (pid == PID_DRIVEMODE) {
        if (val == DM_VAL_INTERVAL) {
            i32 rc = orig_pdm_setValue(mgr, PID_DRIVEMODE, DM_VAL_SINGLE);
            if (rc != 0) {
                bk_write_u8(BK_ENABLE, 1u);
                send_model_u32("@M00F", EV_SET_SPINAL_REFLEX, PRM_KEY_SHUTTER, 0);
            }
            return rc;
        }
        i32 rc = orig_pdm_setValue(mgr, PID_DRIVEMODE, val);
        if (rc != 0) {
            bk_write_u8(BK_ENABLE, 0u);
            if (st->running && !st->bulb_mode_only) {
                interval_stop(st, 1u);
            }
            send_model_u32("@M00F", EV_SET_SPINAL_REFLEX, PRM_KEY_SHUTTER, need_sw_shutter() ? 0 : 1);
        }
        return rc;
    }
    if (pid == PID_ENABLE) {
        if (val == 1u) {
            (void)orig_pdm_setValue(mgr, PID_DRIVEMODE, DM_VAL_SINGLE);
        } else if (st->running && !st->bulb_mode_only) {
            interval_stop(st, 2u);
        }
        i32 rc = orig_pdm_setValue(mgr, pid, val);
        if (rc != 0) {
            send_model_u32("@M00F", EV_SET_SPINAL_REFLEX, PRM_KEY_SHUTTER, need_sw_shutter() ? 0 : 1);
        }
        return rc;
    }
    if (pid == PID_FN_INTV) {
        return 1;
    }
    if (pid == PID_MIN_SS) {
        i32 rc = orig_pdm_setValue(mgr, pid, val);
        if (rc != 0) {
            /* Match MIN_SS_SPEC.backup_bytes: opt 0->1, 1->2, 2->0 (Standard), 3..23->3..23 */
            u32 bk = (val == 2u) ? 0u : (val < 2u ? (val + 1u) : val);
            push_auto_iso_min_ss_bk(st, bk);
        }
        return rc;
    }
    if (pid == PID_EXT_SHUTTER) {
        if (val == 0u) {
            st->ext_ss_idx = 0u;
            st->ext_ss_entering = 0u;
            if (st->running && st->bulb_mode_only) {
                interval_stop(st, 2u);
            }
        }
        i32 rc = orig_pdm_setValue(mgr, pid, val);
        if (rc != 0) {
            notify_shutter_ui(st->model_cam);
            send_model_u32("@M00F", EV_SET_SPINAL_REFLEX, PRM_KEY_SHUTTER, need_sw_shutter() ? 0 : 1);
        }
        return rc;
    }
    return orig_pdm_setValue(mgr, pid, val);
}

i32 pdm_getValue_hook(void *mgr, u32 pid, u32 *out) {
    if (pid == PID_FN_INTV) {
        if (out) {
            *out = 0u;
        }
        return 1;
    }
    i32 rc = orig_pdm_getValue(mgr, pid, out);
    if (rc != 0 && pid == PID_DRIVEMODE && out && *out == DM_VAL_SINGLE) {
        if (bk_read_u8(BK_ENABLE) == 1u) {
            *out = DM_VAL_INTERVAL;
        }
    }
    return rc;
}

/* Hooked on ViewSettingMenu::onTransition @ 0x0080DA7C.
 * Handles two direct Fn Menu launchers during initial Layout 0 boot (ev == 0x11012001):
 *   - bootElement == 0x77 (Interval Shoot Func.): clear bootElement, run Action 7 setup,
 *     and route directly to Layout 5 (Folder Sub-Page) + Action 0x38!
 *   - bootElement == 0x78 (ISO AUTO Min. SS): clear bootElement, arm Layout 1's native
 *     reopen-popup state ([+0x120]=2, [+0x130]=current opt_idx, *(u8*)[+0x160]=1), and
 *     route to Layout 1 + Action 7 so Layout 1 onEnterState @ 0x0080b758 immediately
 *     opens the 24-option scrollable selector focused on the active value! */
i32 vsm_on_transition_check(void *vsm, u32 *next_layout, u32 *action_id) {
    if (*(const volatile u32 *)((const u8 *)vsm + VSM_OFF_LAYOUT) != 0u) {
        return 0;
    }
    u32 boot = *(const volatile u32 *)((const u8 *)vsm + VSM_OFF_BOOT_EL);
    if (boot != VSM_BOOT_INTV && boot != VSM_BOOT_MIN_SS) {
        return 0;
    }
    if (CALL(vsm_get_u32_t, P_VIEW_GET_EVID)(vsm) != VSM_EV_BOOT) {
        return 0;
    }
    *(volatile u32 *)((u8 *)vsm + VSM_OFF_BOOT_EL) = 0u;
    if (boot == VSM_BOOT_INTV) {
        CALL(widget_void_t, V_VSM_ACTION7)(vsm);
        if (next_layout) {
            *next_layout = VSM_LAYOUT_FOLDER;
        }
        if (action_id) {
            *action_id = VSM_ACTION_FOLDER_OPEN;
        }
        return 1;
    }
    /* boot == VSM_BOOT_MIN_SS: map persisted Backup byte (0->2, 1->0, 2->1, 3..23->3..23) */
    u32 bk = bk_read_u8(BK_MIN_SS);
    u32 opt_idx = (bk == 0u) ? 2u : ((bk <= 2u) ? (bk - 1u) : ((bk <= 23u) ? bk : 2u));
    *(volatile u32 *)((u8 *)vsm + VSM_OFF_SUBSTATE) = 2u;
    *(volatile u32 *)((u8 *)vsm + VSM_OFF_POPUP_IDX) = opt_idx;
    *(volatile u8 *)((u8 *)vsm + VSM_OFF_REOPEN_POPUP) = 1u;
    if (next_layout) {
        *next_layout = VSM_LAYOUT_MAIN;
    }
    if (action_id) {
        *action_id = VSM_ACTION_BOOT_MAIN;
    }
    return 1;
}

/* Hooked on ViewSettingMenu::initMenuNode @ 0x0080CF84 (runs during State 0 onEnterState
 * BEFORE VSM_EV_BOOT hits onTransition!). When bootElement == 0x77 or 0x78, select the
 * corresponding Tab 2 P3 node (slot 4 = Interval folder, slot 5 = ISO AUTO Min. SS) and
 * sync page/tab ONLY — leave bootElement intact for vsm_on_transition_check! */
i32 vsm_init_menu_node_check(void *vsm) {
    u32 boot = *(const volatile u32 *)((const u8 *)vsm + VSM_OFF_BOOT_EL);
    u32 slot = 0u;
    if (boot == VSM_BOOT_INTV) {
        slot = 4u;
    } else if (boot == VSM_BOOT_MIN_SS) {
        slot = 5u;
    } else {
        return 0;
    }
    void *target = ((void *const *)pic_ptr(G_TAB2_P3_NEW_ARR))[slot];
    if (!target) {
        return 0;
    }
    CALL(vsm_void_ptr_t, V_VSM_SELECT_NODE)(vsm, target);
    CALL(widget_void_t, V_VSM_SYNC_INDICES)(vsm);
    u32 page = CALL(vsm_get_u32_t, V_VSM_GET_PAGE)(vsm);
    u32 tab = *(const volatile u32 *)((const u8 *)vsm + VSM_OFF_TAB);
    CALL(vsm_set_page_tab_t, V_VSM_SET_PAGE_TAB)(vsm, page, tab);
    return 1;
}

u32 interval_act_drivemode_icon(u32 icon_id, u32 appearance_mode, void *act_dm_this) {
    struct interval_state *st = get_state();
    st->dm_widget = act_dm_this;
    if (!st->min_ss_synced) {
        sync_auto_iso_min_ss(st);
    }
    if (appearance_mode < 5u && icon_id == ACT_SINGLE_ICON[appearance_mode]) {
        if (bk_read_u8(BK_ENABLE) == 1u) {
            return ACT_INTV_ICON[appearance_mode];
        }
    }
    return icon_id;
}

/* Replaces empty 6-byte stub @ 0x00964734 called exclusively by ~ACT_DriveMode @ 0x00943e42. */
void act_drivemode_destroy_hook(void *act_dm_this) {
    struct interval_state *st = get_state();
    if (st->dm_widget == act_dm_this) {
        st->dm_widget = (void *)0;
    }
}

/* Hooked into ACT_LabelStillRemain formatted remain count (@ 0x00924D9A & 0x00924826). */
u32 interval_remain_override(u32 orig_count, void *remain_this) {
    struct interval_state *st = get_state();
    st->remain_widget = remain_this;
    if (!st->running) {
        return orig_count;
    }
    if (st->bulb_mode_only) {
        return bulb_remaining_sec(st, now_ms());
    }
    if (st->max_shots != 0u) {
        return (st->max_shots > st->shots_taken) ? (st->max_shots - st->shots_taken) : 0u;
    }
    /* Unlimited mode: display shots taken so far (1..9998; 9999 during initial delay before shot #1) */
    return (st->shots_taken > 0u) ? st->shots_taken : 9999u;
}

/* Hooked into ~ACT_LabelStillRemain @ 0x00918A84 (ARM EABI C++ dtor returns `this`). */
void *act_remain_dtor_hook(void *remain_this) {
    struct interval_state *st = get_state();
    if (st->remain_widget == remain_this) {
        st->remain_widget = (void *)0;
    }
    return orig_remain_dtor(remain_this);
}

/* ================= Main-Thread TimerManager Interval Engine ================= */

static u64 now_ms(void) {
    struct timespec32 ts;
    ts.tv_sec = 0;
    ts.tv_nsec = 0;
    CALL(time_now_t, V_TIME_NOW)(&ts);
    return (u64)(u32)ts.tv_sec * 1000ULL + (u64)(u32)ts.tv_nsec / 1000000ULL;
}

static u32 read_model_state(const struct interval_state *st) {
    void *m = st->model_rec;
    if (!m) {
        return STATE_READY_MIN;
    }
    return *(const volatile u32 *)((const u8 *)m + MODEL_OFF_STATE);
}

static u32 is_still_ready(const struct interval_state *st) {
    void *m = st->model_rec;
    if (!m) {
        return 0u;
    }
    u32 ms = *(const volatile u32 *)((const u8 *)m + MODEL_OFF_STATE);
    if (ms < STATE_READY_MIN || ms > STATE_READY_MAX) {
        return 0u;
    }
    if (*(const volatile u32 *)((const u8 *)m + MODEL_OFF_STS_CAP) != 0u) {
        return 0u;
    }
    if (*(const volatile u8 *)((const u8 *)m + MODEL_OFF_DEV_RDY1) == 0u ||
        *(const volatile u8 *)((const u8 *)m + MODEL_OFF_DEV_RDY2) == 0u) {
        return 0u;
    }
    return 1u;
}

/* Dispatch a single-parameter Event on the Main EventLoop thread (identical to Sony UI). */
static void send_model_u32(const char *model, u32 ev, u32 key, i32 val) {
    void *pl = CALL(op_new_t, P_NEW)(0x10u);
    if (!pl) {
        return;
    }
    CALL(pl_ctor_t, P_PL_CTOR)(pl);
    void *pn = CALL(op_new_t, P_NEW)(0x10u);
    if (pn) {
        CALL(prm_ctor_t, V_PRM_CTOR)(pn, val);
        CALL(pl_add_t, P_PL_ADD)(pl, key, pn);
    }
    CALL(req_exec_t, P_REQ_EXEC)(model, ev, pl);
}

/* Dispatch a zero-parameter Event (e.g. @M058 ActionReloadAutoPowerOffTimer). */
static void send_model_empty(const char *model, u32 ev) {
    void *pl = CALL(op_new_t, P_NEW)(0x10u);
    if (!pl) {
        return;
    }
    CALL(pl_ctor_t, P_PL_CTOR)(pl);
    CALL(req_exec_t, P_REQ_EXEC)(model, ev, pl);
}

/* Query active Panel/EVF output via ModelDisplay ("@M006") info 0x20:
 * returns 0 (ALL OFF), 1 (PANEL ON), or 2 (EVF ON). */
static u32 get_panel_evf_status(void) {
    u32 pl[4];
    pl[0] = 0u;
    pl[1] = 0u;
    pl[2] = 0u;
    pl[3] = 0u;
    CALL(pl_ctor_t, P_PL_CTOR)(pl);
    u32 res = DISP_PANEL_ON;
    if (CALL(get_model_info_t, P_GET_MODEL_INFO)("@M006", INFO_PANEL_EVF_STS, pl, (void *)0) != 0) {
        void *prm = CALL(pl_get_t, V_PL_GET)(pl, PRM_KEY_PANEL_EVF);
        if (prm) {
            i32 v = CALL(prm_getnum_t, V_PRM_GETNUM)(prm);
            if (v >= 0 && v <= 2) {
                res = (u32)v;
            }
        }
    }
    CALL(pl_dtor_t, P_PL_DTOR)(pl);
    return res;
}

/* True hardware LCD + EVF power switch via ModelDisplay ("@M006") ev=0x2F, key=0x1200600B. */
static void set_display_sleep(struct interval_state *st, u32 sleep_on) {
    if (sleep_on) {
        if (!st->disp_is_off) {
            u32 cur = get_panel_evf_status();
            st->saved_disp_out = (cur == (u32)DISP_EVF_ON) ? (u32)DISP_EVF_ON : (u32)DISP_PANEL_ON;
        }
        send_model_u32("@M006", EV_PANEL_EVF_SWITCH, PRM_KEY_PANEL_EVF, DISP_ALL_OFF);
        st->disp_is_off = 1u;
        st->disp_recheck = 0u;
        st->peek_active = 0u;
    } else if (st->disp_is_off) {
        if (!st->peek_active) {
            i32 target = (st->saved_disp_out == (u32)DISP_EVF_ON) ? DISP_EVF_ON : DISP_PANEL_ON;
            send_model_u32("@M006", EV_PANEL_EVF_SWITCH, PRM_KEY_PANEL_EVF, target);
        }
        st->disp_is_off = 0u;
        st->disp_recheck = 0u;
        st->peek_active = 0u;
    }
}

/* Engage post-Shot-#1 Focus / Exposure lock according to `st->af_mode`:
 *   AF_MODE_LOCK_AFAE (0): MF Hold (if currently AF) + AEL ON
 *   AF_MODE_LOCK_AF   (1): MF Hold ONLY (Holy Grail sunrise/sunset — AE stays automatic!)
 *   AF_MODE_EACH      (2): no lock (re-focus + re-meter each shot) */
static void engage_first_shot_lock(struct interval_state *st) {
    if (st->af_mode == AF_MODE_EACH || st->lock_applied != 0u) {
        return;
    }
    st->lock_applied = 1u;
    u32 fm = CALL(get_focus_mode_t, V_GET_FOCUS_MODE)();
    if (fm != FOCUS_MODE_MF && st->model_rec) {
        CALL(model_void_cmd_t, V_AFMF_ON_CMD)(st->model_rec);
        st->afmf_locked = 1u;
    }
    if (st->af_mode == AF_MODE_LOCK_AFAE) {
        send_model_u32("@M00B", EV_SET_AEL, PRM_KEY_AEL, AEL_VAL_ON);
        st->ael_locked = 1u;
    }
}

/* Arm a one-shot TimerManager callback after `delay_ms` milliseconds and wake EventLoop. */
static u32 schedule_after_ms(struct interval_state *st, u32 delay_ms) {
    void *tm = get_timer_mgr();
    if (!tm) {
        return 0u;
    }
    if (st->active_rec) {
        CALL(tmr_rem_t, V_TMR_REMOVE)(tm, st->active_rec);
        st->active_rec = (void *)0;
    }
    if (delay_ms == 0u) {
        delay_ms = 1u;
    }
    struct timespec32 ts;
    ts.tv_sec = 0;
    ts.tv_nsec = 0;
    CALL(time_now_t, V_TIME_NOW)(&ts);
    CALL(time_add_ms_t, V_TIME_ADD_MS)(&ts, delay_ms);
    void *rec = CALL(tmr_add_t, V_TMR_ADD)(tm, &ts, interval_timer_cb, (void *)st);
    st->active_rec = rec;
    if (rec) {
        CALL(wakeup_t, V_WAKEUP_LOOP)();
        return 1u;
    }
    return 0u;
}

/* Immediately cancel pending timer, release S2/S1/AFMF/AEL, wake display, restore OSD, stop. */
static void interval_stop(struct interval_state *st, u32 reason) {
    void *tm = get_timer_mgr();
    if (tm && st->active_rec) {
        CALL(tmr_rem_t, V_TMR_REMOVE)(tm, st->active_rec);
        st->active_rec = (void *)0;
    }
    if (st->cap_held && st->model_rec) {
        CALL(model_cmd_t, V_CAP_STOP_CMD)(st->model_rec, 1u);
        st->cap_held = 0u;
    }
    if (st->af_held && st->model_rec) {
        CALL(model_cmd_t, V_AF_STOP_CMD)(st->model_rec, 0u);
        st->af_held = 0u;
    }
    if (st->afmf_locked && st->model_rec) {
        CALL(model_void_cmd_t, V_AFMF_OFF_CMD)(st->model_rec);
        st->afmf_locked = 0u;
    }
    if (st->ael_locked) {
        send_model_u32("@M00B", EV_SET_AEL, PRM_KEY_AEL, AEL_VAL_OFF);
        st->ael_locked = 0u;
    }
    st->lock_applied = 0u;
    if (st->disp_is_off) {
        set_display_sleep(st, 0u);
    }
    st->running = 0u;
    st->bulb_active = 0u;
    st->bulb_mode_only = 0u;
    st->phase = PHASE_IDLE;
    st->abort_reason = reason;
    st->inactive_ticks = 0u;
    st->busy_ticks = 0u;
    st->disp_recheck = 0u;
    st->peek_active = 0u;
    st->blink_phase = 1u;

    /* Immediately restore solid Drive Mode icon & real SD remaining count on Live View! */
    set_dm_icon_visible(st, 1u);
    refresh_remain_osd(st);
}

/* Schedule next wake slice toward `st->next_shot_ms`, capped at POLL_SLICE_MS (500ms)
 * so turning Interval Shoot Off, Playback abort, icon blink, peek expiry, & APO reload stay snappy. */
static void schedule_wait_slot(struct interval_state *st) {
    st->phase = PHASE_WAIT_SLOT;
    u64 cur = now_ms();
    u32 delta = 1u;
    if (st->next_shot_ms > cur) {
        u64 rem = st->next_shot_ms - cur;
        delta = (rem > (u64)POLL_SLICE_MS) ? POLL_SLICE_MS : (u32)rem;
    }
    if (!schedule_after_ms(st, delta)) {
        interval_stop(st, 4u);
    }
}

static void schedule_bulb_hold(struct interval_state *st, u64 cur_t) {
    st->phase = PHASE_BULB_HOLD;
    u32 delta = 1u;
    if (st->bulb_end_ms > cur_t) {
        u64 rem = st->bulb_end_ms - cur_t;
        delta = (rem > (u64)POLL_SLICE_MS) ? POLL_SLICE_MS : (u32)rem;
    }
    if (!schedule_after_ms(st, delta)) {
        interval_stop(st, 4u);
    }
}

/* Record shot completion, refresh remaining count, apply post-shot-1 lock/sleep, arm next slot. */
static void after_shot_fired(struct interval_state *st) {
    u64 fire_t = st->last_fire_ms;
    st->shots_taken = st->shots_taken + 1u;

    if (st->shots_taken == 1u) {
        /* Shot #1 fired after Start Delay > 0 -> anchor monotonic grid to shot start! */
        st->anchor_ms = fire_t;
        st->seq_idx = 1u;
    } else {
        /* Zero-drift monotonic grid; if a long exposure pushed past the next slot,
         * re-anchor cleanly so we never burst-spam catch-up shots. */
        u64 next_grid = st->anchor_ms + (u64)(st->seq_idx + 1u) * (u64)st->interval_s * 1000ULL;
        if (fire_t >= next_grid) {
            st->anchor_ms = fire_t;
            st->seq_idx = 1u;
        } else {
            st->seq_idx = st->seq_idx + 1u;
        }
    }

    refresh_remain_osd(st);

    if (st->max_shots != 0u && st->shots_taken >= st->max_shots) {
        if (st->disp_is_off || st->afmf_locked || st->ael_locked) {
            st->phase = PHASE_FINAL_CLEANUP;
            st->busy_ticks = 0u;
            if (!schedule_after_ms(st, 200u)) {
                interval_stop(st, 0u);
            }
        } else {
            interval_stop(st, 0u);
        }
        return;
    }

    st->next_shot_ms = st->anchor_ms + (u64)st->seq_idx * (u64)st->interval_s * 1000ULL;

    if (st->shots_taken == 1u && (st->af_mode != AF_MODE_EACH || st->disp_sleep != 0u)) {
        /* Wait until Shot #1 finishes writing/clearing sc==0, then apply AF/MF(+AEL) lock & ALL OFF! */
        st->phase = PHASE_FIRST_SLEEP;
        st->busy_ticks = 0u;
        if (!schedule_after_ms(st, READY_POLL_MS)) {
            interval_stop(st, 4u);
        }
        return;
    }

    if (st->disp_is_off && !st->peek_active) {
        st->disp_recheck = 1u;
    }
    schedule_wait_slot(st);
}

/* Issue CaptureStart(model, 1) and poll m_StsCapture until BIONZ replies 0xF000 (sc==2). */
static void begin_direct_capture(struct interval_state *st) {
    void *m = st->model_rec;
    if (!m) {
        interval_stop(st, 3u);
        return;
    }
    st->last_fire_ms = now_ms();
    st->busy_ticks = 0u;
    CALL(model_cmd_t, V_CAP_START_CMD)(m, 1u);
    st->cap_held = 1u;

    st->phase = PHASE_CAP_STOP;
    if (!schedule_after_ms(st, CAP_ACK_POLL_MS)) {
        interval_stop(st, 4u);
    }
}

/* Native Sony TimerManager callback — executes on the Main EventLoop thread! */
void interval_timer_cb(void *rec, struct timespec32 *fired_ts, void *arg) {
    (void)fired_ts;
    struct interval_state *st = (struct interval_state *)arg;
    if (!st) {
        st = get_state();
    }

    /* CRITICAL (verified in Sony's TimerEvent callback @ 0x00af486e):
     * TimerManager::fire unlinks `rec` without freeing it; caller MUST invoke
     * TimerManager::remove(tm, rec) to free the 24-byte timeCallBackRecord! */
    void *tm = get_timer_mgr();
    if (st->active_rec == rec) {
        st->active_rec = (void *)0;
    }
    if (tm && rec) {
        CALL(tmr_rem_t, V_TMR_REMOVE)(tm, rec);
    }

    if (!st->running) {
        return;
    }

    st->cb_count++;
    u64 cur_t = now_ms();

    /* Check user menu disable */
    if (st->bulb_mode_only) {
        if (bk_read_u8(BK_EXT_SHUTTER) != 1u) {
            interval_stop(st, 2u);
            return;
        }
    } else {
        if (bk_read_u8(BK_ENABLE) != 1u) {
            interval_stop(st, 2u);
            return;
        }
    }

    /* Check Playback / Movie / Standby (ModelStillRec state <= 2 for >= 1.5s) */
    if (read_model_state(st) <= 2u) {
        u32 inc = (st->phase == PHASE_WAIT_SLOT || st->phase == PHASE_BULB_HOLD) ? 10u : 1u;
        st->inactive_ticks += inc;
        if (st->inactive_ticks >= 30u) {
            interval_stop(st, 3u);
            return;
        }
    } else {
        st->inactive_ticks = 0u;
    }

    /* Keep Auto Power Off / Power Save timer alive during long intervals & timed BULB */
    keepalive_apo(st, cur_t, 0u);

    /* Drive steady ~1 Hz Live View Drive Mode waiting icon blink */
    update_waiting_blink(st, cur_t);

    /* Refresh live seconds countdown in ACT_LabelStillRemain during standalone timed BULB */
    if (st->bulb_mode_only && st->phase == PHASE_BULB_HOLD) {
        u32 rem_s = bulb_remaining_sec(st, cur_t);
        if (rem_s != st->bulb_last_osd_sec) {
            st->bulb_last_osd_sec = rem_s;
            refresh_remain_osd(st);
        }
    }

    /* Re-sleep display automatically once 5-second S1 peek window expires */
    if (st->disp_is_off && st->peek_active && cur_t >= st->peek_until_ms) {
        send_model_u32("@M006", EV_PANEL_EVF_SWITCH, PRM_KEY_PANEL_EVF, DISP_ALL_OFF);
        st->peek_active = 0u;
        st->disp_recheck = 0u;
    }

    u32 ph = st->phase;

    if (ph == PHASE_FIRST_SLEEP) {
        /* Wait until Shot #1 completes (`is_still_ready`) so BIONZ cleanly accepts
         * AF/MF Hold, AE Lock, and Monitor Sleep ALL OFF without BUSY rejection. */
        if (!is_still_ready(st)) {
            if (++st->busy_ticks >= MAX_BUSY_TICKS) {
                interval_stop(st, 4u);
                return;
            }
            if (!schedule_after_ms(st, READY_POLL_MS)) {
                interval_stop(st, 4u);
            }
            return;
        }
        st->busy_ticks = 0u;
        if (st->max_shots != 0u && st->shots_taken >= st->max_shots) {
            interval_stop(st, 0u);
            return;
        }
        if (st->af_mode != AF_MODE_EACH && !st->lock_applied) {
            engage_first_shot_lock(st);
        }
        if (st->disp_sleep && !st->disp_is_off) {
            set_display_sleep(st, 1u);
        }
        schedule_wait_slot(st);
        return;
    }

    if (ph == PHASE_WAIT_SLOT) {
        /* If display woke briefly (e.g. Auto Review after Shot #2..N), restore ALL OFF once ready */
        if (st->disp_is_off && !st->peek_active && st->disp_recheck && is_still_ready(st)) {
            st->disp_recheck = 0u;
            if (get_panel_evf_status() != (u32)DISP_ALL_OFF) {
                send_model_u32("@M006", EV_PANEL_EVF_SWITCH, PRM_KEY_PANEL_EVF, DISP_ALL_OFF);
            }
        }
        if (cur_t < st->next_shot_ms) {
            schedule_wait_slot(st);
            return;
        }
        /* Deadline reached! Proceed directly into readiness check. */
        st->phase = PHASE_WAIT_READY;
        st->busy_ticks = 0u;
        ph = PHASE_WAIT_READY;
    }

    if (ph == PHASE_WAIT_READY) {
        if (!is_still_ready(st)) {
            if (++st->busy_ticks >= MAX_BUSY_TICKS) {
                interval_stop(st, 4u);
                return;
            }
            if (!schedule_after_ms(st, READY_POLL_MS)) {
                interval_stop(st, 4u);
            }
            return;
        }
        st->busy_ticks = 0u;
        if (st->af_mode == AF_MODE_EACH) {
            CALL(model_cmd_t, V_AF_START_CMD)(st->model_rec, 0u);
            st->af_held = 1u;
            st->phase = PHASE_WAIT_AF;
            if (!schedule_after_ms(st, 250u)) {
                interval_stop(st, 4u);
            }
            return;
        }
        /* Lock AF+AE or Lock AF Only: ensure MF hold is active on Shots #2..N */
        if (st->shots_taken >= 1u) {
            if (!st->lock_applied) {
                engage_first_shot_lock(st);
            } else if (st->afmf_locked && CALL(get_focus_mode_t, V_GET_FOCUS_MODE)() != FOCUS_MODE_MF) {
                CALL(model_void_cmd_t, V_AFMF_ON_CMD)(st->model_rec);
            }
        }
        begin_direct_capture(st);
        return;
    }

    if (ph == PHASE_WAIT_AF) {
        begin_direct_capture(st);
        return;
    }

    if (ph == PHASE_CAP_STOP) {
        void *m = st->model_rec;
        if (st->cap_held && m) {
            u32 sc_pre = *(const volatile u32 *)((const u8 *)m + MODEL_OFF_STS_CAP);
            /* CRITICAL (proven on real ILCE-7R hardware):
             * BIONZ RTOS holds m_StsCapture == 1 while preparing AF/AE/shutter (160-400ms),
             * then sets m_StsCapture = 2 upon 0x8503 (0xF000 = shutter released!).
             * Wait until sc != 1 (up to 3000ms safety timeout) before issuing CaptureStop. */
            if (sc_pre == 1u && ++st->busy_ticks < MAX_CAP_ACK_TICKS) {
                if (!schedule_after_ms(st, CAP_ACK_POLL_MS)) {
                    interval_stop(st, 4u);
                }
                return;
            }
            st->busy_ticks = 0u;
            if (st->bulb_active && sc_pre != 0u) {
                st->bulb_start_ms = cur_t;
                st->bulb_end_ms = cur_t + (u64)st->bulb_duration_s * 1000ULL;
                st->bulb_last_osd_sec = st->bulb_duration_s;
                refresh_remain_osd(st);
                schedule_bulb_hold(st, cur_t);
                return;
            }
            if (sc_pre != 0u) {
                CALL(model_cmd_t, V_CAP_STOP_CMD)(m, 1u);
            }
            st->cap_held = 0u;
        }
        if (st->af_held && m) {
            CALL(model_cmd_t, V_AF_STOP_CMD)(m, 0u);
            st->af_held = 0u;
        }
        after_shot_fired(st);
        return;
    }

    if (ph == PHASE_BULB_HOLD) {
        void *m = st->model_rec;
        if (m && cur_t >= st->bulb_start_ms + (u64)BULB_EARLY_GUARD_MS) {
            u32 sc_now = *(const volatile u32 *)((const u8 *)m + MODEL_OFF_STS_CAP);
            if (sc_now == 0u) {
                st->cap_held = 0u;
                if (st->af_held) {
                    CALL(model_cmd_t, V_AF_STOP_CMD)(m, 0u);
                    st->af_held = 0u;
                }
                if (st->bulb_mode_only) {
                    interval_stop(st, 0u);
                } else {
                    after_shot_fired(st);
                }
                return;
            }
        }
        if (cur_t < st->bulb_end_ms) {
            schedule_bulb_hold(st, cur_t);
            return;
        }
        if (st->cap_held && m) {
            u32 sc_end = *(const volatile u32 *)((const u8 *)m + MODEL_OFF_STS_CAP);
            if (sc_end != 0u) {
                CALL(model_cmd_t, V_CAP_STOP_CMD)(m, 1u);
            }
            st->cap_held = 0u;
        }
        if (st->af_held && m) {
            CALL(model_cmd_t, V_AF_STOP_CMD)(m, 0u);
            st->af_held = 0u;
        }
        if (st->bulb_mode_only) {
            st->shots_taken = 1u;
            interval_stop(st, 0u);
            return;
        }
        after_shot_fired(st);
        return;
    }

    if (ph == PHASE_FINAL_CLEANUP) {
        if (!is_still_ready(st) && ++st->busy_ticks < MAX_BUSY_TICKS) {
            if (!schedule_after_ms(st, READY_POLL_MS)) {
                interval_stop(st, 0u);
            }
            return;
        }
        interval_stop(st, 0u);
        return;
    }

    interval_stop(st, 0u);
}

/* Called from `interval_capstart_trampoline` on `ModelStillRec::pvt_ActionExeCaptureStart`.
 * Returns:
 *   0 -> pass through to original pvt_ActionExeCaptureStart (execute shot now)
 *   1 -> swallow this trigger cleanly (Abort press or Start Delay > 0)
 */
i32 interval_on_capstart(void *model_still_rec) {
    struct interval_state *st = get_state();
    st->model_rec = model_still_rec;
    st->magic = MAGIC_INTV;

    /* User shutter press while sequence is active -> immediate clean abort! */
    if (st->running) {
        if (st->bulb_active) {
            st->swallow_next_capstop = 1u;
        }
        interval_stop(st, 1u);
        return 1;
    }

    u32 intv_on = (bk_read_u8(BK_ENABLE) == 1u) ? 1u : 0u;
    u32 ext_sec = active_ext_shutter_sec(st);

    /* Neither Interval Shoot nor an Extended Shutter step (45"..120") -> 0ms transparent pass-through!
     * (Includes all normal <=30" speeds AND Manual BULB!) */
    if (!intv_on && ext_sec == 0u) {
        return 0;
    }

    void *tm = get_timer_mgr();
    if (!tm) {
        return 0;
    }

    /* Preserve live UI widget pointers, ext_ss_idx, model_cam, & min_ss_synced; reset sequence state! */
    st->af_held              = 0u;
    st->cap_held             = 0u;
    st->disp_is_off          = 0u;
    st->disp_recheck         = 0u;
    st->peek_active          = 0u;
    st->lock_applied         = 0u;
    st->afmf_locked          = 0u;
    st->ael_locked           = 0u;
    st->saved_disp_out       = (u32)DISP_PANEL_ON;
    st->abort_reason         = 0u;
    st->inactive_ticks       = 0u;
    st->busy_ticks           = 0u;
    st->active_rec           = (void *)0;
    st->cb_count             = 0u;
    st->swallow_next_capstop = 0u;
    st->bulb_active          = (ext_sec != 0u) ? 1u : 0u;
    st->bulb_duration_s      = ext_sec;

    u64 t0 = now_ms();
    st->anchor_ms     = t0;
    st->last_fire_ms  = t0;
    st->last_blink_ms = t0;
    st->last_apo_ms   = t0;
    st->peek_until_ms = 0ULL;
    st->blink_phase   = 0u;

    i32 ret_val = 0;

    if (!intv_on) {
        /* Standalone Extended Shutter (45"/60"/80"/100"/120"): 0ms pass-through open, timed close! */
        st->bulb_mode_only    = 1u;
        st->start_delay_s     = 0u;
        st->interval_s        = 1u;
        st->max_shots         = 1u;
        st->af_mode           = AF_MODE_EACH;
        st->disp_sleep        = 0u;
        st->shots_taken       = 0u;
        st->seq_idx           = 0u;
        st->cap_held          = 1u;
        st->bulb_start_ms     = t0;
        st->bulb_end_ms       = t0 + (u64)ext_sec * 1000ULL;
        st->bulb_last_osd_sec = ext_sec;
        st->running           = 1u;
        st->phase             = PHASE_BULB_HOLD;
        if (!schedule_after_ms(st, POLL_SLICE_MS)) {
            st->running = 0u;
            st->bulb_active = 0u;
            st->bulb_mode_only = 0u;
            st->cap_held = 0u;
            st->phase = PHASE_IDLE;
        }
        ret_val = 0;
    } else {
        /* Interval Shooting (optionally using 45"..120" Extended Shutter on each shot!) */
        u32 d_idx = bk_read_u8(BK_DELAY);
        u32 i_idx = bk_read_u8(BK_INTERVAL);
        u32 s_idx = bk_read_u8(BK_SHOTS);
        u32 af    = bk_read_u8(BK_AF_MODE);
        u32 dslp  = bk_read_u8(BK_DISP_SLEEP);

        st->bulb_mode_only = 0u;
        st->start_delay_s  = (d_idx < 6u)  ? DELAY_SEC[d_idx]    : 0u;
        st->interval_s     = (i_idx < 10u) ? INTERVAL_SEC[i_idx] : 1u;
        st->max_shots      = (s_idx < 9u)  ? SHOTS_CNT[s_idx]    : 30u;
        st->af_mode        = (af <= 2u)    ? af                  : AF_MODE_LOCK_AFAE;
        st->disp_sleep     = (dslp == 1u)  ? 1u                  : 0u;
        st->running        = 1u;

        if (st->start_delay_s == 0u) {
            if (ext_sec != 0u) {
                st->shots_taken   = 0u;
                st->seq_idx       = 0u;
                st->cap_held      = 1u;
                st->bulb_start_ms = t0;
                st->bulb_end_ms   = t0 + (u64)ext_sec * 1000ULL;
                st->phase         = PHASE_BULB_HOLD;
                if (!schedule_after_ms(st, POLL_SLICE_MS)) {
                    st->running = 0u;
                    st->bulb_active = 0u;
                    st->cap_held = 0u;
                    st->phase = PHASE_IDLE;
                }
            } else {
                /* Shot #1 passes straight through synchronously (0.0ms shutter latency!). */
                st->shots_taken  = 1u;
                st->seq_idx      = 1u;
                st->next_shot_ms = t0 + (u64)st->interval_s * 1000ULL;
                st->phase        = PHASE_FIRST_SLEEP;
                if (!schedule_after_ms(st, 150u)) {
                    st->running = 0u;
                    st->phase = PHASE_IDLE;
                    st->shots_taken = 0u;
                }
            }
            ret_val = 0;
        } else {
            /* Start Delay > 0: swallow initial press; TimerManager fires shot #1 after countdown. */
            st->shots_taken  = 0u;
            st->seq_idx      = 0u;
            st->next_shot_ms = t0 + (u64)st->start_delay_s * 1000ULL;
            st->phase        = PHASE_WAIT_SLOT;
            u64 rem = st->next_shot_ms - t0;
            u32 first_ms = (rem > (u64)POLL_SLICE_MS) ? POLL_SLICE_MS : (u32)rem;
            if (!schedule_after_ms(st, first_ms)) {
                st->running = 0u;
                st->bulb_active = 0u;
                st->phase = PHASE_IDLE;
                ret_val = 0;
            } else {
                ret_val = 1;
            }
        }
    }

    if (st->running) {
        /* Reload Auto Power Off timer & trigger immediate visual confirmation! */
        keepalive_apo(st, t0, 1u);
        set_dm_icon_visible(st, 0u);
        refresh_remain_osd(st);
    }

    return ret_val;
}

/* ================= Thumb-2 PIC Trampolines ================= */

__asm__(
    ".text\n"
    ".syntax unified\n"
    ".thumb\n"

    /* 1. CaptureStart hook @ 0x00723c24 (replaces push {r4..r7,lr}; sub sp, #0x5c) */
    ".global interval_capstart_trampoline\n"
    ".type interval_capstart_trampoline, %function\n"
    ".thumb_func\n"
    "interval_capstart_trampoline:\n"
    "  push {r0, r1, r2, r3, r4, lr}\n"  /* 24 bytes -> preserves 8-byte SP alignment */
    "  bl interval_on_capstart\n"
    "  cmp r0, #0\n"
    "  bne 1f\n"
    "  pop {r0, r1, r2, r3, r4, lr}\n"
    "  push {r4, r5, r6, r7, lr}\n"      /* Replicated original prologue @ 0x723c24 */
    "  sub sp, #0x5c\n"                  /* Replicated original prologue @ 0x723c26 */
    "  ldr r12, 2f\n"
    "3:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    "1:\n"
    "  pop {r0, r1, r2, r3, r4, pc}\n"   /* Swallow shot cleanly (void return) */
    ".p2align 2\n"
    "2:\n"
    "  .word V_CAPSTART_CONT - (3b + 4)\n"

    /* 2. FocusStart (S1 half-press) hook @ 0x00722300 (replaces 90 b5 89 b0 = push {r4,r7,lr}; sub sp,#0x24) */
    ".global interval_focstart_trampoline\n"
    ".type interval_focstart_trampoline, %function\n"
    ".thumb_func\n"
    "interval_focstart_trampoline:\n"
    "  push {r0, r1, r2, r3, r4, lr}\n"  /* 24 bytes -> preserves 8-byte SP alignment */
    "  bl interval_on_focus_start\n"
    "  pop {r0, r1, r2, r3, r4, lr}\n"
    "  push {r4, r7, lr}\n"              /* Replicated original prologue @ 0x722300 */
    "  sub sp, #0x24\n"                  /* Replicated original prologue @ 0x722302 */
    "  ldr r12, 30f\n"
    "31:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "30:\n"
    "  .word V_FOCSTART_CONT - (31b + 4)\n"

    /* 3. Original CmnViewProcessDataMgr::setValue continuation (replaces 98 b5 00 af) */
    ".global orig_pdm_setValue\n"
    ".type orig_pdm_setValue, %function\n"
    ".thumb_func\n"
    "orig_pdm_setValue:\n"
    "  push {r3, r4, r7, lr}\n"
    "  add r7, sp, #0\n"
    "  ldr r12, 4f\n"
    "5:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "4:\n"
    "  .word V_PDM_SETVAL_CONT - (5b + 4)\n"

    /* 4. Original CmnViewProcessDataMgr::getValue continuation (replaces 98 b5 00 af) */
    ".global orig_pdm_getValue\n"
    ".type orig_pdm_getValue, %function\n"
    ".thumb_func\n"
    "orig_pdm_getValue:\n"
    "  push {r3, r4, r7, lr}\n"
    "  add r7, sp, #0\n"
    "  ldr r12, 6f\n"
    "7:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "6:\n"
    "  .word V_PDM_GETVAL_CONT - (7b + 4)\n"

    /* 5. Zero-overhead remap trampoline: isValueInvalid @ 0x00841756 */
    ".global pdm_isInvalid_trampoline\n"
    ".type pdm_isInvalid_trampoline, %function\n"
    ".thumb_func\n"
    "pdm_isInvalid_trampoline:\n"
    "  cmp r1, #7\n"
    "  bne 8f\n"
    "  cmp r2, #0x24\n"
    "  it eq\n"
    "  moveq r2, #0\n"
    "8:\n"
    "  push {r3, r4, r7, lr}\n"
    "  add r7, sp, #0\n"
    "  ldr r12, 9f\n"
    "10:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "9:\n"
    "  .word V_PDM_INVALID_CONT - (10b + 4)\n"

    /* 6. Zero-overhead remap trampoline: isValueDisable @ 0x00841782 */
    ".global pdm_isDisable_trampoline\n"
    ".type pdm_isDisable_trampoline, %function\n"
    ".thumb_func\n"
    "pdm_isDisable_trampoline:\n"
    "  cmp r1, #7\n"
    "  bne 11f\n"
    "  cmp r2, #0x24\n"
    "  it eq\n"
    "  moveq r2, #0\n"
    "11:\n"
    "  push {r3, r4, r7, lr}\n"
    "  add r7, sp, #0\n"
    "  ldr r12, 12f\n"
    "13:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "12:\n"
    "  .word V_PDM_DISABLE_CONT - (13b + 4)\n"

    /* 7. Zero-overhead remap trampoline: isValueGrayedOut @ 0x008417ae */
    ".global pdm_isGrayedOut_trampoline\n"
    ".type pdm_isGrayedOut_trampoline, %function\n"
    ".thumb_func\n"
    "pdm_isGrayedOut_trampoline:\n"
    "  cmp r1, #7\n"
    "  bne 14f\n"
    "  cmp r2, #0x24\n"
    "  it eq\n"
    "  moveq r2, #0\n"
    "14:\n"
    "  push {r3, r4, r7, lr}\n"
    "  add r7, sp, #0\n"
    "  ldr r12, 15f\n"
    "16:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "15:\n"
    "  .word V_PDM_GRAY_CONT - (16b + 4)\n"

    /* 8. Zero-overhead remap trampoline: getValueCaution @ 0x008417da (replaces f8 b5 00 af) */
    ".global pdm_getCaution_trampoline\n"
    ".type pdm_getCaution_trampoline, %function\n"
    ".thumb_func\n"
    "pdm_getCaution_trampoline:\n"
    "  cmp r1, #7\n"
    "  bne 17f\n"
    "  cmp r2, #0x24\n"
    "  it eq\n"
    "  moveq r2, #0\n"
    "17:\n"
    "  push {r3, r4, r5, r6, r7, lr}\n"
    "  add r7, sp, #0\n"
    "  ldr r12, 18f\n"
    "19:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "18:\n"
    "  .word V_PDM_CAUTION_CONT - (19b + 4)\n"

    /* 9. ACT_DriveMode OSD icon hook @ 0x009651ee (replaces ldr.w r1, [r5, #0x588]) */
    ".global interval_act_drivemode_trampoline\n"
    ".type interval_act_drivemode_trampoline, %function\n"
    ".thumb_func\n"
    "interval_act_drivemode_trampoline:\n"
    "  push {r0, r2, r3, lr}\n"
    "  ldr.w r0, [r5, #0x588]\n"
    "  ldr.w r1, [r5, #0x58c]\n"
    "  mov r2, r5\n"
    "  bl interval_act_drivemode_icon\n"
    "  mov r1, r0\n"
    "  pop {r0, r2, r3, lr}\n"
    "  ldr r12, 20f\n"
    "21:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "20:\n"
    "  .word V_ACT_DM_CONT - (21b + 4)\n"

    /* 10. Original ModelStillRec::pvt_SetSpinalReflexCmd continuation (replaces b0 b5 00 af) */
    ".global orig_set_spinal_reflex\n"
    ".type orig_set_spinal_reflex, %function\n"
    ".thumb_func\n"
    "orig_set_spinal_reflex:\n"
    "  push {r4, r5, r7, lr}\n"
    "  add r7, sp, #0\n"
    "  ldr r12, 22f\n"
    "23:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "22:\n"
    "  .word V_SPINAL_CONT - (23b + 4)\n"

    /* 11. ACT_LabelStillRemain::0x00924b7c count hook @ 0x00924d9a (replaces add.w r4, r5, #0x1ac) */
    ".global interval_remain_a_trampoline\n"
    ".type interval_remain_a_trampoline, %function\n"
    ".thumb_func\n"
    "interval_remain_a_trampoline:\n"
    "  push {r1, r2, r3, lr}\n"
    "  mov r1, r5\n"
    "  bl interval_remain_override\n"
    "  mov r6, r0\n"
    "  pop {r1, r2, r3, lr}\n"
    "  add.w r4, r5, #0x1ac\n"
    "  ldr r12, 24f\n"
    "25:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "24:\n"
    "  .word V_REMAIN_A_CONT - (25b + 4)\n"

    /* 12. ACT_LabelStillRemain::0x00924634 count hook @ 0x00924826 (replaces add.w r4, r5, #0x1ac) */
    ".global interval_remain_b_trampoline\n"
    ".type interval_remain_b_trampoline, %function\n"
    ".thumb_func\n"
    "interval_remain_b_trampoline:\n"
    "  push {r1, r2, r3, lr}\n"
    "  mov r1, r5\n"
    "  bl interval_remain_override\n"
    "  mov r6, r0\n"
    "  pop {r1, r2, r3, lr}\n"
    "  add.w r4, r5, #0x1ac\n"
    "  ldr r12, 26f\n"
    "27:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "26:\n"
    "  .word V_REMAIN_B_CONT - (27b + 4)\n"

    /* 13. Original ~ACT_LabelStillRemain continuation @ 0x00918a84 (replaces f8 b5 00 af) */
    ".global orig_remain_dtor\n"
    ".type orig_remain_dtor, %function\n"
    ".thumb_func\n"
    "orig_remain_dtor:\n"
    "  push {r3, r4, r5, r6, r7, lr}\n"
    "  add r7, sp, #0\n"
    "  ldr r12, 28f\n"
    "29:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "28:\n"
    "  .word V_REMAIN_DTOR_CONT - (29b + 4)\n"

    /* 14. ViewSettingMenu::onTransition hook @ 0x0080da7c (replaces 2d e9 f0 41 = push.w {r4-r8, lr}) */
    ".global vsm_onTransition_trampoline\n"
    ".type vsm_onTransition_trampoline, %function\n"
    ".thumb_func\n"
    "vsm_onTransition_trampoline:\n"
    "  push {r0, r1, r2, r3, r4, lr}\n"
    "  bl vsm_on_transition_check\n"
    "  cmp r0, #0\n"
    "  bne 50f\n"
    "  pop {r0, r1, r2, r3, r4, lr}\n"
    "  push.w {r4, r5, r6, r7, r8, lr}\n"
    "  ldr r12, 51f\n"
    "52:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    "50:\n"
    "  pop {r0, r1, r2, r3, r4, pc}\n"
    ".p2align 2\n"
    "51:\n"
    "  .word V_VSM_ONTRANS_CONT - (52b + 4)\n"

    /* 15. ViewSettingMenu::initMenuNode hook @ 0x0080cf84 (replaces f0 b5 8b b0 = push {r4-r7, lr}; sub sp, #0x2c) */
    ".global vsm_initMenuNode_trampoline\n"
    ".type vsm_initMenuNode_trampoline, %function\n"
    ".thumb_func\n"
    "vsm_initMenuNode_trampoline:\n"
    "  push {r0, r1, r2, r3, r4, lr}\n"
    "  bl vsm_init_menu_node_check\n"
    "  cmp r0, #0\n"
    "  bne 53f\n"
    "  pop {r0, r1, r2, r3, r4, lr}\n"
    "  push {r4, r5, r6, r7, lr}\n"
    "  sub sp, #0x2c\n"
    "  ldr r12, 54f\n"
    "55:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    "53:\n"
    "  pop {r0, r1, r2, r3, r4, pc}\n"
    ".p2align 2\n"
    "54:\n"
    "  .word V_VSM_INITNODE_CONT - (55b + 4)\n"

    /* 16. CaptureStop hook @ 0x00721b90 (replaces 90 b5 89 b0 = push {r4,r7,lr}; sub sp,#0x24) */
    ".global interval_capstop_trampoline\n"
    ".type interval_capstop_trampoline, %function\n"
    ".thumb_func\n"
    "interval_capstop_trampoline:\n"
    "  push {r0, r1, r2, r3, r4, lr}\n"
    "  bl interval_on_capstop\n"
    "  cmp r0, #0\n"
    "  bne 32f\n"
    "  pop {r0, r1, r2, r3, r4, lr}\n"
    "  push {r4, r7, lr}\n"
    "  sub sp, #0x24\n"
    "  ldr r12, 33f\n"
    "34:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    "32:\n"
    "  pop {r0, r1, r2, r3, r4, pc}\n"
    ".p2align 2\n"
    "33:\n"
    "  .word V_CAPSTOP_CONT - (34b + 4)\n"

    /* 17. ModelCamera::pvt_ActionShiftShutterSpeed hook @ 0x00551474 (replaces b0 b5 0c 46) */
    ".global ext_ss_shift_trampoline\n"
    ".type ext_ss_shift_trampoline, %function\n"
    ".thumb_func\n"
    "ext_ss_shift_trampoline:\n"
    "  push {r0, r1, r2, r3, r4, lr}\n"
    "  bl ext_ss_on_shift\n"
    "  cmp r0, #0\n"
    "  bne 35f\n"
    "  pop {r0, r1, r2, r3, r4, lr}\n"
    "  push {r4, r5, r7, lr}\n"
    "  mov r4, r1\n"
    "  ldr r12, 36f\n"
    "37:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    "35:\n"
    "  pop {r0, r1, r2, r3, r4, pc}\n"
    ".p2align 2\n"
    "36:\n"
    "  .word V_SHIFT_SS_CONT - (37b + 4)\n"

    /* 18. ModelCamera info 0x35 numerator hook @ 0x0053ecc6 (replaces ldr.w r1, [r5, #0x200]) */
    ".global ext_ss_num_trampoline\n"
    ".type ext_ss_num_trampoline, %function\n"
    ".thumb_func\n"
    "ext_ss_num_trampoline:\n"
    "  push {r0, r2, r3, lr}\n"
    "  mov r0, r5\n"
    "  bl ext_ss_override_num\n"
    "  mov r1, r0\n"
    "  pop {r0, r2, r3, lr}\n"
    "  ldr r12, 38f\n"
    "39:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "38:\n"
    "  .word V_INFO35_NUM_CONT - (39b + 4)\n"

    /* 19. ModelCamera info 0x35 denominator hook @ 0x0053ecd6 (replaces ldr.w r1, [r5, #0x204]) */
    ".global ext_ss_den_trampoline\n"
    ".type ext_ss_den_trampoline, %function\n"
    ".thumb_func\n"
    "ext_ss_den_trampoline:\n"
    "  push {r0, r2, r3, lr}\n"
    "  mov r0, r5\n"
    "  bl ext_ss_override_den\n"
    "  mov r1, r0\n"
    "  pop {r0, r2, r3, lr}\n"
    "  ldr r12, 40f\n"
    "41:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "40:\n"
    "  .word V_INFO35_DEN_CONT - (41b + 4)\n"

    /* 20. fast_disp_get Slot 1 hook @ 0x0050e9c4 (replaces blx memcpy @ 0x001964f8)
     * Preserves r4 (lock flag), r5 (slot), r6 (dst buf); overrides Slot 1 shutter speed! */
    ".global fast_disp_get_trampoline\n"
    ".type fast_disp_get_trampoline, %function\n"
    ".thumb_func\n"
    "fast_disp_get_trampoline:\n"
    "  push {r4, lr}\n"
    "  ldr r12, 42f\n"
    "43:\n"
    "  add r12, pc\n"
    "  blx r12\n"
    "  cmp r5, #1\n"
    "  bne 44f\n"
    "  mov r0, r6\n"
    "  bl ext_ss_override_fast_disp\n"
    "44:\n"
    "  pop {r4, lr}\n"
    "  ldr r12, 45f\n"
    "46:\n"
    "  add r12, pc\n"
    "  bx r12\n"
    ".p2align 2\n"
    "42:\n"
    "  .word P_MEMCPY - (43b + 4)\n"
    "45:\n"
    "  .word V_FAST_DISP_CONT - (46b + 4)\n"
);
