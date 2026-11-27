"""End-to-end builder for Sony ILCE-7R (A7R1) Unified All-in-One CFW (v3.33)
and Official Stock Rollback Firmware (v3.34).

Integrates all four project features into a single conflict-free production release:
1. `firmware/repacked/a7r_cfw_v333/firmware_packed.dat` (CFW v3.33)
   - Feature 1 — Native Interval Shooting (real-hardware verified):
       * Drive Mode (Rear Wheel Left / Tab 1 / Fn bar) + Option A untouched stock icons
       * Tab 2 P3 Item 5 `Interval Shoot Func.` folder (6 Backup settings PIDs 0xE4..0xE9)
       * Single-slot Fn shortcut (slot 35 / PID 0xEA -> `ViewSettingMenu` Layout 5)
       * Zero-thread Main-EventLoop Thumb-2 engine (3 AF/AE modes, Monitor Sleep + 5s S1 peek,
         4s @M058 APO keep-alive, Live View blinking icon & remaining shots countdown)
   - Feature 2 — Hardware 60fps BIONZ Auto ISO Min. Shutter Speed (real-hardware verified):
       * Tab 2 P3 Item 6 `ISO AUTO Min. SS` + Fn Menu 12-slot support (slot 29 / PID 0xEB ->
         `ViewSettingMenu` Layout 1 24-option scrollable popup via `bootElement=0x78`)
       * 24 Sony A7R II/III-style options (5 Auto relative -2..+2 EV around live 1/f + 19 fixed 1/8000..30")
       * Persisted in `Backup.bin 0x010700D0` (attr=0x50, resetData=0 -> Standard)
       * Surgical 2-block in-place patch to `av-cam.bin` inside `/dev/nflasha3` (Block #38 & #55 ONLY;
         Block 0 & FAT12 metadata 100% byte-identical; P/A modes only; zero underexposure at ISO_MAX)
   - Feature 3 — Native Extended Shutter Speed & Timed BULB (real-hardware verified):
       * Tab 2 P5 Item 5 `Extended Shutter & BULB` Off/On toggle (PID 0xEC, `Backup.bin 0x01070147`)
       * Off: 100% stock `30" <-> BULB`; On: M-mode dial extends `30" <-> 45" <-> 60" <-> 80" <-> 100" <-> 120" <-> BULB`
       * Single shutter click on `45"..120"` runs hands-free timed Bulb (seconds countdown, blinking icon,
         APO keep-alive, press-again-to-cancel, Interval combo); `BULB` stays normal manual press-and-hold
   - Feature 4 — Playback Zoom Control Wheel Reassignment (`lib/viewUnified3.so`, in-place):
       * Inside zoomed playback (`ViewZoomPb` States 3 & 4), remaps the rear Control Wheel from
         Next/Prev photo to CCW = Zoom Out (`0x21`) and CW = Zoom In (`0x03`), matching modern Alpha bodies
   - `nflasha5` (WBI) removed from `firmware.tar` & `part_image.sum` for clean cold boot
   - ZERO modifications to Bootloader, Darwin MCU, Updater (`nflasha7`), or partition table
2. `firmware/repacked/a7r_stock_rollback_v334/firmware_packed.dat` (Stock Rollback v3.34)
   - 100% byte-identical official Sony ILCE-7R v3.20 `firmware.tar` (restores BOTH `nflasha3` and
     `nflasha15` + official `nflasha5` WBI in one step!) bumped to version `3.34`.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ext2_patcher  # noqa: E402
import patch_avcam  # noqa: E402
import patch_caution  # noqa: E402
import patch_libobj  # noqa: E402
import patch_uxc  # noqa: E402
import patch_view_drivemode  # noqa: E402
import patch_view_zoompb  # noqa: E402
import tar_and_sum  # noqa: E402
import tpzl_mod as tpzl  # noqa: E402

UNPACKED_FW320 = ROOT / "firmware/unpacked/fw320"
TAR_UNPACKED = UNPACKED_FW320 / "firmware.tar_unpacked"
USR_DIR = TAR_UNPACKED / "0700_part_image/dev/nflasha15_unpacked_unpacked"
STOCK_N15 = TAR_UNPACKED / "0700_part_image/dev/nflasha15"
STOCK_N3 = TAR_UNPACKED / "0700_part_image/dev/nflasha3"
STOCK_PART_SUM = TAR_UNPACKED / "0701_part_image_sum/part_image.sum"
STOCK_FW_TAR = UNPACKED_FW320 / "firmware.tar"
FWTOOL = ROOT / "tools/fwtool.py-master/fwtool.py"

OUT_CFW_DIR = ROOT / "firmware/repacked/a7r_cfw_v336"
OUT_ROLLBACK_DIR = ROOT / "firmware/repacked/a7r_stock_rollback_v337"
CFW_VERSION = "3.36"
ROLLBACK_VERSION = "3.37"

PARTITION_LIMIT = 104_857_600  # 100 MiB (/dev/nflasha15)
N15_TAR_NAME = "0700_part_image/dev/nflasha15"
N3_TAR_NAME = "0700_part_image/dev/nflasha3"
N5_TAR_NAME = "0700_part_image/dev/nflasha5"
PART_SUM_TAR_NAME = "0701_part_image_sum/part_image.sum"


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _pack_dat(work_dir: Path, version: str, firmware_tar_bytes: bytes, out_dir: Path) -> Path:
    """Invoke `fwtool.py pack` with stock updater.img & bumped config.yaml version."""
    stage = work_dir / f"pack_stage_{version}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    cfg_text = (UNPACKED_FW320 / "config.yaml").read_text(encoding="utf-8")
    assert "version: '3.20'" in cfg_text, f"Unexpected config.yaml:\n{cfg_text}"
    cfg_new = cfg_text.replace("version: '3.20'", f"version: '{version}'", 1)
    cfg_path = stage / "config.yaml"
    cfg_path.write_text(cfg_new, encoding="utf-8")

    tar_path = stage / "firmware.tar"
    tar_path.write_bytes(firmware_tar_bytes)
    updater_path = UNPACKED_FW320 / "updater.img"
    assert updater_path.exists(), f"Missing {updater_path}"

    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable, str(FWTOOL), "pack",
            "-c", str(cfg_path),
            "-u", str(updater_path),
            "-f", str(tar_path),
            "-o", str(out_dir),
        ],
        check=True,
    )
    shutil.rmtree(stage)
    packed = out_dir / "firmware_packed.dat"
    assert packed.exists(), f"fwtool.py did not produce {packed}"
    return packed


def build() -> dict:
    t_start = time.time()
    zig_exe = patch_libobj.find_zig(ROOT)
    c_src = ROOT / "src/interval_engine.c"

    print("=== [1/8] Patching multilingual UXC string tables ===")
    key_to_id, patched_uxc = patch_uxc.patch_all_uxc(USR_DIR)
    print(f"  Allocated {len(key_to_id)} free UXC IDs across {len(patched_uxc)} language files.")

    print("=== [2/8] Patching lib/CautionConfig.so (Interval + Min. SS + Ext. Shutter + Drive Mode + 12 Fn slots) ===")
    orig_caution = (USR_DIR / "lib/CautionConfig.so").read_bytes()
    (
        new_caution,
        folder_delta,
        min_ss_delta,
        ext_shutter_delta,
        fn_root_delta,
        fn_min_ss_delta,
        caution_rep,
    ) = patch_caution.patch_caution(orig_caution, key_to_id)
    print(
        f"  CautionConfig.so: {len(orig_caution)} -> {len(new_caution)} B, "
        f"folder={caution_rep['delta']}, min_ss={caution_rep['min_ss_delta']}, "
        f"ext_ss={caution_rep['ext_shutter_delta']}, "
        f"Fn DriveMode={caution_rep['dm_count']}, Fn Set.=12x{caution_rep['fn_cf_count']}"
    )

    print("=== [3/8] Patching lib/viewUnified4.so & lib/viewUnified3.so (in-place, 0 size change) ===")
    orig_vu4 = (USR_DIR / "lib/viewUnified4.so").read_bytes()
    new_vu4, vu4_rep = patch_view_drivemode.patch_view_drivemode(
        orig_vu4, key_to_id, fn_root_delta, fn_min_ss_delta
    )
    print(
        f"  viewUnified4.so: {len(orig_vu4)} -> {len(new_vu4)} B (in-place), "
        f"Fn slot35 -> PID {vu4_rep['fn_cursor_pid']}, Fn slot29 -> PID {vu4_rep['fn_min_ss_cursor_pid']}"
    )
    orig_vu3 = (USR_DIR / "lib/viewUnified3.so").read_bytes()
    new_vu3, vu3_rep = patch_view_zoompb.patch_view_zoompb(orig_vu3)
    print(
        f"  viewUnified3.so: {len(orig_vu3)} -> {len(new_vu3)} B (in-place), "
        f"ViewZoomPb sites={vu3_rep['patch_site_count']}, changed_bytes={vu3_rep['changed_bytes']}"
    )

    print("=== [4/8] Compiling Thumb-2 unified engine & patching lib/libObj.so ===")
    orig_libobj = (USR_DIR / "lib/libObj.so").read_bytes()
    new_libobj, libobj_rep = patch_libobj.patch_libobj(
        orig_libobj, folder_delta, min_ss_delta, ext_shutter_delta, c_src, zig_exe
    )
    print(
        f"  libObj.so: {len(orig_libobj)} -> {len(new_libobj)} B, "
        f"engine={libobj_rep['code_size']} B @ {libobj_rep['code_va']}, "
        f"hooks={len(libobj_rep['hooks'])}"
    )

    pkg_dir = OUT_CFW_DIR / "pkg"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    replacements: dict[str, bytes] = {
        "lib/viewUnified3.so": new_vu3,
        "lib/viewUnified4.so": new_vu4,
        "lib/CautionConfig.so": new_caution,
        "lib/libObj.so": new_libobj,
        **patched_uxc,
    }
    manifest = {
        "model": "ILCE-7R",
        "base_version": "3.20",
        "cfw_version": CFW_VERSION,
        "feature": "native_interval_auto_iso_min_ss_extended_shutter_and_zoompb_wheel",
        "uxc_ids": {k: hex(v) for k, v in key_to_id.items()},
        "view_zoompb": vu3_rep,
        "view_drivemode": vu4_rep,
        "caution": caution_rep,
        "libobj": libobj_rep,
        "original_sha256": {},
        "patched_sha256": {},
    }
    for rel, blob in sorted(replacements.items()):
        dst = pkg_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(blob)
        manifest["original_sha256"][rel] = _sha256((USR_DIR / rel).read_bytes())
        manifest["patched_sha256"][rel] = _sha256(blob)

    print("=== [5/8] Patching nflasha15 ext2 (in-place block reuse & Block 0 preservation) ===")
    stock_n15_raw = STOCK_N15.read_bytes()
    stock_tpzl = tpzl.TpzlImage.parse(stock_n15_raw)
    assert tpzl.hardware_violations(stock_tpzl) == [], "Stock nflasha15 has LDEC violations?!"
    orig_ext2 = stock_tpzl.decompressed()
    patched_ext2, ext2_rep = ext2_patcher.patch_ext2_preserving_block0(orig_ext2, replacements)
    print(
        f"  ext2 verified: {ext2_rep['total_files_verified']} files OK, "
        f"Block 0 identical={ext2_rep['block0_restored_identical']}, "
        f"free_blocks {ext2_rep['free_blocks_before']} -> {ext2_rep['free_blocks_after']}"
    )

    print("=== [6/8] Recompressing changed nflasha15 TPZL blocks (hardware LDEC check) ===")
    new_n15_raw, block_info = tpzl.patch_decompressed(stock_tpzl, patched_ext2)
    new_tpzl = tpzl.TpzlImage.parse(new_n15_raw)
    viols = tpzl.hardware_violations(new_tpzl)
    assert not viols, f"nflasha15 TPZL LDEC hardware violations: {viols[:5]}"
    assert len(new_n15_raw) <= PARTITION_LIMIT, f"nflasha15 exceeds 100 MiB: {len(new_n15_raw)}"
    assert new_tpzl.block(0) == stock_tpzl.block(0), "nflasha15 TPZL Block 0 is NOT byte-identical to Sony!"
    assert new_tpzl.decompressed() == patched_ext2, "nflasha15 TPZL decompress round-trip mismatch!"
    recomp_blocks = [i for i, info in enumerate(block_info) if info["recompressed"]]
    max_sec_old = max((s + 511) // 512 for _, s in stock_tpzl.entries)
    max_sec_new = max((s + 511) // 512 for _, s in new_tpzl.entries)
    assert max_sec_new <= max_sec_old, f"nflasha15 max_sectors increased: {max_sec_new} > {max_sec_old}"
    print(
        f"  nflasha15 TPZL: {len(stock_n15_raw)/1048576:.2f} -> {len(new_n15_raw)/1048576:.2f} MiB "
        f"(limit 100 MiB), recompressed {len(recomp_blocks)}/{len(block_info)} blocks, "
        f"LDEC violations=0, max_sec={max_sec_new} (<= {max_sec_old})"
    )

    print("=== [7/8] Patching nflasha3 av-cam.bin (2 blocks ONLY: #38 & #55, 0 FAT12 edits, LDEC check) ===")
    stock_n3_raw = STOCK_N3.read_bytes()
    stock_n3_tpzl = tpzl.TpzlImage.parse(stock_n3_raw)
    assert tpzl.hardware_violations(stock_n3_tpzl) == [], "Stock nflasha3 has LDEC violations?!"
    orig_n3_dec = stock_n3_tpzl.decompressed()
    patched_n3_dec, avcam_rep = patch_avcam.patch_nflasha3_decompressed(orig_n3_dec)
    new_n3_raw, n3_block_info = tpzl.patch_decompressed(stock_n3_tpzl, patched_n3_dec)
    new_n3_tpzl = tpzl.TpzlImage.parse(new_n3_raw)
    n3_viols = tpzl.hardware_violations(new_n3_tpzl)
    assert not n3_viols, f"nflasha3 TPZL LDEC hardware violations: {n3_viols[:5]}"
    assert len(new_n3_raw) <= patch_avcam.N3_PARTITION_LIMIT, f"nflasha3 exceeds limit: {len(new_n3_raw)}"
    assert new_n3_tpzl.block(0) == stock_n3_tpzl.block(0), "nflasha3 TPZL Block 0 is NOT byte-identical to Sony!"
    assert new_n3_tpzl.decompressed() == patched_n3_dec, "nflasha3 TPZL decompress round-trip mismatch!"
    n3_recomp = [i for i, info in enumerate(n3_block_info) if info["recompressed"]]
    assert tuple(n3_recomp) == patch_avcam.EXPECTED_CHANGED_BLOCKS, (
        f"Unexpected nflasha3 recompressed blocks: {n3_recomp}"
    )
    n3_max_sec_old = max((s + 511) // 512 for _, s in stock_n3_tpzl.entries)
    n3_max_sec_new = max((s + 511) // 512 for _, s in new_n3_tpzl.entries)
    assert n3_max_sec_new <= n3_max_sec_old, f"nflasha3 max_sectors increased: {n3_max_sec_new} > {n3_max_sec_old}"
    print(
        f"  nflasha3 TPZL: {len(stock_n3_raw)/1048576:.2f} -> {len(new_n3_raw)/1048576:.2f} MiB, "
        f"recompressed {len(n3_recomp)}/{len(n3_block_info)} blocks ({n3_recomp}), "
        f"LDEC violations=0, max_sec={n3_max_sec_new} (<= {n3_max_sec_old})"
    )

    print("=== [8/8] Rebuilding firmware.tar (removing WBI nflasha5) & packing .dat files ===")
    stock_sum_text = STOCK_PART_SUM.read_text(encoding="ascii")
    new_sum_text, sum_changes = tar_and_sum.update_sum_text(
        stock_sum_text,
        updates={
            "dev/nflasha15": new_n15_raw,
            "dev/nflasha3": new_n3_raw,
        },
        remove_paths={"dev/nflasha5"},
    )
    new_sum_bytes = new_sum_text.encode("ascii")

    stock_tar_raw = STOCK_FW_TAR.read_bytes()
    cfw_tar_raw, tar_rep = tar_and_sum.rebuild_tar(
        stock_tar_raw,
        {
            N15_TAR_NAME: new_n15_raw,
            N3_TAR_NAME: new_n3_raw,
            PART_SUM_TAR_NAME: new_sum_bytes,
            N5_TAR_NAME: None,
        },
    )

    cfw_dat = _pack_dat(OUT_CFW_DIR, CFW_VERSION, cfw_tar_raw, OUT_CFW_DIR)
    rollback_dat = _pack_dat(OUT_ROLLBACK_DIR, ROLLBACK_VERSION, stock_tar_raw, OUT_ROLLBACK_DIR)

    manifest["ext2"] = ext2_rep
    manifest["avcam"] = avcam_rep
    manifest["tpzl"] = {
        "orig_bytes": len(stock_n15_raw),
        "new_bytes": len(new_n15_raw),
        "partition_limit": PARTITION_LIMIT,
        "total_blocks": len(block_info),
        "recompressed_blocks": len(recomp_blocks),
        "block0_identical": True,
        "max_sectors_old": max_sec_old,
        "max_sectors_new": max_sec_new,
        "ldec_violations": 0,
    }
    manifest["tpzl_nflasha3"] = {
        "orig_bytes": len(stock_n3_raw),
        "new_bytes": len(new_n3_raw),
        "partition_limit": patch_avcam.N3_PARTITION_LIMIT,
        "total_blocks": len(n3_block_info),
        "recompressed_blocks": n3_recomp,
        "block0_identical": True,
        "max_sectors_old": n3_max_sec_old,
        "max_sectors_new": n3_max_sec_new,
        "ldec_violations": 0,
        "orig_sha256": _sha256(stock_n3_raw),
        "patched_sha256": _sha256(new_n3_raw),
    }
    manifest["sum_changes"] = sum_changes
    manifest["tar_changes"] = tar_rep
    manifest["outputs"] = {
        "cfw_dat": str(cfw_dat),
        "cfw_sha256": _sha256(cfw_dat.read_bytes()),
        "cfw_size": cfw_dat.stat().st_size,
        "rollback_dat": str(rollback_dat),
        "rollback_sha256": _sha256(rollback_dat.read_bytes()),
        "rollback_size": rollback_dat.stat().st_size,
    }
    manifest["elapsed_sec"] = round(time.time() - t_start, 2)

    (pkg_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (pkg_dir / "SHA256SUMS").write_text(
        "".join(f"{h}  {r}\n" for r, h in sorted(manifest["patched_sha256"].items())),
        encoding="utf-8",
    )
    print(f"\nBUILD COMPLETE in {manifest['elapsed_sec']}s!")
    print(f"  CFW v{CFW_VERSION}     : {cfw_dat} ({manifest['outputs']['cfw_size']:,} B, SHA256={manifest['outputs']['cfw_sha256'][:16]}...)")
    print(f"  Rollback v{ROLLBACK_VERSION}: {rollback_dat} ({manifest['outputs']['rollback_size']:,} B, SHA256={manifest['outputs']['rollback_sha256'][:16]}...)")
    return manifest


if __name__ == "__main__":
    build()
