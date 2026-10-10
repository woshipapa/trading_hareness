"""Read-only TDX GPCW historical financial statements.

TDX publishes one ZIP per report period.  A GPCW row is a vendor snapshot: the report-period header is not an
availability timestamp and values may be restated in a later snapshot.  A row carries no ``available_at`` until
:func:`date_gpcw_rows` sets it from the first disclosure date in ``tipinfo.dat`` (the evidence for that date is
in ``tdx_zhb_extras.parse_tipinfo``).  ``tipinfo.dat`` dates each security's latest report only, so every older
period stays undated and is not point-in-time evidence.  Callers keep ``report_period`` and collection time
separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO
import hashlib
import re
import struct
from typing import Any, Iterable
import zipfile
import zlib
from zoneinfo import ZoneInfo

from . import tdx_files, tdx_protocol


class TdxFinanceError(tdx_files.TdxFileError):
    """Typed error for GPCW parsing and download issues."""


CN_TZ = ZoneInfo("Asia/Shanghai")
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
_HEADER = "<hI H 3L"
_ITEM = "<6s1sL"

# Copy of the stable public GPCW table.  TDX appends vendor-specific columns;
# those remain addressable as colN instead of being silently discarded.
GPCW_FIELD_NAMES: dict[int, str] = {
    1: "基本每股收益", 2: "扣除非经常性损益每股收益", 3: "每股未分配利润", 4: "每股净资产",
    5: "每股资本公积金", 6: "净资产收益率", 7: "每股经营现金流量", 8: "货币资金",
    17: "存货", 21: "流动资产合计", 27: "固定资产", 33: "无形资产", 40: "资产总计",
    54: "流动负债合计", 63: "负债合计", 64: "实收资本（或股本）", 65: "资本公积",
    68: "未分配利润", 72: "所有者权益（或股东权益）合计", 73: "负债和所有者（或股东权益）合计",
    74: "其中：营业收入", 75: "其中：营业成本", 76: "营业税金及附加", 77: "销售费用",
    78: "管理费用", 80: "财务费用", 81: "资产减值损失", 83: "投资收益", 86: "三、营业利润",
    88: "营业外收入", 89: "减：营业外支出", 92: "四、利润总额", 93: "减：所得税",
    95: "五、净利润", 96: "归属于母公司所有者的净利润", 97: "少数股东损益",
    98: "销售商品、提供劳务收到的现金", 101: "经营活动现金流入小计", 106: "经营活动现金流出小计",
    107: "经营活动产生的现金流量净额", 113: "投资活动现金流入小计", 118: "投资活动现金流出小计",
    119: "投资活动产生的现金流量净额", 123: "筹资活动现金流入小计", 127: "筹资活动现金流出小计",
    128: "筹资活动产生的现金流量净额", 131: "五、现金及现金等价物净增加额", 132: "期初现金及现金等价物余额",
    133: "期末现金及现金等价物余额", 134: "净利润", 150: "经营活动产生的现金流量净额2",
    238: "总股本", 239: "已上市流通A股", 240: "已上市流通B股", 241: "已上市流通H股", 242: "股东人数(户)",
}
_SHARE_COLUMNS = frozenset({238, 239, 240, 241})
_COUNT_COLUMNS = frozenset({242})
_RATIO_COLUMNS = frozenset({6})
_PER_SHARE_COLUMNS = frozenset({1, 2, 3, 4, 5, 7})


@dataclass(frozen=True)
class ManifestEntry:
    filename: str
    md5: str
    size: int


def parse_manifest(data: str | bytes) -> list[ManifestEntry]:
    """Parse ``tdxfin/gpcw.txt`` and reject malformed or unsafe entries."""
    if isinstance(data, bytes):
        data = data.decode("ascii", "replace")
    entries: list[ManifestEntry] = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3 or not re.fullmatch(r"gpcw\d{8}\.zip", parts[0], re.I):
            raise TdxFinanceError(f"invalid GPCW manifest row: {line!r}")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", parts[1]):
            raise TdxFinanceError(f"invalid GPCW md5: {parts[1]!r}")
        if not re.fullmatch(r"[0-9]+", parts[2]):
            raise TdxFinanceError(f"invalid GPCW size: {parts[2]!r}")
        size = int(parts[2])
        if size > MAX_DOWNLOAD_BYTES:
            raise TdxFinanceError("manifest size exceeds bounded downloader limit")
        entries.append(ManifestEntry(parts[0], parts[1].lower(), size))
    if not entries:
        raise TdxFinanceError("empty GPCW manifest")
    return entries


def manifest_changes(previous: Iterable[ManifestEntry], current: Iterable[ManifestEntry]) -> dict[str, list[str]]:
    """Return added/removed/changed filenames for cache invalidation."""
    old = {item.filename: item for item in previous}
    new = {item.filename: item for item in current}
    return {"added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)),
            "changed": sorted(name for name in set(old) & set(new) if old[name] != new[name])}


def verify_manifest_entry(entry: ManifestEntry, payload: bytes) -> bool:
    """Verify both advertised byte size and MD5 before a ZIP enters the cache."""
    if len(payload) != entry.size:
        return False
    return hashlib.md5(payload).hexdigest() == entry.md5


def normalize_report_period(value: str | int) -> str:
    match = re.search(r"(\d{8})", str(value))
    if not match:
        raise TdxFinanceError(f"invalid report period: {value!r}")
    raw = match.group(1)
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat()
    except ValueError as error:
        raise TdxFinanceError(f"invalid report period: {value!r}") from error


def gpcw_field_name(index: int) -> str:
    return GPCW_FIELD_NAMES.get(index, f"col{index}")


def gpcw_field_unit(index: int) -> str | None:
    """The unit of a documented column; None for the vendor columns no source documents (they stay raw)."""
    if index in _SHARE_COLUMNS:
        return "shares"
    if index in _COUNT_COLUMNS:
        return "count"
    if index in _RATIO_COLUMNS:
        return "ratio"
    if index in _PER_SHARE_COLUMNS:
        return "yuan/share"
    return "yuan" if index in GPCW_FIELD_NAMES else None


def parse_gpcw_dat(data: bytes, *, filename: str | None = None) -> list[dict[str, Any]]:
    """Parse a bounded GPCW ``.dat`` payload, preserving unknown columns."""
    header_size = struct.calcsize(_HEADER)
    item_size = struct.calcsize(_ITEM)
    if len(data) < header_size:
        raise TdxFinanceError("short GPCW header")
    _kind, report_date, count, _unknown, record_size, _reserved = struct.unpack_from(_HEADER, data)
    if record_size <= 0 or record_size % 4:
        raise TdxFinanceError("invalid GPCW record size")
    field_count = record_size // 4
    report_period = normalize_report_period(filename or report_date)
    units = {gpcw_field_name(col): gpcw_field_unit(col) for col in range(1, field_count + 1)}
    rows: list[dict[str, Any]] = []
    for index in range(count):
        item_pos = header_size + index * item_size
        if item_pos + item_size > len(data):
            break
        raw_code, _flag, offset = struct.unpack_from(_ITEM, data, item_pos)
        if offset + record_size > len(data):
            continue
        code = raw_code.split(b"\0", 1)[0].decode("ascii", "replace")
        values = struct.unpack_from("<" + "f" * field_count, data, offset)
        fields = {gpcw_field_name(col): value for col, value in enumerate(values, 1)}
        rows.append({"code": code, "report_date": report_date, "report_period": report_period,
                     "field_count": field_count, "raw_values": values, "fields": fields, "field_units": units})
    return rows


def parse_gpcw_zip(data: bytes, *, filename: str | None = None, max_uncompressed: int = MAX_DOWNLOAD_BYTES) -> list[dict[str, Any]]:
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise TdxFinanceError("GPCW ZIP exceeds download cap")
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = [info for info in archive.infolist() if not info.is_dir() and info.filename.lower().endswith(".dat")]
            if len(members) != 1:
                raise TdxFinanceError("GPCW ZIP must contain exactly one .dat member")
            info = members[0]
            if info.file_size > max_uncompressed:
                raise TdxFinanceError("GPCW member exceeds size cap")
            return parse_gpcw_dat(archive.read(info), filename=filename or info.filename)
    except (zipfile.BadZipFile, zlib.error) as error:
        raise TdxFinanceError(f"GPCW ZIP format error: {error}") from error


def download_report_file(client: tdx_protocol.TdxClient, filename: str, size: int, *,
                         max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """The ``size`` bytes of one server file, never a prefix of it.

    ``size`` is what the server advertises in ``tdxfin/gpcw.txt``: a 0x06b9 reply carries only the length of its
    own chunk, and report files have no size query.  A file over ``max_bytes`` is refused before the first
    request; one that ends early, or runs past ``size``, raises.  Every request asks for a full
    ``tdx_files.FILE_CHUNK_SIZE``, as the clients this was checked against do; the last reply is what remains.
    """
    if size > max_bytes:
        raise TdxFinanceError(f"{filename}: advertised size {size} exceeds the {max_bytes}-byte cap")
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        chunk = tdx_files.parse_file_chunk(client._exchange(tdx_files.build_report_file_request(filename, offset)))
        if not chunk:
            raise TdxFinanceError(f"{filename} ended at byte {offset} of {size}")
        if offset + len(chunk) > size:
            raise TdxFinanceError(f"{filename} runs past its advertised {size} bytes")
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def gpcw(client: tdx_protocol.TdxClient, filename: str, entry: ManifestEntry) -> list[dict[str, Any]]:
    """Download one period ZIP against its manifest entry, check size and MD5, and parse it.

    The rows carry no ``available_at``: :func:`date_gpcw_rows` sets it where ``tipinfo.dat`` dates the row.
    """
    if entry.filename != filename:
        raise TdxFinanceError(f"manifest entry {entry.filename} is not the entry of {filename}")
    payload = download_report_file(client, "tdxfin/" + filename, entry.size)
    if not verify_manifest_entry(entry, payload):
        raise TdxFinanceError(f"GPCW manifest mismatch for {filename}")
    return parse_gpcw_zip(payload, filename=filename)


def date_gpcw_rows(rows: list[dict[str, Any]], tipinfo_rows: list[dict[str, Any]]) -> int:
    """Set ``available_at`` on the GPCW rows whose (code, report period) ``tipinfo.dat`` dates; return how many stay undated.

    ``rows`` is :func:`parse_gpcw_dat` output and ``tipinfo_rows`` is ``tdx_zhb_extras.parse_tipinfo`` output.
    A dated row is available from the end of its report's first disclosure day in Asia/Shanghai (23:59:59), so it
    is usable from the next session. ``tipinfo.dat`` lists each security's latest report only: every older period,
    and a security it does not list, keeps no ``available_at``. The date is that of the report's first
    disclosure, not of the snapshot's values, which a later snapshot may restate.
    """
    first_disclosure = {(item["code"], normalize_report_period(item["report_period"])): item["first_disclosure_date"]
                        for item in tipinfo_rows}
    undated = 0
    for row in rows:
        day = first_disclosure.get((row["code"], row["report_period"]))
        if day is None:
            undated += 1
        else:
            row["available_at"] = datetime.combine(day, time(23, 59, 59), tzinfo=CN_TZ)
    return undated


def ttm_from_cumulative(previous_fy: float, current_cumulative: float, prior_cumulative: float) -> float:
    """Compute TTM from the confirmed year-to-date cumulative series.

    Formula: TTM = FY_prev + cum_now - cum_prior_year_same_period
    """
    return previous_fy + current_cumulative - prior_cumulative


__all__ = ["GPCW_FIELD_NAMES", "MAX_DOWNLOAD_BYTES", "ManifestEntry", "TdxFinanceError", "date_gpcw_rows", "download_report_file", "gpcw", "gpcw_field_name", "gpcw_field_unit",
           "manifest_changes", "normalize_report_period", "parse_gpcw_dat", "parse_gpcw_zip", "parse_manifest", "ttm_from_cumulative", "verify_manifest_entry"]
