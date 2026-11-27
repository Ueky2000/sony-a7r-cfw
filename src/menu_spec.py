"""Sony ILCE-7R (A7R1) v3.20 Unified CFW Spec (Release v3.33):
- Native Interval Shooting (Drive Mode + Tab 2 P3 Item 5 + Fn Menu Slot 35)
- Auto ISO Min. Shutter Speed (BIONZ 60fps AE + Tab 2 P3 Item 6 + Fn Menu Slot 29)
- Native Extended Shutter Speed & Timed BULB (30"->45"/60"/80"/100"/120"->BULB + Tab 2 P5 Item 5)
- Playback Zoom Control Wheel (ViewZoomPb CCW=Zoom Out, CW=Zoom In)

Single source of truth shared by:
- src/patch_uxc.py            (multilingual UXC string table patching)
- src/patch_caution.py        (CautionConfig.so CmnViewSettingNode hierarchy + Fn Menu slots)
- src/patch_view_drivemode.py (viewUnified4.so Drive Mode + Fn slots 29 & 35 + sorted BST)
- src/patch_view_zoompb.py    (viewUnified3.so Playback Zoom Control Wheel CCW/CW)
- src/patch_libobj.py         (libObj.so Tab 2 P3/P5 + PID 0xE4..0xEC + unified Thumb-2 engine)
- src/patch_avcam.py          (nflasha3 av-cam.bin 2-block in-place CalcHandShakeTv patch)
- src/interval_engine.c       (compiled Thumb-2 PIC engine for all features)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingSpec:
    key: str
    pid: int
    backup_id: int
    title_key: str
    help_key: str
    options: tuple[tuple[str, int], ...]  # (text_key, runtime_value)
    backup_bytes: tuple[int, ...] | None = None  # optional explicit Backup byte per option index


# Expanded ProcessIds in libObj.so (stock N_ORIG = 0xE4: PIDs 0x00..0xE3)
N_ORIG_PIDS = 0xE4
PID_MAX = 0xEF  # Fits directly in 2-byte Thumb `cmp rN, #0xef`!

# Drive Mode ProcessId & values (verified in libObj.so WrapperDriveMode / CustomDriveMode)
PID_DRIVEMODE = 0x07
DM_VAL_SINGLE = 0x00
DM_VAL_INTERVAL = 0x24  # Reuses hidden Remote Cdr slot (WRAPPER_DRIVE_MODE 0x2e, menu_id 0x0c)
PID_ENABLE = 0xE4
PID_FN_INTV = 0xEA      # Single-slot Fn shortcut launcher -> view/SETTINGMENUX (bootElement=0x77)
PID_MIN_SS = 0xEB       # Auto ISO Min. Shutter Speed (Tab 2 P3 Item 6 + Fn Menu slot 29)
PID_EXT_SHUTTER = 0xEC  # Extended Shutter Speed & BULB toggle (Tab 2 P5 Item 5)
VSM_BOOT_INTV = 0x77    # Custom ViewSettingMenu bootElement jumping straight into Folder Layout 5
VSM_BOOT_MIN_SS = 0x78  # Custom ViewSettingMenu bootElement opening ISO AUTO Min. SS 24-option popup in Layout 1
BK_MIN_SS = 0x010700D0        # Verified free 1-byte writable attr=0x50 (resetData=0 -> Standard)
BK_EXT_SHUTTER = 0x01070147   # Verified free 1-byte writable attr=0x50 (0 refs across all 1,078 fw320 files; resetData=0 -> Off)

# Verified unused 1-byte writable Backup.bin IDs (subsystem 0x0107, zero default,
# unreferenced in both libObj.so and av-cam.bin).
SETTINGS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="enable",
        pid=0xE4,
        backup_id=0x010700F6,
        title_key="s_enable",
        help_key="h_enable",
        options=(
            ("opt_off", 0),
            ("opt_on", 1),
        ),
    ),
    SettingSpec(
        key="delay",
        pid=0xE5,
        backup_id=0x010700F7,
        title_key="s_delay",
        help_key="h_delay",
        options=(
            ("opt_0s", 0),
            ("opt_2s", 2),
            ("opt_5s", 5),
            ("opt_10s", 10),
            ("opt_30s", 30),
            ("opt_60s", 60),
        ),
    ),
    SettingSpec(
        key="interval",
        pid=0xE6,
        backup_id=0x0107003C,
        title_key="s_interval",
        help_key="h_interval",
        options=(
            ("opt_1s", 1),
            ("opt_2s", 2),
            ("opt_3s", 3),
            ("opt_5s", 5),
            ("opt_10s", 10),
            ("opt_15s", 15),
            ("opt_20s", 20),
            ("opt_30s", 30),
            ("opt_45s", 45),
            ("opt_60s", 60),
        ),
    ),
    SettingSpec(
        key="shots",
        pid=0xE7,
        backup_id=0x0107003F,
        title_key="s_shots",
        help_key="h_shots",
        options=(
            ("opt_30", 30),
            ("opt_60", 60),
            ("opt_100", 100),
            ("opt_120", 120),
            ("opt_240", 240),
            ("opt_300", 300),
            ("opt_500", 500),
            ("opt_999", 999),
            ("opt_unlim", 0),  # 0 = Unlimited
        ),
    ),
    SettingSpec(
        key="af_mode",
        pid=0xE8,
        backup_id=0x0107007E,
        title_key="s_af_mode",
        help_key="h_af_mode",
        options=(
            ("opt_af_lock", 0),     # Lock AF + AE on 1st shot
            ("opt_af_only", 1),     # Lock AF only, Auto AE each shot (Holy Grail sunrise/sunset)
            ("opt_af_each", 2),     # Re-focus + re-meter each shot (S1+S2)
        ),
    ),
    SettingSpec(
        key="disp_sleep",
        pid=0xE9,
        backup_id=0x01070121,  # Verified 0-ref 1-byte attr=0x50 slot (migrated off colliding 0x010700C2)
        title_key="s_disp_sleep",
        help_key="h_disp_sleep",
        options=(
            ("opt_off", 0),
            ("opt_on", 1),
        ),
    ),
)

# Standalone Tab 2 Page 3 Item 6 + Fn Menu Slot 29: Auto ISO Min. Shutter Speed (24 options)
# Backup byte 0 maps to index 2 ("Standard") so fresh / factory-reset Backup.bin defaults cleanly!
MIN_SS_SPEC = SettingSpec(
    key="auto_iso_min_ss",
    pid=PID_MIN_SS,
    backup_id=BK_MIN_SS,
    title_key="s_min_ss",
    help_key="h_min_ss",
    options=(
        ("opt_ss_slowest", 0x8000FE00),  # Auto -2 EV around 1/f
        ("opt_ss_slower",  0x8000FF00),  # Auto -1 EV around 1/f
        ("opt_ss_std",     0x80000000),  # Auto  0 EV around 1/f (DEFAULT: Backup byte 0)
        ("opt_ss_faster",  0x80000100),  # Auto +1 EV around 1/f
        ("opt_ss_fastest", 0x80000200),  # Auto +2 EV around 1/f
        ("opt_ss_1_8000",  0x80011D00),
        ("opt_ss_1_4000",  0x80011C00),
        ("opt_ss_1_2000",  0x80011B00),
        ("opt_ss_1_1000",  0x80011A00),
        ("opt_ss_1_500",   0x80011900),
        ("opt_ss_1_250",   0x80011800),
        ("opt_ss_1_125",   0x80011700),
        ("opt_ss_1_60",    0x80011600),
        ("opt_ss_1_30",    0x80011500),
        ("opt_ss_1_15",    0x80011400),
        ("opt_ss_1_8",     0x80011300),
        ("opt_ss_1_4",     0x80011200),
        ("opt_ss_1_2",     0x80011100),
        ("opt_ss_1s",      0x80011000),
        ("opt_ss_2s",      0x80010F00),
        ("opt_ss_4s",      0x80010E00),
        ("opt_ss_8s",      0x80010D00),
        ("opt_ss_15s",     0x80010C00),
        ("opt_ss_30s",     0x80010B00),
    ),
    backup_bytes=(1, 2, 0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23),
)

# Standalone Tab 2 Page 5 Item 5 toggle: Extended Shutter Speed & BULB (PID 0xEC, Backup ID 0x01070147)
# Off (0): 100% stock shutter dial (`30" -> BULB`).
# On  (1): Extends shutter dial to `30" -> 45" -> 60" -> 80" -> 100" -> 120" -> BULB`
#          (45"..120" = single-click hands-free timed Bulb; BULB = normal manual hold Bulb).
EXT_SHUTTER_SPEC = SettingSpec(
    key="ext_shutter",
    pid=PID_EXT_SHUTTER,
    backup_id=BK_EXT_SHUTTER,
    title_key="s_ext_shutter",
    help_key="h_ext_shutter",
    options=(
        ("opt_off", 0),
        ("opt_on", 1),
    ),
)

ALL_BACKUP_SETTINGS: tuple[SettingSpec, ...] = SETTINGS + (MIN_SS_SPEC, EXT_SHUTTER_SPEC)

FOLDER_TITLE_KEY = "folder_title"
FOLDER_HELP_KEY = "folder_help"
EXT_SHUTTER_TITLE_KEY = "s_ext_shutter"
DM_TITLE_KEY = "dm_title"
DM_HELP_KEY = "dm_help"

# Localized strings: key -> {lang_code: text}
# Supported explicit translations: en (also fallback), zh_cn, zh_tw, ja
STRINGS: dict[str, dict[str, str]] = {
    "dm_title": {
        "en": "Interval Shoot",
        "zh_cn": "间隔拍摄",
        "zh_tw": "間隔拍攝",
        "ja": "インターバル撮影",
    },
    "dm_help": {
        "en": "Shoots at set intervals (shutter starts/stops; tune in Menu).",
        "zh_cn": "按设定间隔自动拍摄（按快门开始/停止，参数见自定义菜单）。",
        "zh_tw": "按設定間隔自動拍攝（按快門開始/停止，參數見自訂選單）。",
        "ja": "設定間隔で自動撮影（シャッター開始/停止、詳細はメニュー）。",
    },
    "folder_title": {
        "en": "Interval Shoot Func.",
        "zh_cn": "间隔拍摄功能",
        "zh_tw": "間隔拍攝功能",
        "ja": "インターバル撮影機能",
    },
    "folder_help": {
        "en": "Sets native interval shooting (0ms start on shutter press).",
        "zh_cn": "设置原生间隔拍摄参数（按下快门即刻启动，再按停止）。",
        "zh_tw": "設定原生間隔拍攝參數（按下快門即刻啟動，再按停止）。",
        "ja": "ネイティブインターバル撮影を設定します（シャッターで開始/停止）。",
    },
    "s_enable": {
        "en": "Interval Shoot",
        "zh_cn": "间隔拍摄",
        "zh_tw": "間隔拍攝",
        "ja": "インターバル撮影",
    },
    "h_enable": {
        "en": "Turns native interval shooting on or off.",
        "zh_cn": "开启或关闭原生间隔拍摄（开启后按快门开始，再按停止）。",
        "zh_tw": "開啟或關閉原生間隔拍攝（開啟後按快門開始，再按停止）。",
        "ja": "インターバル撮影の入/切を設定します。",
    },
    "s_delay": {
        "en": "Start Delay",
        "zh_cn": "拍摄开始时间",
        "zh_tw": "拍攝開始時間",
        "ja": "撮影開始時間",
    },
    "h_delay": {
        "en": "Sets the delay before the first interval shot.",
        "zh_cn": "设置按下快门后开始第一张拍摄前的延迟时间。",
        "zh_tw": "設定按下快門後開始第一張拍攝前的延遲時間。",
        "ja": "シャッターを押してから1枚目を撮影するまでの待ち時間。",
    },
    "s_interval": {
        "en": "Shooting Interval",
        "zh_cn": "拍摄间隔",
        "zh_tw": "拍攝間隔",
        "ja": "撮影間隔",
    },
    "h_interval": {
        "en": "Sets the interval between consecutive shots.",
        "zh_cn": "设置相邻两张照片之间的拍摄间隔时间。",
        "zh_tw": "設定相鄰兩張照片之間的拍攝間隔時間。",
        "ja": "各撮影間のインターバル時間を設定します。",
    },
    "s_shots": {
        "en": "Number of Shots",
        "zh_cn": "拍摄张数",
        "zh_tw": "拍攝張數",
        "ja": "撮影枚数",
    },
    "h_shots": {
        "en": "Sets total number of shots to capture.",
        "zh_cn": "设置间隔拍摄的总张数（可设无限制）。",
        "zh_tw": "設定間隔拍攝的總張數（可設無限制）。",
        "ja": "インターバル撮影の合計枚数を設定します。",
    },
    "s_af_mode": {
        "en": "AF/AE Tracking",
        "zh_cn": "对焦/测光模式",
        "zh_tw": "對焦/測光模式",
        "ja": "AF/AE追従",
    },
    "h_af_mode": {
        "en": "Lock AF+AE, Lock AF with Auto AE (Holy Grail), or AF/AE Each.",
        "zh_cn": "选锁定对焦+测光、仅锁对焦自动测光(日出日落)，或每张重测。",
        "zh_tw": "選鎖定對焦+測光、僅鎖對焦自動測光(日出日落)，或每張重測。",
        "ja": "AF/AE固定・AFのみ固定(自動露出)・毎コマAF/AEを選択します。",
    },
    "s_disp_sleep": {
        "en": "Monitor Sleep",
        "zh_cn": "拍摄时息屏",
        "zh_tw": "拍攝時息屏",
        "ja": "撮影中画面オフ",
    },
    "h_disp_sleep": {
        "en": "Turns off LCD/EVF during shooting (half-press peeks 5s).",
        "zh_cn": "拍摄期间关闭屏幕省电（半按快门可临时亮屏预览5秒）。",
        "zh_tw": "拍攝期間關閉螢幕省電（半按快門可臨時亮屏預覽5秒）。",
        "ja": "撮影中LCD/EVF消灯（半押しで5秒間プレビュー点灯）。",
    },
    "opt_off": {"en": "Off", "zh_cn": "关", "zh_tw": "關", "ja": "切"},
    "opt_on": {"en": "On", "zh_cn": "开", "zh_tw": "開", "ja": "入"},
    "opt_0s": {"en": "0 s", "zh_cn": "0 秒", "zh_tw": "0 秒", "ja": "0 秒"},
    "opt_1s": {"en": "1 s", "zh_cn": "1 秒", "zh_tw": "1 秒", "ja": "1 秒"},
    "opt_2s": {"en": "2 s", "zh_cn": "2 秒", "zh_tw": "2 秒", "ja": "2 秒"},
    "opt_3s": {"en": "3 s", "zh_cn": "3 秒", "zh_tw": "3 秒", "ja": "3 秒"},
    "opt_5s": {"en": "5 s", "zh_cn": "5 秒", "zh_tw": "5 秒", "ja": "5 秒"},
    "opt_10s": {"en": "10 s", "zh_cn": "10 秒", "zh_tw": "10 秒", "ja": "10 秒"},
    "opt_15s": {"en": "15 s", "zh_cn": "15 秒", "zh_tw": "15 秒", "ja": "15 秒"},
    "opt_20s": {"en": "20 s", "zh_cn": "20 秒", "zh_tw": "20 秒", "ja": "20 秒"},
    "opt_30s": {"en": "30 s", "zh_cn": "30 秒", "zh_tw": "30 秒", "ja": "30 秒"},
    "opt_45s": {"en": "45 s", "zh_cn": "45 秒", "zh_tw": "45 秒", "ja": "45 秒"},
    "opt_60s": {"en": "60 s", "zh_cn": "60 秒", "zh_tw": "60 秒", "ja": "60 秒"},
    "opt_30": {"en": "30", "zh_cn": "30 张", "zh_tw": "30 張", "ja": "30 枚"},
    "opt_60": {"en": "60", "zh_cn": "60 张", "zh_tw": "60 張", "ja": "60 枚"},
    "opt_100": {"en": "100", "zh_cn": "100 张", "zh_tw": "100 張", "ja": "100 枚"},
    "opt_120": {"en": "120", "zh_cn": "120 张", "zh_tw": "120 張", "ja": "120 枚"},
    "opt_240": {"en": "240", "zh_cn": "240 张", "zh_tw": "240 張", "ja": "240 枚"},
    "opt_300": {"en": "300", "zh_cn": "300 张", "zh_tw": "300 張", "ja": "300 枚"},
    "opt_500": {"en": "500", "zh_cn": "500 张", "zh_tw": "500 張", "ja": "500 枚"},
    "opt_999": {"en": "999", "zh_cn": "999 张", "zh_tw": "999 張", "ja": "999 枚"},
    "opt_unlim": {"en": "Unlimited", "zh_cn": "无限制", "zh_tw": "無限制", "ja": "無制限"},
    "opt_af_lock": {
        "en": "Lock AF+AE",
        "zh_cn": "锁定对焦+测光",
        "zh_tw": "鎖定對焦+測光",
        "ja": "AF/AE固定",
    },
    "opt_af_only": {
        "en": "Lock AF (Auto AE)",
        "zh_cn": "仅锁对焦(自动测光)",
        "zh_tw": "僅鎖對焦(自動測光)",
        "ja": "AF固定(自動露出)",
    },
    "opt_af_each": {
        "en": "AF/AE Each",
        "zh_cn": "每张对焦+测光",
        "zh_tw": "每張對焦+測光",
        "ja": "毎回AF/AE",
    },
    # Auto ISO Min. SS (ISO AUTO 最低速度)
    "s_min_ss": {
        "en": "ISO AUTO Min. SS",
        "zh_cn": "ISO AUTO 最低速度",
        "zh_tw": "ISO AUTO 最低速度",
        "ja": "ISO AUTO 低速限界",
    },
    "h_min_ss": {
        "en": "Sets min. shutter speed before Auto ISO rises (P/A mode).",
        "zh_cn": "设置 P/A 档自动 ISO 开始提高前的最低快门速度。",
        "zh_tw": "設定 P/A 檔自動 ISO 開始提高前的最低快門速度。",
        "ja": "P/AモードでISO AUTO感度が上がり始めるシャッター速度。",
    },
    "opt_ss_slowest": {
        "en": "SLOWER (-2)",
        "zh_cn": "更慢 (-2)",
        "zh_tw": "更慢 (-2)",
        "ja": "より低速 (-2)",
    },
    "opt_ss_slower": {
        "en": "SLOW (-1)",
        "zh_cn": "慢 (-1)",
        "zh_tw": "慢 (-1)",
        "ja": "低速 (-1)",
    },
    "opt_ss_std": {
        "en": "STD (Standard)",
        "zh_cn": "标准 (STD)",
        "zh_tw": "標準 (STD)",
        "ja": "標準 (STD)",
    },
    "opt_ss_faster": {
        "en": "FAST (+1)",
        "zh_cn": "快 (+1)",
        "zh_tw": "快 (+1)",
        "ja": "高速 (+1)",
    },
    "opt_ss_fastest": {
        "en": "FASTER (+2)",
        "zh_cn": "更快 (+2)",
        "zh_tw": "更快 (+2)",
        "ja": "より高速 (+2)",
    },
    "opt_ss_1_8000": {"en": "1/8000", "zh_cn": "1/8000", "zh_tw": "1/8000", "ja": "1/8000"},
    "opt_ss_1_4000": {"en": "1/4000", "zh_cn": "1/4000", "zh_tw": "1/4000", "ja": "1/4000"},
    "opt_ss_1_2000": {"en": "1/2000", "zh_cn": "1/2000", "zh_tw": "1/2000", "ja": "1/2000"},
    "opt_ss_1_1000": {"en": "1/1000", "zh_cn": "1/1000", "zh_tw": "1/1000", "ja": "1/1000"},
    "opt_ss_1_500":  {"en": "1/500",  "zh_cn": "1/500",  "zh_tw": "1/500",  "ja": "1/500"},
    "opt_ss_1_250":  {"en": "1/250",  "zh_cn": "1/250",  "zh_tw": "1/250",  "ja": "1/250"},
    "opt_ss_1_125":  {"en": "1/125",  "zh_cn": "1/125",  "zh_tw": "1/125",  "ja": "1/125"},
    "opt_ss_1_60":   {"en": "1/60",   "zh_cn": "1/60",   "zh_tw": "1/60",   "ja": "1/60"},
    "opt_ss_1_30":   {"en": "1/30",   "zh_cn": "1/30",   "zh_tw": "1/30",   "ja": "1/30"},
    "opt_ss_1_15":   {"en": "1/15",   "zh_cn": "1/15",   "zh_tw": "1/15",   "ja": "1/15"},
    "opt_ss_1_8":    {"en": "1/8",    "zh_cn": "1/8",    "zh_tw": "1/8",    "ja": "1/8"},
    "opt_ss_1_4":    {"en": "1/4",    "zh_cn": "1/4",    "zh_tw": "1/4",    "ja": "1/4"},
    "opt_ss_1_2":    {"en": "1/2",    "zh_cn": "1/2",    "zh_tw": "1/2",    "ja": "1/2"},
    "opt_ss_1s":     {"en": '1"',     "zh_cn": '1"',     "zh_tw": '1"',     "ja": '1"'},
    "opt_ss_2s":     {"en": '2"',     "zh_cn": '2"',     "zh_tw": '2"',     "ja": '2"'},
    "opt_ss_4s":     {"en": '4"',     "zh_cn": '4"',     "zh_tw": '4"',     "ja": '4"'},
    "opt_ss_8s":     {"en": '8"',     "zh_cn": '8"',     "zh_tw": '8"',     "ja": '8"'},
    "opt_ss_15s":    {"en": '15"',    "zh_cn": '15"',    "zh_tw": '15"',    "ja": '15"'},
    "opt_ss_30s":    {"en": '30"',    "zh_cn": '30"',    "zh_tw": '30"',    "ja": '30"'},
    # Extended Shutter Speed & BULB (拓展快门速度与BULB)
    "s_ext_shutter": {
        "en": "Extended Shutter & BULB",
        "zh_cn": "拓展快门速度与BULB",
        "zh_tw": "拓展快門速度與BULB",
        "ja": "拡張シャッター速度",
    },
    "h_ext_shutter": {
        "en": "Adds 45s/60s/80s/100s/120s timed steps between 30s and BULB.",
        "zh_cn": "开启后在30秒与BULB间新增45/60/80/100/120秒定时快门挡位。",
        "zh_tw": "開啟後在30秒與BULB間新增45/60/80/100/120秒定時快門檔位。",
        "ja": "30秒とBULBの間に45/60/80/100/120秒を追加します。",
    },
}


def all_string_keys() -> list[str]:
    """Deterministic ordered list of unique string keys used in our menu tree."""
    keys: list[str] = []

    def add(k: str) -> None:
        if k not in keys:
            keys.append(k)

    add(DM_TITLE_KEY)
    add(DM_HELP_KEY)
    add(FOLDER_TITLE_KEY)
    add(FOLDER_HELP_KEY)
    for s in ALL_BACKUP_SETTINGS:
        add(s.title_key)
        add(s.help_key)
        for opt_k, _ in s.options:
            add(opt_k)
    return keys


def lang_for_uxc_filename(filename: str) -> str:
    low = filename.lower()
    if "simplifiedchinese" in low:
        return "zh_cn"
    if "traditionalchinese" in low:
        return "zh_tw"
    if "japanese" in low:
        return "ja"
    return "en"
