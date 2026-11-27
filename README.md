# Sony ILCE-7R (A7R 一代) 四合一原生自制固件 (CFW v3.36)

[![Target Camera](https://img.shields.io/badge/Camera-Sony%20ILCE--7R%20(A7R%20Mk1)-blue)](#)
[![Base Firmware](https://img.shields.io/badge/Base%20FW-Official%20v3.20-informational)](#)
[![CFW Version](https://img.shields.io/badge/CFW-v3.36%20(Real--Hardware%20Verified)-brightgreen)](#)
[![Offline Audit](https://img.shields.io/badge/Offline%20Audit-64%2F64%20PASS-success)](#)
[![Unicorn Tests](https://img.shields.io/badge/Unicorn%20ARMv7--A-32%2F32%20PASS-success)](#)

为 **Sony ILCE-7R（一代，CXD90014 SoC / 官方固件 v3.20）** 开发的系统原生四合一自制固件。全部功能均以原生菜单项、原生持久化存储（`Backup.bin` 零引用空闲槽位）与原生 Thumb-2 / BIONZ 机器码方式深度整合，**已在实机 ILCE-7R 上完整刷机与实拍验证**，并提供 **100% 逐字节官方 v3.20 一键回滚包（v3.37）**。

---

## 核心功能一览（CFW v3.36）

### 1. 原生间隔拍摄（Interval Shooting）
- **三端同步拍摄模式入口 + 原厂独立图标**：后波轮左键 / Tab 1 / Fn 拍摄模式条带直接选择 **【间隔拍摄】**（复用原厂未改动独立秒表+多张叠加图标 `0x0205D3E9` / `0x0205D67E` / `0x020E8787`），Live View 实时显示 ~1 Hz 闪烁与剩余张数倒计时。
- **单格 Fn 快捷键直达设置页**：功能菜单设置（12 槽任选）新增 **「间隔拍摄功能」**（槽位 35 / `PID 0xEA`），按下直达 6 项完整设置页，仅占用 1 个 Fn 格位。
- **自定义设置 Tab 2 第 3 页 第 5 项「间隔拍摄功能」**：6 项原生掉电持久化参数（`PID 0xE4..0xE9`，支持 26 种机身语言）：
  - `间隔拍摄`（开/关）
  - `拍摄开始时间`（0 ~ 60s）
  - `拍摄间隔`（1 ~ 60s）
  - `拍摄张数`（30 ~ 999 / 无限制）
  - `对焦 / 测光模式`（3 档：每张重对焦测光 / 首张锁定 / **日出日落圣杯模式：锁焦 + 实时测光**）
  - `拍摄时息屏`（支持半按快门 5 秒临时亮屏预览）
- **零闪存磨损、零额外线程、零漂移原生 Thumb-2 引擎**：挂接主事件循环 `TimerManager` 调度 + 每 4 秒 `@M058` 防休眠保活；随时再次按快门、切模式或进回放可安全中止。

### 2. 硬件级 60fps「自动 ISO 最低快门速度（ISO AUTO Min. SS）」
- **与索尼 A7R II / A7 III 官方逻辑一致的 24 档完整选项**：
  - 5 档自动相对安全快门：`更慢 (-2)` / `慢 (-1)` / `标准 (STD)` / `快 (+1)` / `更快 (+2)`（实时追踪 `1mm..1000mm` 镜头焦距；转接手动头基准 `1/60s`）；
  - 19 档整档固定最低快门：`1/8000` .. `30"`（可突破原厂 `1/60s` 限制，亦可高速锁门防运动模糊）。
- **100% BIONZ 硬件级 60fps 零延迟生效**：
  - 复用原厂隐藏通道 `ModelCamera (@M00B)` Event `0x7E`（key `0x39F`）→ `ObjCamera` → `LIRO 0x40CE` → `av-cam.bin`；
  - 对 `/dev/nflasha3` 中的 `av-cam.bin` 仅实施 **2 处极简原地补丁**（355 个 TPZL 块中**仅改动 Block #38 与 Block #55 两块，共 121 字节**；Block 0 与 FAT12 元数据 100% 逐字节不变）；
  - 仅在 **P 档 / A 档** 生效；光线更暗导致 ISO 触顶上限时快门自动平滑放慢至 `30"` **绝不欠曝**；S / M / 自动 / 场景 / 全景 / 视频等模式 100% 保持原厂行为；自动兼容闪光灯同步与曝光补偿。
- **双原生入口 + 掉电持久化**：主菜单 **Tab 2 第 3 页 第 6 项「ISO AUTO 最低速度」** + **Fn 快捷菜单 12 槽任选**（槽位 29 / `PID 0xEB`，Fn 内滚动波轮切换或**按中心确认键直弹 24 档完整滚动列表**），持久化至 `Backup.bin 0x010700D0`（出厂初始化自动归零回「标准」）。

### 3. 原生「拓展快门速度与 BULB（Extended Shutter & BULB）」
- **唯一菜单开关**：**自定义设置（齿轮 Tab 2）→ 第 5 页 → 第 5 项「拓展快门速度与BULB / Extended Shutter & BULB」**（紧邻「镜头补偿」正下方，`PID 0xEC`，持久化至 `Backup.bin 0x01070147`，默认「关」）：
  - **关（默认）**：100% 保持原厂逻辑，M 档拨轮 `30"` 后直接进入 `BULB`。
  - **开**：M 档快门拨轮原生无缝扩展为 **`30" ↔ 45" ↔ 60" ↔ 80" ↔ 100" ↔ 120" ↔ BULB`**，LCD / EVF / Quick Navi 原生实时显示对应秒数。
- **免手按定时长曝 + 间隔拍摄联动**：
  - 拨至 **`45"` / `60"` / `80"` / `100"` / `120"`**：**单按一下快门松手**即自动开始定时 B 门长曝，左上角实时秒数倒计时 + 图标闪烁 + 防休眠保活 + 到点自动关门；曝光中途再按一下快门立即提前结束并保存；可与**间隔拍摄**组合联动连续多张长曝；
  - 再往慢拨一格至 **`BULB`**：恢复 100% 原厂按住曝光、松手结束的手动 B 门。

### 4. 回放放大圆盘滚轮缩放 + 1.0× 左右键切图（`ViewZoomPb` 原地补丁）
- 查看相册照片并点击放大图标进入放大模式后：
  - **顺时针转动后背圆盘滚轮**：逐级放大画面倍率（`ev=0x03` / `Action 0x04`）；
  - **逆时针转动后背圆盘滚轮**：逐级缩小画面倍率（`ev=0x21` / `Action 0x25`），**到达最小 `1.0×` 自动稳停，即使快速连续逆时针狂转也绝不会退出放大或误翻多张照片**；
  - **方向键左 / 右**：放大状态（`> 1.0×`）下保持原厂画面左右平移；**缩至最小 `1.0×` 后按方向键左 / 右直接切换上一张 / 下一张照片**（保持在 `1.0×` 状态，切完图随时顺时针转滚轮即可直接放大新照片）；
  - 机顶前后拨轮、方向键上下平移、单张模式及其他全部按键 100% 保持原厂逻辑（仅原地修改 `/usr/lib/viewUnified3.so` 70 字节，体积零变化）。

---

## 安全边界与防变砖设计

| 组件 / 分区 | 改动情况 | 安全说明 |
| :--- | :---: | :--- |
| Bootloader / `partinf.conf` / `nflasha7` / `nflasha16` | **0 字节改动** | 完整保留官方引导与 USB 升级恢复能力 |
| Darwin MCU (`darwin_core.bin`) | **0 字节改动** | 不触碰机械快门微控制器 |
| WBI 休眠快照 (`nflasha5`) | **从升级包移除** | 避免旧 WBI1 快照不匹配导致 `LED -307`（日常开关机 Suspend-to-RAM 仍约 **1 秒**；仅拔电池重装后冷启动约 15–20 秒） |
| BIONZ (`nflasha3` / `av-cam.bin`) | **仅 2 块原地补丁** | 355 个 TPZL 块仅重压 Block #38 & #55（共 121 B）；Block 0 与 FAT12 100% 不变，0 LDEC 违规 |
| Linux 应用层 (`nflasha15` ext2) | **30 个文件** | 26 种语言 UXC + `viewUnified3.so`（原地）+ `viewUnified4.so`（原地）+ `CautionConfig.so` + `libObj.so`；Block 0 不变，0 LDEC 违规 |
| 官方一键回滚包 (`v3.37`) | **100% 官方 v3.20** | `firmware.tar` 与官方 v3.20 逐字节一致，一步完整还原 `nflasha3` + `nflasha15` + `nflasha5` |

---

## 快速刷机（直接下载 Release 固件）

> [!CAUTION]
> **刷机前必做**：
> 1. 强烈建议先使用 `pmca-console.py updatershell` 备份本机 `/setting/Backup.bin`、`/dev/nflasha15` 与 `/dev/nflasha3`；
> 2. **取出 SD 卡**，确保电池电量 **≥ 50%**，相机 USB 连接模式设为 **大容量存储（Mass Storage）**，直连主板 USB 口，全程切勿断电或拔线；
> 3. 详细备份、验收与救砖步骤请务必先阅读 **[docs/FLASHING_AND_RECOVERY.md](docs/FLASHING_AND_RECOVERY.md)**。

### Release 固件文件与校验（SHA-256）

请前往本仓库的 **GitHub Releases (`v3.36`)** 下载对应 `.dat` 文件，并核对 SHA-256：

| 固件包 | 文件名 | 大小 | SHA-256 |
| :--- | :--- | :---: | :--- |
| **四合一自制固件 CFW v3.36** *(支持从官方 v3.20 或任意旧版 CFW 直刷)* | `ILCE-7R_CFW_v3.36_firmware.dat` | `168,008,856 B` | `e4afedad84d645103d205adce0e2e4ef8837f7944e86ebacc6907f495cbbfed0` |
| **100% 官方 v3.20 一键回滚包 v3.37** | `ILCE-7R_Stock_v3.20_Rollback_v3.37_firmware.dat` | `205,553,816 B` | `ded11f8d9ca9eea0684e465ec95fc726195ec72990b8bd6aa2fdf16991b3c9e5` |

使用 [Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE) 刷入（以 Windows 为例）：
```powershell
# 1. 检测相机型号是否为 ILCE-7R
python pmca-console.py info -d native

# 2. 刷入 CFW v3.36（若在 Switching to updater mode 超时，按相机屏幕提示确认后重跑同一条命令续接）
python pmca-console.py firmware -d native -f ILCE-7R_CFW_v3.36_firmware.dat
```

---

## 仓库目录结构

- [src/menu_spec.py](src/menu_spec.py) — 四大功能菜单项、PID（`0xE4..0xEC`）、启动参数（`0x77`/`0x78`）、零引用 Backup ID（`0x0107003C..0x01070147`）与 26 语言 69 条文案单一事实源
- [src/interval_engine.c](src/interval_engine.c) — 纯位置无关（`-fropi`）ARMv7-A Thumb-2 统一引擎（7,192 B，21 处 Hook）
- [src/patch_avcam.py](src/patch_avcam.py) — `/dev/nflasha3` (`av-cam.bin`) 2 块极简原地补丁器（Block #38 5 B + Block #55 128 B `CalcHandShakeTv`）
- [src/patch_view_zoompb.py](src/patch_view_zoompb.py) — `lib/viewUnified3.so` 原地补丁（70 B，体积 0 变化：回放放大圆盘滚轮缩放稳停 1.0× + `onTransition` 1.0× 左右键切图）
- [src/patch_view_drivemode.py](src/patch_view_drivemode.py) — `lib/viewUnified4.so` 原地补丁（体积 0 变化：拍摄模式 + Fn 槽位 35 & 29 + 严格有序光标记忆树）
- [src/patch_caution.py](src/patch_caution.py) — `lib/CautionConfig.so` 菜单节点树（Tab 2 P3/P5）+ Fn 拍摄模式（9→10）+ 12 槽功能菜单设置（28→30）
- [src/patch_libobj.py](src/patch_libobj.py) — `lib/libObj.so` 统一补丁（Tab 2 P3 `4→6`、Tab 2 P5 `4→5` + PID `0xE4..0xEC` + 21 处 Hook + 24 处指纹校验）
- [src/patch_uxc.py](src/patch_uxc.py) — 多语言 `share/app/string_*.uxc` 空闲槽位安全注入器（26 种语言）
- [src/ext2_patcher.py](src/ext2_patcher.py) — 纯 Python `bs=1024` ext2 原地块复用写入器 + 离线全盘 fsck 校验
- [src/tpzl_mod.py](src/tpzl_mod.py) — 纯 Python TPZL 分块压缩/解压缩器（含硬件 LDEC 违规检测）
- [src/tar_and_sum.py](src/tar_and_sum.py) — 保守 `firmware.tar` 重构器、`.sum` CRC-32 更新与 WBI 移除
- [src/build_a7r_cfw.py](src/build_a7r_cfw.py) — 端到端一键构建 **CFW v3.36** 与 **官方回滚包 v3.37**
- [src/audit_a7r_cfw.py](src/audit_a7r_cfw.py) — 独立反解与 64 项防变砖红线离线审计工具（覆盖 `nflasha15` 与 `nflasha3`）
- [tests/test_interval_engine.py](tests/test_interval_engine.py) — Unicorn ARMv7-A 间隔拍摄与 Fn 直达弹窗 12 项全场景测试
- [tests/test_auto_iso_min_ss.py](tests/test_auto_iso_min_ss.py) — Unicorn ARMv7-A 真实 `av-cam.bin` BIONZ AE + PIC 引擎 7 项测试
- [tests/test_bulb_engine.py](tests/test_bulb_engine.py) — Unicorn ARMv7-A 拓展快门速度与多特征协同 9 项测试
- [tests/test_ui_patches.py](tests/test_ui_patches.py) — Unicorn ARMv7-A 真实 `viewUnified3.so`（全 218 键 + `onTransition` FSM）与 `viewUnified4.so`（全 256 PID）4 项测试
- [docs/FLASHING_AND_RECOVERY.md](docs/FLASHING_AND_RECOVERY.md) — 实机完整备份、刷机、验收与一键回滚/救砖手册
- [CHANGELOG.md](CHANGELOG.md) — 版本演进日志

---

## 从源码复现构建、仿真与审计

本仓库源码不包含索尼官方二进制镜像与第三方工具链，构建前需准备：

1. **安装 Python 3.10+ 依赖**：
   ```powershell
   pip install -r requirements.txt
   ```
2. **准备外部工具到 `tools/` 目录**：
   - 将 [ma1co/fwtool.py](https://github.com/ma1co/fwtool.py) 放入 `tools/fwtool.py-master/`；
   - 将便携版 [Zig](https://ziglang.org/download/)（自带 Clang + LLD 用于编译 `-fropi` Thumb-2 C 引擎）解压至 `tools/zig-*/`（保证 `tools/zig-*/zig.exe` 存在）；
   - （可选，刷机用）将 [ma1co/Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE) 放入 `tools/Sony-PMCA-RE-master/`。
3. **解包官方 Sony ILCE-7R v3.20 固件**：
   从索尼官网下载官方 `Update_ILCE7RV320.exe` / 固件 `.dat`，使用 `fwtool.py unpack` 解包至 `firmware/unpacked/fw320/`（包含 `config.yaml`、`updater.img`、`firmware.tar` 及 `firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked_unpacked/`）。
4. **运行 32 项 Unicorn 仿真测试、构建固件并运行 64 项独立审计**：
   ```powershell
   # 1. 运行全部 32 项 Unicorn ARMv7-A Thumb-2 仿真测试
   python -B tests\test_interval_engine.py
   python -B tests\test_auto_iso_min_ss.py
   python -B tests\test_bulb_engine.py
   python -B tests\test_ui_patches.py

   # 2. 一键构建 CFW v3.36 与官方回滚包 v3.37
   python -B src\build_a7r_cfw.py

   # 3. 运行 64 项独立反解离线审计（必须输出 ALL_CHECKS_PASSED: true 才可刷机）
   python -B src\audit_a7r_cfw.py
   ```

---

## 致谢与免责声明

- **开源社区致谢**：本项目受益于 [ma1co/Sony-PMCA-RE](https://github.com/ma1co/Sony-PMCA-RE)、[ma1co/fwtool.py](https://github.com/ma1co/fwtool.py)、[OpenMemories-Docs](https://github.com/ma1co/OpenMemories-Docs) 等索尼相机逆向生态项目。
- **免责声明**：本自制固件与源码仅供学习、研究与个人爱好者自用，**不隶属于索尼公司（Sony Corporation）**。刷写非官方固件存在一定硬件风险，请务必提前做好 `Backup.bin` / `nflasha15` / `nflasha3` 全量备份并严格按照说明操作，因操作不当造成的任何损失需由使用者自行承担。
- **许可协议**：本仓库中的原创补丁脚本、C 语言引擎源码、测试套件及文档采用 [MIT License](LICENSE) 开源；索尼原厂固件版权归 Sony Corporation 所有。
