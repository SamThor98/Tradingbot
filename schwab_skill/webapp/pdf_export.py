"""High-fidelity PDF rendering for the institutional research dossier.

This module implements a small, dependency-free layout engine that converts
a structured dossier dictionary (the output of
``schwab_skill.webapp.routes.research._compose_research_dossier``) into a
presentation-grade PDF. It is intentionally written without external
libraries (no reportlab, weasyprint, or wkhtmltopdf) so it can run in the
same restricted environments as the rest of the app.

Capabilities:
    * Cover block with title, ticker, generated-at, and KPI strip
    * Section eyebrow + heading + subtitle hierarchy
    * Wrapped paragraphs (analyst-style narrative)
    * Bullet lists
    * Semantic tables with column widths, header strip, alternating fill,
      cell borders, and right-aligned numeric columns
    * Automatic page breaks with header and footer ("Page X of Y")
    * Helvetica + Helvetica-Bold metrics (no font embedding required)

Public API:
    * ``dossier_to_pdf(dossier: dict) -> bytes`` — main entry point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Standard Helvetica advance-width tables (1/1000 em) for ASCII 32..126.
# Embedded so we can wrap text accurately without parsing AFM files.
# Sourced from Adobe Helvetica AFM (regular and bold variants).
# ---------------------------------------------------------------------------

_HELVETICA_WIDTHS: dict[int, int] = {
    32: 278, 33: 278, 34: 355, 35: 556, 36: 556, 37: 889, 38: 667, 39: 191,
    40: 333, 41: 333, 42: 389, 43: 584, 44: 278, 45: 333, 46: 278, 47: 278,
    48: 556, 49: 556, 50: 556, 51: 556, 52: 556, 53: 556, 54: 556, 55: 556,
    56: 556, 57: 556, 58: 278, 59: 278, 60: 584, 61: 584, 62: 584, 63: 556,
    64: 1015, 65: 667, 66: 667, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778,
    72: 722, 73: 278, 74: 500, 75: 667, 76: 556, 77: 833, 78: 722, 79: 778,
    80: 667, 81: 778, 82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944,
    88: 667, 89: 667, 90: 611, 91: 278, 92: 278, 93: 278, 94: 469, 95: 556,
    96: 222, 97: 556, 98: 556, 99: 500, 100: 556, 101: 556, 102: 278,
    103: 556, 104: 556, 105: 222, 106: 222, 107: 500, 108: 222, 109: 833,
    110: 556, 111: 556, 112: 556, 113: 556, 114: 333, 115: 500, 116: 278,
    117: 556, 118: 500, 119: 722, 120: 500, 121: 500, 122: 500, 123: 334,
    124: 260, 125: 334, 126: 584,
}

_HELVETICA_BOLD_WIDTHS: dict[int, int] = {
    32: 278, 33: 333, 34: 474, 35: 556, 36: 556, 37: 889, 38: 722, 39: 238,
    40: 333, 41: 333, 42: 389, 43: 584, 44: 278, 45: 333, 46: 278, 47: 278,
    48: 556, 49: 556, 50: 556, 51: 556, 52: 556, 53: 556, 54: 556, 55: 556,
    56: 556, 57: 556, 58: 333, 59: 333, 60: 584, 61: 584, 62: 584, 63: 611,
    64: 975, 65: 722, 66: 722, 67: 722, 68: 722, 69: 667, 70: 611, 71: 778,
    72: 722, 73: 278, 74: 556, 75: 722, 76: 611, 77: 833, 78: 722, 79: 778,
    80: 667, 81: 778, 82: 722, 83: 667, 84: 611, 85: 722, 86: 667, 87: 944,
    88: 667, 89: 667, 90: 611, 91: 333, 92: 278, 93: 333, 94: 584, 95: 556,
    96: 278, 97: 556, 98: 611, 99: 556, 100: 611, 101: 556, 102: 333,
    103: 611, 104: 611, 105: 278, 106: 278, 107: 556, 108: 278, 109: 889,
    110: 611, 111: 611, 112: 611, 113: 611, 114: 389, 115: 556, 116: 333,
    117: 611, 118: 556, 119: 778, 120: 556, 121: 556, 122: 500, 123: 389,
    124: 280, 125: 389, 126: 584,
}

_FALLBACK_WIDTH = 500  # generic glyph width used when a code-point is missing


def _measure(text: str, size: float, bold: bool = False) -> float:
    """Return the rendered width of ``text`` in PDF user-space units (points)."""

    table = _HELVETICA_BOLD_WIDTHS if bold else _HELVETICA_WIDTHS
    total = 0
    for ch in text:
        cp = ord(ch)
        total += table.get(cp, _FALLBACK_WIDTH)
    return total * size / 1000.0


def _escape_pdf_text(text: str) -> str:
    """Escape characters that cannot appear inside a PDF string literal."""

    return (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _strip_to_latin1(text: str) -> str:
    """Replace non-Latin1 characters with safe ASCII-friendly substitutions.

    Helvetica with the default WinAnsi encoding cannot render arbitrary
    Unicode, so we translate the most common analyst-glyphs to ASCII to
    keep the PDF readable.
    """

    if not text:
        return ""
    table = {
        "—": "-",
        "–": "-",
        "−": "-",
        "•": "*",
        "·": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "→": "->",
        "←": "<-",
        "⇒": "=>",
        "©": "(c)",
        "®": "(R)",
        "™": "(TM)",
        "≈": "~",
        "±": "+/-",
        "≥": ">=",
        "≤": "<=",
        "≠": "!=",
        "×": "x",
        "÷": "/",
        "…": "...",
        "\u00a0": " ",
        "\u200b": "",
    }
    out = []
    for ch in text:
        if ch in table:
            out.append(table[ch])
            continue
        cp = ord(ch)
        if cp < 32 and ch not in ("\n", "\t"):
            out.append(" ")
            continue
        if cp <= 126:
            out.append(ch)
            continue
        # Try latin-1 fallback for accented letters; otherwise replace.
        try:
            ch.encode("latin-1")
            out.append(ch)
        except UnicodeEncodeError:
            out.append("?")
    return "".join(out)


# ---------------------------------------------------------------------------
# Layout primitives
# ---------------------------------------------------------------------------

@dataclass
class Style:
    page_w: float = 612.0  # US Letter, points
    page_h: float = 792.0
    margin_l: float = 56.0
    margin_r: float = 56.0
    margin_t: float = 64.0
    margin_b: float = 56.0

    body_size: float = 9.5
    body_leading: float = 13.5
    small_size: float = 8.0
    small_leading: float = 11.0

    h1_size: float = 22.0
    h1_leading: float = 28.0
    h2_size: float = 13.5
    h2_leading: float = 18.0
    h3_size: float = 11.0
    h3_leading: float = 14.5
    eyebrow_size: float = 7.5

    table_header_size: float = 8.0
    table_cell_size: float = 8.5
    table_row_h: float = 16.0
    table_header_h: float = 18.0

    text_color: tuple[float, float, float] = (0.10, 0.13, 0.18)
    muted_color: tuple[float, float, float] = (0.45, 0.50, 0.60)
    accent_color: tuple[float, float, float] = (0.13, 0.36, 0.62)
    rule_color: tuple[float, float, float] = (0.82, 0.85, 0.92)
    table_header_fill: tuple[float, float, float] = (0.94, 0.96, 0.99)
    table_zebra_fill: tuple[float, float, float] = (0.97, 0.98, 1.00)
    cover_strip_fill: tuple[float, float, float] = (0.95, 0.97, 1.00)


@dataclass
class _PageOps:
    ops: list[str] = field(default_factory=list)


class PDFBuilder:
    """Thin layout engine that emits PDF 1.4 content streams.

    The builder maintains a y cursor and breaks pages automatically when
    content would overflow the bottom margin. Callers issue high-level
    requests (``paragraph``, ``heading``, ``table``...) and the builder
    handles measurement and pagination.
    """

    def __init__(self, style: Style | None = None, *, header_text: str = "", footer_text: str = "") -> None:
        self.style = style or Style()
        self.pages: list[_PageOps] = []
        self.header_text = _strip_to_latin1(header_text)
        self.footer_text = _strip_to_latin1(footer_text)
        self._begin_page()

    # ---- page management ------------------------------------------------

    def _begin_page(self) -> None:
        self.pages.append(_PageOps())
        self.y = self.style.page_h - self.style.margin_t

    def _ensure_space(self, needed: float) -> None:
        if self.y - needed < self.style.margin_b:
            self._begin_page()

    def _new_page(self) -> None:
        self._begin_page()

    @property
    def content_width(self) -> float:
        return self.style.page_w - self.style.margin_l - self.style.margin_r

    # ---- raw PDF ops ----------------------------------------------------

    def _ops(self) -> list[str]:
        return self.pages[-1].ops

    def _set_fill(self, color: tuple[float, float, float]) -> None:
        self._ops().append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg")

    def _set_stroke(self, color: tuple[float, float, float]) -> None:
        self._ops().append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG")

    def _draw_text(
        self,
        text: str,
        x: float,
        y: float,
        size: float,
        *,
        bold: bool = False,
        color: tuple[float, float, float] | None = None,
    ) -> None:
        if not text:
            return
        font = "F2" if bold else "F1"
        if color is not None:
            self._set_fill(color)
        else:
            self._set_fill(self.style.text_color)
        ops = self._ops()
        ops.append("BT")
        ops.append(f"/{font} {size} Tf")
        ops.append(f"{x:.2f} {y:.2f} Td")
        ops.append(f"({_escape_pdf_text(_strip_to_latin1(text))}) Tj")
        ops.append("ET")

    def _draw_rect(self, x: float, y: float, w: float, h: float, fill: tuple[float, float, float] | None = None,
                   stroke: tuple[float, float, float] | None = None, line_width: float = 0.5) -> None:
        ops = self._ops()
        if fill is not None:
            self._set_fill(fill)
            ops.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")
        if stroke is not None:
            self._set_stroke(stroke)
            ops.append(f"{line_width} w {x:.2f} {y:.2f} {w:.2f} {h:.2f} re S")

    def _draw_line(self, x1: float, y1: float, x2: float, y2: float,
                   color: tuple[float, float, float], width: float = 0.5) -> None:
        self._set_stroke(color)
        self._ops().extend([
            f"{width} w",
            f"{x1:.2f} {y1:.2f} m",
            f"{x2:.2f} {y2:.2f} l",
            "S",
        ])

    # ---- text wrapping --------------------------------------------------

    def _wrap_words(self, text: str, max_width: float, size: float, *, bold: bool = False) -> list[str]:
        if not text:
            return [""]
        text = _strip_to_latin1(str(text))
        words = text.split()
        if not words:
            return [""]
        lines: list[str] = []
        current = ""
        for word in words:
            trial = (current + " " + word).strip() if current else word
            if _measure(trial, size, bold) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                # Hard-break absurdly long words
                if _measure(word, size, bold) > max_width:
                    chunk = ""
                    for ch in word:
                        candidate = chunk + ch
                        if _measure(candidate, size, bold) > max_width and chunk:
                            lines.append(chunk)
                            chunk = ch
                        else:
                            chunk = candidate
                    current = chunk
                else:
                    current = word
        if current:
            lines.append(current)
        return lines or [""]

    # ---- block elements -------------------------------------------------

    def vertical_space(self, h: float) -> None:
        self._ensure_space(h)
        self.y -= h

    def horizontal_rule(self, color: tuple[float, float, float] | None = None) -> None:
        self._ensure_space(8)
        c = color or self.style.rule_color
        x1 = self.style.margin_l
        x2 = self.style.page_w - self.style.margin_r
        self._draw_line(x1, self.y - 1, x2, self.y - 1, c, 0.6)
        self.y -= 8

    def eyebrow(self, text: str) -> None:
        if not text:
            return
        size = self.style.eyebrow_size
        leading = size + 4
        self._ensure_space(leading)
        self._draw_text(text.upper(), self.style.margin_l, self.y - size, size,
                        bold=True, color=self.style.muted_color)
        self.y -= leading

    def heading(self, text: str, *, level: int = 2, color: tuple[float, float, float] | None = None) -> None:
        if not text:
            return
        if level <= 1:
            size, leading, bold = self.style.h1_size, self.style.h1_leading, True
        elif level == 2:
            size, leading, bold = self.style.h2_size, self.style.h2_leading, True
        else:
            size, leading, bold = self.style.h3_size, self.style.h3_leading, True
        text = _strip_to_latin1(text)
        for line in self._wrap_words(text, self.content_width, size, bold=bold):
            self._ensure_space(leading)
            self._draw_text(line, self.style.margin_l, self.y - size, size,
                            bold=bold, color=color or self.style.text_color)
            self.y -= leading

    def subtitle(self, text: str) -> None:
        if not text:
            return
        size = self.style.small_size
        leading = self.style.small_leading
        for line in self._wrap_words(text, self.content_width, size):
            self._ensure_space(leading)
            self._draw_text(line, self.style.margin_l, self.y - size, size,
                            color=self.style.muted_color)
            self.y -= leading
        self.y -= 2

    def paragraph(self, text: str, *, size: float | None = None, leading: float | None = None,
                  bold: bool = False, color: tuple[float, float, float] | None = None) -> None:
        if not text:
            return
        s = size or self.style.body_size
        ld = leading or self.style.body_leading
        for line in self._wrap_words(text, self.content_width, s, bold=bold):
            self._ensure_space(ld)
            self._draw_text(line, self.style.margin_l, self.y - s, s, bold=bold,
                            color=color or self.style.text_color)
            self.y -= ld
        self.y -= 4

    def bullets(self, items: Iterable[Any], *, indent: float = 14.0,
                size: float | None = None, leading: float | None = None) -> None:
        items = [str(it) for it in items if it is not None and str(it).strip()]
        if not items:
            return
        s = size or self.style.body_size
        ld = leading or self.style.body_leading
        for item in items:
            wrap_width = self.content_width - indent
            wrapped = self._wrap_words(item, wrap_width, s)
            for idx, line in enumerate(wrapped):
                self._ensure_space(ld)
                if idx == 0:
                    self._draw_text("•", self.style.margin_l + 2, self.y - s, s,
                                    color=self.style.accent_color, bold=True)
                self._draw_text(line, self.style.margin_l + indent, self.y - s, s,
                                color=self.style.text_color)
                self.y -= ld
        self.y -= 4

    # ---- tables ---------------------------------------------------------

    def table(
        self,
        headers: list[str],
        rows: list[list[Any]],
        *,
        col_widths: list[float] | None = None,
        alignments: list[str] | None = None,
    ) -> None:
        """Render a semantic table with header strip, borders, and zebra fill.

        ``alignments`` entries may be ``"left"``, ``"right"``, or ``"center"``.
        ``col_widths`` is interpreted as relative weights when given.
        """

        if not headers:
            return
        n_cols = len(headers)
        rows = [
            [("" if cell is None else str(cell)) for cell in (row + [""] * (n_cols - len(row)))[:n_cols]]
            for row in (rows or [])
        ]
        alignments = (alignments or ["left"] * n_cols)[:n_cols]
        if len(alignments) < n_cols:
            alignments = alignments + ["left"] * (n_cols - len(alignments))

        # Compute column widths
        total_w = self.content_width
        if col_widths and len(col_widths) == n_cols:
            weight_sum = sum(col_widths) or 1.0
            widths = [w / weight_sum * total_w for w in col_widths]
        else:
            # Heuristic: max measured width per column, scaled to fit.
            measured = []
            for ci in range(n_cols):
                head_w = _measure(headers[ci], self.style.table_header_size, bold=True) + 14
                cell_max = max(
                    [_measure(row[ci][:80], self.style.table_cell_size) + 14 for row in rows] or [40]
                )
                measured.append(max(head_w, cell_max, 50))
            scale = total_w / sum(measured)
            widths = [w * scale for w in measured]

        cell_pad_x = 6.0
        line_color = self.style.rule_color

        def draw_header(start_y: float) -> float:
            h = self.style.table_header_h
            self._draw_rect(self.style.margin_l, start_y - h, total_w, h,
                            fill=self.style.table_header_fill, stroke=line_color, line_width=0.4)
            x = self.style.margin_l
            for ci in range(n_cols):
                w = widths[ci]
                txt = headers[ci]
                wrapped = self._wrap_words(txt, w - 2 * cell_pad_x, self.style.table_header_size, bold=True)
                if wrapped:
                    line = wrapped[0]
                    align = alignments[ci]
                    line_w = _measure(line, self.style.table_header_size, bold=True)
                    if align == "right":
                        tx = x + w - cell_pad_x - line_w
                    elif align == "center":
                        tx = x + (w - line_w) / 2
                    else:
                        tx = x + cell_pad_x
                    self._draw_text(line, tx, start_y - 12, self.style.table_header_size,
                                    bold=True, color=self.style.text_color)
                if ci < n_cols - 1:
                    self._draw_line(x + w, start_y - h, x + w, start_y, line_color, 0.4)
                x += w
            return start_y - h

        # Pre-wrap each cell to know how tall each row will be
        wrapped_rows: list[list[list[str]]] = []
        row_heights: list[float] = []
        line_h = self.style.table_cell_size + 4
        for row in rows:
            cell_lines: list[list[str]] = []
            row_max_lines = 1
            for ci in range(n_cols):
                w = widths[ci]
                wrapped = self._wrap_words(row[ci], w - 2 * cell_pad_x, self.style.table_cell_size)
                cell_lines.append(wrapped)
                row_max_lines = max(row_max_lines, len(wrapped))
            wrapped_rows.append(cell_lines)
            row_heights.append(max(self.style.table_row_h, row_max_lines * line_h + 4))

        # Ensure header fits on current page; if not, new page.
        if self.y - self.style.table_header_h < self.style.margin_b:
            self._new_page()
        self.y = draw_header(self.y)

        for ri, cells in enumerate(wrapped_rows):
            row_h = row_heights[ri]
            if self.y - row_h < self.style.margin_b:
                self._new_page()
                self.y = draw_header(self.y)
            row_top = self.y
            row_bottom = self.y - row_h
            zebra = ri % 2 == 1
            if zebra:
                self._draw_rect(self.style.margin_l, row_bottom, total_w, row_h,
                                fill=self.style.table_zebra_fill)
            # Cell separators
            x = self.style.margin_l
            for ci in range(n_cols):
                w = widths[ci]
                if ci < n_cols - 1:
                    self._draw_line(x + w, row_bottom, x + w, row_top, line_color, 0.3)
                x += w
            # Bottom border
            self._draw_line(self.style.margin_l, row_bottom,
                            self.style.margin_l + total_w, row_bottom, line_color, 0.3)
            # Cell text
            x = self.style.margin_l
            for ci in range(n_cols):
                w = widths[ci]
                lines = cells[ci]
                # Vertically center text within row
                content_h = len(lines) * line_h
                ty = row_top - 4 - self.style.table_cell_size - max(0, (row_h - 8 - content_h) / 2)
                for line in lines:
                    line_w = _measure(line, self.style.table_cell_size)
                    align = alignments[ci]
                    if align == "right":
                        tx = x + w - cell_pad_x - line_w
                    elif align == "center":
                        tx = x + (w - line_w) / 2
                    else:
                        tx = x + cell_pad_x
                    self._draw_text(line, tx, ty, self.style.table_cell_size,
                                    color=self.style.text_color)
                    ty -= line_h
                x += w
            self.y = row_bottom
        self.y -= 6

    def cover_strip(self, cells: list[tuple[str, str]]) -> None:
        """Render a 2- or 4-cell KPI strip used at the top of the cover page."""

        if not cells:
            return
        n = len(cells)
        cell_h = 46.0
        gap = 8.0
        total_w = self.content_width
        cell_w = (total_w - gap * (n - 1)) / n
        self._ensure_space(cell_h + 6)
        x = self.style.margin_l
        top_y = self.y
        for label, value in cells:
            self._draw_rect(x, top_y - cell_h, cell_w, cell_h,
                            fill=self.style.cover_strip_fill, stroke=self.style.rule_color, line_width=0.5)
            label_size = self.style.eyebrow_size
            self._draw_text(_strip_to_latin1(label).upper(), x + 8, top_y - 14, label_size,
                            bold=True, color=self.style.muted_color)
            value_text = _strip_to_latin1(value or "—")
            value_size = 12.5
            wrapped = self._wrap_words(value_text, cell_w - 16, value_size, bold=True)
            line = wrapped[0] if wrapped else value_text
            self._draw_text(line, x + 8, top_y - 32, value_size, bold=True,
                            color=self.style.text_color)
            x += cell_w + gap
        self.y -= cell_h + 8

    # ---- finalize -------------------------------------------------------

    def _draw_chrome(self, page_idx: int, total_pages: int) -> None:
        """Add header/footer text to a single page (called during finalize)."""

        # Header
        if self.header_text:
            text = _strip_to_latin1(self.header_text)
            text_w = _measure(text, 7.5, bold=False)
            x = self.style.page_w - self.style.margin_r - text_w
            y = self.style.page_h - 30
            # Insert at start of page so it renders behind body content
            ops = self.pages[page_idx].ops
            ops.insert(0, f"BT /F1 7.5 Tf {x:.2f} {y:.2f} Td ({_escape_pdf_text(text)}) Tj ET")
            ops.insert(0, f"{self.style.muted_color[0]:.3f} {self.style.muted_color[1]:.3f} {self.style.muted_color[2]:.3f} rg")
            ops.insert(0, (
                f"{self.style.rule_color[0]:.3f} {self.style.rule_color[1]:.3f} "
                f"{self.style.rule_color[2]:.3f} RG 0.4 w "
                f"{self.style.margin_l:.2f} {self.style.page_h - 38:.2f} m "
                f"{self.style.page_w - self.style.margin_r:.2f} {self.style.page_h - 38:.2f} l S"
            ))

        # Footer
        footer_text = self.footer_text or ""
        page_text = f"Page {page_idx + 1} of {total_pages}"
        ops = self.pages[page_idx].ops
        ops.append(
            f"{self.style.rule_color[0]:.3f} {self.style.rule_color[1]:.3f} "
            f"{self.style.rule_color[2]:.3f} RG 0.4 w "
            f"{self.style.margin_l:.2f} {self.style.margin_b - 14:.2f} m "
            f"{self.style.page_w - self.style.margin_r:.2f} {self.style.margin_b - 14:.2f} l S"
        )
        ops.append(f"{self.style.muted_color[0]:.3f} {self.style.muted_color[1]:.3f} {self.style.muted_color[2]:.3f} rg")
        if footer_text:
            ops.append(
                f"BT /F1 7.5 Tf {self.style.margin_l:.2f} {self.style.margin_b - 26:.2f} Td "
                f"({_escape_pdf_text(footer_text)}) Tj ET"
            )
        page_w = _measure(page_text, 7.5)
        x_center = (self.style.page_w - page_w) / 2
        ops.append(
            f"BT /F1 7.5 Tf {x_center:.2f} {self.style.margin_b - 26:.2f} Td "
            f"({_escape_pdf_text(page_text)}) Tj ET"
        )

    def to_bytes(self) -> bytes:
        total_pages = max(1, len(self.pages))
        for i in range(total_pages):
            self._draw_chrome(i, total_pages)

        # Build PDF objects
        # 1: Catalog, 2: Pages, then Page+Content pairs, then 2 Font objects.
        objs: list[bytes] = []
        objs.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")

        first_page_obj = 3
        font_regular_obj = first_page_obj + (total_pages * 2)
        font_bold_obj = font_regular_obj + 1
        kids = " ".join(f"{first_page_obj + (i * 2)} 0 R" for i in range(total_pages))
        objs.append(
            f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {total_pages} >> endobj\n".encode("ascii")
        )

        for i in range(total_pages):
            page_obj_num = first_page_obj + (i * 2)
            content_obj_num = page_obj_num + 1
            stream = "\n".join(self.pages[i].ops).encode("latin-1", "replace")
            objs.append(
                (
                    f"{page_obj_num} 0 obj << /Type /Page /Parent 2 0 R "
                    f"/MediaBox [0 0 {self.style.page_w:.0f} {self.style.page_h:.0f}] "
                    f"/Resources << /Font << /F1 {font_regular_obj} 0 R /F2 {font_bold_obj} 0 R >> >> "
                    f"/Contents {content_obj_num} 0 R >> endobj\n"
                ).encode("ascii")
            )
            objs.append(
                f"{content_obj_num} 0 obj << /Length {len(stream)} >> stream\n".encode("ascii")
                + stream
                + b"\nendstream endobj\n"
            )

        objs.append(
            f"{font_regular_obj} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >> endobj\n".encode("ascii")
        )
        objs.append(
            f"{font_bold_obj} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >> endobj\n".encode("ascii")
        )

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for obj in objs:
            offsets.append(len(out))
            out.extend(obj)
        xref_start = len(out)
        out.extend(f"xref\n0 {len(objs) + 1}\n".encode("ascii"))
        out.extend(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
        out.extend(
            (
                f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\n"
                f"startxref\n{xref_start}\n%%EOF\n"
            ).encode("ascii")
        )
        return bytes(out)


# ---------------------------------------------------------------------------
# Dossier-aware composition
# ---------------------------------------------------------------------------

def _safe_str(value: Any, default: str = "n/a") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_num(value: Any, digits: int = 2) -> str:
    n = _safe_float(value)
    if n is None:
        return "n/a"
    return f"{n:.{digits}f}"


def _fmt_pct(value: Any, digits: int = 1) -> str:
    """Format a value already expressed in percentage points (e.g., 14.7 → 14.7%).

    Finnhub metric fields and the report stack's DCF/portfolio numbers are
    consistently emitted in percent already, so we render directly without
    auto-multiplying by 100 (which previously caused 0.85% dividend yields to
    render as 85%).
    """

    n = _safe_float(value)
    if n is None:
        return "n/a"
    return f"{n:.{digits}f}%"


def _fmt_pct_fraction(value: Any, digits: int = 1) -> str:
    """Format a fractional ratio (0.0-1.0) as a percentage."""

    n = _safe_float(value)
    if n is None:
        return "n/a"
    return f"{n * 100.0:.{digits}f}%"


def _fmt_money(value: Any, digits: int = 2) -> str:
    n = _safe_float(value)
    if n is None:
        return "n/a"
    if n < 0:
        return f"-${abs(n):,.{digits}f}"
    return f"${n:,.{digits}f}"


def _fmt_money_scaled(value: Any) -> str:
    n = _safe_float(value)
    if n is None:
        return "n/a"
    abs_n = abs(n)
    if abs_n >= 1e12:
        return f"${n/1e12:.2f}T"
    if abs_n >= 1e9:
        return f"${n/1e9:.2f}B"
    if abs_n >= 1e6:
        return f"${n/1e6:.2f}M"
    return f"${n:,.2f}"


def _section(b: PDFBuilder, eyebrow: str, title: str, subtitle: str = "") -> None:
    b.vertical_space(8)
    b.eyebrow(eyebrow)
    b.heading(title, level=2)
    if subtitle:
        b.subtitle(subtitle)
    b.horizontal_rule()


def _paragraphs(b: PDFBuilder, *paragraphs: str) -> None:
    for p in paragraphs:
        if p:
            b.paragraph(p)


def dossier_to_pdf(dossier: dict[str, Any]) -> bytes:
    """Convert a research dossier dict into a presentation-grade PDF."""

    ticker = _safe_str(dossier.get("ticker"), "—")
    generated_at = _safe_str(dossier.get("generated_at"), "")
    pitch = dossier.get("executive_pitch") or {}
    sections = dossier.get("sections") or {}
    fundamentals = sections.get("technical_valuation_fundamentals") or {}
    report_v2 = fundamentals.get("report_v2") or {}
    raw_report = fundamentals.get("raw_report") or {}
    technical = raw_report.get("technical") or {}
    dcf = raw_report.get("dcf") or {}
    comps = raw_report.get("comps") or {}
    edgar = raw_report.get("edgar") or {}
    sec_narr = sections.get("sec_narrative") or {}
    sec_analyze = sec_narr.get("analyze") or {}
    sec_compare_root = sec_narr.get("compare") or {}
    sec_compare = sec_compare_root.get("compare") if isinstance(sec_compare_root.get("compare"), dict) else {}
    portfolio = sections.get("portfolio_and_sector_context") or {}
    portfolio_summary = portfolio.get("portfolio_summary") or {}
    portfolio_risk = portfolio.get("portfolio_risk") or {}
    fin = sections.get("finnhub_catalysts_risks") or {}
    snapshot = (fin.get("snapshot") or {}) if isinstance(fin.get("snapshot"), dict) else {}
    profile = snapshot.get("profile") or {}
    quote = snapshot.get("quote") or {}
    metrics = snapshot.get("metrics") or {}
    earnings_rows = list(snapshot.get("earnings") or [])
    upcoming_earnings = list(snapshot.get("earnings_calendar") or [])
    peers = list(snapshot.get("peers") or [])
    insider_tx = snapshot.get("insider_transactions") or {}
    insider_sent = snapshot.get("insider_sentiment") or {}
    upgrades_rows = list(snapshot.get("upgrade_downgrade") or [])
    rec_history = list((snapshot.get("recommendation_trends") or {}).get("history") or [])
    dividends_rows = list(snapshot.get("dividends") or [])
    splits_rows = list(snapshot.get("splits") or [])
    news_sent = snapshot.get("news_sentiment") or {}
    catalysts = list(fin.get("catalysts") or [])
    risks = list(fin.get("risks") or [])
    source_metadata = list(dossier.get("source_metadata") or [])
    fallback_notes = list(dossier.get("fallback_notes") or [])

    industry_text = _safe_str(profile.get("finnhub_industry") or profile.get("industry"), "Equity Research")
    issuer_name = _safe_str(profile.get("name"), ticker)
    horizon_text = _safe_str(pitch.get("time_horizon"), "3-6 months")
    recommendation = _safe_str(pitch.get("recommendation"), "WATCH")
    confidence_label = _safe_str(pitch.get("confidence_label"), "Moderate")
    confidence_score = _safe_str(pitch.get("confidence_score"), "n/a")

    header_text = f"Institutional Research Report — {ticker}"
    footer_text = f"{ticker} · TradingBot Research Engine"
    builder = PDFBuilder(header_text=header_text, footer_text=footer_text)

    # ----- Cover page -----
    builder.eyebrow(f"Institutional Research Report · {industry_text}")
    builder.heading(f"{issuer_name} ({ticker})", level=1)
    builder.subtitle(
        f"Prepared: {generated_at}    Coverage: {industry_text}    "
        f"Region: {_safe_str(profile.get('country'), 'Global')}"
    )
    builder.horizontal_rule()

    builder.cover_strip([
        ("Recommendation", recommendation),
        ("Confidence", f"{confidence_label} ({confidence_score}/100)" if confidence_score not in ("n/a", "") else confidence_label),
        ("Expected Return (Base)", _fmt_pct((report_v2.get("ic_snapshot") or {}).get("expected_return_base_pct"), 1)),
        ("Horizon", horizon_text),
    ])

    builder.paragraph(
        f"Current Price: {_fmt_money(quote.get('current'))}    "
        f"52-Week Range: {_fmt_money(metrics.get('52week_low'))} – {_fmt_money(metrics.get('52week_high'))}    "
        f"Consensus Target (Mean): {_fmt_money((snapshot.get('price_target') or {}).get('mean'))}",
        size=builder.style.small_size + 0.5,
        color=builder.style.muted_color,
    )

    # ----- Executive Investment Summary -----
    _section(
        builder,
        "Investment Summary",
        "Executive Investment Summary",
        "Decision-first synthesis for investment committee review.",
    )
    thesis_claim = _safe_str(pitch.get("thesis"), "No thesis generated.")
    builder.paragraph(thesis_claim)
    trends = snapshot.get("recommendation_trends") or {}
    bull_votes = int(trends.get("buy", 0) or 0) + int(trends.get("strong_buy", 0) or 0)
    bear_votes = int(trends.get("sell", 0) or 0) + int(trends.get("strong_sell", 0) or 0)
    hhi_label = _safe_str(((portfolio_risk.get("concentration") or {}).get("hhi_label")), "Unavailable")
    builder.paragraph(
        f"{issuer_name} ({ticker}) is evaluated through a blended institutional framework integrating market structure, "
        f"valuation underwriting, filing intelligence, and scenario-based risk control. The technical signal score reads "
        f"{_fmt_num(technical.get('signal_score'), 0)}/100 and DCF margin of safety is {_fmt_pct(dcf.get('margin_of_safety'))}. "
        f"Street positioning shows {bull_votes} bullish vs {bear_votes} bearish votes (Finnhub). Portfolio concentration "
        f"context is {hhi_label}, which informs sizing and risk-budget interpretation."
    )
    builder.paragraph(
        f"Framing is {recommendation} with {confidence_label} confidence over a {horizon_text} horizon. Treat this as a "
        "structured starting point: any execution decision should follow position-sizing rules and the explicit invalidation "
        "criteria listed in the Catalyst and Risk section."
    )

    ic_snap = report_v2.get("ic_snapshot") or {}
    thesis_pts = ic_snap.get("thesis_top3") or []
    risks_pts = ic_snap.get("risks_top3") or []
    if thesis_pts:
        builder.eyebrow("Top Thesis Points")
        builder.bullets(thesis_pts)
    if risks_pts:
        builder.eyebrow("Top Risks")
        builder.bullets(risks_pts)

    # ----- Part I: Company and Business Model -----
    _section(
        builder,
        "Part I",
        "Company and Business Model",
        "Issuer profile, geography, and operating context.",
    )
    builder.paragraph(
        f"{issuer_name} operates within the {industry_text} segment, with primary listing on "
        f"{_safe_str(profile.get('exchange'))} and reporting in {_safe_str(profile.get('currency'))}. "
        f"The issuer maps to sector ETF proxy {_safe_str(technical.get('sector_etf'), 'unknown')} for relative-strength reads "
        "and is benchmarked against peers using fundamental multiples and tape structure."
    )
    builder.table(
        headers=["Field", "Value"],
        rows=[
            ["Issuer", issuer_name],
            ["Industry", industry_text],
            ["Exchange", _safe_str(profile.get("exchange"))],
            ["Country / Currency", f"{_safe_str(profile.get('country'))} / {_safe_str(profile.get('currency'))}"],
            ["Market Cap", _fmt_money_scaled(profile.get("market_cap"))],
            ["IPO Date", _safe_str(profile.get("ipo"))],
            ["Sector ETF Proxy", _safe_str(technical.get("sector_etf"), "Unknown")],
        ],
        col_widths=[2.4, 4.0],
        alignments=["left", "left"],
    )

    # ----- Part II: Fundamental Performance -----
    _section(
        builder,
        "Part II",
        "Fundamental Performance",
        "Growth, margins, capital efficiency, and earnings cadence.",
    )
    builder.paragraph(
        "Fundamental performance is read through a growth, margin, capital-efficiency, and balance-sheet lens. These four "
        "dimensions together inform whether the business is in a re-rating regime or whether multiple-compression risk is elevated."
    )
    builder.table(
        headers=["Metric", "Value", "Commentary"],
        rows=[
            ["Revenue Growth (TTM YoY)", _fmt_pct(metrics.get("revenue_growth_ttm_yoy")), "Top-line momentum"],
            ["EPS Growth (TTM YoY)", _fmt_pct(metrics.get("eps_growth_ttm_yoy")), "Earnings trajectory"],
            ["Operating Margin (TTM)", _fmt_pct(metrics.get("operating_margin_ttm")), "Operating efficiency"],
            ["Net Margin (TTM)", _fmt_pct(metrics.get("net_margin_ttm")), "Bottom-line profitability"],
            ["ROE / ROA (TTM)", f"{_fmt_pct(metrics.get('roe_ttm'))} / {_fmt_pct(metrics.get('roa_ttm'))}", "Capital efficiency"],
            ["Current Ratio / D/E (Q)", f"{_fmt_num(metrics.get('current_ratio_quarterly'))} / {_fmt_num(metrics.get('debt_to_equity_quarterly'))}", "Liquidity & leverage"],
        ],
        col_widths=[2.6, 1.8, 3.0],
        alignments=["left", "right", "left"],
    )

    builder.eyebrow("Earnings Quality (Recent Prints)")
    builder.paragraph(
        "Earnings dispersion and surprise cadence remain central to near-term re-rating potential. Read these prints "
        "alongside valuation multiples; profitable growth at expanding margins typically supports multiple expansion."
    )
    earnings_table_rows = [
        [
            _safe_str(row.get("period")),
            _fmt_num(row.get("actual")),
            _fmt_num(row.get("estimate")),
            _fmt_pct(row.get("surprise_percent")),
        ]
        for row in earnings_rows[:6]
    ]
    builder.table(
        headers=["Period", "Actual EPS", "Estimate EPS", "Surprise %"],
        rows=earnings_table_rows or [["n/a", "n/a", "n/a", "n/a"]],
        col_widths=[2.0, 1.5, 1.5, 1.5],
        alignments=["left", "right", "right", "right"],
    )

    # ----- Part III: Valuation and Technical Positioning -----
    _section(
        builder,
        "Part III",
        "Valuation and Technical Positioning",
        "Intrinsic value, multiples, and trend structure.",
    )
    builder.paragraph(
        "Valuation and technical positioning are read jointly. Intrinsic-value framing (DCF) is anchored by assumed growth, "
        "discount rate, and terminal-growth combination, while multiples provide cross-section context against history and peers."
    )
    builder.table(
        headers=["Valuation / Technical", "Value"],
        rows=[
            ["DCF Intrinsic Value", _fmt_money(dcf.get("intrinsic_value"))],
            ["DCF Margin of Safety", _fmt_pct(dcf.get("margin_of_safety"))],
            ["P/E (TTM)", _fmt_num(metrics.get("pe_ttm"))],
            ["P/B (Annual)", _fmt_num(metrics.get("pb_annual"))],
            ["P/S (TTM)", _fmt_num(metrics.get("ps_ttm"))],
            ["EV / EBITDA", _fmt_num(metrics.get("ev_to_ebitda"))],
            ["EV / Sales", _fmt_num(metrics.get("ev_to_sales"))],
            ["Median Peer P/E", _fmt_num(comps.get("median_pe"))],
            ["Implied Price (P/E)", _fmt_money(comps.get("implied_price_pe"))],
            ["Implied Price (P/S)", _fmt_money(comps.get("implied_price_ps"))],
        ],
        col_widths=[3.0, 2.0],
        alignments=["left", "right"],
    )
    builder.eyebrow("Technical Positioning")
    builder.table(
        headers=["Metric", "Value"],
        rows=[
            ["Current Price", _fmt_money(technical.get("current_price"))],
            ["52w High / Low", f"{_fmt_money(technical.get('high_52w'))} / {_fmt_money(technical.get('low_52w'))}"],
            ["SMA 50 / 150 / 200", f"{_fmt_money(technical.get('sma_50'))} / {_fmt_money(technical.get('sma_150'))} / {_fmt_money(technical.get('sma_200'))}"],
            ["Stage 2", "YES" if technical.get("stage_2") else "NO"],
            ["VCP", "YES" if technical.get("vcp") else "NO"],
            ["Signal Score", f"{_fmt_num(technical.get('signal_score'), 1)} / 100"],
            ["Sector ETF", _safe_str(technical.get("sector_etf"), "Unknown")],
        ],
        col_widths=[3.0, 2.0],
        alignments=["left", "right"],
    )

    # ----- Part IV: SEC Narrative -----
    _section(
        builder,
        "Part IV",
        "SEC Narrative and Filing Deltas",
        "Filing intelligence and disclosure drift.",
    )
    builder.paragraph(
        "SEC narrative and comparative filing deltas surface qualitative changes that quantitative metrics often miss: "
        "shifts in risk-factor language, evolving guidance posture, and incremental management commentary."
    )
    builder.eyebrow("Filing Analyze")
    builder.paragraph(
        f"Headline: {_safe_str(sec_analyze.get('summary_headline') or sec_analyze.get('error'), 'Unavailable')}"
    )
    builder.paragraph(
        f"Narrative: {_safe_str(sec_analyze.get('narrative_summary'), 'Detailed filing narrative unavailable.')}"
    )
    builder.eyebrow("Filing Compare (Over Time)")
    builder.paragraph(
        f"Headline: {_safe_str(sec_compare.get('summary_headline') or (sec_compare_root.get('error') if isinstance(sec_compare_root, dict) else None), 'Unavailable')}"
    )
    builder.paragraph(
        f"Narrative: {_safe_str(sec_compare.get('narrative_summary'), 'Detailed compare narrative unavailable.')}"
    )
    edgar_filings = list(edgar.get("recent_filings") or [])[:5]
    if edgar_filings:
        builder.eyebrow("Recent EDGAR Filings")
        builder.table(
            headers=["Form", "Date", "Description"],
            rows=[
                [_safe_str(f.get("form")), _safe_str(f.get("date")), _safe_str(f.get("description"))[:90]]
                for f in edgar_filings
            ],
            col_widths=[1.2, 1.5, 4.0],
            alignments=["left", "left", "left"],
        )

    # ----- Part V: Portfolio Fit -----
    _section(
        builder,
        "Part V",
        "Portfolio Fit and Risk Budget",
        "Sector overlap, concentration, and sizing context.",
    )
    builder.paragraph(
        "Portfolio fit converts a standalone idea into a position decision. Sector overlap, concentration contribution, and "
        f"risk-budget impact dictate sizing rather than the headline thesis alone. Concentration here reads as {hhi_label}, with "
        f"{_safe_str(portfolio_summary.get('positions_count'))} open positions across a total market value of "
        f"{_safe_str(portfolio_summary.get('total_market_value'))}."
    )
    pf = report_v2.get("portfolio_fit") or {}
    builder.table(
        headers=["Portfolio Lens", "Value"],
        rows=[
            ["Open positions", _safe_str(portfolio_summary.get("positions_count"))],
            ["Total market value", _safe_str(portfolio_summary.get("total_market_value"))],
            ["Concentration label", hhi_label],
            ["Risk budget impact", _safe_str(pf.get("risk_budget_impact"), "Unavailable")],
            ["Sector overlap %", _fmt_pct(pf.get("sector_overlap_pct"))],
        ],
        col_widths=[2.6, 3.0],
        alignments=["left", "left"],
    )

    # ----- Part VI: Catalysts and Risks -----
    _section(
        builder,
        "Part VI",
        "Catalyst and Risk Matrix",
        "Forward catalysts, risks, and invalidation triggers.",
    )
    builder.paragraph(
        "Catalysts and risks are aggregated across filing cadence, sentiment surfaces, and quantitative checks. "
        "Treat this as a forward-event map: catalysts can re-rate price quickly in either direction, and explicit risk lines "
        "define when the thesis must be re-underwritten or unwound."
    )
    matrix_rows = [["Catalyst", _safe_str(c)] for c in catalysts[:8]]
    matrix_rows += [["Risk", _safe_str(r)] for r in risks[:8]]
    if not matrix_rows:
        matrix_rows = [["Catalyst", "No catalysts surfaced from current inputs."],
                       ["Risk", "No risks surfaced from current inputs."]]
    builder.table(
        headers=["Type", "Item"],
        rows=matrix_rows,
        col_widths=[1.4, 5.6],
        alignments=["left", "left"],
    )

    invalidation = (report_v2.get("ic_snapshot") or {}).get("invalidation") or []
    if invalidation:
        builder.eyebrow("Invalidation Criteria")
        builder.bullets(invalidation)

    # ----- Part VII: Insider Activity -----
    if insider_tx.get("rows") or insider_sent.get("rows"):
        _section(
            builder,
            "Part VII",
            "Insider Activity (Trailing 180 Days)",
            "Form-4 transactions and Finnhub MSPR insider sentiment.",
        )
        builder.paragraph(
            "Form-4 insider transactions and Finnhub's Monthly Share Purchase Ratio (MSPR) capture executive "
            "and director conviction. The aggregate scoring below counts only open-market activity "
            "(SEC Form-4 codes P and S); stock-based compensation grants (A), option exercises (M), "
            "tax-withholding sales (F), and dispositions to the issuer (D) are excluded because they are "
            "non-discretionary and do not represent a market signal."
        )
        builder.table(
            headers=["Insider Lens (Open-Market Only)", "Value"],
            rows=[
                ["Net Shares (Buys − Sells, 180d)", _safe_str(insider_tx.get("net_shares_180d"), "n/a")],
                ["Net Dollars (Approx., 180d)", _fmt_money(insider_tx.get("net_dollars_180d"))],
                ["Open-Market Buys (P)", _safe_str(insider_tx.get("buy_count_180d"), "0")],
                ["Open-Market Sells (S)", _safe_str(insider_tx.get("sell_count_180d"), "0")],
                ["Insider Sentiment (MSPR Sum, 6m)", _fmt_num(insider_sent.get("net_mspr_6m"))],
                ["Net Share Change (Sentiment, 6m)", _fmt_num(insider_sent.get("net_change_6m"), 0)],
            ],
            col_widths=[3.0, 2.0],
            alignments=["left", "right"],
        )
        ins_rows = insider_tx.get("rows") or []
        if ins_rows:
            builder.eyebrow("Recent Form-4 Activity (P=Buy, S=Sell, A=Award, M=Exercise, F=Tax)")
            code_label = {
                "P": "Buy", "S": "Sell", "A": "Award", "M": "Exercise",
                "F": "Tax", "D": "Disposition", "G": "Gift",
            }
            builder.table(
                headers=["Date", "Insider", "Code", "Type", "Shares", "Price"],
                rows=[
                    [
                        _safe_str(row.get("transaction_date")),
                        _safe_str(row.get("name"))[:32],
                        _safe_str(row.get("transaction_code"), "-"),
                        code_label.get((row.get("transaction_code") or "").upper(), "Other"),
                        _fmt_num(row.get("share"), 0),
                        _fmt_money(row.get("transaction_price")),
                    ]
                    for row in ins_rows[:8]
                ],
                col_widths=[1.2, 2.2, 0.5, 0.9, 1.0, 1.0],
                alignments=["left", "left", "center", "left", "right", "right"],
            )

    # ----- Part VIII: Sell-Side Analyst Activity -----
    if upgrades_rows or rec_history:
        _section(
            builder,
            "Part VIII",
            "Sell-Side Analyst Activity",
            "Consensus drift and recent ratings actions.",
        )
        builder.paragraph(
            "Sell-side ratings actions and consensus drift surface how the analyst community is repricing the "
            "issuer in real time. Upgrades clustered near earnings or guidance often co-incide with revisions "
            "cycles, while persistent downgrade flow tends to lead price weakness on a multi-week basis."
        )
        if rec_history:
            builder.eyebrow("Consensus History")
            builder.table(
                headers=["Period", "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"],
                rows=[
                    [
                        _safe_str(row.get("period")),
                        str(row.get("strong_buy") or 0),
                        str(row.get("buy") or 0),
                        str(row.get("hold") or 0),
                        str(row.get("sell") or 0),
                        str(row.get("strong_sell") or 0),
                    ]
                    for row in rec_history[:6]
                ],
                col_widths=[1.4, 1.0, 1.0, 1.0, 1.0, 1.2],
                alignments=["left", "right", "right", "right", "right", "right"],
            )
        if upgrades_rows:
            builder.eyebrow("Recent Ratings Actions")
            builder.table(
                headers=["Date", "Firm", "From", "To", "Action"],
                rows=[
                    [
                        (_safe_str(row.get("grade_time")).split("T")[0] or "n/a"),
                        _safe_str(row.get("company"))[:32],
                        _safe_str(row.get("from_grade"), "-"),
                        _safe_str(row.get("to_grade"), "-"),
                        _safe_str(row.get("action"), "-").title(),
                    ]
                    for row in upgrades_rows[:6]
                ],
                col_widths=[1.2, 2.4, 1.4, 1.4, 1.2],
                alignments=["left", "left", "left", "left", "left"],
            )

    # ----- Part IX: Capital Returns -----
    if dividends_rows or splits_rows:
        _section(
            builder,
            "Part IX",
            "Capital Returns and Corporate Actions",
            "Dividend cadence and historical share-action.",
        )
        builder.paragraph(
            "Dividend cadence, payout cover, and share-action history (splits, special distributions) are part "
            "of the total-return picture. Read the dividend yield in Part II alongside the schedule below — "
            "declining or skipped dividends materially change the income leg of the thesis."
        )
        if dividends_rows:
            freq_map = {1: "Annual", 2: "Semi-annual", 4: "Quarterly", 12: "Monthly"}
            builder.eyebrow("Recent Dividends")
            builder.table(
                headers=["Ex-Date", "Pay Date", "Amount", "Currency", "Frequency"],
                rows=[
                    [
                        _safe_str(row.get("ex_date")),
                        _safe_str(row.get("pay_date")),
                        _fmt_money(row.get("amount")),
                        _safe_str(row.get("currency"), "USD"),
                        freq_map.get(int(row.get("frequency") or 0), "n/a"),
                    ]
                    for row in dividends_rows[:6]
                ],
                col_widths=[1.4, 1.4, 1.2, 1.0, 1.4],
                alignments=["left", "left", "right", "left", "left"],
            )
        if splits_rows:
            builder.eyebrow("Stock Splits")
            split_rows_fmt = []
            for row in splits_rows[:4]:
                tf = _safe_float(row.get("to_factor"))
                ff = _safe_float(row.get("from_factor"))
                ratio = f"{tf:.0f} : {ff:.0f}" if (tf and ff) else "n/a"
                split_rows_fmt.append([_safe_str(row.get("date")), ratio])
            builder.table(
                headers=["Date", "Ratio (To : From)"],
                rows=split_rows_fmt,
                col_widths=[2.0, 3.0],
                alignments=["left", "left"],
            )

    # ----- Part X: News and Sentiment Pulse -----
    sent_buzz = _safe_float(news_sent.get("buzz_articles_in_last_week"))
    sent_score = _safe_float(news_sent.get("company_news_score"))
    if sent_score is not None or sent_buzz is not None:
        _section(
            builder,
            "Part X",
            "News and Sentiment Pulse",
            "Article volume and sentiment vs sector baseline.",
        )
        sector_score = _safe_float(news_sent.get("sector_avg_news_score"))
        delta = ""
        if sent_score is not None and sector_score is not None:
            delta = f" ({(sent_score - sector_score)*100:+.0f}bp vs sector)"
        builder.paragraph(
            f"Finnhub composite news score: {_fmt_num(sent_score, 2)}{delta}. "
            f"Articles in the trailing week: {int(sent_buzz) if sent_buzz is not None else 'n/a'}. "
            f"Bullish article share: {_fmt_pct_fraction(news_sent.get('bullish_percent'))}; "
            f"bearish article share: {_fmt_pct_fraction(news_sent.get('bearish_percent'))}."
        )

    # ----- Upcoming Earnings -----
    if upcoming_earnings:
        builder.eyebrow("Upcoming Earnings Calendar")
        rows_fmt = []
        for row in upcoming_earnings[:4]:
            quarter_str = ""
            if row.get("year") and row.get("quarter"):
                quarter_str = f"{row.get('year')} Q{row.get('quarter')}"
            rev = _safe_float(row.get("revenue_estimate"))
            rev_text = f"${rev/1e9:.2f}B" if rev else "n/a"
            rows_fmt.append([
                _safe_str(row.get("date")),
                quarter_str or "n/a",
                _fmt_num(row.get("eps_estimate")),
                rev_text,
            ])
        builder.table(
            headers=["Date", "Quarter", "EPS Estimate", "Revenue Estimate"],
            rows=rows_fmt,
            col_widths=[1.4, 1.2, 1.4, 1.6],
            alignments=["left", "left", "right", "right"],
        )

    # ----- Peers -----
    if peers:
        builder.eyebrow("Peer Universe (Finnhub)")
        builder.paragraph(
            "Comparable issuers identified by Finnhub: " + ", ".join(peers[:8]) + ". "
            "Differences in scale, capital intensity, and end-market exposure should be considered before "
            "treating multiples as directly comparable."
        )

    # ----- Monitoring Plan -----
    _section(
        builder,
        "Monitoring",
        "Monitoring Plan",
        "Cadence, kill switches, and review triggers.",
    )
    monitoring = report_v2.get("monitoring_plan") or {}
    weekly = monitoring.get("weekly_checks") or []
    monthly = monitoring.get("monthly_checks") or []
    kills = monitoring.get("kill_switches") or []
    if weekly:
        builder.eyebrow("Weekly Checks")
        builder.bullets(weekly)
    if monthly:
        builder.eyebrow("Monthly Checks")
        builder.bullets(monthly)
    if kills:
        builder.eyebrow("Kill Switches")
        builder.bullets(kills)

    # ----- References -----
    _section(
        builder,
        "References",
        "References and Source Metadata",
        "Provenance for cited evidence.",
    )
    if source_metadata:
        builder.table(
            headers=["#", "Source", "Status", "Detail"],
            rows=[
                [
                    str(i + 1),
                    _safe_str(row.get("name")),
                    _safe_str(row.get("status")),
                    _safe_str(row.get("detail"))[:120],
                ]
                for i, row in enumerate(source_metadata)
            ],
            col_widths=[0.5, 1.5, 1.0, 4.0],
            alignments=["right", "left", "left", "left"],
        )
    else:
        builder.paragraph("No source metadata recorded for this run.")
    if fallback_notes:
        builder.eyebrow("Fallback Notes")
        builder.bullets(fallback_notes)

    # ----- Disclaimer -----
    _section(
        builder,
        "Disclaimer",
        "Disclaimer",
        "Use of this report.",
    )
    builder.paragraph(
        "This report is generated automatically for informational research workflows. It is not investment advice and "
        "should not be relied upon as a sole basis for trading decisions. Verify all data points against primary sources "
        "before acting on any framing presented above."
    )
    if generated_at:
        builder.paragraph(f"Generated: {generated_at}", color=builder.style.muted_color, size=builder.style.small_size)

    return builder.to_bytes()


__all__ = [
    "PDFBuilder",
    "Style",
    "dossier_to_pdf",
]
