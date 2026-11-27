"""Independent end-to-end offline safety auditor for ILCE-7R Unified CFW v3.33 `.dat` packages.

Unpacks the final `firmware_packed.dat` (v3.33) AND rollback `v3.34` from scratch using
`fwtool.py unpack`, then independently verifies EVERY brick-prevention & feature invariant:
1. Container / FDAT / config.yaml / version (3.33 & 3.34) / model `0x41030034` / untouched updater
2. `firmware.tar` member diff vs official Sony v3.20 (ONLY `nflasha15`, `nflasha3`, and
   `part_image.sum` changed; ONLY `nflasha5` WBI removed; ZERO changes to Bootloader,
   Updater `nflasha7`, `nflasha16`, or Darwin MCU `darwin_core.bin`)
3. Every `.sum` CRC-32 + size inside `firmware.tar`
4. `nflasha15` TPZL geometry: <= 100 MiB, 0 LDEC violations, 512 B alignment, contiguous
   blocks, Block 0 byte-identical to Sony, max_sectors <= Sony, AND independent
   decompression check via `fwtool.lz77.inflateLz77`
5. `nflasha3` (`av-cam.bin`) TPZL + FAT12 + machine-code audit (ONLY blocks `#38` & `#55`)
6. Full ext2 filesystem walk on `nflasha15`: ONLY patched files changed (30 files), every untouched
   file in `/usr` matches Sony SHA-256 & mode/uid/gid, zero duplicate blocks
7. Deep ELF & code verification on extracted `viewUnified3.so`, `viewUnified4.so`, `CautionConfig.so`, `libObj.so`:
   - `viewUnified3.so`: Playback Zoom (`ViewZoomPb`) Control Wheel 4 in-place branch retargets ONLY
   - `viewUnified4.so`: Drive Mode + Fn slot 35 (`0xEA`) + Fn slot 29 (`0xEB`) + strictly-sorted cursor BST
   - `CautionConfig.so`: Fn Drive Mode (10) + 12x Function Menu Set. (30) + Fn root +
     `min_ss_node` (24 children) + `ext_shutter_node` (2 children)
   - `libObj.so`: ALL 21 Thumb-2 `b.w` hooks + 24 fingerprints + Tab 2 P3 count `6` +
     Tab 2 P5 count `5` + PID bounds `0xef` + `PID 0xEA` (`SETTINGMENUX`, `0x77`) +
     `PID 0xEB` (`BK_MIN_SS = 0x010700D0`, 24 opts + `SETTINGMENUX`, `0x78`) +
     `PID 0xEC` (`BK_EXT_SHUTTER = 0x01070147`, 2 opts)
8. UXC slot-bounds & localized string check across all 26 languages
9. Stock rollback v3.34 `.dat` (`firmware.tar` 100% byte-identical to official Sony v3.20)
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools/fwtool.py-master"))

import ext2_patcher  # noqa: E402
import menu_spec  # noqa: E402
import patch_avcam as PA  # noqa: E402
import patch_caution as PC  # noqa: E402
import patch_libobj as PL  # noqa: E402
import patch_uxc  # noqa: E402
import patch_view_drivemode as PVD  # noqa: E402
import patch_view_zoompb as PVZ  # noqa: E402
import tar_and_sum  # noqa: E402
import tpzl_mod as tpzl  # noqa: E402
from fwtool import lz77 as fw_lz77  # noqa: E402

FWTOOL = ROOT / "tools/fwtool.py-master/fwtool.py"
STOCK_UNPACKED = ROOT / "firmware/unpacked/fw320"
CFW_DAT = ROOT / "firmware/repacked/a7r_cfw_v336/firmware_packed.dat"
CFW_MANIFEST = ROOT / "firmware/repacked/a7r_cfw_v336/pkg/MANIFEST.json"
ROLLBACK_DAT = ROOT / "firmware/repacked/a7r_stock_rollback_v337/firmware_packed.dat"

PARTITION_LIMIT = 104_857_600
N15_NAME = "0700_part_image/dev/nflasha15"
N3_NAME = "0700_part_image/dev/nflasha3"
N5_NAME = "0700_part_image/dev/nflasha5"
PART_SUM_NAME = "0701_part_image_sum/part_image.sum"


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _fwtool_unpack(dat_path: Path, out_dir: Path) -> None:
    subprocess.run(
        [sys.executable, str(FWTOOL), "unpack", "-f", str(dat_path), "-o", str(out_dir)],
        check=True,
        capture_output=True,
        text=True,
    )


def _fwtool_decode_block(block_bytes: bytes, block_size: int = 65536) -> bytes:
    bio = io.BytesIO(block_bytes)
    out = bytearray()
    while len(out) < block_size:
        out.extend(fw_lz77.inflateLz77(bio))
    return bytes(out)


def _va_to_off(loads: list[dict], va: int) -> int:
    for s in loads:
        if s["p_vaddr"] <= va < s["p_vaddr"] + s["p_filesz"]:
            return s["p_offset"] + (va - s["p_vaddr"])
    raise KeyError(f"VA 0x{va:08x} unmapped")


def _tpzl_block_unchanged(old_img: tpzl.TpzlImage, new_img: tpzl.TpzlImage, idx: int) -> bool:
    """True iff TPZL block `idx` was copied verbatim from Sony stock.

    Sony omits trailing 512 B sector-padding zeros on the final block (EOF < off + toc_sz).
    `tpzl_mod.rebuild()` copies that untouched block verbatim and zero-pads to the exact same TOC size.
    """
    ob = old_img.block(idx)
    nb = new_img.block(idx)
    if nb == ob:
        return True
    if idx == len(old_img.entries) - 1:
        old_off, old_sz = old_img.entries[idx]
        _, new_sz = new_img.entries[idx]
        if old_off + old_sz > len(old_img.data) and new_sz == old_sz:
            return nb == ob + b"\x00" * (old_sz - len(ob))
    return False


def _audit_elf(name: str, blob: bytes, extra_relocs: int) -> dict:
    with io.BytesIO(blob) as f:
        elf = ELFFile(f)
        loads = [s.header for s in elf.iter_segments() if s.header["p_type"] == "PT_LOAD"]
        for s in loads:
            assert (s["p_offset"] % 0x1000) == (s["p_vaddr"] % 0x1000), f"{name}: bad PT_LOAD page alignment"
        dyn_seg = next(s.header for s in elf.iter_segments() if s.header["p_type"] == "PT_DYNAMIC")
        d_off, d_sz = dyn_seg["p_offset"], dyn_seg["p_filesz"]
        tags = dict(struct.unpack_from("<II", blob, d_off + 8 * i) for i in range(d_sz // 8))
        rel_va, rel_sz = tags[0x11], tags[0x12]
        rel_seg = next(
            (s for s in loads if s["p_vaddr"] <= rel_va and rel_va + rel_sz <= s["p_vaddr"] + s["p_filesz"]),
            None,
        )
        assert rel_seg is not None, f"{name}: DT_REL outside PT_LOAD!"
        rel_off = rel_seg["p_offset"] + (rel_va - rel_seg["p_vaddr"])
        w_loads = [s for s in loads if s["p_flags"] & 2]
        n_rel = rel_sz // 8
        for i in range(n_rel - extra_relocs, n_rel):
            r_off, r_info = struct.unpack_from("<II", blob, rel_off + 8 * i)
            rtype = r_info & 0xFF
            assert rtype in (2, 23), f"{name}: unexpected appended reloc type {rtype} at 0x{r_off:x}"
            assert any(s["p_vaddr"] <= r_off < s["p_vaddr"] + s["p_memsz"] for s in w_loads), (
                f"{name}: reloc target 0x{r_off:x} not in writable segment!"
            )
        return {
            "pt_load_count": len(loads),
            "phnum": elf["e_phnum"],
            "dt_rel": hex(rel_va),
            "dt_relsz": rel_sz,
            "total_relocs": n_rel,
            "verified_appended_relocs": extra_relocs,
        }


def run_audit() -> dict:
    manifest = json.loads(CFW_MANIFEST.read_text(encoding="utf-8"))
    checks: dict[str, bool] = {}

    with tempfile.TemporaryDirectory(prefix="a7r_audit_") as td:
        tmp = Path(td)
        cfw_unp = tmp / "cfw_v336"
        rb_unp = tmp / "rb_v337"
        _fwtool_unpack(CFW_DAT, cfw_unp)
        _fwtool_unpack(ROLLBACK_DAT, rb_unp)

        # 1. Container & updater checks
        cfg_cfw = (cfw_unp / "config.yaml").read_text(encoding="utf-8")
        cfg_rb = (rb_unp / "config.yaml").read_text(encoding="utf-8")
        checks["container.model_0x41030034"] = "model: 0x41030034" in cfg_cfw and "model: 0x41030034" in cfg_rb
        checks["container.version_cfw_3.36"] = "version: '3.36'" in cfg_cfw
        checks["container.version_rollback_3.37"] = "version: '3.37'" in cfg_rb

        stock_updater = _sha256((STOCK_UNPACKED / "updater.img").read_bytes())
        cfw_updater = _sha256((cfw_unp / "updater.img").read_bytes())
        rb_updater = _sha256((rb_unp / "updater.img").read_bytes())
        checks["container.updater_img_untouched"] = (stock_updater == cfw_updater == rb_updater)

        # 2. Rollback tar byte-identical to stock Sony v3.20 (restores nflasha3 + nflasha15 + nflasha5!)
        stock_tar = (STOCK_UNPACKED / "firmware.tar").read_bytes()
        cfw_tar = (cfw_unp / "firmware.tar").read_bytes()
        rb_tar = (rb_unp / "firmware.tar").read_bytes()
        checks["rollback.firmware_tar_byte_identical_to_stock_3.20"] = (rb_tar == stock_tar)

        # 3. Tar member-by-member diff & CRC-32 sum audit
        stock_m = {e.name: e.data for e in tar_and_sum.parse_tar(stock_tar)[0]}
        cfw_m = {e.name: e.data for e in tar_and_sum.parse_tar(cfw_tar)[0]}
        changed = sorted(k for k in cfw_m if cfw_m[k] != stock_m.get(k))
        removed = sorted(set(stock_m) - set(cfw_m))
        added = sorted(set(cfw_m) - set(stock_m))
        checks["tar.only_nflasha15_nflasha3_and_part_sum_changed"] = (
            changed == sorted([N15_NAME, N3_NAME, PART_SUM_NAME])
        )
        checks["tar.only_wbi_nflasha5_removed"] = (removed == [N5_NAME])
        checks["tar.no_unexpected_members_added"] = (added == [])
        checks["tar.part_sum_no_nflasha5_line"] = (b",dev/nflasha5," not in cfw_m[PART_SUM_NAME])
        sum_res = tar_and_sum.verify_all_sums(cfw_m)
        checks["tar.all_crc32_sums_valid"] = (len(sum_res["failed"]) == 0 and sum_res["checked"] > 0)
        for crit in (
            "0700_part_image/dev/nflasha7",
            "0700_part_image/dev/nflasha16",
            "0640_darwin/darwin_core.bin",
        ):
            if crit in stock_m:
                checks[f"tar.untouched_{crit.split('/')[-1]}"] = (cfw_m.get(crit) == stock_m[crit])

        # 4. TPZL hardware LDEC & independent lz77 audit on nflasha15
        p_old = tpzl.TpzlImage.parse(stock_m[N15_NAME])
        p_new = tpzl.TpzlImage.parse(cfw_m[N15_NAME])
        ends = [o + s for o, s in p_new.entries]
        recomp = [i for i in range(len(p_old.entries)) if not _tpzl_block_unchanged(p_old, p_new, i)]
        indep_bad = [
            i for i in recomp
            if _fwtool_decode_block(p_new.block(i), 65536) != tpzl.decode_block(p_new.block(i), 65536)
        ]
        max_sec_old = max((s + 511) // 512 for _, s in p_old.entries)
        max_sec_new = max((s + 511) // 512 for _, s in p_new.entries)
        checks["tpzl.fits_100MiB_partition"] = (len(cfw_m[N15_NAME]) <= PARTITION_LIMIT)
        checks["tpzl.zero_ldec_hardware_violations"] = (len(tpzl.hardware_violations(p_new)) == 0)
        checks["tpzl.all_blocks_512B_aligned"] = all((o & 0x1FF) == 0 for o, _ in p_new.entries)
        checks["tpzl.all_blocks_contiguous"] = all(ends[i] == p_new.entries[i + 1][0] for i in range(len(ends) - 1))
        checks["tpzl.same_block_count_2291"] = (len(p_new.entries) == len(p_old.entries) == 2291)
        checks["tpzl.max_sectors_within_sony_limit"] = (max_sec_new <= max_sec_old)
        checks["tpzl.block0_byte_identical_to_sony"] = (0 not in recomp and p_new.block(0) == p_old.block(0))
        checks["tpzl.independent_fwtool_lz77_matches"] = (len(indep_bad) == 0)

        # 4B. TPZL + FAT12 + av-cam.bin deep audit on nflasha3!
        n3_old = tpzl.TpzlImage.parse(stock_m[N3_NAME])
        n3_new = tpzl.TpzlImage.parse(cfw_m[N3_NAME])
        n3_ends = [o + s for o, s in n3_new.entries]
        n3_recomp = [i for i in range(len(n3_old.entries)) if not _tpzl_block_unchanged(n3_old, n3_new, i)]
        n3_indep_bad = [
            i for i in (n3_recomp + [len(n3_old.entries) - 1])
            if _fwtool_decode_block(n3_new.block(i), 65536) != tpzl.decode_block(n3_new.block(i), 65536)
        ]
        n3_max_old = max((s + 511) // 512 for _, s in n3_old.entries)
        n3_max_new = max((s + 511) // 512 for _, s in n3_new.entries)
        checks["nflasha3.fits_partition_limit"] = (len(cfw_m[N3_NAME]) <= PA.N3_PARTITION_LIMIT)
        checks["nflasha3.zero_ldec_hardware_violations"] = (len(tpzl.hardware_violations(n3_new)) == 0)
        checks["nflasha3.all_blocks_512B_aligned"] = all((o & 0x1FF) == 0 for o, _ in n3_new.entries)
        checks["nflasha3.all_blocks_contiguous"] = all(
            n3_ends[i] == n3_new.entries[i + 1][0] for i in range(len(n3_ends) - 1)
        )
        checks["nflasha3.same_block_count_355"] = (len(n3_new.entries) == len(n3_old.entries) == 355)
        checks["nflasha3.max_sectors_within_sony_limit"] = (n3_max_new <= n3_max_old)
        checks["nflasha3.block0_byte_identical_to_sony"] = (0 not in n3_recomp and n3_new.block(0) == n3_old.block(0))
        checks["nflasha3.only_blocks_38_and_55_recompressed"] = (tuple(n3_recomp) == PA.EXPECTED_CHANGED_BLOCKS)
        checks["nflasha3.independent_fwtool_lz77_matches"] = (len(n3_indep_bad) == 0)

        n3_dec_old = n3_old.decompressed()
        n3_dec_new = n3_new.decompressed()
        checks["nflasha3.fat12_metadata_and_tail_byte_identical"] = (
            n3_dec_new[:PA.AVCAM_FAT12_OFF] == n3_dec_old[:PA.AVCAM_FAT12_OFF]
            and n3_dec_new[PA.AVCAM_FAT12_OFF + PA.AVCAM_SIZE:] == n3_dec_old[PA.AVCAM_FAT12_OFF + PA.AVCAM_SIZE:]
        )
        av_old = n3_dec_old[PA.AVCAM_FAT12_OFF:PA.AVCAM_FAT12_OFF + PA.AVCAM_SIZE]
        av_new = n3_dec_new[PA.AVCAM_FAT12_OFF:PA.AVCAM_FAT12_OFF + PA.AVCAM_SIZE]
        av_expected, _ = PA.patch_avcam_bytes(av_old)
        checks["nflasha3.avcam_exact_bytes_site1_and_site2_only"] = (av_new == av_expected)
        checks["nflasha3.avcam_external_fingerprints_intact"] = all(
            av_new[va - PA.AVCAM_BASE_VA:va - PA.AVCAM_BASE_VA + len(want)] == want
            for _, va, want in PA.TARGET_FINGERPRINTS
        )

        # 5. Full ext2 filesystem walk & file-by-file audit on nflasha15
        ext_old = p_old.decompressed()
        ext_new = p_new.decompressed()
        img_old = ext2_patcher.Ext2Image(ext_old)
        img_new = ext2_patcher.Ext2Image(ext_new)
        man_old = img_old.walk_and_fsck(check_counters=True)
        man_new = img_new.walk_and_fsck(check_counters=False)

        diff_files = sorted(k for k in man_new if man_new[k][0] != man_old.get(k, (None,))[0])
        want_files = sorted(manifest["patched_sha256"].keys())
        checks["ext2.only_manifest_files_changed"] = (diff_files == want_files)
        checks["ext2.all_patched_hashes_match"] = all(
            man_new[k][0] == manifest["patched_sha256"][k] for k in want_files
        )
        checks["ext2.all_mode_uid_gid_preserved"] = all(
            man_new[k][2:] == man_old[k][2:] for k in man_new
        )
        checks["ext2.all_untouched_files_identical"] = all(
            man_new[k] == man_old[k] for k in man_old if k not in want_files
        )

        # 6. Deep ELF & code audit on extracted viewUnified3.so, viewUnified4.so, CautionConfig.so, libObj.so
        vu3_orig = img_old.read_file_bytes(img_old.lookup("lib/viewUnified3.so"))
        vu3_blob = img_new.read_file_bytes(img_new.lookup("lib/viewUnified3.so"))
        vu4_orig = img_old.read_file_bytes(img_old.lookup("lib/viewUnified4.so"))
        vu4_blob = img_new.read_file_bytes(img_new.lookup("lib/viewUnified4.so"))
        caution_blob = img_new.read_file_bytes(img_new.lookup("lib/CautionConfig.so"))
        libobj_blob = img_new.read_file_bytes(img_new.lookup("lib/libObj.so"))

        elf_vu3 = _audit_elf("viewUnified3.so", vu3_blob, 0)
        elf_vu4 = _audit_elf("viewUnified4.so", vu4_blob, 0)
        elf_caution = _audit_elf("CautionConfig.so", caution_blob, manifest["caution"]["extra_relocs"])
        elf_libobj = _audit_elf("libObj.so", libobj_blob, manifest["libobj"]["extra_relocs"])
        checks["elf.viewunified3_valid_and_same_size"] = (len(vu3_blob) == len(vu3_orig))
        checks["elf.viewunified4_valid_and_same_size"] = (len(vu4_blob) == len(vu4_orig))
        checks["elf.caution_valid_structure_and_relocs"] = True
        checks["elf.libobj_valid_structure_and_relocs"] = True

        # Verify viewUnified3.so Playback Zoom Control Wheel (4 branches) + 1.0x D-pad Left/Right block ONLY
        md_audit = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        with io.BytesIO(vu3_blob) as f:
            vu3_elf = ELFFile(f)
            vu3_loads = [s.header for s in vu3_elf.iter_segments() if s.header["p_type"] == "PT_LOAD"]
            dpad_off = _va_to_off(vu3_loads, PVZ.ONTRANS_DPAD_VA)
            vu3_allowed: set[int] = set(range(dpad_off, dpad_off + PVZ.ONTRANS_DPAD_SIZE))
            vu3_sites_ok = True
            for _, va, orig_b, _, exp_mnem, exp_target in PVZ.PATCH_SITES:
                off = _va_to_off(vu3_loads, va)
                vu3_allowed.update(range(off, off + len(orig_b)))
                insns = list(md_audit.disasm(vu3_blob[off:off + len(orig_b)], va))
                vu3_sites_ok &= (
                    len(insns) == 1
                    and insns[0].mnemonic == exp_mnem
                    and int(insns[0].op_str.lstrip("#"), 16) == exp_target
                )
            dpad_bytes = vu3_blob[dpad_off:dpad_off + PVZ.ONTRANS_DPAD_SIZE]
            dpad_dis = tuple(
                (i.address, i.mnemonic, i.op_str)
                for i in md_audit.disasm(dpad_bytes, PVZ.ONTRANS_DPAD_VA)
            )
            vu3_changed = {i for i in range(len(vu3_orig)) if vu3_blob[i] != vu3_orig[i]}
            checks["zoompb.vu3_wheel_branches_and_1x_dpad_switch_exact"] = (
                vu3_sites_ok
                and dpad_bytes == PVZ.ONTRANS_DPAD_NEW
                and dpad_dis == PVZ.ONTRANS_DPAD_EXPECTED_DISASM
                and len(vu3_changed) > 0
                and vu3_changed.issubset(vu3_allowed)
            )

        # Verify viewUnified4.so Drive Mode + Fn slots 35 (Interval) & 29 (Auto ISO Min. SS) + sorted BST
        with io.BytesIO(vu4_blob) as f:
            vu4_elf = ELFFile(f)
            vu4_loads = [s.header for s in vu4_elf.iter_segments() if s.header["p_type"] == "PT_LOAD"]
            sv_off = _va_to_off(vu4_loads, PVD.SETVALID_0C_VA)
            s06_off = _va_to_off(vu4_loads, PVD.SLOT_06_VA)
            s12_off = _va_to_off(vu4_loads, PVD.SLOT_12_VA)
            w06 = struct.unpack_from("<10I", vu4_blob, s06_off)
            w12 = struct.unpack_from("<10I", vu4_blob, s12_off)
            exp_dm_title = PVD.RES_STR | int(manifest["uxc_ids"][menu_spec.DM_TITLE_KEY], 16)
            exp_dm_help = PVD.RES_STR | int(manifest["uxc_ids"][menu_spec.DM_HELP_KEY], 16)
            reldyn = vu4_elf.get_section_by_name(".rel.dyn")
            vu4_dynsym = vu4_elf.get_section_by_name(".dynsym")
            rel_offs = {r["r_offset"]: (r["r_info_type"], r["r_info_sym"]) for r in reldyn.iter_relocations()}

            fn_slot_off = _va_to_off(vu4_loads, PVD.FN_SLOT35_VA)
            fn_slot_word = struct.unpack_from("<I", vu4_blob, fn_slot_off)[0]
            fn_slot_rel = rel_offs.get(PVD.FN_SLOT35_VA)
            fn_slot_sym = vu4_dynsym.get_symbol(fn_slot_rel[1]).name if fn_slot_rel else ""
            fn_pid_off = _va_to_off(vu4_loads, PVD.FN_PID_TBL_36_VA)
            exp_fn_delta = int(manifest["caution"]["fn_root_delta"], 16)

            fn29_off = _va_to_off(vu4_loads, PVD.FN_SLOT29_VA)
            fn29_word = struct.unpack_from("<I", vu4_blob, fn29_off)[0]
            fn29_rel = rel_offs.get(PVD.FN_SLOT29_VA)
            fn29_sym = vu4_dynsym.get_symbol(fn29_rel[1]).name if fn29_rel else ""
            fn29_pid_off = _va_to_off(vu4_loads, PVD.FN_PID_TBL_30_VA)
            exp_fn_minss_delta = int(manifest["caution"]["fn_min_ss_delta"], 16)

            bst_off = _va_to_off(vu4_loads, PVD.FN_CURSOR_BST_VA)
            bst_bytes = vu4_blob[bst_off:bst_off + len(PVD.FN_CURSOR_BST_NEW)]
            bst_dis = tuple(
                (i.address, i.mnemonic, i.op_str)
                for i in md_audit.disasm(bst_bytes, PVD.FN_CURSOR_BST_VA)
            )

            checks["drivemode.vu4_setvalid_0c_enabled"] = (
                vu4_blob[sv_off:sv_off + 4] == PVD.SETVALID_0C_NEW
            )
            checks["drivemode.vu4_slot06_is_interval_leaf_option_a_icons"] = (
                w06[0] == 0 and w06[1] == 0 and w06[2] == 0x0C
                and w06[3] == exp_dm_title and w06[4] == 0x0205D67E
                and w06[5] == exp_dm_help and w06[6] == 0x0205D3E9
            )
            checks["drivemode.vu4_slot12_hidden_06_and_reloc_moved"] = (
                w12[0] == PVD.SLOT_06_ORIG_CHILDREN and w12[2] == 0x06
                and (PVD.SLOT_06_VA not in rel_offs)
                and (rel_offs.get(PVD.SLOT_12_VA) == (PVD.R_ARM_RELATIVE, 0))
            )
            checks["fn_shortcut.vu4_slot35_retargeted_and_pid_0xea"] = (
                fn_slot_rel is not None
                and fn_slot_rel[0] == PVD.R_ARM_ABS32
                and fn_slot_sym == PVD.FN_ANCHOR_SYM.decode()
                and fn_slot_word == exp_fn_delta
                and struct.unpack_from("<I", vu4_blob, fn_pid_off)[0] == menu_spec.PID_FN_INTV
            )
            checks["min_ss.vu4_slot29_retargeted_and_pid_0xeb"] = (
                fn29_rel is not None
                and fn29_rel[0] == PVD.R_ARM_ABS32
                and fn29_sym == PVD.FN_ANCHOR_SYM.decode()
                and fn29_word == exp_fn_minss_delta
                and struct.unpack_from("<I", vu4_blob, fn29_pid_off)[0] == menu_spec.PID_MIN_SS
            )
            checks["fn_shortcut.vu4_cursor_bst_strictly_sorted_exact"] = (
                bst_bytes == PVD.FN_CURSOR_BST_NEW
                and bst_dis == PVD.FN_CURSOR_BST_EXPECTED_DISASM
            )

        # Verify CautionConfig.so Fn Drive Mode (10) + 12x Function Menu Set. (30) + Fn root + min_ss_node (24 opts)
        with io.BytesIO(caution_blob) as f:
            cau_elf = ELFFile(f)
            cau_loads = [s.header for s in cau_elf.iter_segments() if s.header["p_type"] == "PT_LOAD"]
            dynsym = cau_elf.get_section_by_name(".dynsym")
            sym_by_name = {s.name: s for s in dynsym.iter_symbols()}
            sym_emnt = sym_by_name[PC.DM_NODES_SYM.decode()]
            sym_num = sym_by_name[PC.DM_NUM_SYM.decode()]
            dm_cnt = struct.unpack_from("<I", caution_blob, _va_to_off(cau_loads, sym_num["st_value"]))[0]
            exp_dm_arr = int(manifest["caution"]["dm_arr_va"], 16)
            exp_dm_opt = int(manifest["caution"]["dm_opt_va"], 16)
            arr_words = struct.unpack_from("<10I", caution_blob, _va_to_off(cau_loads, exp_dm_arr))
            checks["drivemode.caution_fn_menu_10_entries_exact"] = (
                dm_cnt == 10
                and sym_emnt["st_value"] == exp_dm_arr
                and sym_emnt["st_size"] == 40
                and arr_words[PC.DM_INSERT_IDX] == exp_dm_opt
            )

            exp_cf1_opt = int(manifest["caution"]["fn_opt_cf1_va"], 16)
            exp_cf2_opt = int(manifest["caution"]["fn_opt_cf2_12_va"], 16)
            exp_cf_arrs = [int(x, 16) for x in manifest["caution"]["fn_cf_arr_vas"]]
            all_cf_ok = (len(exp_cf_arrs) == PC.FN_CF_SLOTS)
            for slot in range(1, PC.FN_CF_SLOTS + 1):
                s_arr = sym_by_name[PC.cf_nodes_sym(slot).decode()]
                s_num = sym_by_name[PC.cf_num_sym(slot).decode()]
                cnt = struct.unpack_from("<I", caution_blob, _va_to_off(cau_loads, s_num["st_value"]))[0]
                exp_va = exp_cf_arrs[slot - 1]
                cf_words = struct.unpack_from("<30I", caution_blob, _va_to_off(cau_loads, exp_va))
                want_opt = exp_cf1_opt if slot == 1 else exp_cf2_opt
                all_cf_ok &= (
                    cnt == 30
                    and s_arr["st_value"] == exp_va
                    and s_arr["st_size"] == 120
                    and cf_words[PC.FN_CF_INSERT_IDX] == want_opt
                    and cf_words[PC.FN_CF_INSERT_IDX + 1] != 0
                )
            checks["fn_shortcut.caution_all_12_customfn_arrays_30_entries_exact"] = all_cf_ok

            exp_fn_root = int(manifest["caution"]["fn_root_va"], 16)
            fn_ref_va = sym_by_name[PC.FN_REF_SYMBOL.decode()]["st_value"]
            ref_va = sym_by_name[PC.REF_SYMBOL.decode()]["st_value"]
            fn_root_node = struct.unpack_from("<10I", caution_blob, _va_to_off(cau_loads, exp_fn_root))
            checks["fn_shortcut.caution_fn_root_delta_and_1_child_exact"] = (
                ((exp_fn_root - fn_ref_va) & 0xFFFFFFFF) == int(manifest["caution"]["fn_root_delta"], 16)
                and fn_root_node[2] == 1
            )

            exp_min_ss = int(manifest["caution"]["min_ss_va"], 16)
            min_ss_node = struct.unpack_from("<10I", caution_blob, _va_to_off(cau_loads, exp_min_ss))
            checks["min_ss.caution_node_deltas_and_24_options_exact"] = (
                ((exp_min_ss - ref_va) & 0xFFFFFFFF) == int(manifest["caution"]["min_ss_delta"], 16)
                and ((exp_min_ss - fn_ref_va) & 0xFFFFFFFF) == int(manifest["caution"]["fn_min_ss_delta"], 16)
                and min_ss_node[2] == len(menu_spec.MIN_SS_SPEC.options) == 24
            )

            exp_ext_ss = int(manifest["caution"]["ext_shutter_va"], 16)
            ext_ss_node = struct.unpack_from("<10I", caution_blob, _va_to_off(cau_loads, exp_ext_ss))
            checks["ext_shutter.caution_node_delta_and_2_options_exact"] = (
                ((exp_ext_ss - ref_va) & 0xFFFFFFFF) == int(manifest["caution"]["ext_shutter_delta"], 16)
                and ext_ss_node[2] == len(menu_spec.EXT_SHUTTER_SPEC.options) == 2
            )

        # Verify ALL 21 Thumb-2 b.w hooks, 24 fingerprints, Tab 2 P3=6, Tab 2 P5=5, PID 0xE9/0xEA/0xEB/0xEC in libObj.so!
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        all_hooks_ok = True
        for h_name, h_va, _, _ in PL.HOOK_SITES:
            hook_ins = next(md.disasm(libobj_blob[h_va:h_va + 4], h_va))
            exp_target = int(manifest["libobj"]["hooks"][h_name]["target"], 16)
            ok = (hook_ins.mnemonic == "b.w" and int(hook_ins.op_str.lstrip("#"), 16) == exp_target)
            all_hooks_ok &= ok
        checks["code.all_21_libobj_bw_hooks_exact"] = (len(PL.HOOK_SITES) == 21 and all_hooks_ok)
        checks["code.all_24_direct_target_fingerprints_exact"] = (
            len(PL.DIRECT_TARGET_FINGERPRINTS) == 24
            and all(
                libobj_blob[t_va:t_va + 4] == t_orig
                for _, t_va, t_orig in PL.DIRECT_TARGET_FINGERPRINTS
            )
        )
        checks["code.tab2_p3_count_is_6"] = (
            struct.unpack_from("<H", libobj_blob, PL.TAB2_P3_MOVS_VA)[0] == 0x2206
        )
        checks["code.tab2_p5_count_is_5"] = (
            struct.unpack_from("<H", libobj_blob, PL.TAB2_P5_MOVS_VA)[0] == 0x2205
        )
        checks["code.pid_bounds_updated_to_0xef"] = all(
            libobj_blob[va:va + 2] == new_b for va, _, new_b, *_ in PL.PID_SITES
        )
        with io.BytesIO(libobj_blob) as f:
            lo_elf = ELFFile(f)
            lo_loads = [s.header for s in lo_elf.iter_segments() if s.header["p_type"] == "PT_LOAD"]
            lo_dyn = next(s.header for s in lo_elf.iter_segments() if s.header["p_type"] == "PT_DYNAMIC")
            lo_tags = dict(
                struct.unpack_from("<II", libobj_blob, lo_dyn["p_offset"] + 8 * i)
                for i in range(lo_dyn["p_filesz"] // 8)
            )
            lo_rel_off = _va_to_off(lo_loads, lo_tags[0x11])
            lo_rels = {
                struct.unpack_from("<I", libobj_blob, lo_rel_off + i)[0]:
                struct.unpack_from("<I", libobj_blob, lo_rel_off + i + 4)[0]
                for i in range(0, lo_tags[0x12], 8)
            }
            exp_d_va = int(manifest["libobj"]["new_d_va"], 16)
            exp_fn_desc = int(manifest["libobj"]["fn_intv_desc_va"], 16)
            d_ea_ptr = struct.unpack_from("<I", libobj_blob, _va_to_off(lo_loads, exp_d_va + 4 * menu_spec.PID_FN_INTV))[0]
            fn_desc = struct.unpack_from("<12I", libobj_blob, _va_to_off(lo_loads, exp_fn_desc))
            checks["fn_shortcut.libobj_pid_0xea_settingmenux_desc_and_relocs_exact"] = (
                d_ea_ptr == exp_fn_desc
                and lo_rels.get(exp_d_va + 4 * menu_spec.PID_FN_INTV) == PL.R_ARM_RELATIVE
                and fn_desc[0] == PL.NADA_VAL
                and fn_desc[5] == 0x0100
                and fn_desc[10] == PL.SETTINGMENUX_STR_VA
                and fn_desc[11] == menu_spec.VSM_BOOT_INTV
                and lo_rels.get(exp_fn_desc + 4) == PL.R_ARM_RELATIVE
                and lo_rels.get(exp_fn_desc + 40) == PL.R_ARM_RELATIVE
            )

            exp_minss_desc = int(manifest["libobj"]["min_ss_desc_va"], 16)
            d_eb_ptr = struct.unpack_from("<I", libobj_blob, _va_to_off(lo_loads, exp_d_va + 4 * menu_spec.PID_MIN_SS))[0]
            ms_desc = struct.unpack_from("<12I", libobj_blob, _va_to_off(lo_loads, exp_minss_desc))
            bk_id, vals_va, _, bk_sz = struct.unpack_from("<4I", libobj_blob, _va_to_off(lo_loads, ms_desc[8]))
            bk_vals = struct.unpack_from("<24I", libobj_blob, _va_to_off(lo_loads, vals_va))
            checks["min_ss.libobj_pid_0xeb_backup_and_fn_launcher_exact"] = (
                d_eb_ptr == exp_minss_desc
                and lo_rels.get(exp_d_va + 4 * menu_spec.PID_MIN_SS) == PL.R_ARM_RELATIVE
                and ms_desc[0] == PL.NADA_VAL
                and ms_desc[5] == (24 << 8)
                and ms_desc[9] == 1
                and ms_desc[10] == PL.SETTINGMENUX_STR_VA
                and ms_desc[11] == menu_spec.VSM_BOOT_MIN_SS
                and bk_id == menu_spec.BK_MIN_SS
                and bk_sz == 1
                and bk_vals == menu_spec.MIN_SS_SPEC.backup_bytes
                and lo_rels.get(exp_minss_desc + 4) == PL.R_ARM_RELATIVE
                and lo_rels.get(exp_minss_desc + 32) == PL.R_ARM_RELATIVE
                and lo_rels.get(exp_minss_desc + 40) == PL.R_ARM_RELATIVE
                and lo_rels.get(ms_desc[8] + 4) == PL.R_ARM_RELATIVE
            )

            exp_ext_desc = int(manifest["libobj"]["ext_shutter_desc_va"], 16)
            d_ec_ptr = struct.unpack_from(
                "<I", libobj_blob, _va_to_off(lo_loads, exp_d_va + 4 * menu_spec.PID_EXT_SHUTTER)
            )[0]
            es_desc = struct.unpack_from("<12I", libobj_blob, _va_to_off(lo_loads, exp_ext_desc))
            es_bk_id, es_vals_va, _, es_bk_sz = struct.unpack_from("<4I", libobj_blob, _va_to_off(lo_loads, es_desc[8]))
            es_bk_vals = struct.unpack_from("<2I", libobj_blob, _va_to_off(lo_loads, es_vals_va))
            checks["ext_shutter.libobj_pid_0xec_backup_0x01070147_desc_exact"] = (
                d_ec_ptr == exp_ext_desc
                and lo_rels.get(exp_d_va + 4 * menu_spec.PID_EXT_SHUTTER) == PL.R_ARM_RELATIVE
                and es_desc[0] == PL.NADA_VAL
                and es_desc[5] == (2 << 8)
                and es_desc[9] == 1
                and es_bk_id == menu_spec.BK_EXT_SHUTTER
                and es_bk_sz == 1
                and es_bk_vals == (0, 1)
                and lo_rels.get(exp_ext_desc + 4) == PL.R_ARM_RELATIVE
                and lo_rels.get(exp_ext_desc + 32) == PL.R_ARM_RELATIVE
                and lo_rels.get(es_desc[8] + 4) == PL.R_ARM_RELATIVE
            )

            # Verify PID 0xE9 (disp_sleep) migrated cleanly off colliding 0x010700C2 -> 0x01070121
            d_e9_ptr = struct.unpack_from("<I", libobj_blob, _va_to_off(lo_loads, exp_d_va + 4 * 0xE9))[0]
            e9_desc = struct.unpack_from("<12I", libobj_blob, _va_to_off(lo_loads, d_e9_ptr))
            e9_bk_id = struct.unpack_from("<I", libobj_blob, _va_to_off(lo_loads, e9_desc[8]))[0]
            checks["backup.pid_0xe9_disp_sleep_migrated_to_0x01070121"] = (e9_bk_id == 0x01070121)

            # Verify Tab 2 P5 array relocation points to ext_shutter_delta via REF_SYMBOL
            exp_p5_arr = int(manifest["libobj"]["new_tab2_p5_arr_va"], 16)
            p5_words = struct.unpack_from("<5I", libobj_blob, _va_to_off(lo_loads, exp_p5_arr))
            checks["ext_shutter.libobj_tab2_p5_slot4_reloc_exact"] = (
                p5_words[4] == int(manifest["caution"]["ext_shutter_delta"], 16)
                and (lo_rels.get(exp_p5_arr + 16, 0) & 0xFF) == PL.R_ARM_ABS32
            )

        # 7. UXC slot-bounds & localized string check on extracted string_*.uxc
        uxc_ok = True
        for rel in want_files:
            if not rel.startswith("share/app/string_"):
                continue
            raw_u = img_new.read_file_bytes(img_new.lookup(rel))
            head, _, recs = patch_uxc.parse_uxc(raw_u)
            slots = (len(head) - 0x10) // 2
            uxc_ok &= all(0 <= rid - patch_uxc.FIRST_ID < slots for rid in recs)
            uxc_ok &= (patch_uxc.build_uxc(head, recs) == raw_u)
            lang = menu_spec.lang_for_uxc_filename(Path(rel).name)
            for check_key in (
                menu_spec.FOLDER_TITLE_KEY,
                menu_spec.DM_TITLE_KEY,
                menu_spec.MIN_SS_SPEC.title_key,
                menu_spec.EXT_SHUTTER_TITLE_KEY,
                "opt_ss_std",
                "opt_ss_1_8000",
                "opt_ss_30s",
            ):
                rid = int(manifest["uxc_ids"][check_key], 16)
                uxc_ok &= (recs.get(rid) == menu_spec.STRINGS[check_key][lang])
        checks["uxc.all_26_languages_in_bounds_and_localized"] = uxc_ok

    all_ok = all(checks.values())
    report = {
        "ALL_CHECKS_PASSED": all_ok,
        "passed_count": sum(checks.values()),
        "total_checks": len(checks),
        "checks": checks,
        "recompressed_nflasha15_tpzl_blocks": len(recomp),
        "recompressed_nflasha3_tpzl_blocks": n3_recomp,
        "sums_verified": sum_res["checked"],
        "elf_viewunified3": elf_vu3,
        "elf_viewunified4": elf_vu4,
        "elf_caution": elf_caution,
        "elf_libobj": elf_libobj,
    }
    print(json.dumps(report, indent=2))
    if not all_ok:
        failed_keys = [k for k, v in checks.items() if not v]
        raise SystemExit(f"AUDIT FAILED: {failed_keys}")
    return report


if __name__ == "__main__":
    run_audit()
