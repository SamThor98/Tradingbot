"""Fundamental model workbook: Assumptions + live DCF formulas + comps/reference tabs."""

from __future__ import annotations

from typing import Any

from core.xlsx_workbook import Cell, SheetSpec, sheets_to_xlsx


def _f(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _hdr(*labels: str) -> list[Any]:
    return [Cell(value=lab, style="header") for lab in labels]


def _label(text: str, style: str = "default") -> Cell:
    return Cell(value=text, style=style)


def _last_fcf(dcf: dict[str, Any]) -> float | None:
    hist = dcf.get("fcf_history") or []
    if isinstance(hist, list) and hist:
        last = hist[-1] if isinstance(hist[-1], dict) else None
        if last:
            return _f(last.get("fcf"))
    projected = dcf.get("projected_fcf") or []
    growth = _f(dcf.get("growth_rate")) or 0.0
    if isinstance(projected, list) and projected:
        y1 = projected[0] if isinstance(projected[0], dict) else None
        y1_fcf = _f((y1 or {}).get("fcf"))
        if y1_fcf is not None and (1.0 + growth) != 0:
            return y1_fcf / (1.0 + growth)
    return None


def _recompute_dcf_cache(
    *,
    last_fcf: float,
    growth: float,
    wacc: float,
    tg: float,
    net_debt: float,
    shares: float,
    price: float,
) -> dict[str, Any]:
    """Python mirror of Excel DCF for cached formula values."""
    projected: list[dict[str, float]] = []
    pv_sum = 0.0
    cf = last_fcf
    for yr in range(1, 6):
        cf *= 1.0 + growth
        pv = cf / ((1.0 + wacc) ** yr) if (1.0 + wacc) != 0 else 0.0
        pv_sum += pv
        projected.append({"year": float(yr), "fcf": cf, "pv": pv})
    terminal_value = 0.0
    pv_terminal = 0.0
    if wacc > tg:
        terminal_value = cf * (1.0 + tg) / (wacc - tg)
        pv_terminal = terminal_value / ((1.0 + wacc) ** 5)
    enterprise = pv_sum + pv_terminal
    equity = enterprise - net_debt
    intrinsic = equity / shares if shares > 0 else 0.0
    mos = (intrinsic - price) / price if price > 0 else 0.0
    return {
        "projected": projected,
        "terminal_value": terminal_value,
        "pv_terminal": pv_terminal,
        "pv_sum": pv_sum,
        "enterprise": enterprise,
        "equity": equity,
        "intrinsic": intrinsic,
        "mos": mos,
    }


def _sensitivity_iv(
    *,
    last_fcf: float,
    growth: float,
    wacc: float,
    tg: float,
    net_debt: float,
    shares: float,
) -> float | None:
    if wacc <= tg or shares <= 0:
        return None
    pv_s = 0.0
    c = last_fcf
    for yr in range(1, 6):
        c *= 1.0 + growth
        pv_s += c / ((1.0 + wacc) ** yr)
    tc = c * (1.0 + tg) / (wacc - tg)
    pv_t = tc / ((1.0 + wacc) ** 5)
    return (pv_s + pv_t - net_debt) / shares


def _build_cover(dossier: dict[str, Any], dcf: dict[str, Any]) -> SheetSpec:
    pitch = dossier.get("executive_pitch") or {}
    rows: list[list[Any]] = [
        [Cell(value="Fundamental Model Workbook", style="title")],
        [],
        _hdr("Field", "Value"),
        ["Ticker", dossier.get("ticker") or ""],
        ["Generated At (UTC)", dossier.get("generated_at") or ""],
        ["Recommendation", pitch.get("recommendation") or ""],
        ["Confidence Label", pitch.get("confidence_label") or ""],
        ["Confidence Score", pitch.get("confidence_score") or ""],
        ["Time Horizon", pitch.get("time_horizon") or ""],
        [],
        [Cell(value="Model Snapshot (linked)", style="section")],
        [
            "Intrinsic Value /sh",
            Cell(formula="Model!B20", value=_f(dcf.get("intrinsic_value")), style="currency_link"),
        ],
        [
            "Margin of Safety",
            Cell(
                formula="Model!B21",
                value=(_f(dcf.get("margin_of_safety")) or 0.0) / 100.0
                if _f(dcf.get("margin_of_safety")) is not None
                else None,
                style="pct_link",
            ),
        ],
        [
            "Current Price",
            Cell(formula="Assumptions!B10", value=_f(dcf.get("current_price")), style="currency_link"),
        ],
        [],
        [
            Cell(
                value="Blue cells on Assumptions are inputs. Black cells on Model are formulas.",
                style="note",
            )
        ],
        [
            Cell(
                value="Change growth / WACC / terminal growth on Assumptions to re-price the DCF.",
                style="note",
            )
        ],
    ]
    return SheetSpec(name="Cover", rows=rows, col_widths=[36, 28], tab_color="1F4E79")


def _build_assumptions(dcf: dict[str, Any]) -> tuple[SheetSpec, dict[str, Any]]:
    last_fcf = _last_fcf(dcf) or 0.0
    growth = _f(dcf.get("growth_rate"))
    if growth is None:
        growth = 0.05
    wacc = _f(dcf.get("wacc"))
    if wacc is None:
        wacc = 0.10
    tg = _f(dcf.get("terminal_growth"))
    if tg is None:
        tg = 0.025
    net_debt = _f(dcf.get("net_debt")) or 0.0
    shares = _f(dcf.get("shares_outstanding")) or 0.0
    price = _f(dcf.get("current_price")) or 0.0
    cache = _recompute_dcf_cache(
        last_fcf=last_fcf,
        growth=growth,
        wacc=wacc,
        tg=tg,
        net_debt=net_debt,
        shares=shares,
        price=price,
    )

    rows: list[list[Any]] = [
        [Cell(value="DCF Assumptions", style="title")],
        [],
        _hdr("Assumption", "Value", "Notes"),
        [
            "Last reported FCF ($)",
            Cell(value=last_fcf, style="currency_input_yellow"),
            "Seed from cash-flow history; edit to stress-test",
        ],
        [
            "FCF growth rate",
            Cell(value=growth, style="pct_input_yellow"),
            "CAGR-capped growth used in live model",
        ],
        [
            "WACC",
            Cell(value=wacc, style="pct_input_yellow"),
            "Discount rate",
        ],
        [
            "Terminal growth",
            Cell(value=tg, style="pct_input_yellow"),
            "Must stay below WACC",
        ],
        [
            "Net debt ($)",
            Cell(value=net_debt, style="currency_input"),
            "Total debt − cash",
        ],
        [
            "Shares outstanding",
            Cell(value=shares, style="number_input"),
            "Diluted shares for per-share value",
        ],
        [
            "Current price ($)",
            Cell(value=price, style="currency_input"),
            "Spot used for margin of safety",
        ],
        [],
        [Cell(value="Named references (for Model sheet)", style="section")],
        ["LastFCF", Cell(formula="B4", value=last_fcf, style="currency_link")],
        ["Growth", Cell(formula="B5", value=growth, style="pct_link")],
        ["WACC", Cell(formula="B6", value=wacc, style="pct_link")],
        ["TerminalG", Cell(formula="B7", value=tg, style="pct_link")],
        ["NetDebt", Cell(formula="B8", value=net_debt, style="currency_link")],
        ["Shares", Cell(formula="B9", value=shares, style="link")],
        ["Price", Cell(formula="B10", value=price, style="currency_link")],
        [],
        [Cell(value="FCF History (source)", style="section")],
        _hdr("Year", "FCF ($)"),
    ]
    for item in dcf.get("fcf_history") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            [
                str(item.get("year") or ""),
                Cell(value=_f(item.get("fcf")), style="currency"),
            ]
        )
    if not (dcf.get("fcf_history") or []):
        rows.append(["—", "No FCF history in dossier"])

    if dcf.get("error"):
        rows.extend([[], [Cell(value=f"DCF engine note: {dcf.get('error')}", style="note")]])

    spec = SheetSpec(
        name="Assumptions",
        rows=rows,
        col_widths=[28, 18, 48],
        freeze="A4",
        tab_color="FFC000",
    )
    inputs = {
        "last_fcf": last_fcf,
        "growth": growth,
        "wacc": wacc,
        "tg": tg,
        "net_debt": net_debt,
        "shares": shares,
        "price": price,
        "cache": cache,
    }
    return spec, inputs


def _build_model(inputs: dict[str, Any]) -> SheetSpec:
    cache = inputs["cache"]
    projected = cache["projected"]

    # Row map (1-indexed):
    # 1 title, 2 blank, 3 headers, 4-8 years, 9 blank,
    # 10 Terminal Value, 11 PV Terminal, 12 PV Explicit, 13 Enterprise,
    # 14 Net Debt, 15 Equity, 16 blank, 17 Shares, 18 Price,
    # 19 blank, 20 Intrinsic, 21 MoS
    rows: list[list[Any]] = [
        [Cell(value="DCF Model (formulas)", style="title")],
        [],
        _hdr("Year", "Projected FCF ($)", "Discount Factor", "PV of FCF ($)"),
    ]
    for yr in range(1, 6):
        excel_row = 3 + yr  # 4..8
        p = projected[yr - 1]
        fcf_f = f"Assumptions!$B$4*(1+Assumptions!$B$5)^{yr}"
        df_f = f"1/(1+Assumptions!$B$6)^{yr}"
        pv_f = f"B{excel_row}*C{excel_row}"
        rows.append(
            [
                yr,
                Cell(formula=fcf_f, value=p["fcf"], style="currency"),
                Cell(formula=df_f, value=1.0 / ((1.0 + inputs["wacc"]) ** yr), style="number"),
                Cell(formula=pv_f, value=p["pv"], style="currency"),
            ]
        )

    rows.extend(
        [
            [],
            [
                _label("Terminal Value", "section"),
                Cell(
                    formula="B8*(1+Assumptions!$B$7)/(Assumptions!$B$6-Assumptions!$B$7)",
                    value=cache["terminal_value"],
                    style="currency",
                ),
                "FCF_5*(1+g)/(WACC-g)",
            ],
            [
                _label("PV of Terminal Value", "section"),
                Cell(
                    formula="B10*C8",
                    value=cache["pv_terminal"],
                    style="currency",
                ),
                "TV discounted 5 years",
            ],
            [
                _label("PV of Explicit FCF", "section"),
                Cell(formula="SUM(D4:D8)", value=cache["pv_sum"], style="currency"),
                "",
            ],
            [
                _label("Enterprise Value", "section"),
                Cell(formula="B11+B12", value=cache["enterprise"], style="currency"),
                "",
            ],
            [
                "Less: Net Debt",
                Cell(formula="Assumptions!$B$8", value=inputs["net_debt"], style="currency_link"),
                "",
            ],
            [
                _label("Equity Value", "section"),
                Cell(formula="B13-B14", value=cache["equity"], style="currency"),
                "",
            ],
            [],
            [
                "Shares outstanding",
                Cell(formula="Assumptions!$B$9", value=inputs["shares"], style="link"),
                "",
            ],
            [
                "Current price",
                Cell(formula="Assumptions!$B$10", value=inputs["price"], style="currency_link"),
                "",
            ],
            [],
            [
                _label("Intrinsic Value / share", "section"),
                Cell(
                    formula='IF(B17<=0,"",B15/B17)',
                    value=cache["intrinsic"],
                    style="currency",
                ),
                "",
            ],
            [
                _label("Margin of Safety", "section"),
                Cell(
                    formula='IF(B18<=0,"",(B20-B18)/B18)',
                    value=cache["mos"],
                    style="pct",
                ),
                "(Intrinsic − Price) / Price",
            ],
            [],
            [
                Cell(
                    value="All outputs are Excel formulas. Cached values shown until the spreadsheet recalculates.",
                    style="note",
                )
            ],
        ]
    )
    return SheetSpec(
        name="Model",
        rows=rows,
        col_widths=[28, 20, 18, 18],
        freeze="A4",
        tab_color="548235",
    )


def _build_sensitivity(inputs: dict[str, Any]) -> SheetSpec:
    waccs = [0.08, 0.09, 0.10, 0.11, 0.12]
    tgs = [0.02, 0.025, 0.03]
    rows: list[list[Any]] = [
        [Cell(value="Sensitivity — Intrinsic Value / share", style="title")],
        [],
        [
            Cell(
                value="Rows = WACC, Columns = terminal growth. Uses Assumptions growth, FCF, net debt, shares.",
                style="note",
            )
        ],
        [],
        _hdr("WACC \\ Terminal g", "2.0%", "2.5%", "3.0%"),
    ]

    # Data starts at Excel row 6. WACC in column A; TG literals in formulas.
    for i, w in enumerate(waccs):
        excel_row = 6 + i
        fixed: list[Any] = [Cell(value=w, style="pct_input")]
        for tg in tgs:
            cached = _sensitivity_iv(
                last_fcf=inputs["last_fcf"],
                growth=inputs["growth"],
                wacc=w,
                tg=tg,
                net_debt=inputs["net_debt"],
                shares=inputs["shares"],
            )
            f = (
                f'IF(OR($A{excel_row}<={tg},Assumptions!$B$9<=0),"",'
                f"(Assumptions!$B$4*(1+Assumptions!$B$5)/(1+$A{excel_row})"
                f"+Assumptions!$B$4*(1+Assumptions!$B$5)^2/(1+$A{excel_row})^2"
                f"+Assumptions!$B$4*(1+Assumptions!$B$5)^3/(1+$A{excel_row})^3"
                f"+Assumptions!$B$4*(1+Assumptions!$B$5)^4/(1+$A{excel_row})^4"
                f"+Assumptions!$B$4*(1+Assumptions!$B$5)^5/(1+$A{excel_row})^5"
                f"+Assumptions!$B$4*(1+Assumptions!$B$5)^5*(1+{tg})"
                f"/($A{excel_row}-{tg})/(1+$A{excel_row})^5"
                f"-Assumptions!$B$8)/Assumptions!$B$9)"
            )
            fixed.append(Cell(formula=f, value=cached, style="currency"))
        rows.append(fixed)

    rows.extend(
        [
            [],
            [
                Cell(
                    value="Edit WACC in column A or Assumptions growth/FCF to refresh the grid.",
                    style="note",
                )
            ],
        ]
    )
    return SheetSpec(
        name="Sensitivity",
        rows=rows,
        col_widths=[20, 14, 14, 14],
        freeze="B6",
        tab_color="C65911",
    )


def _build_comps(comps: dict[str, Any], metrics: dict[str, Any]) -> SheetSpec:
    rows: list[list[Any]] = [
        [Cell(value="Comparable Multiples", style="title")],
        [],
        _hdr("Lens", "Value", "Source"),
        [
            "Median Peer P/E",
            Cell(value=_f(comps.get("median_pe")), style="number_input"),
            "report.comps",
        ],
        [
            "Median Peer P/S",
            Cell(value=_f(comps.get("median_ps")), style="number_input"),
            "report.comps",
        ],
        [
            "Median Peer EV/EBITDA",
            Cell(value=_f(comps.get("median_ev_ebitda")), style="number"),
            "report.comps",
        ],
        [
            "Implied Price (P/E)",
            Cell(value=_f(comps.get("implied_price_pe")), style="currency"),
            "median PE × trailing EPS (engine)",
        ],
        [
            "Implied Price (P/S)",
            Cell(value=_f(comps.get("implied_price_ps")), style="currency"),
            "median PS × revenue/share (engine)",
        ],
        [
            "P/E TTM (subject)",
            Cell(value=_f(metrics.get("pe_ttm")), style="number"),
            "Finnhub metrics",
        ],
        [
            "P/S TTM (subject)",
            Cell(value=_f(metrics.get("ps_ttm")), style="number"),
            "Finnhub metrics",
        ],
        [
            "EV/EBITDA (subject)",
            Cell(value=_f(metrics.get("ev_to_ebitda")), style="number"),
            "Finnhub metrics",
        ],
        [],
        [Cell(value="Peer Table", style="section")],
        _hdr("Ticker", "Name", "P/E", "P/S", "EV/EBITDA", "Market Cap"),
    ]
    peers = comps.get("peers") or []
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        rows.append(
            [
                peer.get("ticker") or "",
                peer.get("name") or "",
                Cell(value=_f(peer.get("pe")), style="number"),
                Cell(value=_f(peer.get("ps")), style="number"),
                Cell(value=_f(peer.get("ev_ebitda")), style="number"),
                Cell(value=_f(peer.get("market_cap")), style="currency"),
            ]
        )
    if not peers:
        rows.append(["—", "No peers in dossier", "", "", "", ""])
    if comps.get("error"):
        rows.extend([[], [Cell(value=f"Comps note: {comps.get('error')}", style="note")]])

    return SheetSpec(
        name="Comps",
        rows=rows,
        col_widths=[14, 28, 12, 12, 14, 16],
        freeze="A3",
        tab_color="2F5496",
    )


def _kv_sheet(
    name: str,
    title: str,
    headers: list[str],
    rows_data: list[list[Any]],
    *,
    tab_color: str | None = None,
) -> SheetSpec:
    rows: list[list[Any]] = [
        [Cell(value=title, style="title")],
        [],
        _hdr(*headers),
    ]
    rows.extend(rows_data)
    widths = [28] + [18] * max(0, len(headers) - 1)
    return SheetSpec(name=name, rows=rows, col_widths=widths, freeze="A4", tab_color=tab_color)


def build_fundamental_workbook_sheets(dossier: dict[str, Any]) -> list[SheetSpec]:
    sections = dossier.get("sections") or {}
    fundamentals = sections.get("technical_valuation_fundamentals") or {}
    raw = fundamentals.get("raw_report") or {}
    tech = raw.get("technical") or {}
    dcf = raw.get("dcf") or {}
    comps = raw.get("comps") or {}
    health = raw.get("health") or {}
    snapshot = (sections.get("finnhub_catalysts_risks") or {}).get("snapshot") or {}
    metrics = snapshot.get("metrics") or {}
    catalysts = list((sections.get("finnhub_catalysts_risks") or {}).get("catalysts") or [])
    risks = list((sections.get("finnhub_catalysts_risks") or {}).get("risks") or [])
    sec_analyze = ((sections.get("sec_narrative") or {}).get("analyze") or {})
    sec_compare = (((sections.get("sec_narrative") or {}).get("compare") or {}).get("compare") or {})

    assumptions, inputs = _build_assumptions(dcf if isinstance(dcf, dict) else {})
    model = _build_model(inputs)
    sensitivity = _build_sensitivity(inputs)
    comps_sheet = _build_comps(comps if isinstance(comps, dict) else {}, metrics if isinstance(metrics, dict) else {})

    fund_rows: list[list[Any]] = [
        ["Revenue Growth TTM YoY", Cell(value=_f(metrics.get("revenue_growth_ttm_yoy")), style="pct"), "Finnhub"],
        ["EPS Growth TTM YoY", Cell(value=_f(metrics.get("eps_growth_ttm_yoy")), style="pct"), "Finnhub"],
        ["Operating Margin TTM", Cell(value=_f(metrics.get("operating_margin_ttm")), style="pct"), "Finnhub"],
        ["Net Margin TTM", Cell(value=_f(metrics.get("net_margin_ttm")), style="pct"), "Finnhub"],
        ["ROE TTM", Cell(value=_f(metrics.get("roe_ttm")), style="pct"), "Finnhub"],
        ["ROA TTM", Cell(value=_f(metrics.get("roa_ttm")), style="pct"), "Finnhub"],
        ["Current Ratio (Q)", Cell(value=_f(metrics.get("current_ratio_quarterly")), style="number"), "Finnhub"],
        ["Quick Ratio (Q)", Cell(value=_f(metrics.get("quick_ratio_quarterly")), style="number"), "Finnhub"],
        ["Debt/Equity (Q)", Cell(value=_f(metrics.get("debt_to_equity_quarterly")), style="number"), "Finnhub"],
        ["Interest Coverage TTM", Cell(value=_f(metrics.get("interest_coverage_ttm")), style="number"), "Finnhub"],
        ["Dividend Yield TTM", Cell(value=_f(metrics.get("dividend_yield_ttm")), style="pct"), "Finnhub"],
    ]
    # Finnhub growth/margins often arrive as whole percents (e.g. 12.3); leave as number if |x|>1.5
    for row in fund_rows:
        cell = row[1]
        if isinstance(cell, Cell) and cell.style == "pct" and cell.value is not None:
            if abs(float(cell.value)) > 1.5:
                cell.style = "number"
                row[2] = f"{row[2]} (raw % points)"

    tech_rows: list[list[Any]] = [
        ["Signal Score", Cell(value=_f(tech.get("signal_score")), style="number"), "report.technical"],
        ["Stage 2", bool(tech.get("stage_2")), "report.technical"],
        ["VCP", bool(tech.get("vcp")), "report.technical"],
        ["Current Price", Cell(value=_f(tech.get("current_price")), style="currency"), "report.technical"],
        ["52w High", Cell(value=_f(tech.get("high_52w")), style="currency"), "report.technical"],
        ["52w Low", Cell(value=_f(tech.get("low_52w")), style="currency"), "report.technical"],
        ["SMA 50", Cell(value=_f(tech.get("sma_50")), style="currency"), "report.technical"],
        ["SMA 150", Cell(value=_f(tech.get("sma_150")), style="currency"), "report.technical"],
        ["SMA 200", Cell(value=_f(tech.get("sma_200")), style="currency"), "report.technical"],
        ["Sector ETF", tech.get("sector_etf") or "", "report.technical"],
    ]

    health_rows: list[list[Any]] = [
        ["Current Ratio", Cell(value=_f(health.get("current_ratio")), style="number"), "report.health"],
        ["Debt to Equity", Cell(value=_f(health.get("debt_to_equity")), style="number"), "report.health"],
        ["Interest Coverage", Cell(value=_f(health.get("interest_coverage")), style="number"), "report.health"],
        ["ROE", Cell(value=_f(health.get("roe")), style="number"), "report.health"],
        ["Operating Margin", Cell(value=_f(health.get("operating_margin")), style="number"), "report.health"],
        ["Flag Count", len(health.get("flags") or []), "report.health"],
    ]
    for idx, flag in enumerate((health.get("flags") or [])[:20], start=1):
        health_rows.append([f"Flag {idx}", flag, "report.health"])

    sec_rows: list[list[Any]] = [
        ["Analyze Headline", sec_analyze.get("summary_headline") or sec_analyze.get("error"), "sec_analyze"],
        ["Analyze Narrative", sec_analyze.get("narrative_summary"), "sec_analyze"],
        ["Compare Headline", sec_compare.get("summary_headline"), "sec_compare"],
        ["Compare Narrative", sec_compare.get("narrative_summary"), "sec_compare"],
        ["Compare Confidence", sec_compare.get("compare_confidence"), "sec_compare"],
    ]
    for idx, item in enumerate((sec_compare.get("top_differences") or [])[:10], start=1):
        sec_rows.append([f"Top Difference {idx}", item, "sec_compare"])

    cr_rows: list[list[Any]] = []
    for item in catalysts[:20]:
        cr_rows.append(["Catalyst", item])
    for item in risks[:20]:
        cr_rows.append(["Risk", item])
    if not cr_rows:
        cr_rows.append(["Info", "No catalyst/risk rows available."])

    return [
        _build_cover(dossier, dcf if isinstance(dcf, dict) else {}),
        assumptions,
        model,
        sensitivity,
        comps_sheet,
        _kv_sheet("Fundamentals", "Fundamental Metrics", ["Metric", "Value", "Source"], fund_rows, tab_color="7030A0"),
        _kv_sheet("Technical", "Technical Snapshot", ["Signal", "Value", "Source"], tech_rows, tab_color="5B9BD5"),
        _kv_sheet("Health", "Financial Health", ["Signal", "Value", "Source"], health_rows, tab_color="A9D08E"),
        _kv_sheet("SEC Trace", "SEC Narrative Trace", ["Lens", "Value", "Source"], sec_rows),
        _kv_sheet("Catalysts Risks", "Catalysts & Risks", ["Type", "Item"], cr_rows, tab_color="FF6B6B"),
    ]


def dossier_to_xlsx(dossier: dict[str, Any]) -> bytes:
    sheets = build_fundamental_workbook_sheets(dossier)
    if not sheets:
        sheets = [
            SheetSpec(
                name="Cover",
                rows=[["Field", "Value"], ["Ticker", dossier.get("ticker") or ""]],
            )
        ]
    return sheets_to_xlsx(sheets)
