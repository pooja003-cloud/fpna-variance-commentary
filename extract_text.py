"""Extract reason-source text from cached filings (DECISIONS.md, decision 5).

- 10-Qs: the MD&A section (Item 2), the only permitted source for Q1-Q3 YoY reasons.
- Releases: the narrative before "Forward-looking Guidance", the permitted source for
  Q4 YoY reasons and for the live quarter until its 10-Q is filed.

Text is cleaned with textnorm.normalize, the same function check.py uses on quotes, and
running page headers/footers are removed so sentences that cross a page break stay whole.
Run after fetch.py. Writes data/mdna/<period>.txt and data/releases/<period>.txt.
"""

import csv
import re
import sys
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from textnorm import normalize

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
ROOT = Path(__file__).resolve().parent

# 10-Q key -> period whose YoY it explains
MDNA = {"10q_Q1FY26": "Q1 FY26", "10q_Q2FY26": "Q2 FY26", "10q_Q3FY26": "Q3 FY26"}
MDNA_START = re.compile(r"ITEM 2 - MANAGEMENT.S DISCUSSION AND ANALYSIS")
MDNA_END = re.compile(r"ITEM 3 - QUANTITATIVE AND QUALITATIVE DISCLOSURES")

# Running headers/footers inserted at page breaks
PAGE_NOISE = [
    re.compile(r"Intuit Q[1-4] Fiscal \d{4} Form 10-Q \d+ Table of Contents"),
    re.compile(r"Intuit Reports [A-Za-z0-9\- ]+?(?:Earnings|Results) Page \d+"),
]


def plain_text(path: Path) -> str:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    for tag in soup.find_all("ix:header"):
        tag.decompose()
    text = normalize(soup.get_text(" "))
    for pat in PAGE_NOISE:
        text = pat.sub(" ", text)
    return normalize(text)


def section(text: str, start: re.Pattern, end: re.Pattern, key: str) -> str:
    s = start.search(text)
    if not s:
        sys.exit(f"{key}: start marker not found")
    e = end.search(text, s.end())
    if not e:
        sys.exit(f"{key}: end marker not found")
    return text[s.start():e.start()].strip()


def release_periods() -> dict:
    """rel_<Qn>FY<yy> -> 'Qn FYyy' for every release in filings.csv."""
    with open(ROOT / "data" / "filings.csv") as f:
        keys = [r["key"] for r in csv.DictReader(f) if r["key"].startswith("rel_")]
    return {k: f"{k[4:6]} FY{k[-2:]}" for k in keys}


def main() -> None:
    (ROOT / "data" / "mdna").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "releases").mkdir(parents=True, exist_ok=True)
    for key, period in MDNA.items():
        path = ROOT / "cache" / f"{key}.htm"
        if not path.exists():
            print(f"  skip {key} (not downloaded)"); continue
        body = section(plain_text(path), MDNA_START, MDNA_END, key)
        out = ROOT / "data" / "mdna" / f"{period.replace(' ', '_')}.txt"
        out.write_text(body + "\n", encoding="utf-8")
        print(f"  MD&A    {period}: {len(body):>7,} chars -> {out.relative_to(ROOT)}")
    for key, period in release_periods().items():
        path = ROOT / "cache" / f"{key}.htm"
        if not path.exists():
            print(f"  skip {key} (not downloaded)"); continue
        text = plain_text(path)
        cut = text.find("Forward-looking Guidance")
        body = (text[:cut] if cut > 0 else text).strip()
        out = ROOT / "data" / "releases" / f"{period.replace(' ', '_')}.txt"
        out.write_text(body + "\n", encoding="utf-8")
        print(f"  release {period}: {len(body):>7,} chars -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
