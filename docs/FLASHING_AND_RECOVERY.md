# 索尼 A7R (ILCE-7R) 四合一自制固件 (CFW v3.36) — 实机备份、刷机与救砖手册

> [!CAUTION]
> **刷机前必读（重要风险边界说明）**：
> - **CFW v3.36** 整合了全部四大功能（原生间隔拍摄 + 硬件级 60fps 自动 ISO 最低快门速度 + 原生拓展快门速度与 BULB + 回放放大圆盘滚轮缩放与 1.0× 左右键切图），同时修改 `/dev/nflasha15`（Linux 应用层，30 个文件）与 `/dev/nflasha3`（`av-cam.bin` 仅 2 块 121 字节原地补丁；Block 0 与 FAT12 元数据 100% 不变；Bootloader、Updater `nflasha7`、Darwin MCU 与分区表完全零改动）。
> - 本固件已通过 **64/64 项独立反解离线审计**（双分区 0 LDEC 硬件违规、`fwtool.lz77` 交叉解压一致校验、全槽位 Backup ID 无冲突校验）以及 **32/32 项 Unicorn ARMv7-A 仿真测试**（含真实 `av-cam.bin` BIONZ AE、`viewUnified3.so` 全 218 键 + `onTransition` FSM、`viewUnified4.so` 全 256 PID 光标记忆树及 PIC 引擎全功能回归测试），但涉及 `nflasha3` 的固件刷写**务必在刷机前先完整备份机身 `Backup.bin`、`nflasha15` 与 `nflasha3`**，取出 SD 卡并确保电量 ≥ 50%、全程不断电！

---

## 一、固件架构与改动清单（CFW v3.36）

| 组件 / 分区 | 是否修改 | 说明 |
| :--- | :---: | :--- |
| Bootloader / `partinf.conf` / `nflasha7` | **否（0 字节改动）** | 保留完整官方引导与 USB 升级恢复能力 |
| Darwin MCU (`darwin_core.bin`) | **否（0 字节改动）** | 不触碰机械快门微控制器固件 |
| WBI 快照 (`nflasha5`) | **已从升级包移除** | 避免旧 WBI1 休眠快照与修改后二进制不匹配导致 `LED -307`（注：日常拨开关关机再开机为 Suspend-to-RAM **约 1 秒**；仅在拔出电池重装后从零冷启动耗时约 15–20 秒） |
| **BIONZ 核心 (`nflasha3` / `av-cam.bin`)** | **极简 2 块原地补丁** | 355 个 TPZL 块仅改 **Block #38**（`0x437a7638` 5 B）与 **Block #55**（`CalcHandShakeTv` 128 B 原地替换）；Block 0 与 FAT12 100% 不变 |
| `/usr/share/app/string_*.uxc` | **是（26 种语言）** | 原有 5,776 槽位内空闲 ID 注入全部 69 条多语言文案 |
| `/usr/lib/viewUnified3.so` | **是（原地补丁，改 70 字节，体积 0 变化）** | 回放放大视图 `ViewZoomPb`：圆盘滚轮逆时针缩小稳停 1.0×（`ev=0x21`）、顺时针放大（`ev=0x03`）；`onTransition` 1.0× 时方向键左右切图、放大时保持平移 |
| `/usr/lib/viewUnified4.so` | **是（原地补丁，体积 0 变化）** | 拍摄模式启用「间隔拍摄」+ Fn 槽位 35（`PID 0xEA`）& 槽位 29（`PID 0xEB`）+ 严格有序光标记忆树（`0xce/0xd0/0xea/0xeb` 全正确） |
| `/usr/lib/CautionConfig.so` | **是（追加 RW 段）** | Fn 拍摄模式（9→10）+ Tab 2 P3「间隔拍摄功能」&「ISO AUTO 最低速度」（24 档）+ **Tab 2 P5「拓展快门速度与BULB」** + 12 槽功能菜单设置（28→30） |
| `/usr/lib/libObj.so` | **是（追加 RWX 段）** | Tab 2 P3 (`4→6`)、Tab 2 P5 (`4→5`) + PID `0xE4..0xEC`（含 `PID 0xEB` Fn 直达 `bootElement=0x78`）+ Thumb-2 PIC 统一引擎（7,192 B，21 处 Hook + 24 处指纹校验） |

---

## 二、功能入口与实机验收清单

### 1. 原生间隔拍摄
- **一键切换**：后波轮左键 / Tab 1 / Fn 拍摄模式中直接选择 **【间隔拍摄】**（原厂独立秒表+多张叠加图标 `0x0205D3E9`）。
- **参数设置**：**自定义设置（齿轮 Tab 2）→ 第 3 页 → 第 5 项「间隔拍摄功能」**，或在「功能菜单设置」中任选 1 格绑定 **「间隔拍摄功能」** 单格直达全部 6 项参数（开关 / 开始时间 / 间隔 / 张数 / 3 档对焦测光含日出日落圣杯 / 拍摄时息屏 + 5s 半按预览）。

### 2. 自动 ISO 最低快门速度（ISO AUTO Min. SS）
- **主菜单入口**：**自定义设置（齿轮 Tab 2）→ 第 3 页 → 第 6 项「ISO AUTO 最低速度 / ISO AUTO Min. SS」**。
- **Fn 快捷菜单直调**：**自定义设置 Tab 2 → 功能菜单设置** 任选 1 格设为 **「ISO AUTO 最低速度」**，取景按 **Fn** 选中该格后可直接滚动波轮切换，或**按下中心确认键直接弹出 24 档完整滚动列表**实时设置并自动同步 BIONZ（`Backup.bin 0x010700D0`）。
- **验收要点**：A 档 + ISO AUTO 设 `1/250`，暗光下快门稳住 `1/250` 且 ISO 抬升；亮度进一步降低至 ISO 触顶后快门自动平滑放慢至最慢 `30"` 绝不欠曝；设 `1/15` 或 `更慢 (-2)` 可突破原厂 `1/60s` 下限；S / M / 自动模式不受影响；重启或休眠唤醒后设定自动生效。

### 3. 原生拓展快门速度与 BULB
- **唯一菜单开关**：**自定义设置（齿轮 Tab 2）→ 第 5 页 → 第 5 项「拓展快门速度与BULB / Extended Shutter & BULB」**（位于「镜头补偿」正下方，`Backup.bin 0x01070147`，默认「关」）。
- **验收要点**：
  1. 选 **「关」**：M 档转动快门拨轮，`30"` 之后直接进入 `BULB`（100% 原厂行为）；
  2. 选 **「开」**：M 档转动快门拨轮，序列无缝扩展为 `30" ↔ 45" ↔ 60" ↔ 80" ↔ 100" ↔ 120" ↔ BULB`，LCD / EVF / Quick Navi 直接显示对应秒数；
  3. 停在 `45"`~`120"` 任一档，**按一下快门松手**：立即开启免手按定时 B 门，左上角实时秒数倒计时 + 图标闪烁 + 防休眠保活，到点自动关门；中途再按一次快门可立即提前结束并保存；
  4. 再往慢拨一格到 `BULB`：恢复 100% 原厂按住曝光、松手结束的手动 B 门；
  5. 可同时开启「间隔拍摄」与 `45"`~`120"` 拓展快门，实现连续定时长曝光。

### 4. 回放放大圆盘滚轮缩放 + 1.0× 左右键切图
- **验收要点**：进入回放相册，按下放大键放大照片后：
  1. **逆时针滚动后背圆盘滚轮**：逐级缩小画面倍率，**到达最小 1.0× 立即稳停**（即使快速连续逆时针狂转也绝不会退出放大或误翻多张照片）；
  2. **顺时针滚动后背圆盘滚轮**：逐级放大画面倍率；
  3. **方向键左 / 右**：放大状态（`> 1.0×`）下保持原厂画面左右平移；**缩至最小 `1.0×` 后按左/右键直接切换上一张/下一张照片**（保持 1.0× 状态，随时顺时针滚轮即可直接放大新照片）；
  4. 单张照片模式（无前后照片可切时）支持滚轮放大/缩小且左右键无异常；机顶拨轮与上下平移等全部其余按键保持原厂逻辑。

---

## 三、刷机前准备工作与备份

- **取出 SD 卡**、确保电池电量 **≥ 50%（至少 3 格电）**。
- 将相机 USB 连接模式设为 **「大容量存储 (Mass Storage)」**，直连电脑主板 USB 口。

```powershell
# 1. 检测相机连接与当前版本
python tools\Sony-PMCA-RE-master\pmca-console.py info -d native

# 2. 进入只读服务 Shell 导出 Backup.bin、nflasha15 与 nflasha3（强烈建议刷前确认已有本地备份）
python tools\Sony-PMCA-RE-master\pmca-console.py updatershell -d native
```
在 `>` 提示符下执行：
```text
> pull /setting/Backup.bin backup_a7r_Backup.bin
> pull /dev/nflasha15 backup_a7r_nflasha15.bin
> pull /dev/nflasha3  backup_a7r_nflasha3.bin
> exit
```

---

## 四、刷入四合一自制固件（v3.36）与一键回滚官方固件（v3.37）

从 [v3.36 发布页](https://github.com/Ueky2000/sony-a7r-cfw/releases/tag/v3.36) 下载[自制固件 .dat](https://github.com/Ueky2000/sony-a7r-cfw/releases/download/v3.36/ILCE-7R_CFW_v3.36_firmware.dat)和[固件 SHA-256 校验清单](https://github.com/Ueky2000/sony-a7r-cfw/releases/download/v3.36/SHA256SUMS_FIRMWARE.txt)。将下载的固件放到仓库根目录。请在刷机前按 [README 构建步骤](https://github.com/Ueky2000/sony-a7r-cfw/blob/main/README.md#从源码复现构建仿真与审计) 准备本地回滚包。下表同时列出从源码构建时的输出位置。

| 固件包 | 文件路径 | 大小 | SHA-256 |
| :--- | :--- | :---: | :--- |
| **四合一自制固件 CFW v3.36** *(支持从 v3.20 及任意既有 v3.21..v3.35 一步直刷)* | `firmware/repacked/a7r_cfw_v336/firmware_packed.dat` | `168,008,856 B` | `e4afedad84d645103d205adce0e2e4ef8837f7944e86ebacc6907f495cbbfed0` |
| **100% 官方 v3.20 一键回滚包 v3.37** *(完整还原 `nflasha3` + `nflasha15` + `nflasha5`)* | `firmware/repacked/a7r_stock_rollback_v337/firmware_packed.dat` | `205,553,816 B` | `ded11f8d9ca9eea0684e465ec95fc726195ec72990b8bd6aa2fdf16991b3c9e5` |

### 核对下载的自制固件

在仓库根目录运行：

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath ".\ILCE-7R_CFW_v3.36_firmware.dat"
```

确认输出的 SHA-256 与上表或校验清单一致。若校验值不同，请勿刷机。

### 本地构建后运行仿真与离线审计

如果你自行构建了固件，请在刷机前运行：
```powershell
python -B tests\test_interval_engine.py
python -B tests\test_auto_iso_min_ss.py
python -B tests\test_bulb_engine.py
python -B tests\test_ui_patches.py
python -B src\audit_a7r_cfw.py
```
确认输出 `ALL_CHECKS_PASSED: true`（64/64）后再刷入本地构建的固件。

### 刷入 CFW v3.36

从仓库根目录执行以下命令：

```powershell
python tools\Sony-PMCA-RE-master\pmca-console.py firmware -d native -f .\ILCE-7R_CFW_v3.36_firmware.dat
```

若使用本地构建的固件，将 `-f` 后的路径替换为 `firmware\repacked\a7r_cfw_v336\firmware_packed.dat`。

> [!IMPORTANT]
> - 刷机期间**切勿断电或拔掉 USB 线**，整个过程约 2–4 分钟。
> - Windows 下若在 `Switching to updater mode` 处超时，按相机屏幕提示确认后直接再次运行同一条命令即可无缝续接写入进度。
> - **日常换电池/冷启动说明**：正常拨电源开关关机再开机走内存休眠（Suspend-to-RAM），约 **1 秒**即亮屏取景；仅在**拔出电池重装**或首次刷机后会触发从零冷启动（约 **15–20 秒**），属移除 `nflasha5` WBI1 休眠快照后的正常现象。此外，拔电池前请先拨开关关机并**等待 1–2 秒至读写红灯完全熄灭**，确保 `/setting/Backup.bin` 完成落盘不丢设置。

---

## 五、一键回滚与异常恢复方案

### 情况 1：恢复 100% 官方原厂系统（含完整还原 `nflasha3`、`nflasha15` 与 `nflasha5` WBI1）
```powershell
python tools\Sony-PMCA-RE-master\pmca-console.py firmware -d native -f firmware\repacked\a7r_stock_rollback_v337\firmware_packed.dat
```

### 情况 2：极端异常恢复
- Bootloader 与 Updater 分区（`nflasha7` 等）完全未改动，可在关机状态下插 USB 尝试通过 `pmca-console.py firmware` 刷回官方回滚包 `v3.37`。
- 若因异常断电等导致 `nflasha3` 受损无法进 USB 升级模式，可通过 CXD90014 主板标准 ARM **SWD 焊盘**（详见 `tools/sony-a6000-re-master/docs/11-swd-recovery.md`）写入官方备份镜像恢复。
