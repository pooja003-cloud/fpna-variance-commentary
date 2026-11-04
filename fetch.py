"""Download Intuit filings and GAAP actuals from SEC EDGAR, then cross-check.

Run from your own Terminal (SEC must be reachable):
    export SEC_USER_AGENT="Your Name your@email"   # SEC requires a contact
    python3 fetch.py

Steps
1. Download every filing in data/filings.csv into cache/ (skips files already there).
2. Pull company facts (GAAP revenue, operating income, diluted EPS) from the SEC API.
3. Build quarterly actuals (Q4 = fiscal year - 9 months, since 10-Ks report no Q4).
4. Cross-check against data/release_actuals.csv, the figures hand-verified from releases.
Writes outputs/sec_actuals.csv and prints every comparison.

Afterwards run extract_segments.py, segments.py and extract_text.py (no network needed).
"""

import csv
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

CIK = 896878
ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
OUT = ROOT / "outputs"

# Candidate us-gaap concepts, first one found wins (logged).
CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "gaap_operating_income": ["OperatingIncomeLoss"],
    "gaap_diluted_eps": ["EarningsPerShareDiluted"],
}
FISCAL_YEARS = (2025, 2026)  # Intuit fiscal year ends July 31


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        sys.exit('Set SEC_USER_AGENT first, e.g. export SEC_USER_AGENT="Name you@example.com"')
    return ua


_last = 0.0


def sec_get(url: str, ua: str) -> requests.Response:
    """GET with SEC's required User-Agent, at most ~5 requests per second."""
    global _last
    wait = 0.2 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers={"User-Agent": ua}, timeout=60)
    _last = time.time()
    resp.raise_for_status()
    return resp


def download_filings(ua: str) -> None:
    CACHE.mkdir(exist_ok=True)
    with open(ROOT / "data" / "filings.csv") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        dest = CACHE / f"{r['key']}.htm"
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  cached   {r['key']:<12} {r['accession']}")
            continue
        acc = r["accession"].replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{CIK}/{acc}/{r['primary_doc']}"
        dest.write_bytes(sec_get(url, ua).content)
        print(f"  fetched  {r['key']:<12} {r['accession']} ({dest.stat().st_size:,} bytes)")


def quarter_ends(fy: int) -> dict:
    """Period-end dates for Intuit fiscal year fy (ends July 31 of year fy)."""
    return {"Q1": date(fy - 1, 10, 31), "Q2": date(fy, 1, 31),
            "Q3": date(fy, 4, 30), "Q4": date(fy, 7, 31)}


def pick_facts(facts: dict, metric: str):
    for concept in CONCEPTS[metric]:
        node = facts["facts"].get("us-gaap", {}).get(concept)
        if node:
            unit = "USD/shares" if metric == "gaap_diluted_eps" else "USD"
            return concept, node["units"].get(unit, [])
    sys.exit(f"No concept found for {metric}: tried {CONCEPTS[metric]}")


WINDOW_START = date(FISCAL_YEARS[0] - 1, 8, 1)  # only warn about periods we use


def by_period(items: list) -> dict:
    """(start, end) -> value. Flags any period in our window reported with different values."""
    out, conflicts = {}, []
    for it in items:
        if "start" not in it or date.fromisoformat(it["start"]) < WINDOW_START:
            continue
        key = (date.fromisoformat(it["start"]), date.fromisoformat(it["end"]))
        if key in out and out[key][0] != it["val"]:
            conflicts.append((key, out[key], (it["val"], it["accn"])))
        out.setdefault(key, (it["val"], it["accn"]))
    for key, a, b in conflicts:
        print(f"  WARNING {key[0]}..{key[1]} reported as {a[0]} ({a[1]}) and {b[0]} ({b[1]})")
    return out


def build_actuals(facts: dict) -> list:
    rows = []
    for metric in CONCEPTS:
        concept, items = pick_facts(facts, metric)
        print(f"  {metric}: us-gaap:{concept}")
        periods = by_period(items)
        for fy in FISCAL_YEARS:
            ends = quarter_ends(fy)
            fy_start = date(fy - 1, 8, 1)
            for q, end in ends.items():
                # 3-month duration ending on the quarter end (80-100 days)
                hit = [(k, v) for k, v in periods.items()
                       if k[1] == end and 80 <= (k[1] - k[0]).days <= 100]
                if hit:
                    (start, _), (val, accn) = hit[0]
                    rows.append([f"{q} FY{fy % 100}", metric, val, accn, "reported 3M"])
                elif q == "Q4" and metric != "gaap_diluted_eps":
                    annual = periods.get((fy_start, end))
                    nine = periods.get((fy_start, ends["Q3"]))
                    if annual and nine:
                        rows.append([f"Q4 FY{fy % 100}", metric, annual[0] - nine[0],
                                     f"{annual[1]} - {nine[1]}", "derived FY - 9M"])
                    else:
                        rows.append([f"Q4 FY{fy % 100}", metric, "", "", "missing"])
                else:
                    # Q4 EPS cannot be derived by subtraction (share counts differ)
                    rows.append([f"{q} FY{fy % 100}", metric, "", "",
                                 "not derivable" if q == "Q4" else "missing"])
            annual = periods.get((fy_start, ends["Q4"]))
            rows.append([f"FY{fy % 100}", metric, annual[0] if annual else "",
                         annual[1] if annual else "", "reported 12M" if annual else "missing"])
    return rows


def to_units(metric: str, val):
    """SEC reports dollars; releases report $ millions (EPS in dollars)."""
    if val == "":
        return None
    return float(val) if metric == "gaap_diluted_eps" else float(val) / 1e6


def cross_check(rows: list) -> int:
    with open(ROOT / "data" / "release_actuals.csv") as f:
        rel = {r["period"]: r for r in csv.DictReader(f)}
    problems = 0
    print(f"\n  {'period':<8} {'metric':<22} {'SEC':>10} {'release':>10}  result  ({'basis'})")
    for period, metric, val, accn, basis in rows:
        sec = to_units(metric, val)
        r = float(rel[period][metric]) if period in rel else None
        if sec is None:
            status = "MISSING" if "not derivable" not in basis else "skip"
        else:
            tol = 0.005 if metric == "gaap_diluted_eps" else 0.5  # half a unit of last digit
            status = "ok" if r is not None and abs(sec - r) <= tol else "MISMATCH"
        problems += status in ("MISMATCH", "MISSING")
        s = "" if sec is None else f"{sec:,.2f}"
        print(f"  {period:<8} {metric:<22} {s:>10} {r if r is not None else '':>10}  {status:<7} ({basis})")
    return problems


def main() -> None:
    ua = user_agent()
    print("1. Downloading filings")
    download_filings(ua)
    print("\n2. Company facts")
    facts = sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK:010d}.json", ua).json()
    (CACHE / "companyfacts.json").write_text(json.dumps(facts))
    rows = build_actuals(facts)
    OUT.mkdir(exist_ok=True)
    with open(OUT / "sec_actuals.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["period", "metric", "value_usd", "accession", "basis"])
        w.writerows(rows)
    print("\n3. Cross-check against data/release_actuals.csv")
    problems = cross_check(rows)
    print(f"\n{'All figures match.' if problems == 0 else f'{problems} problem(s) found - see above.'}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
