"""Pure-Python ext2 (bs=1024) surgical file replacer & offline structural fsck.

Designed specifically for Sony CXD90014 `nflasha15` (`/usr`) read-only ext2 images:
- Replaces existing regular files in-place by inode number, preserving exact
  `i_mode`, `i_uid`, `i_gid`, `i_links_count`, timestamps, and directory layout.
- Supports direct (`i_block[0..11]`), single-indirect (`i_block[12]`), and
  double-indirect (`i_block[13]`) block trees (up to ~64 MiB per file at bs=1024).
- Strictly avoids touching bytes `0..65535` (TPZL Block 0) for any data/indirect
  block allocation, asserts only superblock/GDT free-block counters change in
  Block 0, runs full offline structural fsck BEFORE restoring Block 0 byte-for-byte
  to Sony's original, and re-verifies every file in the entire filesystem AFTER
  restoring Block 0.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

BLOCK0_SIZE = 65536
SB_OFFSET = 1024
EXT2_MAGIC = 0xEF53
S_IFMT = 0xF000
S_IFDIR = 0x4000
S_IFREG = 0x8000
S_IFLNK = 0xA000


@dataclass
class Ext2Info:
    bs: int
    s_inodes_count: int
    s_blocks_count: int
    s_free_blocks_count: int
    s_free_inodes_count: int
    s_first_data_block: int
    s_blocks_per_group: int
    s_inodes_per_group: int
    s_inode_size: int
    ngroups: int
    gdt_off: int


class Ext2Image:
    def __init__(self, data: bytearray | bytes) -> None:
        self.buf = bytearray(data)
        self.info = self._parse_super()

    def _parse_super(self) -> Ext2Info:
        b = self.buf
        magic = struct.unpack_from("<H", b, SB_OFFSET + 56)[0]
        if magic != EXT2_MAGIC:
            raise ValueError(f"Bad ext2 magic: 0x{magic:04x}")
        (
            s_inodes, s_blocks, _s_rblocks, s_fblocks, s_finodes,
            s_first, s_log_bs, _s_log_fs, s_bpg, _s_fpg, s_ipg
        ) = struct.unpack_from("<11I", b, SB_OFFSET)
        s_rev = struct.unpack_from("<I", b, SB_OFFSET + 76)[0]
        s_inode_size = struct.unpack_from("<H", b, SB_OFFSET + 88)[0] if s_rev >= 1 else 128
        bs = 1024 << s_log_bs
        ngroups = (s_blocks - s_first + s_bpg - 1) // s_bpg
        gdt_off = (s_first + 1) * bs
        return Ext2Info(
            bs=bs,
            s_inodes_count=s_inodes,
            s_blocks_count=s_blocks,
            s_free_blocks_count=s_fblocks,
            s_free_inodes_count=s_finodes,
            s_first_data_block=s_first,
            s_blocks_per_group=s_bpg,
            s_inodes_per_group=s_ipg,
            s_inode_size=s_inode_size,
            ngroups=ngroups,
            gdt_off=gdt_off,
        )

    def _group_desc(self, g: int) -> tuple[int, int, int, int, int, int]:
        return struct.unpack_from("<3I3H", self.buf, self.info.gdt_off + 32 * g)

    def _set_group_free_blocks(self, g: int, val: int) -> None:
        struct.pack_into("<H", self.buf, self.info.gdt_off + 32 * g + 12, val)

    def _set_super_free_blocks(self, val: int) -> None:
        struct.pack_into("<I", self.buf, SB_OFFSET + 12, val)
        self.info.s_free_blocks_count = val

    def inode_offset(self, ino: int) -> int:
        assert 1 <= ino <= self.info.s_inodes_count
        g = (ino - 1) // self.info.s_inodes_per_group
        idx = (ino - 1) % self.info.s_inodes_per_group
        _, _, itable_blk, _, _, _ = self._group_desc(g)
        return itable_blk * self.info.bs + idx * self.info.s_inode_size

    def read_inode(self, ino: int) -> dict:
        off = self.inode_offset(ino)
        b = self.buf
        mode, uid, size_lo = struct.unpack_from("<HHI", b, off)
        gid = struct.unpack_from("<H", b, off + 24)[0]
        links = struct.unpack_from("<H", b, off + 26)[0]
        blocks_512 = struct.unpack_from("<I", b, off + 28)[0]
        iblk = list(struct.unpack_from("<15I", b, off + 40))
        return {
            "ino": ino,
            "off": off,
            "mode": mode,
            "uid": uid,
            "gid": gid,
            "size": size_lo,
            "links": links,
            "blocks_512": blocks_512,
            "iblk": iblk,
        }

    def collect_inode_blocks(self, inode: dict) -> tuple[list[int], list[int]]:
        """Return (data_blocks_in_order, metadata_indirect_blocks)."""
        bs = self.info.bs
        ptrs = bs // 4
        iblk = inode["iblk"]
        n_data = (inode["size"] + bs - 1) // bs
        data_blks: list[int] = []
        meta_blks: list[int] = []

        # Direct blocks 0..11
        for i in range(12):
            if len(data_blks) >= n_data:
                break
            data_blks.append(iblk[i])

        # Single indirect (iblk[12])
        if iblk[12]:
            meta_blks.append(iblk[12])
            if len(data_blks) < n_data:
                ents = struct.unpack_from(f"<{ptrs}I", self.buf, iblk[12] * bs)
                for blk in ents:
                    if len(data_blks) >= n_data:
                        break
                    data_blks.append(blk)

        # Double indirect (iblk[13])
        if iblk[13]:
            meta_blks.append(iblk[13])
            l1 = struct.unpack_from(f"<{ptrs}I", self.buf, iblk[13] * bs)
            for b1 in l1:
                if not b1:
                    if len(data_blks) < n_data:
                        # Sparse hole inside double indirect
                        for _ in range(min(ptrs, n_data - len(data_blks))):
                            data_blks.append(0)
                    continue
                meta_blks.append(b1)
                if len(data_blks) < n_data:
                    l2 = struct.unpack_from(f"<{ptrs}I", self.buf, b1 * bs)
                    for b2 in l2:
                        if len(data_blks) >= n_data:
                            break
                        data_blks.append(b2)

        # Triple indirect (iblk[14]) — read support for completeness
        if iblk[14]:
            meta_blks.append(iblk[14])
            l1 = struct.unpack_from(f"<{ptrs}I", self.buf, iblk[14] * bs)
            for b1 in l1:
                if not b1:
                    continue
                meta_blks.append(b1)
                l2 = struct.unpack_from(f"<{ptrs}I", self.buf, b1 * bs)
                for b2 in l2:
                    if not b2:
                        continue
                    meta_blks.append(b2)
                    if len(data_blks) < n_data:
                        l3 = struct.unpack_from(f"<{ptrs}I", self.buf, b2 * bs)
                        for b3 in l3:
                            if len(data_blks) >= n_data:
                                break
                            data_blks.append(b3)

        return data_blks, meta_blks

    def read_file_bytes(self, ino: int) -> bytes:
        inode = self.read_inode(ino)
        bs = self.info.bs
        size = inode["size"]
        if size == 0:
            return b""
        data_blks, _ = self.collect_inode_blocks(inode)
        chunks = []
        zero_blk = b"\0" * bs
        for blk in data_blks:
            chunks.append(zero_blk if blk == 0 else bytes(self.buf[blk * bs:(blk + 1) * bs]))
        return b"".join(chunks)[:size]

    def list_dir(self, dir_ino: int) -> list[tuple[str, int, int]]:
        raw = self.read_file_bytes(dir_ino)
        entries: list[tuple[str, int, int]] = []
        pos = 0
        while pos + 8 <= len(raw):
            ino, rec_len, name_len, ftype = struct.unpack_from("<IHBB", raw, pos)
            if rec_len == 0:
                break
            if ino != 0:
                name = raw[pos + 8:pos + 8 + name_len].decode("utf-8", "replace")
                entries.append((name, ino, ftype))
            pos += rec_len
        return entries

    def lookup(self, path: str) -> int:
        ino = 2
        for part in [p for p in path.strip("/").split("/") if p]:
            found = None
            for name, child_ino, _ in self.list_dir(ino):
                if name == part:
                    found = child_ino
                    break
            if found is None:
                raise FileNotFoundError(f"Path not found in ext2: /{path}")
            ino = found
        return ino

    def _block_group_and_bit(self, blk: int) -> tuple[int, int, int]:
        rel = blk - self.info.s_first_data_block
        g = rel // self.info.s_blocks_per_group
        bit = rel % self.info.s_blocks_per_group
        bb_blk = self._group_desc(g)[0]
        return g, bb_blk * self.info.bs, bit

    def _is_block_used(self, blk: int) -> bool:
        _, bmp_off, bit = self._block_group_and_bit(blk)
        return bool(self.buf[bmp_off + (bit >> 3)] & (1 << (bit & 7)))

    def _free_block(self, blk: int) -> None:
        if blk == 0:
            return
        g, bmp_off, bit = self._block_group_and_bit(blk)
        byte_off = bmp_off + (bit >> 3)
        mask = 1 << (bit & 7)
        assert self.buf[byte_off] & mask, f"Double free of ext2 block {blk}"
        self.buf[byte_off] &= ~mask
        fb = self._group_desc(g)[3]
        self._set_group_free_blocks(g, fb + 1)
        self._set_super_free_blocks(self.info.s_free_blocks_count + 1)

    def _alloc_blocks(self, count: int, min_group: int = 1) -> list[int]:
        """Allocate `count` contiguous-preferred free blocks from groups >= `min_group`."""
        out: list[int] = []
        info = self.info
        for g in range(min_group, info.ngroups):
            if len(out) >= count:
                break
            bb_blk, _, _, fb, _, _ = self._group_desc(g)
            if fb == 0:
                continue
            bmp_off = bb_blk * info.bs
            max_bits = min(
                info.s_blocks_per_group,
                info.s_blocks_count - (info.s_first_data_block + g * info.s_blocks_per_group),
            )
            allocated_here = 0
            for bit in range(max_bits):
                if len(out) >= count:
                    break
                b_idx = bmp_off + (bit >> 3)
                mask = 1 << (bit & 7)
                if not (self.buf[b_idx] & mask):
                    self.buf[b_idx] |= mask
                    blk = info.s_first_data_block + g * info.s_blocks_per_group + bit
                    # Never touch TPZL Block 0 (< 65536 bytes)
                    assert blk * info.bs >= BLOCK0_SIZE
                    out.append(blk)
                    allocated_here += 1
            if allocated_here:
                self._set_group_free_blocks(g, fb - allocated_here)
                self._set_super_free_blocks(info.s_free_blocks_count - allocated_here)
        if len(out) < count:
            raise RuntimeError(f"Out of ext2 free blocks in groups >= {min_group} (need {count}, got {len(out)})")
        return out

    def replace_file(self, rel_path: str, new_data: bytes) -> dict:
        """Replace an existing regular file in-place, reusing existing blocks when possible.

        Reusing existing block positions keeps unchanged 64 KiB TPZL blocks byte-identical
        to Sony's factory image while preserving exact inode number, mode, uid, gid, and
        directory entries.
        """
        ino = self.lookup(rel_path)
        inode = self.read_inode(ino)
        assert (inode["mode"] & S_IFMT) == S_IFREG, f"Not a regular file: {rel_path}"
        assert inode["off"] >= BLOCK0_SIZE, f"Refusing to modify inode inside TPZL Block 0: {rel_path}"

        bs = self.info.bs
        ptrs = bs // 4
        old_data_blks, old_meta_blks = self.collect_inode_blocks(inode)
        old_data_nonzero = [b for b in old_data_blks if b != 0]
        old_meta_nonzero = [b for b in old_meta_blks if b != 0]

        n_data = (len(new_data) + bs - 1) // bs if new_data else 0
        need_s1 = 1 if n_data > 12 else 0
        rem_after_s1 = max(0, n_data - (12 + ptrs))
        need_d1 = 1 if rem_after_s1 > 0 else 0
        need_d2 = (rem_after_s1 + ptrs - 1) // ptrs if rem_after_s1 > 0 else 0
        assert need_d2 <= ptrs, f"File too large for double-indirect ext2: {len(new_data)} B"
        n_meta_need = need_s1 + need_d1 + need_d2

        # Reuse existing blocks in order so unmodified prefixes stay at identical disk offsets!
        reuse_data = old_data_nonzero[:n_data]
        surplus_data = old_data_nonzero[n_data:]
        reuse_meta = old_meta_nonzero[:n_meta_need]
        surplus_meta = old_meta_nonzero[n_meta_need:]

        for blk in (*surplus_data, *surplus_meta):
            self._free_block(blk)

        need_extra_data = n_data - len(reuse_data)
        need_extra_meta = n_meta_need - len(reuse_meta)
        extra_blks = (
            self._alloc_blocks(need_extra_data + need_extra_meta, min_group=0)
            if (need_extra_data + need_extra_meta) > 0
            else []
        )

        data_blks = reuse_data + extra_blks[:need_extra_data]
        meta_blks = reuse_meta + extra_blks[need_extra_data:]
        assert len(data_blks) == n_data and len(meta_blks) == n_meta_need

        for idx, blk in enumerate(data_blks):
            assert blk * bs >= BLOCK0_SIZE
            chunk = new_data[idx * bs:(idx + 1) * bs]
            padded = chunk.ljust(bs, b"\0")
            if self.buf[blk * bs:(blk + 1) * bs] != padded:
                self.buf[blk * bs:(blk + 1) * bs] = padded

        new_iblk = [0] * 15
        d_iter = iter(data_blks)
        m_iter = iter(meta_blks)

        for i in range(min(12, n_data)):
            new_iblk[i] = next(d_iter)

        if need_s1:
            s1_blk = next(m_iter)
            new_iblk[12] = s1_blk
            s1_table = [0] * ptrs
            for i in range(min(ptrs, n_data - 12)):
                s1_table[i] = next(d_iter)
            s1_raw = struct.pack(f"<{ptrs}I", *s1_table)
            if self.buf[s1_blk * bs:(s1_blk + 1) * bs] != s1_raw:
                self.buf[s1_blk * bs:(s1_blk + 1) * bs] = s1_raw

        if need_d1:
            d1_blk = next(m_iter)
            new_iblk[13] = d1_blk
            d1_table = [0] * ptrs
            left = rem_after_s1
            for j in range(need_d2):
                d2_blk = next(m_iter)
                d1_table[j] = d2_blk
                d2_table = [0] * ptrs
                cnt = min(ptrs, left)
                for k in range(cnt):
                    d2_table[k] = next(d_iter)
                left -= cnt
                d2_raw = struct.pack(f"<{ptrs}I", *d2_table)
                if self.buf[d2_blk * bs:(d2_blk + 1) * bs] != d2_raw:
                    self.buf[d2_blk * bs:(d2_blk + 1) * bs] = d2_raw
            assert left == 0
            d1_raw = struct.pack(f"<{ptrs}I", *d1_table)
            if self.buf[d1_blk * bs:(d1_blk + 1) * bs] != d1_raw:
                self.buf[d1_blk * bs:(d1_blk + 1) * bs] = d1_raw

        # Update inode in-place: ONLY i_size, i_blocks (512B sectors), and i_block[0..14]
        off = inode["off"]
        total_blocks = n_data + n_meta_need
        blocks_512 = total_blocks * (bs // 512)
        struct.pack_into("<I", self.buf, off + 4, len(new_data))
        struct.pack_into("<I", self.buf, off + 28, blocks_512)
        struct.pack_into("<15I", self.buf, off + 40, *new_iblk)

        got = self.read_file_bytes(ino)
        assert got == new_data, f"Immediate read-back mismatch for {rel_path}"
        return {
            "path": rel_path,
            "ino": ino,
            "old_size": inode["size"],
            "new_size": len(new_data),
            "reused_data_blocks": len(reuse_data),
            "new_data_blocks": need_extra_data,
            "total_blocks": total_blocks,
        }

    def walk_and_fsck(self, check_counters: bool = True) -> dict[str, tuple[str, int, int, int, int]]:
        """Full offline structural ext2 fsck & file manifest generator.

        Verifies:
        - Every directory & regular file reachable from `/` has valid in-bounds blocks.
        - ZERO doubly-allocated blocks across the entire filesystem.
        - Every referenced block is marked allocated in its group's block bitmap.
        - When `check_counters=True`, every group's `bg_free_blocks_count` and
          superblock's `s_free_blocks_count` match the actual bitmaps 100%.

        Returns {rel_path: (sha256, size, mode, uid, gid)} for all regular files.
        """
        info = self.info
        manifest: dict[str, tuple[str, int, int, int, int]] = {}
        seen_inodes: set[int] = set()
        used_blocks: dict[int, int] = {}  # blk -> first owner inode

        def record_blocks(ino: int, inode: dict) -> None:
            ftype = inode["mode"] & S_IFMT
            if ftype == S_IFLNK and inode["size"] <= 60:
                return  # Fast inline symlink stored inside i_block[]
            if ftype not in (S_IFREG, S_IFDIR, S_IFLNK):
                return
            dblks, mblks = self.collect_inode_blocks(inode)
            for blk in (*dblks, *mblks):
                if blk == 0:
                    continue
                assert info.s_first_data_block <= blk < info.s_blocks_count, \
                    f"Out-of-range block {blk} in inode {ino}"
                assert blk not in used_blocks, \
                    f"Doubly-allocated block {blk} shared by inodes {used_blocks[blk]} and {ino}"
                assert self._is_block_used(blk), \
                    f"Referenced block {blk} (inode {ino}) NOT marked used in block bitmap!"
                used_blocks[blk] = ino

        def walk_dir(dir_ino: int, prefix: str) -> None:
            if dir_ino in seen_inodes:
                return
            seen_inodes.add(dir_ino)
            dinode = self.read_inode(dir_ino)
            record_blocks(dir_ino, dinode)
            for name, cino, _ in self.list_dir(dir_ino):
                if name in (".", ".."):
                    continue
                rel = f"{prefix}/{name}" if prefix else name
                cinode = self.read_inode(cino)
                ftype = cinode["mode"] & S_IFMT
                if ftype == S_IFDIR:
                    walk_dir(cino, rel)
                else:
                    if cino not in seen_inodes:
                        seen_inodes.add(cino)
                        record_blocks(cino, cinode)
                    if ftype == S_IFREG:
                        raw = self.read_file_bytes(cino)
                        manifest[rel] = (
                            hashlib.sha256(raw).hexdigest(),
                            len(raw),
                            cinode["mode"],
                            cinode["uid"],
                            cinode["gid"],
                        )

        walk_dir(2, "")

        if check_counters:
            total_free = 0
            for g in range(info.ngroups):
                bb_blk, _, _, fb, _, _ = self._group_desc(g)
                bmp_off = bb_blk * info.bs
                max_bits = min(
                    info.s_blocks_per_group,
                    info.s_blocks_count - (info.s_first_data_block + g * info.s_blocks_per_group),
                )
                ones = 0
                for bit in range(max_bits):
                    if self.buf[bmp_off + (bit >> 3)] & (1 << (bit & 7)):
                        ones += 1
                actual_free = max_bits - ones
                assert actual_free == fb, f"Group {g} free block mismatch: GDT={fb} vs bitmap={actual_free}"
                total_free += actual_free
            assert total_free == info.s_free_blocks_count, \
                f"Superblock free block mismatch: SB={info.s_free_blocks_count} vs sum={total_free}"

        return manifest


def patch_ext2_preserving_block0(orig_ext2: bytes, replacements: dict[str, bytes]) -> tuple[bytes, dict]:
    """Replace files in `orig_ext2`, run full fsck, restore TPZL Block 0, and re-verify."""
    orig_img = Ext2Image(orig_ext2)
    orig_manifest = orig_img.walk_and_fsck(check_counters=True)

    img = Ext2Image(orig_ext2)
    file_reports = []
    for rel_path, new_bytes in sorted(replacements.items()):
        file_reports.append(img.replace_file(rel_path, new_bytes))

    # Full fsck BEFORE restoring Block 0 (strict bitmap + counter checks!)
    pre_manifest = img.walk_and_fsck(check_counters=True)

    # Assert ONLY superblock/GDT free-block counters changed inside Block 0 (0..65535)
    sb_free_offsets = {SB_OFFSET + i for i in (12, 13, 14, 15)}
    gdt_free_offsets = {
        img.info.gdt_off + 32 * g + k
        for g in range(img.info.ngroups)
        for k in (12, 13)
    }
    allowed_block0 = sb_free_offsets | gdt_free_offsets
    diff0 = {i for i in range(BLOCK0_SIZE) if img.buf[i] != orig_ext2[i]}
    illegal0 = sorted(diff0 - allowed_block0)
    assert not illegal0, f"Unexpected modifications inside TPZL Block 0: {illegal0[:16]}"

    # Restore TPZL Block 0 (0..65535) byte-identically to Sony's original (R55 rule!)
    img.buf[:BLOCK0_SIZE] = orig_ext2[:BLOCK0_SIZE]
    patched_bytes = bytes(img.buf)

    # Re-parse from scratch & verify EVERY file in the entire ext2 image AFTER Block 0 restore!
    post_img = Ext2Image(patched_bytes)
    post_manifest = post_img.walk_and_fsck(check_counters=False)
    assert post_manifest == pre_manifest, "Restoring Block 0 altered file contents!"
    assert set(post_manifest) == set(orig_manifest), "File list changed unexpectedly!"

    for rel, meta in post_manifest.items():
        if rel in replacements:
            exp_sha = hashlib.sha256(replacements[rel]).hexdigest()
            exp_sz = len(replacements[rel])
            orig_meta = orig_manifest[rel]
            assert meta[0] == exp_sha and meta[1] == exp_sz, f"Patched file content mismatch: {rel}"
            assert meta[2:] == orig_meta[2:], f"Patched file mode/uid/gid changed: {rel}"
        else:
            assert meta == orig_manifest[rel], f"Untouched file unexpectedly altered: {rel}"

    report = {
        "total_files_verified": len(post_manifest),
        "patched_files_count": len(replacements),
        "block0_diff_bytes_before_restore": len(diff0),
        "block0_restored_identical": patched_bytes[:BLOCK0_SIZE] == orig_ext2[:BLOCK0_SIZE],
        "free_blocks_before": orig_img.info.s_free_blocks_count,
        "free_blocks_after": img.info.s_free_blocks_count,
        "files": file_reports,
    }
    return patched_bytes, report
