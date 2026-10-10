"""TDX server-side board/report files.

The byte layouts follow the read-only ``GetBlockInfo``/``GetReportFile``
implementations in pytdx and the injoyai/tdx reference client.  This module
intentionally does not replace :mod:`tdx_local_files`: that module owns local
``vipdoc`` bar files, while these functions parse server-delivered board and
statistics files.  A parallel branch is adding local ``block_*.dat`` and
``tdxzs.cfg`` readers; the pure byte parsers here are the wire-client surface
and keep the overlap explicit for later consolidation.
"""

from __future__ import annotations

from io import BytesIO
import struct
from typing import Any, Iterable
import zipfile

from . import tdx_protocol


REPORT_COMMAND = 0x06B9
BLOCK_META_COMMAND = 0x02C5
FILE_CHUNK_SIZE = 0x7530
MAX_FILE_SIZE = 64 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED = 128 * 1024 * 1024
FILENAME_BYTES = 100
BLOCK_FILES = frozenset({"block_gn.dat", "block_fg.dat", "block_zs.dat", "block_hy.dat", "block.dat"})
REPORT_FILES = frozenset({"zhb.zip"})


class TdxFileError(tdx_protocol.TdxProtocolError):
    """Malformed or over-sized TDX file response."""


def _filename(value: str, width: int = FILENAME_BYTES) -> bytes:
    encoded = value.encode("utf-8")
    if not value or "\x00" in value or len(encoded) > width:
        raise ValueError(f"TDX filename must be non-empty and fit in {width} bytes")
    return encoded.ljust(width, b"\x00")


def build_file_meta_request(filename: str) -> bytes:
    """Build the 0x02C5 block-file size query."""
    name = _filename(filename, 40)
    return bytes.fromhex("0c 39 18 69 00 01 2a 00 2a 00 c5 02") + name[:40]


def build_file_chunk_request(filename: str, offset: int = 0, size: int = FILE_CHUNK_SIZE) -> bytes:
    """Build the 0x06B9 block/config chunk request (offset, size, filename)."""
    if offset < 0 or size <= 0 or size > FILE_CHUNK_SIZE:
        raise ValueError("invalid TDX file chunk bounds")
    return (bytes.fromhex("0c 37 18 6a 00 01 6e 00 6e 00 b9 06")
            + struct.pack("<II100s", offset, size, _filename(filename)))


def build_report_file_request(filename: str, offset: int = 0, size: int = FILE_CHUNK_SIZE) -> bytes:
    """Build the report-file request used by pytdx ``GetReportFile``."""
    if offset < 0 or size <= 0 or size > FILE_CHUNK_SIZE:
        raise ValueError("invalid TDX report chunk bounds")
    raw = struct.pack("<HII100s", REPORT_COMMAND, offset, size, _filename(filename))
    return bytes.fromhex("0c 12 34 00 00 00") + struct.pack("<HH", len(raw), len(raw)) + raw


def parse_file_size(body: bytes) -> int:
    if len(body) < 4:
        raise TdxFileError("TDX file-size response is truncated")
    return struct.unpack_from("<I", body)[0]


build_file_size_request = build_file_meta_request


def parse_file_chunk(body: bytes) -> bytes:
    if len(body) < 4:
        raise TdxFileError("TDX file chunk response is truncated")
    size = struct.unpack_from("<I", body)[0]
    if size > len(body) - 4:
        raise TdxFileError("TDX file chunk length exceeds response")
    return body[4:4 + size]


def _text(data: bytes) -> str:
    return data.decode("gbk", "replace")


def _field(fields: list[str], index: int, default: str = "") -> str:
    return fields[index] if index < len(fields) else default


def _float(fields: list[str], index: int) -> float | None:
    try:
        return float(_field(fields, index).strip())
    except (TypeError, ValueError):
        return None


def _int(fields: list[str], index: int) -> int | None:
    try:
        return int(_field(fields, index).strip())
    except (TypeError, ValueError):
        return None


def parse_block_file(data: bytes) -> list[dict[str, Any]]:
    """Parse ``block_gn/fg/zs.dat`` fixed records into grouped rows."""
    if len(data) < 386:
        return []
    count = struct.unpack_from("<H", data, 384)[0]
    pos = 386
    rows: list[dict[str, Any]] = []
    for _ in range(count):
        if pos + 13 > len(data):
            break
        name = data[pos:pos + 9].split(b"\x00", 1)[0].decode("gbk", "replace").strip()
        member_count, block_type = struct.unpack_from("<HH", data, pos + 9)
        members_pos = pos + 13
        members: list[str] = []
        for index in range(member_count):
            start = members_pos + index * 7
            if start + 7 > len(data):
                break
            member = data[start:start + 7].split(b"\x00", 1)[0].decode("ascii", "ignore")
            if member:
                members.append(member)
        rows.append({"name": name, "type": block_type, "members": members, "member_count": len(members)})
        pos = members_pos + 400 * 7
    return rows


def parse_tdxzs(data: bytes) -> list[dict[str, Any]]:
    """Parse ``tdxzs.cfg``/``tdxzs3.cfg``; unknown columns remain in ``fields``."""
    rows = []
    for line in _text(data).splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < 2 or not fields[0] or not fields[1]:
            continue
        rows.append({"name": fields[0], "code": fields[1], "type": _int(fields, 2),
                     "subtype": _int(fields, 3), "ref": _field(fields, 5), "fields": fields})
    return rows


def parse_spblock(data: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in _text(data).splitlines():
        line = line.strip("\x00\r ")
        if not line:
            continue
        if line.startswith("#"):
            current = {"name": line[1:].strip(), "members": []}
            rows.append(current)
        elif current is not None and len(line) == 7 and line.isdigit():
            current["members"].append(line)
    for row in rows:
        row["member_count"] = len(row["members"])
    return rows


def parse_tdxstat(data: bytes) -> list[dict[str, Any]]:
    """Parse ``tdxstat.cfg`` with documented fields plus all raw columns."""
    rows = []
    for line in _text(data).splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < 2 or not fields[1]:
            continue
        rows.append({
            "market": _int(fields, 0), "code": fields[1], "date": _field(fields, 4),
            "pe_ttm": _float(fields, 3), "trend_days": _int(fields, 5), "change_pct": _float(fields, 6),
            "pe_static": _float(fields, 9), "dividend_yield_pct": _float(fields, 10),
            "change_5d_pct": _float(fields, 28), "change_10d_pct": _float(fields, 30),
            "change_20d_pct": _float(fields, 18), "change_60d_pct": _float(fields, 20),
            "change_ytd_pct": _float(fields, 21), "fields": fields,
        })
    return rows


def parse_tdxstat2(data: bytes) -> list[dict[str, Any]]:
    """Parse ``tdxstat2.cfg``; preserve unverified capital-flow columns raw."""
    rows = []
    for line in _text(data).splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < 2 or not fields[1]:
            continue
        rows.append({
            "market": _int(fields, 0), "code": fields[1], "date": _field(fields, 2),
            "amount_10k_yuan": _float(fields, 3), "amount_prev_10k_yuan": _float(fields, 5),
            "block_index": _field(fields, 13), "ipo_price_yuan": _float(fields, 16),
            "high_52w_yuan": _float(fields, 17), "low_52w_yuan": _float(fields, 18),
            "fields": fields,
        })
    return rows


def parse_zhb_zip(data: bytes, *, max_uncompressed: int = MAX_ZIP_UNCOMPRESSED) -> dict[str, bytes]:
    """Read a bounded ``zhb.zip`` into basename -> bytes, rejecting traversal."""
    out: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise TdxFileError("unsafe path in zhb.zip")
            if info.is_dir():
                continue
            total += info.file_size
            if total > max_uncompressed:
                raise TdxFileError("zhb.zip exceeds uncompressed size cap")
            out[name.rsplit("/", 1)[-1]] = archive.read(info)
    return out


class TdxFilesClient(tdx_protocol.TdxClient):
    """A standard-library TDX client for bounded server file downloads."""

    def file_size(self, filename: str) -> int:
        size = parse_file_size(self._exchange(build_file_meta_request(filename)))
        if size > MAX_FILE_SIZE:
            raise TdxFileError(f"TDX file exceeds size cap: {size}")
        return size

    def _download_block(self, filename: str, size: int) -> bytes:
        if size < 0 or size > MAX_FILE_SIZE:
            raise TdxFileError("invalid TDX file size")
        out = bytearray()
        offset = 0
        while offset < size:
            requested = min(FILE_CHUNK_SIZE, size - offset)
            chunk = parse_file_chunk(self._exchange(build_file_chunk_request(filename, offset, requested)))
            if not chunk:
                raise TdxFileError("TDX block file ended before advertised size")
            out.extend(chunk)
            offset += len(chunk)
            if len(chunk) > requested:
                raise TdxFileError("TDX block chunk exceeded request")
        return bytes(out[:size])

    def _download_report(self, filename: str) -> bytes:
        out = bytearray()
        offset = 0
        while offset <= MAX_FILE_SIZE:
            chunk = parse_file_chunk(self._exchange(build_report_file_request(filename, offset)))
            if not chunk:
                break
            if len(out) + len(chunk) > MAX_FILE_SIZE:
                raise TdxFileError("TDX report exceeds size cap")
            out.extend(chunk)
            offset += len(chunk)
            if len(chunk) < FILE_CHUNK_SIZE:
                break
        return bytes(out)

    def download(self, filename: str) -> bytes:
        """Download one file; block/config files use the size-query path."""
        if filename in BLOCK_FILES:
            return self._download_block(filename, self.file_size(filename))
        return self._download_report(filename)


__all__ = [
    "BLOCK_FILES", "FILE_CHUNK_SIZE", "MAX_FILE_SIZE", "REPORT_FILES", "TdxFileError", "TdxFilesClient",
    "build_file_chunk_request", "build_file_meta_request", "build_file_size_request", "build_report_file_request",
    "parse_block_file",
    "parse_file_chunk", "parse_file_size", "parse_spblock", "parse_tdxstat", "parse_tdxstat2", "parse_tdxzs",
    "parse_zhb_zip",
]
