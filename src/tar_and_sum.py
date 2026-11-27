"""Conservative `firmware.tar` ustar rebuilder and Sony `.sum` CRC-32 updater/verifier.

Preserves exact member order, ustar headers, permissions, timestamps, and trailing
padding of Sony's stock `firmware.tar`. Supports replacing specific members
(e.g. `0700_part_image/dev/nflasha15`, `0701_part_image_sum/part_image.sum`) and
cleanly removing `0700_part_image/dev/nflasha5` (WBI snapshot, mandatory whenever
`nflasha15` is modified so Loader3 performs a clean cold boot instead of resuming
a stale TPZL TOC).
"""
from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass

TAR_BLOCK = 512


def sony_crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def update_sum_text(
    text: str,
    updates: dict[str, bytes],
    remove_paths: set[str] | None = None,
) -> tuple[str, dict]:
    """Update CRC-32 & size lines in a Sony `.sum` file and optionally remove lines."""
    remove_paths = remove_paths or set()
    seen_updates: set[str] = set()
    seen_removes: set[str] = set()
    out_lines: list[str] = []
    changes: dict[str, dict] = {}

    for line in text.splitlines():
        if not line or line.startswith("#"):
            out_lines.append(line)
            continue
        fields = line.split(",")
        if len(fields) < 11:
            raise ValueError(f"Invalid .sum line: {line!r}")
        path = fields[10]
        if path in remove_paths:
            seen_removes.add(path)
            changes[path] = {"removed": True, "line_before": line}
            continue
        if path in updates:
            if path in seen_updates:
                raise ValueError(f"Duplicate path in .sum: {path}")
            seen_updates.add(path)
            blob = updates[path]
            old_crc, old_sz = fields[1], fields[2]
            fields[1] = f"{sony_crc32(blob):08x}"
            fields[2] = f"{len(blob):08x}"
            new_line = ",".join(fields)
            assert len(new_line) == len(line), f".sum line length changed for {path}"
            out_lines.append(new_line)
            changes[path] = {
                "crc_before": old_crc,
                "size_before": old_sz,
                "crc_after": fields[1],
                "size_after": fields[2],
            }
            continue
        out_lines.append(line)

    missing_u = set(updates) - seen_updates
    missing_r = remove_paths - seen_removes
    if missing_u:
        raise KeyError(f"Paths not found in .sum for update: {sorted(missing_u)}")
    if missing_r:
        raise KeyError(f"Paths not found in .sum for removal: {sorted(missing_r)}")

    result = "\n".join(out_lines)
    if text.endswith("\n"):
        result += "\n"
    return result, changes


def verify_all_sums(members: dict[str, bytes]) -> dict:
    """Verify every `.sum` manifest inside an unpacked `firmware.tar` dict."""
    checked = 0
    failed: list[str] = []
    for name, raw in sorted(members.items()):
        if not name.endswith(".sum"):
            continue
        for line in raw.decode("ascii").splitlines():
            fields = line.split(",")
            if len(fields) < 11 or fields[0] != "0":
                continue
            rel = fields[10]
            target = next((m for m in members if m.endswith("/" + rel)), None)
            if target is None:
                failed.append(f"{name}: missing member for {rel}")
                continue
            checked += 1
            blob = members[target]
            if fields[1] != f"{sony_crc32(blob):08x}" or fields[2] != f"{len(blob):08x}":
                failed.append(f"{name}: mismatch for {rel}")
    return {"checked": checked, "failed": failed}


def _parse_octal(field: bytes) -> int:
    if field and (field[0] & 0x80):
        val = int.from_bytes(field, "big", signed=True)
        return val & ((1 << (len(field) * 8 - 1)) - 1)
    s = field.split(b"\0", 1)[0].strip()
    return int(s, 8) if s else 0


def _encode_octal(field_len: int, value: int) -> bytes:
    width = field_len - 1
    txt = f"{value:0{width}o}".encode("ascii")
    if len(txt) > width:
        raise ValueError(f"Value {value} does not fit in {field_len}-byte octal field")
    return txt + b"\0"


def _header_checksum(hdr: bytes) -> int:
    tmp = bytearray(hdr)
    tmp[148:156] = b" " * 8
    return sum(tmp)


def _rebuild_header(hdr: bytes, new_size: int) -> bytes:
    out = bytearray(hdr)
    out[124:136] = _encode_octal(12, new_size)
    out[148:156] = b" " * 8
    chk = _header_checksum(bytes(out))
    out[148:156] = f"{chk:06o}".encode("ascii") + b"\0 "
    return bytes(out)


@dataclass(frozen=True)
class TarEntry:
    name: str
    header: bytes
    data: bytes
    offset: int
    typeflag: bytes


def parse_tar(raw: bytes) -> tuple[list[TarEntry], bytes]:
    entries: list[TarEntry] = []
    pos = 0
    while pos + TAR_BLOCK <= len(raw):
        hdr = raw[pos:pos + TAR_BLOCK]
        if hdr == b"\0" * TAR_BLOCK:
            break
        name = hdr[:100].split(b"\0", 1)[0].decode("utf-8", "replace")
        size = _parse_octal(hdr[124:136])
        tflag = hdr[156:157]
        if tflag not in (b"0", b"\0", b"5", b"7"):
            raise ValueError(f"Unsupported tar member type {tflag!r}: {name}")
        body = raw[pos + TAR_BLOCK:pos + TAR_BLOCK + size]
        if len(body) != size:
            raise ValueError(f"Truncated tar member: {name}")
        entries.append(TarEntry(name, hdr, body, pos, tflag))
        pos += TAR_BLOCK + ((size + TAR_BLOCK - 1) // TAR_BLOCK) * TAR_BLOCK
    if not entries:
        raise ValueError("Empty tar archive")
    return entries, raw[pos:]


def rebuild_tar(raw: bytes, replacements: dict[str, bytes | None]) -> tuple[bytes, list[dict]]:
    """Replace or delete (`None`) members by name while keeping everything else byte-identical."""
    entries, trailing = parse_tar(raw)
    names = {e.name for e in entries}
    missing = set(replacements) - names
    if missing:
        raise KeyError(f"Tar members not found: {sorted(missing)}")

    out = bytearray()
    report: list[dict] = []
    for e in entries:
        if e.name in replacements:
            body = replacements[e.name]
            if body is None:
                report.append({"name": e.name, "removed": True, "old_size": len(e.data)})
                continue
            out.extend(_rebuild_header(e.header, len(body)))
            out.extend(body)
            out.extend(b"\0" * ((-len(body)) % TAR_BLOCK))
            report.append({
                "name": e.name,
                "old_size": len(e.data),
                "new_size": len(body),
                "sha256_old": hashlib.sha256(e.data).hexdigest(),
                "sha256_new": hashlib.sha256(body).hexdigest(),
            })
        else:
            out.extend(e.header)
            out.extend(e.data)
            out.extend(b"\0" * ((-len(e.data)) % TAR_BLOCK))
    out.extend(trailing)
    result = bytes(out)

    # Structural round-trip verification
    check_entries, check_trailing = parse_tar(result)
    kept = [e for e in entries if not (e.name in replacements and replacements[e.name] is None)]
    assert [e.name for e in check_entries] == [e.name for e in kept]
    assert check_trailing == trailing
    for old, new in zip(kept, check_entries):
        if old.name in replacements:
            assert new.data == replacements[old.name]
        else:
            assert new.header == old.header and new.data == old.data
    return result, report
