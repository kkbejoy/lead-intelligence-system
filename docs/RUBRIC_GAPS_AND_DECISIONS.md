# Rubric — Open Gaps & Design Decisions Log

Companion to `lead_qualification_rubric_spec.md`. Every item is either a
contradiction to fix, a missing rule to add, or an assumption to state
out loud. Status values: **OPEN** (needs a decision) / **DECIDED** (resolution
recorded, fold into the spec) / **DEFERRED** (revisit after a named milestone).

Last updated: 2026-09-03

---

## Gap A — "Unrecognized category" vs "Missing" contradict each other

**Status:** OPEN

**Where:** spec §4 vs §3.2, §8, and Example D.

- §4 says an unrecognized category "is treated as **missing** for
  scoring-default purposes."
- §3.2 / §8 / Example D say an unrecognized-but-real value gets the
  neutral-tier score and does **not** raise a `data_quality_flag`.

These cannot both hold. Pick one.

**Recommended resolution:**

| Class | Definition | Scoring | `data_quality_flag`? |
|---|---|---|---|
| Missing | blank, whitespace-only, or a literal token in `{na, n/a, none, null, unknown, -}` (case-insensitive, trimmed) | field default score (§4) | **yes** |
| Unrecognized | a real value not in our pre-classified lists (e.g. `industry = "Enterprise Software"`) | neutral / default tier score | **no** |

Consequence for the sample data:
- `industry = "Unknown Sector"` → contains token `unknown` → **Missing**
  (default 6 + `industry_missing`).
- `source = "Unknown Source"` → contains token `unknown` → **Missing**
  (default 4 + `source_missing`).
- `industry = "Enterprise Software"` (row `Test_VeryLargeEnterprise`) →
  **Unrecognized** → neutral 6, no flag. Matches Example D.

**Action:** delete the contradictory sentence in §4; add the table above.

---

## Gap B — Priority ranking is undefined

**Status:** OPEN

**Where:** not in the spec at all. The brief requires it: deliverables list
"priority rank" per lead and a "priority queue" in the report.

The rubric currently outputs `final_score` + `decision` only. There is no
rule that turns those into an ordered outreach queue.

**Decisions needed:**

1. Integer rank (1..N) or tiers (P1/P2/P3)?
2. Rank by `final_score` desc — and what breaks ties?
3. Do **Review** leads get a rank, or only **Qualified**?
4. Does interaction recency override raw score for outreach *urgency*
   (call the fresh lead before the stale one)?

**Recommended resolution:**

- Priority applies to **Qualified leads only**.
- `priority_rank` = position after sorting Qualified leads by
  `final_score` desc, then by `last_interaction_date` desc (most recent
  first) as the tie-breaker, then by `company` A→Z as a final
  deterministic tie-breaker so re-runs are stable.
- **Review** leads are listed in their own section, unranked, sorted by
  `final_score` desc.
- **Rejected** leads are listed last, no rank.
- Optional nicety (only if time allows): tag the top-N Qualified as
  `P1` and the rest `P2` using a config'd cutoff (e.g. top 20%). Not
  required for the deliverable.

**Action:** add a new §6.1 "Priority Ranking" to the spec.

---

## Gap C — Bad-data edge cases the spec doesn't name

**Status:** OPEN (cheap to close)

§4 covers blank / `NA`. It does not cover **present-but-malformed** values.

| Case | Example in the wild | Proposed handling |
|---|---|---|
| `last_interaction_date` in the future | `2027-01-01` | clamp days-since to 0 → multiplier 1.0; raise `data_quality_flag: date_future` |
| `last_interaction_date` unparseable | `13/45/2020`, `"last Tuesday"` | treat as Missing → 0.5 multiplier + `data_quality_flag: date_invalid` |
| `company_size` non-numeric range/label | `"50-200"`, `"SMB"`, `"1k+"` | attempt a parse (take lower bound of a range); if that fails, treat as Missing → 5 + `company_size_missing`; **also forces Review** (Rule 1) |
| `company_size` <= 0 or absurd (`0`, `-5`, `9999999`) | typo | `0`/negative → Missing path; very large but plausible (`50000`) is a *valid* enterprise value, not an error — score it (1) |

Note on the sample data: `leads_testing.csv` has **missing** dates
(rows 6, 14, 15) but no **malformed** ones, and no future dates. These
branches will be coded defensively but not exercised by the provided data
— call that out in the README, or add 2–3 rows to a `leads_edge.csv` of
your own.

**Action:** add these rows to the §4 table.

---

## Gap D — "Qualified" threshold (≥ 7.5) may be too strict

**Status:** DEFERRED — revisit after the scoring engine runs on the full
dataset and produces a score histogram.

Spot-check: a 150-employee, neutral-industry, webinar-attendee lead that
engaged 3 weeks ago scores ≈ 6.8 → **Review**, not Qualified. That might
be intentional (the company currently pursues only ~5% of leads), but the
brief says they "suspect they're missing opportunities."

**Do not change the numbers yet.** After Phase 4:
1. Plot the distribution of `final_score` across all ~100 leads.
2. Check what % lands in each band.
3. If Qualified % is implausibly low (< 8–10%), consider lowering the
   Qualified cut to ~7.0 and/or the Rejected cut — **with a one-line
   justification recorded here.**

---

## Gap E — The "as-of" date: sample data is historical  ⚠️ BLOCKER

**Status:** OPEN — must decide before the recency multiplier is coded.

**The problem:** every `last_interaction_date` in the sample files falls
between **2022-06-15 and 2024-01-20**. Today is **2026-09-03**. If recency
is measured against the real system clock, *every lead* is 590+ days stale
→ every lead gets the **0.15** multiplier → the Intent signal collapses to
near-zero for the entire dataset → almost nothing qualifies, and the
rubric's Intent half becomes dead weight. The system would "work" but
produce a garbage report.

**Recommended resolution:**

- Add a config key `evaluation_date` (a.k.a. "score as of").
- Default behaviour: if unset, use `max(last_interaction_date)` across the
  ingested file **+ 1 day**. For the provided data that resolves to
  **2024-01-21**.
- Allow an explicit override in `config.yaml` (e.g. for reproducible
  report generation, pin it to `2024-01-21`).
- State the assumption loudly in the README: *"Recency is scored relative
  to the most recent interaction in the batch, not wall-clock time,
  because the sample dataset is historical. In production this would be
  `date.today()`."*

**Action:** add `evaluation_date` to the config schema and to §3.4 of the
spec.

---

## Gap F — Only 50 leads provided; the bar is 100+

**Status:** OPEN

`leads_training.csv` (30) + `leads_testing.csv` (20) = **50**. The grading
rubric's "Excellent" row for Processing is "**100+ leads, zero crashes**."
The brief also explicitly allows *"or link to generate it."*

**Options (pick one):**

1. **Generator script** (`generate_leads.py`) that emits N synthetic leads
   by recombining the observed value vocabularies (names, companies,
   size bands, industries, sources, date offsets) with a fixed random
   seed. Cleanest; also demonstrates data handling. Produces
   `leads_generated.csv`.
2. Hand-author ~55 more rows. Slower, no upside over (1).
3. Duplicate the 50 with small perturbations. Weakest — reviewers can
   see it's padded.

**Recommendation:** option 1. Keep the real 50 as the "curated" set the
README points to for diversity; use `leads_training.csv + leads_testing.csv
+ leads_generated.csv` for the 100+ scale run.

**Action:** build `generate_leads.py` in Phase 2.5 (after ingest, before
the full batch run).

---

## Gap G — `DATA_README.md` contradicts its own data on "NA"

**Status:** DECIDED — no action needed, spec already handles it.

`DATA_README.md` line 65 says *"Blank/empty cells represent missing data
(not the string 'NA')."* But `leads_testing.csv` rows 2, 15, 16 use the
literal string `NA` for `company_size` / all fields.

Spec §4 already treats **both** blank **and** literal `NA` (case-insensitive)
as Missing, so the pipeline is correct regardless. Just note in the README
that we defensively accept both forms.

---

## Gap H — No truly-blank `name` row in the sample data

**Status:** OPEN (minor)

Row 14 of `leads_testing.csv` is `Test_MissingName,,400,Consulting,...` —
the `name` column actually contains the string `"Test_MissingName"`; it is
the **company** column that is blank. So the `personalization_flag:
missing_name` branch is never exercised by the provided data (only
`missing_company` is).

**Options:** add one row with a genuinely empty `name`, or accept that the
branch is defensively coded but untested, and say so in the README.

---

## Quick decision checklist

| Gap | Decision | Where it lives now |
|---|---|---|
| A — unrecognized vs missing | **DECIDED** — value is *missing* if empty or any word is a `missing_token` (`unknown`, `na`, …); otherwise a real-but-unmapped value scores at the neutral tier with no flag | `config.yaml: missing_tokens`; `normalize._text_or_missing`; tests in `test_normalize.py` |
| B — priority ranking rule | **DECIDED** — Qualified only; sort by `final_score` desc, then most-recent interaction, then company A→Z; Review/Rejected listed unranked by score | `postprocess.priority_order`; report `priority_queue` |
| C — malformed-data handling | **DECIDED** — range/label sizes → lower bound or missing; non-positive size → missing; unparseable date → `date_invalid` (treated as missing); future date → clamped + `date_future` flag | `normalize._parse_company_size`, `_parse_date`; `rubric._recency_multiplier` |
| E — `evaluation_date` default | **DECIDED** — `auto` = `max(last_interaction_date) + 1 day` (= 2024-01-21 for the sample data); overridable in config or `--evaluation-date` | `config.AppConfig.resolve_evaluation_date` |
| D — Qualified threshold | **DEFERRED** — offline run currently yields 28.6% qualified at `qualified_min: 7.5`; revisit with a real-LLM histogram before finalising |
| F — how to reach 100+ leads | **OPEN** — 49 leads today. Recommend `generate_leads.py` (seeded synthetic expansion). |
| G — README vs data on "NA" | **DECIDED** — pipeline accepts both blank and literal `NA`; no action |
| H — missing-name test row | **OPEN (minor)** — no genuinely blank `name` in sample data; `missing_name` branch is unit-tested but not exercised end-to-end |

## Still to reconcile between the two spec docs

`lead_qualification_rubric_spec.md` §4.1 says deterministic code *forces* the
Review decision. `pipeline_architecture_spec.md` §1 says the LLM is the sole
decision-maker and code never overrides it. **The pipeline implements the
pipeline-spec version**: the forced-review rule is passed to the LLM as a hard
instruction (`prompts.SYSTEM_PROMPT`), and if the LLM ignores it we raise a
`policy_flags: ["forced_review_not_applied"]` QA signal rather than overriding.
Decide in the tuning pass whether that QA signal is strong enough or whether a
deterministic guard should be reinstated. `RubricEvidence.forced_review_expected`
already carries the deterministic answer if you want to switch.
