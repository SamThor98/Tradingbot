from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import yfinance as yf
from PIL import Image, ImageDraw, ImageFont

WIDTH = 1200
HEIGHT = 675
PADDING = 56
CHART_TOP = 210
CHART_BOTTOM = 560
CHART_LEFT = 70
CHART_RIGHT = 1130


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


def _draw_card(ticker: str, closes: list[float], output_path: Path) -> None:
    latest = closes[-1]
    prev = closes[-2]
    change = latest - prev
    change_pct = (change / prev) * 100 if prev else 0.0
    high_3m = max(closes)
    low_3m = min(closes)
    sma_20 = sum(closes[-20:]) / 20

    img = Image.new("RGB", (WIDTH, HEIGHT), (12, 18, 31))
    draw = ImageDraw.Draw(img)

    # Background gradients/bands.
    draw.rectangle((0, 0, WIDTH, 130), fill=(16, 25, 46))
    draw.rectangle((0, CHART_TOP - 18, WIDTH, CHART_BOTTOM + 18), outline=(36, 58, 96), width=2)

    title_font = _font(74)
    subtitle_font = _font(34)
    label_font = _font(28)
    value_font = _font(44)
    small_font = _font(22)

    draw.text((PADDING, 34), f"${ticker.upper()}", fill=(236, 245, 255), font=title_font)
    draw.text((PADDING + 330, 58), "Scanner Highlight", fill=(145, 175, 230), font=subtitle_font)

    # Chart grid.
    for idx in range(5):
        y = CHART_TOP + idx * ((CHART_BOTTOM - CHART_TOP) / 4)
        draw.line((CHART_LEFT, y, CHART_RIGHT, y), fill=(35, 53, 85), width=1)

    x_step = (CHART_RIGHT - CHART_LEFT) / (len(closes) - 1)
    ys = _normalize(closes, low_3m, high_3m, CHART_TOP, CHART_BOTTOM)
    points = [(CHART_LEFT + i * x_step, ys[i]) for i in range(len(closes))]
    for i in range(len(points) - 1):
        draw.line((*points[i], *points[i + 1]), fill=(96, 216, 175), width=4)
    draw.ellipse(
        (points[-1][0] - 7, points[-1][1] - 7, points[-1][0] + 7, points[-1][1] + 7),
        fill=(141, 243, 209),
        outline=(220, 255, 245),
        width=2,
    )

    change_color = (124, 241, 165) if change >= 0 else (255, 137, 137)
    change_arrow = "▲" if change >= 0 else "▼"

    # Metrics row.
    metric_y = 580
    draw.text((PADDING, metric_y), "Last", fill=(160, 187, 237), font=label_font)
    draw.text((PADDING, metric_y + 36), _format_money(latest), fill=(238, 247, 255), font=value_font)

    draw.text((395, metric_y), "1D", fill=(160, 187, 237), font=label_font)
    draw.text(
        (395, metric_y + 36),
        f"{change_arrow} {abs(change_pct):.2f}%",
        fill=change_color,
        font=value_font,
    )

    draw.text((660, metric_y), "20D Avg", fill=(160, 187, 237), font=label_font)
    draw.text((660, metric_y + 36), _format_money(sma_20), fill=(238, 247, 255), font=value_font)

    draw.text((930, metric_y), "3M Range", fill=(160, 187, 237), font=label_font)
    draw.text(
        (930, metric_y + 36),
        f"{_format_money(low_3m)}–{_format_money(high_3m)}",
        fill=(238, 247, 255),
        font=small_font,
    )

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    draw.text((PADDING, HEIGHT - 30), f"Data: Yahoo Finance | Generated {timestamp}", fill=(129, 156, 200), font=small_font)

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
