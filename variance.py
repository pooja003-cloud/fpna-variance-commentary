"""Compute every variance the commentary may mention. The model never calculates.

Comparisons
- plan_vs_actual   quarterly GAAP totals vs. guidance midpoint, plus position in range
- budget/reforecast FY26 annual: budget vs. actual, reforecast vs. actual, budget vs. reforecast
- yoy              FY26 quarters (and live Q1 FY27 at total level) vs. recast prior year
- bridge           YoY GAAP operating income: per-segment revenue and margin effects plus
                   each unallocated item, summing exactly to the change (DECISIONS.md 10)

Output: outputs/variance_table.json (+ .csv). Every row has an id the prompts and
checker refer to, the raw numbers, display strings at the stated rounding, direction,
materiality and the permitted reason source.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA, OUT = ROOT / "data", ROOT / "outputs"
QUARTERS = ["Q1 FY25", "Q2 FY25", "Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26", "Q3 FY26", "Q4 FY26", "Q1 FY27"]

# Materiality (DECISIONS.md 9). Dollar rows need both tests; EPS and margin rows use their own.
MATERIALITY = {"pct": 5.0, "usd_m": 50.0, "eps_usd": 0.05, "margin_pp": 1.0}

# Levels whose material items the commentary must cover (DECISIONS.md 9)
REQUIRED_LEVELS = {"total", "segment", "unallocated"}

# Lines where an increase is unfavourable
COST_PREFIXES = ("unalloc_", "cost_of_revenue", "selling_", "research_", "general_",
                 "total_costs", "gbs_segment_costs", "consumer_segment_costs")

LEVEL = {
    "total_revenue": "total", "gaap_operating_income": "total", "gaap_diluted_eps": "total",
    "gaap_operating_margin": "total",
    "gbs_revenue": "segment", "consumer_revenue": "segment",
    "gbs_segment_oi": "segment", "consumer_segment_oi": "segment", "total_segment_oi": "segment",
    "gbs_segment_margin": "segment", "consumer_segment_margin": "segment",
    "gbs_segment_costs": "segment", "consumer_segment_costs": "segment",
}


def level(metric: str) -> str:
    if metric in LEVEL:
        return LEVEL[metric]
    if metric.startswith("unalloc_"):
        return "unallocated"
    if metric.endswith("_revenue"):
        return "product_line"
    return "expense_line"


def read_csv(name):
    with open(DATA / name) as f:
        return list(csv.DictReader(f))


def unit_of(metric: str) -> str:
    if metric.endswith("_eps"):
        return "usd_per_share"
    if metric.endswith("_margin"):
        return "pct_of_revenue"
    return "usd_m"


def fmt_level(v: float, unit: str) -> str:
    if unit == "usd_per_share":
        return f"${v:,.2f}"
    if unit == "pct_of_revenue":
        return f"{v:.1f}%"
    return f"${v:,.0f}M"


def fmt_change(v: float, unit: str) -> str:
    sign = "+" if v > 0 else "-" if v < 0 else ""
    a = abs(v)
    if unit == "usd_per_share":
        return f"{sign}${a:,.2f}"
    if unit == "pct_of_revenue":
        return f"{sign}{a:.1f}pp"
    return f"{sign}${a:,.0f}M"


def fmt_pct(v):
    return None if v is None else f"{'+' if v > 0 else '-' if v < 0 else ''}{abs(v):.1f}%"


def direction(v: float) -> str:
    return "up" if v > 0 else "down" if v < 0 else "flat"


def is_material(unit: str, var_abs: float, var_pct) -> bool:
    if unit == "pct_of_revenue":
        return abs(var_abs) >= MATERIALITY["margin_pp"]
    if unit == "usd_per_share":
        return var_pct is not None and abs(var_pct) >= MATERIALITY["pct"] and abs(var_abs) >= MATERIALITY["eps_usd"]
    return var_pct is not None and abs(var_pct) >= MATERIALITY["pct"] and abs(var_abs) >= MATERIALITY["usd_m"]


def row(rid, period, comparison, metric, base, actual, base_label, reason_source, **extra):
    unit = unit_of(metric)
    var_abs = actual - base
    var_pct = None if unit == "pct_of_revenue" or base == 0 else var_abs / abs(base) * 100
    cost = metric.startswith(COST_PREFIXES)
    r = {
        "id": rid, "period": period, "comparison": comparison, "metric": metric,
        "level": level(metric), "unit": unit, "base_label": base_label,
        "base": round(base, 6), "actual": round(actual, 6),
        "var_abs": round(var_abs, 6), "var_pct": None if var_pct is None else round(var_pct, 6),
        "direction": direction(var_abs),
        "favourable": None if var_abs == 0 else (var_abs < 0) if cost else (var_abs > 0),
        "material": is_material(unit, var_abs, var_pct),
        "required": is_material(unit, var_abs, var_pct) and level(metric) in REQUIRED_LEVELS,
        "display": {"base": fmt_level(base, unit), "actual": fmt_level(actual, unit),
                    "var_abs": fmt_change(var_abs, unit), "var_pct": fmt_pct(var_pct)},
        "reason_source": reason_source,
    }
    r.update(extra)
    return r


def reason_source_yoy(period: str) -> str:
    q, fy = period.split()
    if fy == "FY27":
        return "releases/Q1_FY27.txt (switch to mdna/Q1_FY27.txt when the 10-Q is filed)"
    return f"releases/{q}_{fy}.txt" if q == "Q4" else f"mdna/{q}_{fy}.txt"


def prior(period: str) -> str:
    q, fy = period.split()
    return f"{q} FY{int(fy[2:]) - 1}"


def main() -> None:
    guidance = read_csv("guidance.csv")
    actuals = {r["period"]: r for r in read_csv("release_actuals.csv")}
    seg = {}
    for r in read_csv("segments.csv"):
        seg.setdefault(r["period"], {})[r["metric"]] = float(r["value_usd_m"])
    # derived margins (operating income / revenue, %)
    for p, d in seg.items():
        d["gbs_segment_margin"] = d["gbs_segment_oi"] / d["gbs_revenue"] * 100
        d["consumer_segment_margin"] = d["consumer_segment_oi"] / d["consumer_revenue"] * 100
        d["gaap_operating_margin"] = d["gaap_operating_income"] / d["total_revenue"] * 100
    rows = []

    # 1. Plan vs. actual, quarterly
    for g in guidance:
        if g["scope"] != "quarter":
            continue
        p, m = g["target_period"], g["metric"]
        if p not in actuals:
            continue  # live quarter before results
        lo, hi = float(g["low"]), float(g["high"])
        mid, act = (lo + hi) / 2, float(actuals[p][m])
        m = {"revenue": "total_revenue"}.get(m, m)
        pos = "above" if act > hi else "below" if act < lo else "within"
        r = row(f"plan_vs_actual|{p}|{m}", p, "plan_vs_actual", m, mid, act, "guidance midpoint", None,
                guidance_low=lo, guidance_high=hi, position=pos,
                distance_from_range=round(act - hi if pos == "above" else act - lo if pos == "below" else 0, 6),
                source=g["accession"])
        r["display"].update(low=fmt_level(lo, r["unit"]), high=fmt_level(hi, r["unit"]))
        rows.append(r)

    # 2. FY26 budget / reforecast / actual
    fy25 = {m: sum(seg[q][m] for q in QUARTERS[:4]) for m in ("gbs_revenue", "consumer_revenue")}
    fy26_seg = {m: sum(seg[q][m] for q in QUARTERS[4:8]) for m in ("gbs_revenue", "consumer_revenue")}
    plans = {}
    conversions = []
    for g in guidance:
        if g["scope"] != "annual":
            continue
        lo, hi = float(g["low"]), float(g["high"])
        m = g["metric"]
        if m.endswith("_growth"):  # growth rate -> dollars on recast FY25 base
            seg_metric = m.replace("_growth", "")
            base = fy25[seg_metric]
            lo, hi = base * (1 + lo / 100), base * (1 + hi / 100)
            conversions.append(f"{g['plan_type']} {seg_metric}: {g['low']}-{g['high']}% x recast FY25 "
                               f"${base:,.0f}M = ${lo:,.0f}-{hi:,.0f}M")
            m = seg_metric
        plans[(g["plan_type"], m)] = (lo, hi, g["accession"])
    fy_actual = {"total_revenue": float(actuals["FY26"]["revenue"]),
                 "gaap_operating_income": float(actuals["FY26"]["gaap_operating_income"]),
                 "gaap_diluted_eps": float(actuals["FY26"]["gaap_diluted_eps"]), **fy26_seg}
    name = {"revenue": "total_revenue"}
    for (ptype, m), (lo, hi, acc) in plans.items():
        mm = name.get(m, m)
        mid = (lo + hi) / 2
        act = fy_actual[mm]
        pos = "above" if act > hi else "below" if act < lo else "within"
        r = row(f"{ptype}_vs_actual|FY26|{mm}", "FY26", f"{ptype}_vs_actual", mm, mid, act, f"{ptype} midpoint", None,
                guidance_low=lo, guidance_high=hi, position=pos, source=acc)
        r["display"].update(low=fmt_level(lo, r["unit"]), high=fmt_level(hi, r["unit"]))
        rows.append(r)
    for (ptype, m), (lo, hi, acc) in plans.items():
        if ptype != "budget" or ("reforecast", m) not in plans:
            continue
        mm = name.get(m, m)
        rlo, rhi, racc = plans[("reforecast", m)]
        rows.append(row(f"budget_vs_reforecast|FY26|{mm}", "FY26", "budget_vs_reforecast", mm,
                        (lo + hi) / 2, (rlo + rhi) / 2, "budget midpoint", None, source=f"{acc} -> {racc}"))

    # 3. YoY and 4. bridge
    yoy_metrics = [m for m in next(iter(seg.values())) if m != "total_costs_and_expenses"]
    bridges = []
    for p in QUARTERS[4:]:
        pp = prior(p)
        if p in seg and pp in seg:
            for m in yoy_metrics:
                rows.append(row(f"yoy|{p}|{m}", p, "yoy", m, seg[pp][m], seg[p][m], f"{pp} (recast)",
                                reason_source_yoy(p)))
            bridges.append(bridge(p, pp, seg))
        if p in actuals and pp in actuals:
            # EPS YoY (all quarters), and totals for the live quarter if segments are not yet in
            todo = ["gaap_diluted_eps"] if p in seg else ["revenue", "gaap_operating_income", "gaap_diluted_eps"]
            for m in todo:
                mm = name.get(m, m)
                rows.append(row(f"yoy|{p}|{mm}", p, "yoy", mm, float(actuals[pp][m]), float(actuals[p][m]),
                                f"{pp}", reason_source_yoy(p)))

    OUT.mkdir(exist_ok=True)
    table = {"materiality": MATERIALITY, "conversions": conversions, "rows": rows, "bridges": bridges}
    (OUT / "variance_table.json").write_text(json.dumps(table, indent=1))
    with open(OUT / "variance_table.csv", "w", newline="") as f:
        cols = ["id", "period", "comparison", "metric", "level", "unit", "base", "actual", "var_abs",
                "var_pct", "direction", "favourable", "material", "required", "position", "reason_source"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    report(rows, bridges, conversions)


def bridge(p, pp, seg) -> dict:
    """GAAP operating income change = sum of segment revenue and margin effects
    - change in each unallocated item. Exact: no residual."""
    cur, pri = seg[p], seg[pp]
    drivers = []
    for s in ("gbs", "consumer"):
        r0, r1 = pri[f"{s}_revenue"], cur[f"{s}_revenue"]
        m0, m1 = pri[f"{s}_segment_oi"] / r0, cur[f"{s}_segment_oi"] / r1
        drivers.append({"driver": f"{s}_revenue_effect", "value": (r1 - r0) * m0})
        drivers.append({"driver": f"{s}_margin_effect", "value": (m1 - m0) * r1})
    for item in ("unalloc_share_based_comp", "unalloc_other_corporate", "unalloc_amort_acquired_tech",
                 "unalloc_amort_other_intangibles", "unalloc_restructuring"):
        drivers.append({"driver": item, "value": -(cur[item] - pri[item])})
    total = cur["gaap_operating_income"] - pri["gaap_operating_income"]
    residual = sum(d["value"] for d in drivers) - total
    if abs(residual) > 1e-6:
        sys.exit(f"Bridge for {p} does not sum: residual {residual}")
    for d in drivers:
        d["display"] = fmt_change(d["value"], "usd_m")
        d["value"] = round(d["value"], 6)
    return {"id": f"bridge|{p}", "period": p, "base_period": pp,
            "start": pri["gaap_operating_income"], "end": cur["gaap_operating_income"],
            "total_change": total, "drivers": drivers, "residual": round(residual, 9)}


def report(rows, bridges, conversions):
    print("Conversions (growth guidance -> dollars):")
    for c in conversions:
        print("  " + c)
    print("\nPlan vs. actual (quarterly):")
    for r in rows:
        if r["comparison"] == "plan_vs_actual":
            print(f"  {r['period']:<8} {r['metric']:<22} {r['display']['var_abs']:>9} {r['display']['var_pct']:>7}"
                  f"  {r['position']:<6} {'MATERIAL' if r['material'] else ''}")
    print("\nFY26 budget / reforecast:")
    for r in rows:
        if r["period"] == "FY26":
            print(f"  {r['comparison']:<22} {r['metric']:<22} {r['display']['base']:>10} -> {r['display']['actual']:>10}"
                  f"  {r['display']['var_abs']:>8} {r['display']['var_pct'] or '':>7} {r.get('position', ''):<6}"
                  f" {'MATERIAL' if r['material'] else ''}")
    print("\nYoY bridges (GAAP operating income, $M):")
    for b in bridges:
        parts = "  ".join(f"{d['driver'].replace('unalloc_', 'u.')}={d['value']:+.0f}" for d in b["drivers"])
        print(f"  {b['period']}: {b['start']:.0f} -> {b['end']:.0f} ({b['total_change']:+.0f}); residual {b['residual']}")
        print(f"     {parts}")
    print("\nMaterial items by comparison and level:")
    counts = {}
    for r in rows:
        k = (r["comparison"], r["level"])
        n, t = counts.get(k, (0, 0))
        counts[k] = (n + r["material"], t + 1)
    for (c, l), (n, t) in sorted(counts.items()):
        print(f"  {c:<22} {l:<13} {n:>3} of {t:>3}")
    by_period = {}
    for r in rows:
        if r["comparison"] == "yoy":
            by_period.setdefault(r["period"], [0, 0])
            by_period[r["period"]][0] += r["material"]
            by_period[r["period"]][1] += 1
    print("\nYoY material items per period:", {p: f"{a}/{b}" for p, (a, b) in by_period.items()})


if __name__ == "__main__":
    main()
