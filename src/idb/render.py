"""HTML -> PDF -> PNG rendering via Playwright/Chromium.

Chromium rather than WeasyPrint because the templates use flexbox and modern
CSS to get genuinely different-looking layouts, and because a browser engine is
what the SaaS tools these templates imitate actually print with.

One Chromium instance is reused across the whole corpus; launching per document
dominates runtime at n=450.
"""
from __future__ import annotations

import json
import pathlib
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "templates"

_ACCENTS = ["#2f6f9f", "#1f7a5a", "#8a3b4d", "#4a4a8f", "#1d6f6f", "#a05a1f"]


def template_ids() -> List[str]:
    return sorted(p.name.split("_")[0] for p in TEMPLATE_DIR.glob("t*.html.j2"))


def _template_file(tid: str) -> str:
    matches = sorted(TEMPLATE_DIR.glob("%s_*.html.j2" % tid))
    if not matches:
        raise FileNotFoundError("no template with id %r" % tid)
    return matches[0].name


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),
                       undefined=StrictUndefined, autoescape=True)


def _date_forms(iso: str) -> Dict[str, str]:
    y, m, d = (int(x) for x in iso.split("-"))
    dt = date(y, m, d)
    return {
        "iso": iso,
        "dmy_slash": dt.strftime("%d/%m/%Y"),
        "dmy_dash": dt.strftime("%d-%m-%Y"),
        "dmy_dot": dt.strftime("%d.%m.%Y"),
        "dmy_mon": dt.strftime("%d-%b-%Y"),
        "dmy_long": dt.strftime("%d %B %Y"),
    }


def _hsn_summary(items: List[Dict]) -> List[Dict]:
    """Aggregate by (HSN, rate) the way the statutory HSN summary block does."""
    agg: Dict[Any, Dict] = {}
    for it in items:
        key = (it["hsn_sac"], str(it["tax_rate"]))
        row = agg.setdefault(key, {"hsn": it["hsn_sac"], "rate": it["tax_rate"],
                                   "taxable": Decimal("0.00"), "tax": Decimal("0.00")})
        row["taxable"] += it["taxable_value"]
        row["tax"] += it["_cgst"] + it["_sgst"] + it["_igst"]
    return list(agg.values())


def build_html(record: Dict, context: Dict, template_id: str) -> str:
    env = _env()
    tpl = env.get_template(_template_file(template_id))
    items = context["items"]
    for it in items:
        it["_line_total"] = (it["taxable_value"] + it["_cgst"] + it["_sgst"] + it["_igst"])
    seed = context["meta"]["seed"]
    return tpl.render(
        r=record,
        items=items,
        sa=context["seller_addr"],
        ba=context["buyer_addr"],
        bank=context["bank"],
        interstate=context["interstate"],
        d=_date_forms(record["invoice_date"]),
        base_css=(TEMPLATE_DIR / "_base.css").read_text(),
        accent=_ACCENTS[seed % len(_ACCENTS)],
        terms=["30 Days", "Cash", "15 Days", "Advance", "45 Days"][seed % 5],
        ack_no="1%013d" % (seed * 7919 % 10**13),
        hsn_summary=_hsn_summary(items),
    )


class Renderer(object):
    """Context manager holding one Chromium instance."""

    def __init__(self, scale: int = 2):
        self.scale = scale
        self._pw = None
        self._browser = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        return self

    def __exit__(self, *exc):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def render(self, html: str, out_pdf: pathlib.Path,
               out_png: Optional[pathlib.Path] = None, dpi: int = 200) -> Dict:
        page = self._browser.new_page(
            viewport={"width": 794, "height": 1123},          # A4 at 96 dpi
            device_scale_factor=max(1, round(dpi / 96)),
        )
        page.set_content(html, wait_until="load")
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        page.pdf(path=str(out_pdf), format="A4", print_background=True,
                 margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"})
        info = {"pdf": str(out_pdf)}
        if out_png is not None:
            out_png.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out_png), full_page=True)
            info["png"] = str(out_png)
        page.close()
        return info


def pdf_to_png(pdf_path: pathlib.Path, out_png: pathlib.Path, dpi: int = 200) -> List[pathlib.Path]:
    """Rasterise a PDF at a chosen DPI. This is the entry point for the
    degradation layer: 300/150/72 dpi is severity level one all by itself."""
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    written = []
    zoom = dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat)
        p = out_png if doc.page_count == 1 else out_png.with_name(
            "%s_p%d%s" % (out_png.stem, i + 1, out_png.suffix))
        pix.save(str(p))
        written.append(p)
    doc.close()
    return written
