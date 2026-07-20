"""Minimal OOXML .xlsx builder/reader (no openpyxl dependency)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZIP_DEFLATED, ZipFile

_INVALID_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")
_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def sheet_name(raw: str, idx: int) -> str:
    base = _INVALID_SHEET_CHARS.sub("_", str(raw or "").strip())[:31]
    if not base:
        base = f"Sheet{idx}"
    return base


def _col_label(index: int) -> str:
    out = ""
    n = index
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _cell_ref(col_idx: int, row_idx: int) -> str:
    return f"{_col_label(col_idx)}{row_idx}"


def _xlsx_text(value: Any) -> str:
    text = str(value if value is not None else "")
    text = _INVALID_XML_CHARS.sub("", text)
    return xml_escape(text, {'"': "&quot;", "'": "&apos;"})


def _sheet_xml(rows: list[list[Any]]) -> bytes:
    body: list[str] = []
    for row_idx, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_idx, value in enumerate(row, start=1):
            if value is None:
                continue
            ref = _cell_ref(col_idx, row_idx)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                txt = _xlsx_text(value)
                cells.append(
                    f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{txt}</t></is></c>'
                )
        body.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData>'
        "</worksheet>"
    )
    return xml.encode("utf-8")


def sheets_to_xlsx(sheets: list[tuple[str, list[list[Any]]]]) -> bytes:
    """Build a workbook from ``[(name, rows), ...]``."""
    if not sheets:
        sheets = [("Sheet1", [["Empty"]])]

    sheet_xml_blobs: list[bytes] = []
    sheet_names: list[str] = []
    for idx, (raw_name, rows) in enumerate(sheets, start=1):
        sheet_names.append(sheet_name(raw_name, idx))
        sheet_xml_blobs.append(_sheet_xml(rows))

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        + "".join(
            f'<sheet name="{_xlsx_text(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
            for idx, name in enumerate(sheet_names, start=1)
        )
        + "</sheets></workbook>"
    ).encode("utf-8")

    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
            for idx in range(1, len(sheet_names) + 1)
        )
        + '<Relationship Id="rIdStyles" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        + "</Relationships>"
    ).encode("utf-8")

    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    ).encode("utf-8")

    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for idx in range(1, len(sheet_names) + 1)
        )
        + '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    ).encode("utf-8")

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="1"><xf xfId="0"/></cellXfs>'
        "</styleSheet>"
    ).encode("utf-8")

    buf = BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", root_rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        for idx, sheet_blob in enumerate(sheet_xml_blobs, start=1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", sheet_blob)
    return buf.getvalue()


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        texts = cell.findall(".//main:t", _NS)
        return "".join((t.text or "") for t in texts)
    if cell_type == "s":
        v = cell.find("main:v", _NS)
        if v is not None and v.text is not None:
            try:
                return shared[int(v.text)]
            except (ValueError, IndexError):
                return v.text
    v = cell.find("main:v", _NS)
    return v.text if v is not None and v.text is not None else ""


def read_sheet_rows(path: Path | str, sheet: str) -> list[list[str]] | None:
    """Read rows from a sheet name. Returns None if file/sheet missing or unreadable."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        with ZipFile(p, "r") as zf:
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            target_rid: str | None = None
            for el in wb.findall("main:sheets/main:sheet", _NS):
                if el.attrib.get("name") == sheet:
                    target_rid = el.attrib.get(
                        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                    )
                    break
            if not target_rid:
                return None
            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            target: str | None = None
            for rel in rels:
                if rel.attrib.get("Id") == target_rid:
                    target = rel.attrib.get("Target")
                    break
            if not target:
                return None
            sheet_path = "xl/" + target.lstrip("/")
            if sheet_path.startswith("xl/xl/"):
                sheet_path = sheet_path[3:]
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                for si in sst.findall("main:si", _NS):
                    texts = si.findall(".//main:t", _NS)
                    shared.append("".join((t.text or "") for t in texts))
            root = ET.fromstring(zf.read(sheet_path))
            rows_out: list[list[str]] = []
            for row in root.findall("main:sheetData/main:row", _NS):
                cells = row.findall("main:c", _NS)
                if not cells:
                    rows_out.append([])
                    continue
                # Sparse cells — pad by column letter roughly via order
                values = [_cell_text(c, shared) for c in cells]
                rows_out.append(values)
            return rows_out
    except Exception:
        return None
