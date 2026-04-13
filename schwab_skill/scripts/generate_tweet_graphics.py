from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import yfinance as yf
from PIL import Image, ImageDraw, ImageFont

WIDTH = 1200
HEIGHT = 675
PADDING = 56
CHART_TOP = 150
CHART_BOTTOM = 472
CHART_LEFT = 74
CHART_RIGHT = 1126


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _fetch_closes(ticker: str) -> list[float]:
    history = yf.Ticker(ticker).history(period="3mo", interval="1d", auto_adjust=False)
    closes = [float(v) for v in history["Close"].dropna().tolist()]
    if len(closes) < 20:
        raise RuntimeError(f"Not enough history for {ticker} (got {len(closes)} closes)")
    return closes


def _normalize(values: list[float], low: float, high: float, top: float, bottom: float) -> list[float]:
    if high == low:
        return [(top + bottom) / 2 for _ in values]
    scale = (bottom - top) / (high - low)
    return [bottom - ((v - low) * scale) for v in values]


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _draw_card(ticker: str, closes: list[float], output_path: Path) -> None:
    latest = closes[-1]
    prev = closes[-2]
    change = latest - prev
    change_pct = (change / prev) * 100 if prev else 0.0
    high_3m = max(closes)
    low_3m = min(closes)
    sma_20 = sum(closes[-20:]) / 20

    img = Image.new("RGB", (WIDTH, HEIGHT), (8, 14, 26))
    draw = ImageDraw.Draw(img)

    # Subtle layered background for cleaner visual depth.
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(7, 13, 25))
    draw.polygon([(0, 0), (WIDTH, 0), (WIDTH, 260), (0, 360)], fill=(12, 24, 45))
    draw.polygon([(0, HEIGHT), (WIDTH, HEIGHT), (WIDTH, 420), (0, 520)], fill=(10, 20, 37))

    title_font = _font(82)
    subtitle_font = _font(30)
    label_font = _font(24)
    value_font = _font(40)
    small_font = _font(20)
    axis_font = _font(18)

    draw.text((PADDING, 34), f"${ticker.upper()}", fill=(236, 245, 255), font=title_font)
    draw.text((PADDING, 112), "Scanner Signal Snapshot", fill=(144, 172, 219), font=subtitle_font)

    chart_box = (46, CHART_TOP - 18, WIDTH - 46, CHART_BOTTOM + 18)
    _rounded_rect(draw, chart_box, radius=24, fill=(11, 20, 37), outline=(42, 69, 108), width=2)

    # Chart grid.
    for idx in range(5):
        y = CHART_TOP + idx * ((CHART_BOTTOM - CHART_TOP) / 4)
        draw.line((CHART_LEFT, y, CHART_RIGHT, y), fill=(39, 61, 95), width=1)

    x_step = (CHART_RIGHT - CHART_LEFT) / (len(closes) - 1)
    ys = _normalize(closes, low_3m, high_3m, CHART_TOP, CHART_BOTTOM)
    points = [(CHART_LEFT + i * x_step, ys[i]) for i in range(len(closes))]

    area_points = [(CHART_LEFT, CHART_BOTTOM), *points, (CHART_RIGHT, CHART_BOTTOM)]
    draw.polygon(area_points, fill=(25, 59, 92))
    for i in range(len(points) - 1):
        draw.line((*points[i], *points[i + 1]), fill=(116, 223, 191), width=5)
    draw.ellipse(
        (points[-1][0] - 8, points[-1][1] - 8, points[-1][0] + 8, points[-1][1] + 8),
        fill=(159, 244, 216),
        outline=(220, 255, 245),
        width=3,
    )
    draw.text((CHART_LEFT, CHART_TOP - 30), _format_money(high_3m), fill=(124, 154, 204), font=axis_font)
    draw.text((CHART_LEFT, CHART_BOTTOM + 12), _format_money(low_3m), fill=(124, 154, 204), font=axis_font)

    change_color = (124, 241, 165) if change >= 0 else (255, 137, 137)
    change_prefix = "+" if change >= 0 else "-"

    # Bottom stat cards.
    card_y1, card_y2 = 512, 636
    card_w = 262
    gap = 18
    card_x0 = 55
    cards = [
        ("Last", _format_money(latest), (238, 247, 255)),
        ("1D Change", f"{change_prefix}{abs(change_pct):.2f}%", change_color),
        ("20D Average", _format_money(sma_20), (238, 247, 255)),
        ("3M Range", f"{_format_money(low_3m)}-{_format_money(high_3m)}", (238, 247, 255)),
    ]

    for idx, (label, value, value_color) in enumerate(cards):
        x1 = card_x0 + idx * (card_w + gap)
        x2 = x1 + card_w
        _rounded_rect(draw, (x1, card_y1, x2, card_y2), radius=20, fill=(12, 24, 44), outline=(48, 75, 115), width=2)
        draw.text((x1 + 18, card_y1 + 16), label, fill=(150, 180, 230), font=label_font)
        value_font_use = small_font if label == "3M Range" else value_font
        draw.text((x1 + 18, card_y1 + 56), value, fill=value_color, font=value_font_use)

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    draw.text((PADDING, HEIGHT - 30), f"Data: Yahoo Finance   |   Generated {timestamp}", fill=(122, 151, 198), font=small_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="PNG")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tweet-ready ticker graphics.")
    parser.add_argument("tickers", nargs="+", help="Tickers to render, e.g. COHR AMKR")
    parser.add_argument(
        "--output-dir",
        default="/opt/cursor/artifacts",
        help="Output directory for generated PNG files.",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    for raw_ticker in args.tickers:
        ticker = raw_ticker.strip().upper().lstrip("$")
        closes = _fetch_closes(ticker)
        out_path = out_dir / f"tweet_{ticker}.png"
        _draw_card(ticker, closes, out_path)
        print(f"saved {out_path}")


if __name__ == "__main__":
    main()
