"""Minimal OOXML .xlsx builder/reader (no openpyxl dependency)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
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


@dataclass
class Cell:
    """A single styled cell.

    ``formula`` holds an Excel expression without the leading ``=``; ``value``
    is then the cached result so viewers that do not recalculate (and our own
    readers) still show a number.
    """

    value: Any = None
    formula: str | None = None
    style: str = "default"


@dataclass
class SheetSpec:
    """One worksheet: rows of plain values and/or :class:`Cell` objects."""

    name: str
    rows: list[list[Any]] = field(default_factory=list)
    col_widths: list[float] | None = None
    freeze: str | None = None
    tab_color: str | None = None


# Custom number formats start at 164; ids below that are reserved by the spec.
_FMT_CURRENCY = 164
_FMT_PERCENT = 165
_FMT_NUMBER = 166

_FONT_DEFAULT, _FONT_BOLD, _FONT_TITLE, _FONT_NOTE, _FONT_LINK = range(5)
# Excel expects fill 0 = none and fill 1 = gray125; anything custom follows.
_FILL_NONE, _FILL_GRAY125, _FILL_HEADER, _FILL_INPUT, _FILL_YELLOW = range(5)
_BORDER_NONE, _BORDER_HEADER = range(2)

_INPUT_YELLOW = "FFFFFF99"

# Named styles in cellXfs order: the tuple index becomes the `s` attribute on a
# cell, so entries may be appended but not reordered.
_STYLES: tuple[tuple[str, int, int, int, int], ...] = (
    # name, numFmtId, fontId, fillId, borderId
    ("default", 0, _FONT_DEFAULT, _FILL_NONE, _BORDER_NONE),
    ("header", 0, _FONT_BOLD, _FILL_HEADER, _BORDER_HEADER),
    ("title", 0, _FONT_TITLE, _FILL_NONE, _BORDER_NONE),
    ("section", 0, _FONT_BOLD, _FILL_NONE, _BORDER_NONE),
    ("note", 0, _FONT_NOTE, _FILL_NONE, _BORDER_NONE),
    ("link", 0, _FONT_LINK, _FILL_NONE, _BORDER_NONE),
    ("currency", _FMT_CURRENCY, _FONT_DEFAULT, _FILL_NONE, _BORDER_NONE),
    ("currency_link", _FMT_CURRENCY, _FONT_LINK, _FILL_NONE, _BORDER_NONE),
    ("currency_input", _FMT_CURRENCY, _FONT_DEFAULT, _FILL_INPUT, _BORDER_NONE),
    ("currency_input_yellow", _FMT_CURRENCY, _FONT_DEFAULT, _FILL_YELLOW, _BORDER_NONE),
    ("pct", _FMT_PERCENT, _FONT_DEFAULT, _FILL_NONE, _BORDER_NONE),
    ("pct_link", _FMT_PERCENT, _FONT_LINK, _FILL_NONE, _BORDER_NONE),
    ("pct_input", _FMT_PERCENT, _FONT_DEFAULT, _FILL_INPUT, _BORDER_NONE),
    ("pct_input_yellow", _FMT_PERCENT, _FONT_DEFAULT, _FILL_YELLOW, _BORDER_NONE),
    ("number", _FMT_NUMBER, _FONT_DEFAULT, _FILL_NONE, _BORDER_NONE),
    ("number_input", _FMT_NUMBER, _FONT_DEFAULT, _FILL_INPUT, _BORDER_NONE),
)
_STYLE_INDEX = {entry[0]: idx for idx, entry in enumerate(_STYLES)}


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


def _col_index(label: str) -> int:
    idx = 0
    for ch in label.upper():
        idx = idx * 26 + (ord(ch) - 64)
    return idx


def _argb(color: str) -> str:
    raw = str(color or "").lstrip("#").upper()
    return raw if len(raw) == 8 else f"FF{raw}"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _cell_xml(ref: str, cell: Cell) -> str:
    style_attr = ""
    style_idx = _STYLE_INDEX.get(cell.style, 0)
    if style_idx:
        style_attr = f' s="{style_idx}"'
    if cell.formula:
        formula = _xlsx_text(cell.formula)
        if _is_number(cell.value):
            return f'<c r="{ref}"{style_attr}><f>{formula}</f><v>{cell.value}</v></c>'
        if cell.value is None or cell.value == "":
            return f'<c r="{ref}"{style_attr}><f>{formula}</f></c>'
        return (
            f'<c r="{ref}"{style_attr} t="str"><f>{formula}</f>'
            f"<v>{_xlsx_text(cell.value)}</v></c>"
        )
    if cell.value is None:
        # Still emit the cell so an input fill or number format stays visible.
        return f'<c r="{ref}"{style_attr}/>' if style_idx else ""
    if _is_number(cell.value):
        return f'<c r="{ref}"{style_attr}><v>{cell.value}</v></c>'
    return (
        f'<c r="{ref}"{style_attr} t="inlineStr">'
        f'<is><t xml:space="preserve">{_xlsx_text(cell.value)}</t></is></c>'
    )


def _freeze_xml(freeze: str | None) -> str:
    if not freeze:
        return ""
    match = re.fullmatch(r"([A-Za-z]*)(\d*)", str(freeze).strip())
    if not match:
        return ""
    letters, digits = match.group(1), match.group(2)
    x_split = _col_index(letters) - 1 if letters else 0
    y_split = int(digits) - 1 if digits else 0
    if x_split <= 0 and y_split <= 0:
        return ""
    attrs = ""
    if x_split > 0:
        attrs += f' xSplit="{x_split}"'
    if y_split > 0:
        attrs += f' ySplit="{y_split}"'
    active = "bottomRight" if x_split > 0 and y_split > 0 else ("topRight" if x_split > 0 else "bottomLeft")
    return (
        '<sheetViews><sheetView workbookViewId="0">'
        f'<pane{attrs} topLeftCell="{_cell_ref(x_split + 1, y_split + 1)}" '
        f'activePane="{active}" state="frozen"/>'
        "</sheetView></sheetViews>"
    )


def _cols_xml(widths: list[float] | None) -> str:
    if not widths:
        return ""
    cols = "".join(
        f'<col min="{idx}" max="{idx}" width="{float(width)}" customWidth="1"/>'
        for idx, width in enumerate(widths, start=1)
        if width
    )
    return f"<cols>{cols}</cols>" if cols else ""


def _sheet_xml(spec: SheetSpec) -> bytes:
    body: list[str] = []
    for row_idx, row in enumerate(spec.rows, start=1):
        cells: list[str] = []
        for col_idx, raw in enumerate(row, start=1):
            cell = raw if isinstance(raw, Cell) else Cell(value=raw)
            rendered = _cell_xml(_cell_ref(col_idx, row_idx), cell)
            if rendered:
                cells.append(rendered)
        body.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    tab_color = (
        f'<sheetPr><tabColor rgb="{_argb(spec.tab_color)}"/></sheetPr>' if spec.tab_color else ""
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + tab_color
        + _freeze_xml(spec.freeze)
        + _cols_xml(spec.col_widths)
        + f'<sheetData>{"".join(body)}</sheetData>'
        + "</worksheet>"
    )
    return xml.encode("utf-8")


def _styles_xml() -> bytes:
    fonts = (
        '<font><sz val="11"/><name val="Calibri"/></font>',
        '<font><b/><sz val="11"/><name val="Calibri"/></font>',
        '<font><b/><sz val="14"/><color rgb="FF1F4E79"/><name val="Calibri"/></font>',
        '<font><i/><sz val="10"/><color rgb="FF808080"/><name val="Calibri"/></font>',
        '<font><sz val="11"/><color rgb="FF0563C1"/><name val="Calibri"/></font>',
    )
    fills = (
        '<fill><patternFill patternType="none"/></fill>',
        '<fill><patternFill patternType="gray125"/></fill>',
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/>'
        '<bgColor indexed="64"/></patternFill></fill>',
        '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F2"/>'
        '<bgColor indexed="64"/></patternFill></fill>',
        f'<fill><patternFill patternType="solid"><fgColor rgb="{_INPUT_YELLOW}"/>'
        '<bgColor indexed="64"/></patternFill></fill>',
    )
    borders = (
        "<border/>",
        '<border><bottom style="thin"><color rgb="FF8EA9DB"/></bottom></border>',
    )
    cell_xfs: list[str] = []
    for _name, num_fmt, font_id, fill_id, border_id in _STYLES:
        attrs = f'numFmtId="{num_fmt}" fontId="{font_id}" fillId="{fill_id}" borderId="{border_id}" xfId="0"'
        if num_fmt:
            attrs += ' applyNumberFormat="1"'
        if font_id:
            attrs += ' applyFont="1"'
        if fill_id:
            attrs += ' applyFill="1"'
        if border_id:
            attrs += ' applyBorder="1"'
        cell_xfs.append(f"<xf {attrs}/>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="3">'
        f'<numFmt numFmtId="{_FMT_CURRENCY}" formatCode="&quot;$&quot;#,##0.00"/>'
        f'<numFmt numFmtId="{_FMT_PERCENT}" formatCode="0.0%"/>'
        f'<numFmt numFmtId="{_FMT_NUMBER}" formatCode="#,##0.00"/>'
        "</numFmts>"
        f'<fonts count="{len(fonts)}">{"".join(fonts)}</fonts>'
        f'<fills count="{len(fills)}">{"".join(fills)}</fills>'
        f'<borders count="{len(borders)}">{"".join(borders)}</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(cell_xfs)}">{"".join(cell_xfs)}</cellXfs>'
        "</styleSheet>"
    )
    return xml.encode("utf-8")


def xlsx_contains_formula(payload: bytes, needle: str) -> bool:
    """True when any worksheet formula in ``payload`` contains ``needle``."""
    try:
        with ZipFile(BytesIO(payload), "r") as zf:
            for name in zf.namelist():
                if not name.startswith("xl/worksheets/"):
                    continue
                root = ET.fromstring(zf.read(name))
                for node in root.iter(f"{{{_NS['main']}}}f"):
                    if needle in (node.text or ""):
                        return True
    except Exception:
        return False
    return False


def _as_spec(entry: SheetSpec | tuple[str, list[list[Any]]], idx: int) -> SheetSpec:
    if isinstance(entry, SheetSpec):
        return entry
    raw_name, rows = entry
    return SheetSpec(name=sheet_name(raw_name, idx), rows=list(rows))


def sheets_to_xlsx(sheets: Sequence[SheetSpec | tuple[str, list[list[Any]]]]) -> bytes:
    """Build a workbook from :class:`SheetSpec` objects or ``[(name, rows), ...]``."""
    specs = [_as_spec(entry, idx) for idx, entry in enumerate(sheets, start=1)]
    if not specs:
        specs = [SheetSpec(name="Sheet1", rows=[["Empty"]])]

    sheet_xml_blobs: list[bytes] = []
    sheet_names: list[str] = []
    for idx, spec in enumerate(specs, start=1):
        sheet_names.append(sheet_name(spec.name, idx))
        sheet_xml_blobs.append(_sheet_xml(spec))

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

    styles_xml = _styles_xml()

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
