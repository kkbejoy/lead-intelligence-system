# Lead Qualification Rubric & Missing-Data Policy — Spec

This document defines the qualification rubric, scoring formulas, decision
logic, and missing-data handling for the Lead Intelligence System. It is
intended to be handed to an LLM/engineer as an implementation spec — all
scoring logic here should be implemented as deterministic code, not left to
LLM judgment at runtime.

---

## 1. Business Context / Assumptions

- Mid-market B2B SaaS company, horizontal product (not industry-locked).
- ACV (Annual Contract Value) assumption: **$15K–$60K/year**.
- Sales motion: sales-assisted (a rep works the deal) — not self-serve,
  not enterprise (no dedicated AE/legal/deal-desk/security-review team).
- Ideal Customer Profile (ICP): companies with a named budget-holder, no
  formal procurement/RFP requirement, roughly 20–1,000 employees.
- These are stated assumptions, not derived from data — they exist so the
  rubric below is defensible and explainable, not because they were proven
  against ground truth.

---

## 2. Input Fields Used

| Field | Role |
|---|---|
| `company_size` | Fit signal |
| `industry` | Fit signal |
| `source` | Intent signal (base) |
| `last_interaction_date` | Intent signal (decay multiplier on source) |
| `name`, `company` | Identifiers only — not scored. Used for personalization/reachability check. |

---

## 3. Raw Factor Scoring (0–10 scale each)

### 3.1 Company size score

| Range (employees) | Score | Rationale |
|---|---|---|
| < 10 | 1 | Below ICP — budget/risk concern |
| 10–19 | 3 | Marginal, borderline |
| 20–200 | 10 | Core sweet spot |
| 201–1,000 | 8 | Still strong, slightly larger buying committee |
| 1,001–3,000 | 5 | Stretch zone — winnable but slower |
| 3,001–10,000 | 3 | Likely enterprise process mismatch |
| 10,000+ | 1 | Enterprise sales cycle — poor fit for our motion |

### 3.2 Industry score (tiered)

| Tier | Score | Industries |
|---|---|---|
| Strong fit | 10 | SaaS, Software, Tech, Technology, Marketing, Analytics, Cybersecurity, EdTech, FinTech |
| Neutral | 6 | Retail, Manufacturing, Logistics, Hospitality, Travel, Media, Events, Consulting, Energy, Supply Chain, Real Estate, Agriculture |
| Weak fit | 3 | Government, Legal, Finance, Insurance, Healthcare, Pharmaceuticals (regulated / long procurement cycles) |
| Unrecognized/unlisted industry not in any tier above | 6 | Treated same as "Neutral" (not a missing-data case — see §4) |

### 3.3 Source score

| Source | Score |
|---|---|
| Referral | 10 |
| Inbound demo request | 10 |
| Sales call | 8 |
| Webinar attendee | 6 |
| Content download | 4 |
| LinkedIn outreach | 3 |

### 3.4 Recency decay multiplier (applied to source score)

| Days since `last_interaction_date` | Multiplier |
|---|---|
| 0–30 | 1.0 |
| 31–90 | 0.75 |
| 91–180 | 0.5 |
| 181–365 | 0.3 |
| 365+ | 0.15 |

---

## 4. Missing-Data Policy

"Missing" = blank/empty cell OR the literal string `"NA"` (case-insensitive).
An unrecognized *category* (e.g., industry `"Unknown Sector"`, source
`"Unknown Source"`) is treated as missing for scoring-default purposes, not
as a valid new category.

| Field | Missing → Default score | Also tag |
|---|---|---|
| `company_size` | 5 (midpoint) | `data_quality_flag: company_size_missing` |
| `industry` | 6 (same as Neutral tier) | `data_quality_flag: industry_missing` |
| `source` | 4 (same as Content download tier) | `data_quality_flag: source_missing` |
| `last_interaction_date` | 0.5 multiplier (same as 91–180 day band) | `data_quality_flag: date_missing` |

Rationale: a missing field must default to a **neutral/conservative** value,
never the worst possible score — otherwise a data-quality gap is
indistinguishable from a genuinely bad-fit signal.

### 4.1 Forced-Review rules (override the computed decision band)

Apply **after** computing the raw Final Score (§5), before mapping to a
decision:

1. **`company_size` missing → always force decision = "Review"**, regardless
   of computed score. Rationale: `company_size` alone drives 38.5% of the
   Final Score (see §5 weight derivation) — too heavily weighted to default
   silently.
2. **2 or more of `{industry, source, last_interaction_date}` missing →
   force decision = "Review".** No single one of these lighter-weight
   fields is disqualifying alone, but compounding uncertainty across two or
   more is enough to distrust the number.
3. **Otherwise**, the computed score stands and may resolve to Qualified,
   Review, or Rejected normally — including a confident **Rejected** for a
   fully-known-but-poor-fit lead (e.g., real `company_size = 50000`). A
   known bad fit is a different case from an unknown fit and must not be
   routed to the same bucket.

Missing `name` and/or `company` do **not** affect scoring. Instead:

- Missing `name` → `personalization_flag: missing_name` — message
  generation must not fabricate a name; fall back to a company-level
  greeting or flag for manual outreach.
- Missing `company` → `personalization_flag: missing_company` — similar
  fallback; do not fabricate a company name.

---

## 5. Score Combination

```
Fit Score    = 0.7 × size_score + 0.3 × industry_score
Intent Score = source_score × recency_multiplier
Final Score  = 0.55 × Fit Score + 0.45 × Intent Score
```

Weight rationale:

- Size (0.7) outweighs industry (0.3) within Fit because size is the more
  reliable signal; industry is thinner/more speculative given the dataset's
  wide spread across many industries with few examples each.
- Fit (0.55) outweighs Intent (0.45) in the final blend: a hot lead at a
  bad-fit company is still a bad-fit company, whereas a good-fit company
  that has gone cold can often be re-engaged. Fit is the more durable
  signal.
- Field-level contribution to Final Score: `company_size` ≈ 38.5%,
  `industry` ≈ 16.5%, `source` up to 45% (scaled down by recency
  multiplier, which has no independent weight of its own — it only scales
  source's contribution).

---

## 6. Decision Bands

Applied to Final Score **after** the forced-Review overrides in §4.1:

| Final Score | Decision |
|---|---|
| ≥ 7.5 | Qualified |
| 5.0 – 7.4 | Review |
| < 5.0 | Rejected |

---

## 7. Worked Examples

**Example A — clean, strong lead**
`company_size=50, industry=SaaS, source=Inbound demo request, last_interaction_date` 0 days ago
- size=10, industry=10 → Fit = 0.7(10)+0.3(10) = 10
- source=10, recency=1.0 → Intent = 10
- Final = 0.55(10)+0.45(10) = **10.0 → Qualified**

**Example B — clean, poor lead**
`company_size=5000, industry=Finance, source=Content download`, ~50 days ago
- size=3, industry=3 → Fit = 0.7(3)+0.3(3) = 3
- source=4, recency=0.75 → Intent = 3.0
- Final = 0.55(3)+0.45(3.0) = **3.0 → Rejected**

**Example C — missing company_size (forces Review despite high raw score)**
`company_size=NA, industry=SaaS, source=Inbound demo request`, 0 days ago
- size defaulted=5, industry=10 → Fit = 0.7(5)+0.3(10) = 6.5
- source=10, recency=1.0 → Intent = 10
- Raw Final = 0.55(6.5)+0.45(10) = 8.075 (would be "Qualified")
- **Overridden → Review** (Rule 1: company_size missing), flagged
  `data_quality_flag: company_size_missing`

**Example D — fully known, very poor fit (NOT forced to Review)**
`company_size=50000, industry=Enterprise Software, source=Inbound demo request`, 0 days ago
- size=1, industry=6 (unrecognized industry label → Neutral default, but
  note: this is a *known* value, not a missing one — no forced-Review flag)
- Fit = 0.7(1)+0.3(6) = 2.5
- source=10, recency=1.0 → Intent = 10
- Final = 0.55(2.5)+0.45(10) = 5.875 → **Review** by score alone (not by
  forced override) — a case worth a human glance because the intent signal
  is strong even though fit is very poor.

**Example E — everything missing**
`company_size=NA, industry=NA, source=NA, last_interaction_date=NA`
- All four fields defaulted → **Forced Review** (Rule 1 and Rule 2 both
  trigger), flagged `data_quality_flag: company_size_missing,
  industry_missing, source_missing, date_missing`.
- `name` present but `company`/other fields absent → check
  `personalization_flag` separately per §4.1.

---

## 8. Implementation Notes for the Engineer/LLM

- All scoring in §3–§6 should be implemented as **deterministic code**
  (no LLM call needed to compute scores) — this keeps decisions
  consistent, auditable, and cheap to run at batch scale.
- The LLM's role (in this system) should be limited to: (a) generating the
  human-readable *reasoning* string that explains a score using the
  factor values, and (b) generating personalized outreach messages for
  Qualified leads — not to re-derive the score itself.
- `"NA"` matching should be case-insensitive and should also treat
  whitespace-only strings as missing.
- Unrecognized categorical values (industry/source not in the defined
  lists) are **not** the same as missing — they get the Neutral/default
  tier's score but do **not** raise a `data_quality_flag`, since a value
  was actually provided, just not one we've pre-classified.
