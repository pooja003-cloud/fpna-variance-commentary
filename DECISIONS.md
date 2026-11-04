# Decisions

Each decision states what was chosen, why, and what it costs.

## 1. GAAP only
Intuit guides GAAP revenue, operating income and EPS in dollars every quarter. Non-GAAP
breaks in FY27 (share-based compensation no longer excluded, not recast). GAAP totals are
unaffected by any segment change, so plan vs. actual is consistent across all 9 periods.

## 2. Plan = guidance midpoint; position vs. range reported separately
Variance = actual - midpoint, in $ and %. Position = below / within / above the range.

## 3. Budget vs. reforecast (FY26)
- Budget = original FY26 guidance, Q4 FY25 release (0000896878-25-000031, 2025-08-21).
- Reforecast = Q3 FY26 release (0000896878-26-000024, 2026-05-21). Q1 and Q2 FY26 releases
  reiterated guidance unchanged, so this is the only revision.
- The reforecast raised revenue but lowered GAAP operating income ($5,782-5,859M to
  $5,705-5,725M) because it included $300M of restructuring charges. Actual restructuring
  was $293M.
- Segment guidance is growth rates only (never dollars). Converted to dollars as
  recast FY25 segment revenue x (1 + growth). Reforecast segment rates are "approximately"
  a single number, so converted dollars carry about +/-0.5% uncertainty (about +/-$60M for
  GBS). Each conversion is logged in guidance.csv (`converted` column).

## 4. Guidance format varies; dollars taken from Table E
Some releases state revenue guidance as growth in the text but give dollars in Table E
(Q1 FY25, Q1 FY26, Q3 FY26, Q4 FY26). Q4 FY25 and Q4 FY26 GAAP operating income guidance
appears only in Table E. Every row in guidance.csv names its table.

## 5. Reason sources by comparison type
| Comparison | Allowed source | Checker rule |
|---|---|---|
| YoY, Q1-Q3 | 10-Q MD&A, exact quote | Reason without quote flagged |
| YoY, Q4 | Q4 release text, exact quote | 10-K full-year text not accepted |
| Plan vs. actual | None | Any causal language flagged |
| Live, day 1 | Release text only | Switch to 10-Q when filed; source logged |

MD&A explains year-over-year changes, never variance against guidance, so a reason for a
beat has no permitted source.

Extraction (extract_text.py): 10-Q MD&A = text from "ITEM 2 - MANAGEMENT'S DISCUSSION" to
"ITEM 3 - QUANTITATIVE"; release narrative = text before "Forward-looking Guidance". Both
cleaned with textnorm.normalize (shared with the checker), and running page headers and
footers removed so sentences crossing a page break can be quoted whole. Releases give
reasons for some lines only (e.g. Q4 FY26: QuickBooks Online "driven by higher effective
prices, customer growth, and mix shift"; none for TurboTax), so a missing reason is a
valid answer the checker must accept.

## 6. Framing: where AI commentary breaks and what code catches
Errors reported by type, not as one falling rate: value, direction, unit, unsupported
reason, missed material item, wrong placeholder. v3 removes value and direction errors by
construction; that is stated, not claimed as a prompting win.

## 7. Tuning and held-out periods for v2
v2 rules are written only from v1 errors on the tuning set; v2 and v3 are scored on the
held-out set.
- Tuning: Q1-Q4 FY25 and Q1-Q2 FY26 (plan vs. actual, plus YoY with 10-Q MD&A reasons).
- Held out: Q3 FY26 (10-Q MD&A), Q4 FY26 (release text only), Q1 FY27 (live).
Why not FY25 only: FY25 quarters have plan vs. actual but no YoY (no recast FY24
quarters), and plan vs. actual allows no reasons, so v2 would never see a reason error.
Cost: the held-out set is 3 periods; results are reported as counts, not trends.

## 8. Every actual beat guidance (finding, affects design)
In all 8 historical quarters, revenue, GAAP operating income and GAAP EPS were above the
top of the range (24 of 24). Position is therefore uninformative as a test; the test is
whether the model reports the right size and stays silent on reasons. Q3 FY26 revenue
($8,558M vs. top $8,553M) is the one near-miss, a useful edge case for "within" vs. "above".

## 9. Materiality and required coverage (confirmed 2026-11-03)
A variance is material when:
- dollar lines: |var %| >= 5% AND |var $| >= $50M (5% is the Acer threshold);
- EPS: |var %| >= 5% AND |var| >= $0.05;
- margins: |change| >= 1.0 percentage point (">=", so Q3 FY26 GAAP operating margin,
  down exactly 1.0pp, is material - a deliberate edge case for the checker).
Coverage is required only at the levels an FP&A reader expects: totals, segments
(revenue, segment operating income, segment margin, segment costs) and unallocated items
(the bridge drivers). Product lines and opex lines are optional: checked for accuracy if
mentioned, not counted as missed if left out. Result: 11-14 required YoY items per quarter,
12 of 24 plan-vs-actual items (operating income 5, EPS 7, revenue 0).

Sensitivity (required YoY items per quarter Q1-Q4 FY26 / plan-vs-actual of 24):
| Setting | YoY | PvA |
|---|---|---|
| 5%, $50M, EPS $0.05, 1.0pp (chosen) | 13, 14, 14, 11 | 12 |
| $25M floor | 14, 14, 14, 12 | 13 |
| $100M floor | 13, 13, 14, 10 | 11 |
| 3% | 13, 14, 14, 11 | 16 |
| 10% | 13, 13, 10, 11 | 10 |
| margin 0.5pp | 14, 15, 14, 11 | 12 |
| margin 2.0pp | 13, 13, 12, 11 | 12 |
Results barely depend on the cut-offs. 10% is rejected because it drops Q3 FY26 GAAP
operating income (+8.1%) and Consumer revenue (+7.5%); 3% makes most revenue beats material.

## 10. Bridge order
Revenue effect = delta revenue x prior margin; margin effect = delta margin x current
revenue. Exact with no residual, but order-dependent: the reverse order gives different
splits. Chosen because it matches the usual "volume first at old rate" convention.

## 11. Recast FY25 and segment data source
FY25 segment figures come only from FY26 filings (recast), never FY25 10-Qs.
- Q1-Q3 (both years): the FY26 10-Q for that quarter, which shows the recast FY25 quarter.
- Q4 (both years): FY26 10-K twelve months minus Q3 FY26 10-Q nine months.
- Figures are read from the filings' inline XBRL tags (extract_segments.py, segments.py),
  not retyped; each row in segments.csv names its filing, accession, concept and period
  instead of a page number. Rows start `verified=no` for a hand check against the note.
- The FY26 10-K tags GBS operating income and costs as `GlobalBusinessSolutionsMember`
  but GBS revenue as `GlobalBusinessSolutionsSegmentMember`. Same segment; both accepted
  and the alias is noted in the source column.
- Tie-outs, all 8 quarters, all pass: segments = total revenue; product lines = segments;
  segment OI - unallocated items = GAAP operating income; costs = cost of revenue + opex;
  revenue and GAAP operating income = the hand-verified release figures (so derived Q4
  matches the Q4 releases).
- All 240 rows hand-checked against the 10-Q/10-K segment notes and income statements on
  2026-11-03; no corrections. segments.py writes verified=yes; reset if the logic changes.
- Q3 FY25 recast confirmed in the tags: $155M of costs moved from Consumer to other
  corporate expenses under the August 2025 reorganization.

## 12. Model
claude-opus-5-5 via `claude -p`, same model for every run; model string recorded in each
output. No temperature control in `claude -p`; run-to-run variation is measured with 2 runs.

## 13. Data entry
Guidance and release actuals were keyed from the SEC filings; every row starts with
`verified=no` and is ticked only after checking against the filing table it cites.
All 37 guidance rows and 10 actuals rows were checked by hand against the releases on
2026-11-03 (no corrections needed). The CSVs are the source of truth; Excel copies are
not tracked.
Check: quarterly revenue and GAAP operating income sum exactly to the annual figures
for FY25 and FY26.

## 14. Live test
Q1 FY27 reports 2026-11-19 after market close. Code and prompts frozen with a git tag
before that date.

## 15. Segment costs are Intuit's management view, not full cost
Intuit charges a segment only the costs managed inside it. Product development, customer
success (the tax and bookkeeping experts behind TurboTax Live / Expert Full Service),
corporate S&M and G&A, share-based compensation, amortization and restructuring stay in
unallocated corporate items ($10.3B in FY26). Consequences:
- Segment margins (77-81%) are not profitability; the checker flags "profit margin" wording.
- Growth in assisted tax raises Consumer segment income while its expert cost sits in
  other corporate expenses, so the bridge shows unallocated items as separate drivers and
  commentary that credits Consumer's segment margin for company profitability is flagged.
- The allocation itself changed in Aug 2025 ($606M of FY25 Consumer and $9M of GBS costs
  moved to corporate), which is why only recast FY25 figures are used.

## 16. Claim schema and checker rules
Claims carry `metric`, `comparison`, `period` (together the variance-table row id),
`measure` (change_pct / change_abs / actual / base), `direction`, `value`, `unit`,
optional `position` for plan comparisons, and `reason` + `quote` + `quote_source`.
`measure` is explicit so the checker never guesses whether "$368M" is a change or a level.
Rules (check.py):
- Values within half a unit of the last digit shown ("+7.5%" -> +/-0.05; "$12.9B" -> +/-$50M).
- Margin changes must be in pp (bps converted); % on a margin, or pp on a dollar line, is a unit error.
- Every $/%/pp figure in the prose must match a claim; direction words between figures must
  agree with the claim. Period labels (Q3 FY26, fiscal 2026) are stripped first.
- Plan comparisons: any reason, or causal wording in a sentence about them, is an error.
- YoY reasons need an exact quote (>= 5 words) from the permitted source. A quote found only
  in another source is `quote_wrong_source`; found nowhere is `quote_not_found`. If the
  quote contains a %, it must match the claim's change (`quote_mismatch`): the 10-Q MD&A
  gives quarter and year-to-date growth in near-identical sentences (Q2 FY26 Consumer:
  15% quarter, 17% six months), so a real quote can still describe the wrong period.
  Code still cannot prove that the words support the reason; hand labels measure that.
- "profit margin" / "profitability" next to a segment result is flagged (decision 15).
- Coverage: recall over required items (decision 9); precision = claims that map to a row.
- Error-rate denominator = figures + directions + causal claims (claims with a reason plus
  causal prose sentences not carried by any claim). Missed items are reported as recall,
  not in the error rate.
Self-test (`python3 check.py --selftest`): a reference commentary built from the table
passes clean for all 8 periods (no false positives), and 14 planted errors each produce
their expected error type. Limit: the reference prose is templated; real model prose is
messier, which is what the ~30 hand-labelled sentences measure.
