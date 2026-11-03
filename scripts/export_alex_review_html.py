"""Export the Alex review docx to a simple HTML Word can open."""

from __future__ import annotations

from pathlib import Path

from docx import Document

SRC = Path.home() / "Downloads" / "Alex_Strategy_Backtest_Review.docx"
OUT = Path.home() / "Downloads" / "Alex_Strategy_Backtest_Review.html"


def esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def main() -> None:
    doc = Document(SRC)
    lines = [
        "<!DOCTYPE html>",
        "<html><head><meta charset=\"utf-8\">",
        "<title>Alex Strategy Backtest Review</title>",
        "<style>",
        "body{font-family:Calibri,Arial,sans-serif;max-width:900px;margin:40px auto;line-height:1.45;color:#222}",
        "h1,h2{color:#111}",
        "table{border-collapse:collapse;width:100%;margin:12px 0 20px}",
        "th,td{border:1px solid #888;padding:6px 8px;text-align:left;vertical-align:top}",
        "th{background:#f0f0f0}",
        "li{margin:4px 0}",
        "</style></head><body>",
    ]
    in_list = False
    for p in doc.paragraphs:
        text = esc(p.text)
        style = p.style.name if p.style else ""
        if not text.strip():
            if in_list:
                lines.append("</ul>")
                in_list = False
            continue
        if style.startswith("List"):
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{text}</li>")
            continue
        if in_list:
            lines.append("</ul>")
            in_list = False
        if style == "Title" or style.startswith("Heading 1"):
            lines.append(f"<h1>{text}</h1>")
        elif style.startswith("Heading"):
            lines.append(f"<h2>{text}</h2>")
        else:
            lines.append(f"<p>{text}</p>")
    if in_list:
        lines.append("</ul>")
    for table in doc.tables:
        lines.append("<table>")
        for i, row in enumerate(table.rows):
            tag = "th" if i == 0 else "td"
            cells = "".join(f"<{tag}>{esc(c.text)}</{tag}>" for c in row.cells)
            lines.append(f"<tr>{cells}</tr>")
        lines.append("</table>")
    lines.append("</body></html>")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
