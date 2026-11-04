"""Check AI variance commentary against the variance table and the permitted sources.

Input: a commentary JSON {"claims": [...], "commentary": "..."} for one period.
Each claim:
  {"metric": "consumer_revenue", "comparison": "yoy", "period": "Q3 FY26",
   "measure": "change_pct" | "change_abs" | "actual" | "base",
   "direction": "up" | "down" | "flat", "value": "+7.5%", "unit": "percent" | "pp" | "bps" | "usd_m" | "usd_per_share",
   "reason": "..." | "", "quote": "..." | "", "quote_source": "mdna/Q3_FY26.txt" | ""}

Every error has a type; the error rate uses figures + directions + causal claims as the
denominator (DECISIONS.md). Run `python3 check.py --selftest` to prove a clean reference
commentary passes and every planted error is caught.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from textnorm import normalize

ROOT = Path(__file__).resolve().parent
TABLE = ROOT / "outputs" / "variance_table.json"

ERROR_TYPES = ["unknown_row", "value", "unit", "direction", "prose_number", "prose_direction",
               "unsupported_reason", "quote_not_found", "quote_wrong_source", "quote_mismatch", "causal_on_plan",
               "missed_material", "profit_margin_wording", "bridge_value", "placeholder"]

UP = r"\b(increase[sd]?|increasing|rose|rise[sn]?|grew|grow(?:s|ing|th)?|up|higher|improve[sd]?|expanded|gain(?:ed|s)?|above|beat)\b"
DOWN = r"\b(decrease[sd]?|decreasing|fell|fall(?:s|ing)?|declined?|declines|declining|down|lower|contract(?:ed|ion)|below|miss(?:ed)?|shr[ai]nk|dropped)\b"
CAUSAL = r"\b(driven by|due to|because|reflect(?:s|ed|ing)?|as a result|owing to|thanks to|on the back of|led by|attributable to|resulting from|caused by|helped by|supported by)\b"
PROFIT = r"\b(profit margin|profitability|profitable)\b"

# Numbers in prose: $12.9B, $368M, 8.1%, -1.0pp, 150 bps, $0.50. Period labels are stripped first.
NUM = re.compile(r"(?P<sign>[+\-−])?\s?(?P<cur>\$)?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?\s?"
                 r"(?P<unit>%|pp\b|percentage points?|bps\b|basis points|B\b|billion|M\b|million)?")
PERIOD_TOKENS = re.compile(r"\b(Q[1-4]\s?FY\s?\d{2,4}|FY\s?\d{2,4}|fiscal (?:year )?\d{4}|\d{4})\b")


# ---------- parsing ----------

def parse_value(text: str):
    """'+$1,234M' -> (1234.0, 'usd_m', 0.5); '+7.5%' -> (7.5, 'percent', 0.05); '-1.0pp' -> (-1.0, 'pp', 0.05).
    The tolerance is half a unit of the last digit shown."""
    t = normalize(text).replace("−", "-").strip()
    m = NUM.search(t)
    if not m:
        return None
    sign = -1 if (m.group("sign") in ("-", "−") or t.startswith("(")) else 1
    num = float(m.group("num").replace(",", "") + (m.group("dec") or ""))
    decimals = len(m.group("dec")) - 1 if m.group("dec") else 0
    tol = 0.5 * 10 ** -decimals
    u = (m.group("unit") or "").lower()
    if u in ("%",):
        unit = "percent"
    elif u in ("pp", "percentage point", "percentage points"):
        unit = "pp"
    elif u in ("bps", "basis points"):
        unit = "bps"
    elif u in ("b", "billion"):
        unit, num, tol = "usd_m", num * 1000, tol * 1000
    elif u in ("m", "million"):
        unit = "usd_m"
    elif m.group("cur"):
        unit = "usd"  # bare dollars: EPS
    else:
        unit = "number"
    return sign * num, unit, tol


def prose_numbers(prose: str):
    clean = PERIOD_TOKENS.sub(" ", normalize(prose))
    out = []
    for m in NUM.finditer(clean):
        if not (m.group("cur") or m.group("unit")):
            continue  # bare integers (e.g. "two segments") are not figures
        v = parse_value(m.group(0))
        if v:
            out.append((m.start(), m.end(), m.group(0).strip(), v))
    return clean, out


def sentences(text: str):
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", normalize(text)) if s]


# ---------- checker ----------

class Checker:
    def __init__(self, table: dict, root: Path = ROOT):
        self.rows = {r["id"]: r for r in table["rows"]}
        self.bridges = {b["period"]: b for b in table.get("bridges", [])}
        self.root = root
        self._src = {}

    def source(self, rel: str) -> str:
        if rel not in self._src:
            p = self.root / "data" / rel
            self._src[rel] = normalize(p.read_text(encoding="utf-8")) if p.exists() else ""
        return self._src[rel]

    def permitted_source(self, row) -> str:
        src = row.get("reason_source")
        return src.split(" ")[0] if src else None

    def expected(self, row, measure):
        return {"change_pct": row["var_pct"], "change_abs": row["var_abs"],
                "actual": row["actual"], "base": row["base"]}.get(measure)

    def check(self, commentary: dict, period: str, required_comparisons=None) -> dict:
        errors, n_fig, n_dir, n_causal = [], 0, 0, 0
        claims = commentary.get("claims", [])
        prose = commentary.get("commentary", "")

        def err(kind, detail, claim=None):
            errors.append({"type": kind, "detail": detail,
                           "claim": None if claim is None else claim.get("_i")})

        matched_ids = set()
        for i, c in enumerate(claims):
            c["_i"] = i
            if c.get("comparison") == "bridge":
                n_fig += 1
                self._check_bridge(c, period, err)
                continue
            rid = f"{c.get('comparison')}|{c.get('period')}|{c.get('metric')}"
            row = self.rows.get(rid)
            if row is None:
                err("unknown_row", f"no variance row {rid}", c); continue
            matched_ids.add(rid)
            # value and unit
            n_fig += 1
            pv = parse_value(str(c.get("value", "")))
            measure = c.get("measure", "change_pct")
            exp = self.expected(row, measure)
            if pv is None or exp is None:
                err("value", f"{rid}: cannot read value {c.get('value')!r} / measure {measure}", c)
            else:
                val, unit, tol = pv
                if row["unit"] == "pct_of_revenue" and measure == "change_abs":
                    if unit == "percent":
                        err("unit", f"{rid}: margin change given in % ({c['value']}), must be pp", c)
                    elif unit == "bps":
                        val, tol = val / 100, tol / 100
                    elif unit != "pp":
                        err("unit", f"{rid}: margin change unit {unit}", c)
                elif measure == "change_pct" and unit != "percent":
                    err("unit", f"{rid}: % change given as {unit} ({c['value']})", c)
                elif unit == "pp" and row["unit"] != "pct_of_revenue":
                    err("unit", f"{rid}: pp used for a non-margin line ({c['value']})", c)
                if measure in ("change_pct", "change_abs") and val != 0 and exp != 0:
                    # sign in the value string is optional; direction is checked separately
                    val = abs(val) * (1 if exp > 0 else -1) if not re.match(r"\s*[+\-−(]", str(c["value"])) else val
                if abs(val - exp) > tol + 1e-9:
                    err("value", f"{rid}: said {c['value']} ({measure}), table {exp:.4g}", c)
            # direction
            n_dir += 1
            if c.get("direction") != row["direction"]:
                err("direction", f"{rid}: said {c.get('direction')}, table {row['direction']}", c)
            if row["comparison"] in ("plan_vs_actual", "budget_vs_actual", "reforecast_vs_actual") and c.get("position") \
                    and c["position"] != row.get("position"):
                err("direction", f"{rid}: position {c['position']}, table {row.get('position')}", c)
            # reasons
            self._check_reason(c, row, err)
            if c.get("reason"):
                n_causal += 1

        # prose
        n_causal += self._check_prose(claims, prose, err)

        # coverage
        required = [r for r in self.rows.values() if r["period"] == period and r.get("required")
                    and (required_comparisons is None or r["comparison"] in required_comparisons)]
        missed = [r["id"] for r in required if r["id"] not in matched_ids]
        for rid in missed:
            err("missed_material", rid)
        recall = 1 - len(missed) / len(required) if required else None
        valid = [c for c in claims if c.get("comparison") == "bridge" or
                 f"{c.get('comparison')}|{c.get('period')}|{c.get('metric')}" in self.rows]
        precision = len(valid) / len(claims) if claims else None

        denom = n_fig + n_dir + n_causal
        counted = [e for e in errors if e["type"] != "missed_material"]
        by_type = {t: sum(e["type"] == t for e in errors) for t in ERROR_TYPES}
        return {"period": period, "errors": errors, "by_type": {k: v for k, v in by_type.items() if v},
                "denominator": {"figures": n_fig, "directions": n_dir, "causal_claims": n_causal, "total": denom},
                "error_rate": len(counted) / denom if denom else None,
                "coverage": {"required": len(required), "missed": len(missed), "recall": recall, "precision": precision}}

    def _check_bridge(self, c, period, err):
        b = self.bridges.get(period)
        d = None if b is None else next((x for x in b["drivers"] if x["driver"] == c.get("metric")), None)
        if d is None:
            err("unknown_row", f"no bridge driver {c.get('metric')} for {period}", c); return
        pv = parse_value(str(c.get("value", "")))
        if pv is None or abs(pv[0] - d["value"]) > pv[2] + 1e-9:
            err("bridge_value", f"bridge {c.get('metric')}: said {c.get('value')}, table {d['value']:+.1f}", c)

    def _check_reason(self, c, row, err):
        reason, quote = (c.get("reason") or "").strip(), (c.get("quote") or "").strip()
        if row["comparison"] != "yoy":
            if reason or quote:
                err("causal_on_plan", f"{row['id']}: reason given for a plan comparison", c)
            return
        if reason and not quote:
            err("unsupported_reason", f"{row['id']}: reason without a quote", c); return
        if not quote:
            return
        permitted = self.permitted_source(row)
        q = normalize(quote)
        if len(q.split()) < 5:
            err("unsupported_reason", f"{row['id']}: quote too short to support a reason ({quote!r})", c); return
        if q in self.source(permitted):
            # Partial support test: a percentage in the quote must match the claim's change.
            # Catches e.g. a year-to-date sentence quoted for a quarterly claim. It cannot prove
            # that the words support the reason (DECISIONS.md 5).
            pcts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s?%", q)]
            if pcts and row["var_pct"] is not None and not any(
                    abs(abs(row["var_pct"]) - p) <= 0.5 * 10 ** -(len(str(p).split(".")[1]) if "." in str(p) and not str(p).endswith(".0") else 0) + 1e-9
                    for p in pcts):
                err("quote_mismatch", f"{row['id']}: quote says {pcts}%, table {row['var_pct']:+.1f}% "
                    "(likely another period, e.g. year-to-date)", c)
            return
        # found somewhere else we hold? then it is the wrong source, not an invention
        others = [str(p.relative_to(self.root / "data")) for p in (self.root / "data").glob("*/*.txt")]
        if any(q in self.source(o) for o in others if o != permitted):
            err("quote_wrong_source", f"{row['id']}: quote is not from the permitted source {permitted}", c)
        else:
            err("quote_not_found", f"{row['id']}: quote not found in {permitted}", c)

    def _check_prose(self, claims, prose, err) -> int:
        clean, nums = prose_numbers(prose)
        claim_vals = []
        for c in claims:
            pv = parse_value(str(c.get("value", "")))
            if pv:
                claim_vals.append((c, pv))
        # every figure in the prose must be one of the claims' figures
        last_end = 0
        for start, end, raw, (val, unit, tol) in nums:
            hits = [c for c, (cv, cu, ct) in claim_vals
                    if (cu == unit or {cu, unit} <= {"usd", "usd_per_share"}) and abs(abs(cv) - abs(val)) <= max(tol, ct) + 1e-9]
            if not hits:
                err("prose_number", f"figure {raw!r} in prose matches no claim")
            else:
                # direction words between the previous figure and this one
                window = clean[last_end:start]
                said_up, said_down = re.search(UP, window, re.I), re.search(DOWN, window, re.I)
                want = {h.get("direction") for h in hits}
                if said_up and not said_down and want == {"down"}:
                    err("prose_direction", f"'{said_up.group(0)}' before {raw!r}, claim is down")
                if said_down and not said_up and want == {"up"}:
                    err("prose_direction", f"'{said_down.group(0)}' before {raw!r}, claim is up")
            last_end = end
        # causal language and profit-margin wording, per sentence
        causal_count = 0
        for s in sentences(prose):
            _, snums = prose_numbers(s)
            linked = [c for c, (cv, cu, ct) in claim_vals
                      if any(abs(abs(cv) - abs(v)) <= max(t, ct) + 1e-9 and (cu == u or {cu, u} <= {"usd", "usd_per_share"})
                             for _, _, _, (v, u, t) in snums)]
            if re.search(CAUSAL, s, re.I):
                if not any(c.get("reason") for c in linked):
                    causal_count += 1  # a reason in prose not carried by any claim
                plan = [c for c in linked if c.get("comparison") != "yoy" and c.get("comparison") != "bridge"]
                yoy = [c for c in linked if c.get("comparison") == "yoy"]
                if plan and not yoy:
                    err("causal_on_plan", f"causal wording in a plan sentence: {s[:90]!r}")
                elif yoy and not any(c.get("quote") for c in yoy):
                    err("unsupported_reason", f"causal wording with no quoted claim: {s[:90]!r}")
            if re.search(PROFIT, s, re.I) and re.search(r"segment", s, re.I):
                err("profit_margin_wording", f"segment result called profit/profitability: {s[:90]!r}")
        return causal_count


# ---------- self-test: reference commentary and planted errors ----------

def reference_commentary(ck: Checker, period: str, with_reason=True) -> dict:
    """A correct commentary built from the table: one claim and sentence per required item."""
    claims, text = [], []
    for r in ck.rows.values():
        if r["period"] != period or not r.get("required"):
            continue
        if r["unit"] == "pct_of_revenue":
            value, measure, unit = r["display"]["var_abs"], "change_abs", "pp"
        else:
            value, measure, unit = r["display"]["var_pct"], "change_pct", "percent"
        c = {"metric": r["metric"], "comparison": r["comparison"], "period": period, "measure": measure,
             "direction": r["direction"], "value": value, "unit": unit, "reason": "", "quote": "", "quote_source": ""}
        if r["comparison"] == "plan_vs_actual":
            c["position"] = r["position"]
        word = {"up": "increased", "down": "decreased", "flat": "was unchanged"}[r["direction"]]
        text.append(f"{r['metric'].replace('_', ' ').capitalize()} {word} {value.lstrip('+-')} versus {r['base_label']}.")
        claims.append(c)
    if with_reason:
        # attach one real quote from the permitted source, if one mentions the line
        for c, sent_i in zip(claims, range(len(text))):
            row = ck.rows[f"{c['comparison']}|{period}|{c['metric']}"]
            src = ck.permitted_source(row)
            if row["comparison"] != "yoy" or not src or c["metric"] != "consumer_revenue":
                continue
            want = round(abs(row["var_pct"]))
            m = re.search(rf"Consumer segment revenue increased {want}% due to [^.]+\.", ck.source(src))
            if m:
                c["reason"], c["quote"], c["quote_source"] = "assisted tax growth", m.group(0), src
                text[sent_i] = text[sent_i][:-1] + ", driven by growth in assisted tax."
                break
    return {"claims": claims, "commentary": " ".join(text)}


def plant(ref: dict, kind: str, ck: Checker, period: str) -> dict:
    import copy
    c = copy.deepcopy(ref)
    claims = c["claims"]
    yoy = [x for x in claims if x["comparison"] == "yoy"]
    if kind == "value":
        x = next(x for x in claims if x["unit"] == "percent")
        old = x["value"]; v = parse_value(old)[0]
        x["value"] = f"{v + 2:+.1f}%"; c["commentary"] = c["commentary"].replace(old.lstrip("+-"), x["value"].lstrip("+-"), 1)
    elif kind == "direction":
        x = claims[0]; x["direction"] = "down" if x["direction"] == "up" else "up"
    elif kind == "prose_direction":
        x = next(x for x in claims if x["direction"] == "up")
        i = c["commentary"].find(" increased ")
        c["commentary"] = c["commentary"][:i] + " decreased " + c["commentary"][i + 11:]
    elif kind == "unit":
        x = next(x for x in claims if x["unit"] == "pp")
        x["value"] = x["value"].replace("pp", "%"); x["unit"] = "percent"
        c["commentary"] = c["commentary"].replace(x["value"].lstrip("+-").replace("%", "pp"), x["value"].lstrip("+-"), 1)
    elif kind == "invented_reason":
        x = next(x for x in yoy if not x["quote"])
        x["reason"] = "strong demand"; x["quote"] = "demand for our products was exceptionally strong this quarter"
    elif kind == "reason_no_quote":
        x = next(x for x in yoy if not x["quote"]); x["reason"] = "pricing"
    elif kind == "dropped_item":
        dropped = claims.pop(0)
        c["commentary"] = " ".join(s for s in sentences(c["commentary"])
                                   if dropped["value"].lstrip("+-") not in s)
    elif kind == "causal_on_plan":
        x = next(x for x in claims if x["comparison"] == "plan_vs_actual")
        x["reason"] = "lower spending"
    elif kind == "profit_margin":
        c["commentary"] += " The GBS segment profit margin remains very high."
    elif kind == "ytd_quote":
        x = next(x for x in claims if x["quote"])
        src = ck.source(x["quote_source"])
        other = [m.group(0) for m in re.finditer(r"Consumer segment revenue increased \d+% due to [^.]+\.", src)
                 if m.group(0) != x["quote"]]
        x["quote"] = other[0]
    elif kind == "wrong_source":
        # quote a 10-Q MD&A sentence for a Q4 claim, whose permitted source is the release
        x = next(x for x in yoy)
        x["reason"], x["quote"] = "growth", re.search(r"Our Global Business Solutions segment revenue increased [^.]+\.",
                                                     ck.source("mdna/Q3_FY26.txt")).group(0)
    elif kind == "bridge_value":
        b = ck.bridges[period]["drivers"][0]
        claims.append({"metric": b["driver"], "comparison": "bridge", "period": period, "measure": "change_abs",
                       "direction": "up", "value": f"{b['value'] + 40:+.0f}", "unit": "usd_m"})
    elif kind == "extra_number":
        c["commentary"] += " Revenue reached $99,999M."
    return c


PLANTS = {"Q2 FY26": ["value", "direction", "prose_direction", "unit", "invented_reason", "reason_no_quote",
                      "ytd_quote", "dropped_item", "causal_on_plan", "profit_margin", "extra_number", "bridge_value"],
          "Q4 FY26": ["wrong_source", "bridge_value"]}


# The error type each planted error must produce
EXPECTED = {"value": "value", "direction": "direction", "prose_direction": "prose_direction", "unit": "unit",
            "invented_reason": "quote_not_found", "reason_no_quote": "unsupported_reason",
            "ytd_quote": "quote_mismatch", "dropped_item": "missed_material", "causal_on_plan": "causal_on_plan",
            "profit_margin": "profit_margin_wording", "extra_number": "prose_number", "bridge_value": "bridge_value",
            "wrong_source": "quote_wrong_source"}


def selftest(ck: Checker) -> int:
    failures = 0
    for period in ["Q1 FY25", "Q2 FY25", "Q3 FY25", "Q4 FY25", "Q1 FY26", "Q2 FY26", "Q3 FY26", "Q4 FY26"]:
        ref = reference_commentary(ck, period)
        res = ck.check(ref, period)
        ok = not res["errors"]
        failures += not ok
        print(f"  reference {period}: {'clean' if ok else 'FALSE POSITIVES'}  "
              f"({res['coverage']['required']} required items, denominator {res['denominator']['total']})")
        for e in res["errors"]:
            print(f"      {e['type']}: {e['detail']}")
    print()
    for period, kinds in PLANTS.items():
        ref = reference_commentary(ck, period)
        for kind in kinds:
            res = ck.check(plant(ref, kind, ck, period), period)
            caught = sorted(res["by_type"])
            ok = EXPECTED[kind] in caught
            failures += not ok
            print(f"  planted {period} {kind:<16} {'caught' if ok else 'MISSED'} (expected {EXPECTED[kind]}; got {', '.join(caught) or '-'})")
    print(f"\n{'Self-test passed.' if not failures else f'{failures} self-test failure(s).'}")
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="commentary JSON files")
    ap.add_argument("--period", help="period the commentary covers, e.g. 'Q3 FY26'")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    ck = Checker(json.loads(TABLE.read_text()))
    if a.selftest:
        sys.exit(1 if selftest(ck) else 0)
    for f in a.files:
        com = json.loads(Path(f).read_text())
        period = a.period or com.get("period")
        print(json.dumps(ck.check(com, period), indent=1))


if __name__ == "__main__":
    main()
