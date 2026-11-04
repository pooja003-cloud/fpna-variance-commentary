"""Build data/segments.csv (8 quarters, FY25 recast + FY26) from outputs/segment_facts.csv.

Sources (DECISIONS.md, recast handling):
- Q1-Q3 of both years: the FY26 10-Q for that quarter. Each shows the FY25 quarter
  recast to the current structure, so original FY25 10-Qs are never used.
- Q4 of both years: FY26 10-K twelve months minus the Q3 FY26 10-Q nine months.

Every figure keeps its filing, XBRL concept and period, so it can be found in the filing.
Tie-outs run at the end; any failure stops with a non-zero exit.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OS = "ConsolidationItemsAxis=OperatingSegmentsMember"
REC = "ConsolidationItemsAxis=MaterialReconcilingItemsMember"
GBS = "StatementBusinessSegmentsAxis=GlobalBusinessSolutionsSegmentMember"
CON = "StatementBusinessSegmentsAxis=ConsumerSegmentMember"
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


def prod(member, seg):
    return (REV, f"ProductOrServiceAxis={member};{seg}")


# metric -> (concept, dims). Dims must match exactly.
METRICS = {
    "total_revenue": ("us-gaap:Revenues", ""),
    "gbs_revenue": (REV, GBS),
    "consumer_revenue": (REV, CON),
    "qbo_accounting_revenue": prod("QuickBooksOnlineAccountingMember", GBS),
    "online_services_revenue": prod("OnlineServicesMember", GBS),
    "online_ecosystem_revenue": prod("OnlineEcosystemMember", GBS),
    "qb_desktop_accounting_revenue": prod("QuickBooksDesktopAccountingMember", GBS),
    "desktop_services_supplies_revenue": prod("DesktopServicesandSuppliesMember", GBS),
    "desktop_ecosystem_revenue": prod("DesktopEcosystemMember", GBS),
    "turbotax_revenue": prod("TurboTaxMember", CON),
    "credit_karma_revenue": prod("CreditKarmaMember", CON),
    "protax_revenue": prod("ProTaxMember", CON),
    "gbs_segment_costs": ("us-gaap:CostsAndExpenses", f"{OS};{GBS}"),
    "consumer_segment_costs": ("us-gaap:CostsAndExpenses", f"{OS};{CON}"),
    "gbs_segment_oi": ("us-gaap:OperatingIncomeLoss", f"{OS};{GBS}"),
    "consumer_segment_oi": ("us-gaap:OperatingIncomeLoss", f"{OS};{CON}"),
    "total_segment_oi": ("us-gaap:OperatingIncomeLoss", OS),
    "unalloc_share_based_comp": ("us-gaap:AllocatedShareBasedCompensationExpense", REC),
    "unalloc_other_corporate": ("us-gaap:OtherExpenses", REC),
    "unalloc_amort_acquired_tech": ("us-gaap:CostOfGoodsAndServicesSoldAmortization", REC),
    "unalloc_amort_other_intangibles": ("us-gaap:AmortizationOfIntangibleAssets", REC),
    "unalloc_restructuring": ("us-gaap:RestructuringCharges", REC),
    "unalloc_total": ("intu:UnallocatedCorporateItemsTotal", REC),
    "gaap_operating_income": ("us-gaap:OperatingIncomeLoss", ""),
    "cost_of_revenue_service": ("us-gaap:CostOfGoodsAndServicesSold", "ProductOrServiceAxis=ServiceMember"),
    "cost_of_revenue_product": ("us-gaap:CostOfGoodsAndServicesSold", "ProductOrServiceAxis=ProductAndOtherMember"),
    "selling_and_marketing": ("us-gaap:SellingAndMarketingExpense", ""),
    "research_and_development": ("us-gaap:ResearchAndDevelopmentExpense", ""),
    "general_and_administrative": ("us-gaap:GeneralAndAdministrativeExpense", ""),
    "total_costs_and_expenses": ("us-gaap:CostsAndExpenses", ""),
}

# (period label, filing, start, end) for directly reported quarters
DIRECT = [
    ("Q1 FY25", "10q_Q1FY26", "2024-08-01", "2024-10-31"),
    ("Q1 FY26", "10q_Q1FY26", "2025-08-01", "2025-10-31"),
    ("Q2 FY25", "10q_Q2FY26", "2024-11-01", "2025-01-31"),
    ("Q2 FY26", "10q_Q2FY26", "2025-11-01", "2026-01-31"),
    ("Q3 FY25", "10q_Q3FY26", "2025-02-01", "2025-04-30"),
    ("Q3 FY26", "10q_Q3FY26", "2026-02-01", "2026-04-30"),
]
# Q4 = annual (10-K) - nine months (Q3 10-Q)
DERIVED = [
    ("Q4 FY25", ("10k_FY26", "2024-08-01", "2025-07-31"), ("10q_Q3FY26", "2024-08-01", "2025-04-30")),
    ("Q4 FY26", ("10k_FY26", "2025-08-01", "2026-07-31"), ("10q_Q3FY26", "2025-08-01", "2026-04-30")),
]
ACCESSION = {"10q_Q1FY26": "0000896878-25-000048", "10q_Q2FY26": "0000896878-26-000014",
             "10q_Q3FY26": "0000896878-26-000025", "10k_FY26": "0000896878-26-000037"}
# Hand-checked against the filings' segment notes and income statements on 2026-11-03.
# Set back to "no" if the extraction logic or source filings change.
VERIFIED = "yes"
ORDER = ["Q1 FY25", "Q2 FY25", "Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26", "Q3 FY26", "Q4 FY26"]


def load_facts() -> dict:
    facts = {}
    with open(ROOT / "outputs" / "segment_facts.csv") as f:
        for r in csv.DictReader(f):
            facts[(r["filing"], r["concept"], r["start"], r["end"], r["dims"])] = float(r["value_usd"]) / 1e6
    return facts


# The FY26 10-K tags GBS operating income and costs with a different member name than
# its revenue (GlobalBusinessSolutionsMember vs ...SegmentMember). Same segment; both accepted.
ALIASES = {"GlobalBusinessSolutionsSegmentMember": "GlobalBusinessSolutionsMember"}
ALIAS_USED = set()


def get(facts, filing, start, end, metric):
    concept, dims = METRICS[metric]
    v = facts.get((filing, concept, start, end, dims))
    if v is None:
        alt = dims
        for a, b in ALIASES.items():
            alt = alt.replace(a, b)
        v = facts.get((filing, concept, start, end, alt))
        if v is not None:
            ALIAS_USED.add((filing, metric))
    return v


def build() -> list:
    facts = load_facts()
    rows = []
    for period, filing, start, end in DIRECT:
        for m in METRICS:
            v = get(facts, filing, start, end, m)
            rows.append({"period": period, "metric": m, "value": v, "basis": "reported 3M",
                         "source": f"{filing} {ACCESSION[filing]} {METRICS[m][0]} {start}..{end}"})
    for period, (fa, sa, ea), (fn, sn, en) in DERIVED:
        for m in METRICS:
            a, n = get(facts, fa, sa, ea, m), get(facts, fn, sn, en, m)
            v = None if a is None or n is None else a - n
            note = " (10-K member GlobalBusinessSolutionsMember)" if (fa, m) in ALIAS_USED else ""
            rows.append({"period": period, "metric": m, "value": v, "basis": "derived FY - 9M",
                         "source": f"{fa} {ACCESSION[fa]} {sa}..{ea} minus {fn} {ACCESSION[fn]} {sn}..{en}{note}"})
    rows.sort(key=lambda r: (ORDER.index(r["period"]), list(METRICS).index(r["metric"])))
    return rows


def tie_outs(rows) -> int:
    v = {(r["period"], r["metric"]): r["value"] for r in rows}
    with open(ROOT / "data" / "release_actuals.csv") as f:
        rel = {r["period"]: r for r in csv.DictReader(f)}
    checks = [
        ("segments = total revenue", ["gbs_revenue", "consumer_revenue"], "total_revenue"),
        ("online = QBO + online services", ["qbo_accounting_revenue", "online_services_revenue"], "online_ecosystem_revenue"),
        ("desktop = QBD + services/supplies", ["qb_desktop_accounting_revenue", "desktop_services_supplies_revenue"], "desktop_ecosystem_revenue"),
        ("GBS = online + desktop", ["online_ecosystem_revenue", "desktop_ecosystem_revenue"], "gbs_revenue"),
        ("Consumer = TT + CK + ProTax", ["turbotax_revenue", "credit_karma_revenue", "protax_revenue"], "consumer_revenue"),
        ("segment OI sums", ["gbs_segment_oi", "consumer_segment_oi"], "total_segment_oi"),
        ("unallocated items sum", ["unalloc_share_based_comp", "unalloc_other_corporate", "unalloc_amort_acquired_tech",
                                   "unalloc_amort_other_intangibles", "unalloc_restructuring"], "unalloc_total"),
        ("costs = COR + opex", ["cost_of_revenue_service", "cost_of_revenue_product", "unalloc_amort_acquired_tech",
                                "selling_and_marketing", "research_and_development", "general_and_administrative",
                                "unalloc_amort_other_intangibles", "unalloc_restructuring"], "total_costs_and_expenses"),
    ]
    fails = 0
    for p in ORDER:
        missing = [m for m in METRICS if v[(p, m)] is None]
        if missing:
            print(f"  {p}: MISSING {missing}"); fails += 1; continue
        for name, parts, total in checks:
            d = sum(v[(p, m)] for m in parts) - v[(p, total)]
            if abs(d) > 0.5:
                print(f"  {p}: FAIL {name} (off by {d:+.0f})"); fails += 1
        bridge = v[(p, "total_segment_oi")] - v[(p, "unalloc_total")] - v[(p, "gaap_operating_income")]
        rev_d = v[(p, "total_revenue")] - float(rel[p]["revenue"])
        oi_d = v[(p, "gaap_operating_income")] - float(rel[p]["gaap_operating_income"])
        for name, d in (("segment OI - unallocated = GAAP OI", bridge), ("revenue = release", rev_d), ("GAAP OI = release", oi_d)):
            if abs(d) > 0.5:
                print(f"  {p}: FAIL {name} (off by {d:+.0f})"); fails += 1
        print(f"  {p}: rev {v[(p,'total_revenue')]:>7,.0f}  seg OI {v[(p,'total_segment_oi')]:>7,.0f}"
              f"  - unalloc {v[(p,'unalloc_total')]:>6,.0f}  = GAAP OI {v[(p,'gaap_operating_income')]:>6,.0f}"
              f"  restructuring {v[(p,'unalloc_restructuring')]:>4,.0f}")
    return fails


def main():
    rows = build()
    out = ROOT / "data" / "segments.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["period", "metric", "value_usd_m", "basis", "source", "verified"])
        for r in rows:
            w.writerow([r["period"], r["metric"], "" if r["value"] is None else f"{r['value']:.0f}",
                        r["basis"], r["source"], VERIFIED])
    print(f"wrote {out} ({len(rows)} rows)\nTie-outs:")
    fails = tie_outs(rows)
    print("All tie-outs pass." if not fails else f"{fails} tie-out failure(s).")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
