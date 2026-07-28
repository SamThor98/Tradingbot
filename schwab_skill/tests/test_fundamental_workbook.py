"""Fundamental model workbook: formulas + sheet shape."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from io import BytesIO
from zipfile import ZipFile

from core.fundamental_workbook import dossier_to_xlsx
from core.xlsx_workbook import xlsx_contains_formula


def _sheet_names(zf: ZipFile) -> list[str]:
    root = ET.fromstring(zf.read("xl/workbook.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return [el.attrib.get("name", "") for el in root.findall("m:sheets/m:sheet", ns)]


def _sample_dossier() -> dict:
    return {
        "ticker": "AAPL",
        "generated_at": "2026-07-21T12:00:00+00:00",
        "executive_pitch": {
            "recommendation": "BUY",
            "confidence_label": "High",
            "confidence_score": 80,
            "time_horizon": "3-6 months",
        },
        "sections": {
            "technical_valuation_fundamentals": {
                "raw_report": {
                    "dcf": {
                        "growth_rate": 0.08,
                        "wacc": 0.10,
                        "terminal_growth": 0.025,
                        "net_debt": 50_000_000_000.0,
                        "shares_outstanding": 15_000_000_000.0,
                        "current_price": 190.0,
                        "intrinsic_value": 210.0,
                        "margin_of_safety": 10.5,
                        "fcf_history": [
                            {"year": "2023", "fcf": 90_000_000_000.0},
                            {"year": "2024", "fcf": 100_000_000_000.0},
                        ],
                        "projected_fcf": [
                            {"year": 1, "fcf": 108_000_000_000.0, "pv": 98_181_818.0},
                        ],
                        "error": "",
                    },
                    "comps": {
                        "median_pe": 28.0,
                        "median_ps": 7.0,
                        "implied_price_pe": 200.0,
                        "implied_price_ps": 180.0,
                        "peers": [{"ticker": "MSFT", "name": "Microsoft", "pe": 30.0}],
                    },
                    "technical": {"signal_score": 72.0, "stage_2": True, "vcp": False},
                    "health": {"current_ratio": 1.2, "flags": []},
                }
            },
            "finnhub_catalysts_risks": {
                "snapshot": {"metrics": {"pe_ttm": 29.0, "revenue_growth_ttm_yoy": 0.06}},
                "catalysts": ["Product cycle"],
                "risks": ["Regulation"],
            },
            "sec_narrative": {"analyze": {}, "compare": {"compare": {}}},
        },
    }


def test_fundamental_workbook_has_model_formulas() -> None:
    payload = dossier_to_xlsx(_sample_dossier())
    assert payload.startswith(b"PK\x03\x04")
    assert xlsx_contains_formula(payload, "Assumptions!$B$4")
    assert xlsx_contains_formula(payload, "SUM(D4:D8)")
    assert xlsx_contains_formula(payload, "B15/B17")

    with ZipFile(BytesIO(payload), "r") as zf:
        names = set(_sheet_names(zf))
    assert "Cover" in names
    assert "Assumptions" in names
    assert "Model" in names
    assert "Sensitivity" in names
    assert "Comps" in names


def test_workbook_styles_present() -> None:
    payload = dossier_to_xlsx(_sample_dossier())
    with ZipFile(BytesIO(payload), "r") as zf:
        styles = zf.read("xl/styles.xml").decode("utf-8")
    assert "numFmtId=\"164\"" in styles  # currency
    assert "numFmtId=\"165\"" in styles  # percent
    assert "FFFFFF99" in styles  # yellow input fill
