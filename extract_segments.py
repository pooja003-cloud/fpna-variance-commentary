"""Extract segment, product-line and unallocated-item figures from the inline XBRL
tags in the cached 10-Qs and 10-K (run fetch.py first).

Reading the tags gives the exact filed numbers with their period and segment, instead
of retyping a table. Output is a long table; segments.py turns it into segments.csv.
"""

import csv
import re
import sys
from pathlib import Path

import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

ROOT = Path(__file__).resolve().parent
FILINGS = ["10q_Q1FY26", "10q_Q2FY26", "10q_Q3FY26", "10k_FY26"]
AXES = ("us-gaap:StatementBusinessSegmentsAxis", "srt:ProductOrServiceAxis",
        "srt:ConsolidationItemsAxis", "us-gaap:SegmentReportingReconcilingItemForOperatingProfitLossFromSegmentToConsolidatedByAxis")

# Company-level income statement lines kept when they carry no dimension.
UNDIMENSIONED = {"us-gaap:Revenues", "us-gaap:OperatingIncomeLoss",
                 "us-gaap:CostOfRevenue", "us-gaap:CostOfGoodsAndServicesSold",
                 "us-gaap:SellingAndMarketingExpense", "us-gaap:ResearchAndDevelopmentExpense",
                 "us-gaap:GeneralAndAdministrativeExpense",
                 "us-gaap:AmortizationOfIntangibleAssets", "us-gaap:RestructuringCharges",
                 "us-gaap:CostsAndExpenses", "us-gaap:AllocatedShareBasedCompensationExpense"}


def contexts(soup) -> dict:
    out = {}
    for c in soup.find_all("xbrli:context"):
        start, end = c.find("xbrli:startdate"), c.find("xbrli:enddate")
        mems = tuple((m["dimension"], m.text.strip()) for m in c.find_all("xbrldi:explicitmember"))
        out[c["id"]] = (start.text if start else "", end.text if end else "", mems)
    return out


def value(tag) -> float:
    """Apply inline-XBRL scale and sign to the displayed number."""
    txt = tag.text.strip().replace(",", "")
    if txt in ("", "-", "—"):
        return 0.0
    v = float(txt) * 10 ** int(tag.get("scale", "0"))
    return -v if tag.get("sign") == "-" else v


def extract(key: str) -> list:
    path = ROOT / "cache" / f"{key}.htm"
    if not path.exists():
        sys.exit(f"{path} missing - run fetch.py first")
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    ctx = contexts(soup)
    rows, seen = [], set()
    for t in soup.find_all("ix:nonfraction"):
        start, end, mems = ctx[t["contextref"]]
        if not start:
            continue
        concept = t["name"]
        dims = ";".join(f"{d.split(':')[1]}={m.split(':')[1]}" for d, m in mems)
        if mems and not any(d in AXES for d, _ in mems):
            continue
        if not mems and concept not in UNDIMENSIONED:
            continue
        k = (concept, start, end, dims)
        if k in seen:
            continue
        seen.add(k)
        rows.append([key, concept, start, end, dims, value(t)])
    return rows


def main() -> None:
    out = ROOT / "outputs" / "segment_facts.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filing", "concept", "start", "end", "dims", "value_usd"])
        for key in FILINGS:
            rows = extract(key)
            w.writerows(rows)
            print(f"{key}: {len(rows)} facts")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
