# Lead Intelligence System — Pipeline Architecture Spec

This document describes the end-to-end pipeline architecture. It assumes
the rubric/missing-data spec (`lead_qualification_rubric_spec.md`) as a
companion document — that file defines the scoring formulas; this file
defines how those scores are used inside the overall system.

Prompt engineering / few-shot examples / temperature tuning are **out of
scope for this document** and will be finalized in a later pass. This
document is about pipeline structure, responsibilities, and data flow only.

---

## 1. Core design principle: LLM is the decision-maker, rubric is evidence

This is the most important architectural decision in the system, and it
should shape every stage below:

- The **rubric does not decide** qualified / rejected / review.
- The **rubric computes structured evidence**: per-factor scores, a
  reference Final Score, and data-quality flags.
- The **LLM receives that evidence plus the raw lead fields and makes the
  actual decision** (qualified / rejected / review), and writes the
  reasoning text and (for qualified leads) the outreach message.
- Deterministic code never overrides the LLM's decision. It only computes
  a **diagnostic flag** afterward, comparing the LLM's decision to the
  rubric's Final Score band, for visibility/QA — not as a correction
  mechanism.

Rationale: this keeps the rubric doing what it's good at (consistent,
auditable, cheap-to-compute evidence) while letting the LLM do what it's
good at (judgment/reasoning over that evidence) — and it directly matches
the requirement that decisions be made by the LLM, not by a scoring
formula acting alone.

---

## 2. Overall pipeline flow

```
1. CSV Ingest
2. Validate & Normalize
3. Compute Rubric Evidence (deterministic)
4. Batch LLM Decision Call  ← LLM decides qualified/rejected/review here
5. Post-process: consistency flag, aggregate stats
6. Assemble & write Report (JSON/CSV) + run log
```

Each numbered stage should be its own function/module — testable in
isolation, and important for the brief's robustness requirement (a failure
in one stage should not silently corrupt or crash the whole run).

---

## 3. Stage-by-stage responsibilities

### 3.1 CSV Ingest
- Read `leads_training.csv` / `leads_testing.csv` (or any leads CSV) via
  a standard CSV reader.
- Columns: `name, company, company_size, industry, source,
  last_interaction_date`.
- Should not assume all files are well-formed — handle missing columns,
  extra columns, or empty files without crashing.

### 3.2 Validate & Normalize
- Treat blank cells and the literal string `"NA"` (case-insensitive,
  whitespace-trimmed) as missing.
- Normalize `company_size` to int where parseable; missing otherwise.
- Normalize `last_interaction_date` to a date object where parseable
  (`YYYY-MM-DD`); missing otherwise. Compute days-since-interaction
  relative to a fixed "run date" (use today's date, or optionally an
  injectable reference date for reproducible testing).
- Normalize `industry` / `source` strings (trim whitespace, consistent
  casing) before matching against the tier tables in the rubric spec.
- Output: a normalized lead record + a set of missing-field flags, per
  lead. This is the input to stage 3.3.

### 3.3 Compute Rubric Evidence (deterministic — see rubric spec for exact
formulas/tables)
For each lead, compute and attach:
- `size_score`, `industry_score`, `source_score`, `recency_multiplier`
- `fit_score`, `intent_score`, `final_score` (reference number only — not
  a decision)
- `data_quality_flags`: list of which fields were missing/defaulted
  (e.g., `["company_size_missing"]`)
- `personalization_flags`: missing `name` / missing `company`, separate
  from data-quality flags (affects message generation, not decision
  input)

This stage produces the exact structured evidence object that gets handed
to the LLM in stage 3.4. Nothing here is a final verdict.

### 3.4 Batch LLM Decision Call
- **Batching**: group leads into batches (exact size to be tuned later —
  placeholder ~15/batch) and send one API call per batch, not one call
  per lead.
- **Input per lead in the batch**: raw fields (`name`, `company`,
  `company_size`, `industry`, `source`, `last_interaction_date`) +
  computed evidence from 3.3 (`size_score`, `industry_score`,
  `source_score`, `recency_multiplier`, `final_score`,
  `data_quality_flags`).
- **Output expected per lead** (structured/JSON): `decision`
  (`qualified`/`rejected`/`review`), `reasoning` (short explanation
  referencing the evidence), `outreach_message` (only required for
  `qualified`; optional/empty otherwise).
- **Cost optimization to consider (not finalized)**: whether every lead
  needs a full LLM pass, or whether extremely clear-cut cases (e.g. all
  fields present, final_score far outside any ambiguous range) could be
  short-circuited — deferred to the tuning pass. Default assumption for
  now: **all leads go through the LLM**, since the LLM is the decision
  authority in this design.
- Missing-data handling: the rubric's forced-review policy (see rubric
  spec §4.1) should be encoded as an instruction to the LLM (e.g. "if
  company_size is flagged missing, decision must be review") — this is a
  prompt-engineering detail to be finalized later, but the pipeline must
  pass `data_quality_flags` into the prompt so the LLM has what it needs
  to apply that rule.

### 3.5 Error Handling & Retries (applies to stage 3.4)
- Retry transient failures (timeouts, HTTP 429, 5xx) with exponential
  backoff — e.g. 3 attempts at 1s/2s/4s.
- **Per-batch isolation**: a batch that fails after retries must not stop
  the whole run. Mark its leads with `llm_call_failed: true`,
  `decision: null`, `reasoning: "LLM generation failed after retries —
  rubric evidence only"`, and continue processing remaining batches.
- Validate LLM output is well-formed JSON matching the expected schema
  before accepting it; malformed output is treated the same as a call
  failure (fallback above), not a crash.
- All of this should be logged (timestamp, batch index, lead IDs in
  batch, status, retry count) to a run log, separate from the final
  report, so failures are diagnosable after the fact.

### 3.6 Post-process
- **Consistency flag** (diagnostic only, does not alter the decision):
  compare the LLM's `decision` against the rubric's `final_score` band
  (≥7.5 / 5.0–7.4 / <5.0 from the rubric spec). If they disagree, set
  `llm_rubric_disagreement: true` on that lead. This is a QA/reporting
  signal, not a correction mechanism — the LLM's decision always stands
  as-is.
- **Aggregate stats**: total leads processed, count/percentage
  qualified/rejected/review, most common rejection reasons (derived from
  LLM reasoning text or from dominant low-scoring factors), count of
  `llm_call_failed` leads, count of `llm_rubric_disagreement` leads.

### 3.7 Report Assembly
Write a single JSON report (CSV export optional/secondary) with roughly:

```json
{
  "run_summary": {
    "total_processed": 0,
    "qualified_count": 0,
    "review_count": 0,
    "rejected_count": 0,
    "qualified_pct": 0.0,
    "common_rejection_reasons": [],
    "llm_call_failures": 0,
    "llm_rubric_disagreements": 0
  },
  "leads": [
    {
      "name": "",
      "company": "",
      "raw_fields": {},
      "rubric_evidence": {
        "size_score": 0, "industry_score": 0, "source_score": 0,
        "recency_multiplier": 0.0, "fit_score": 0, "intent_score": 0,
        "final_score": 0
      },
      "data_quality_flags": [],
      "personalization_flags": [],
      "decision": "qualified | rejected | review",
      "reasoning": "",
      "outreach_message": "",
      "llm_rubric_disagreement": false,
      "llm_call_failed": false
    }
  ]
}
```

Also write a separate run log (plain text or JSON lines) capturing batch
-level status/errors from stage 3.5 — kept distinct from the report so the
report stays clean for the sales team to read.

---

## 4. What is explicitly deferred to the fine-tuning pass

- Exact batch size
- The system/user prompt template wording
- Few-shot examples to include for calibration
- Temperature / sampling parameters
- Whether to short-circuit obviously clear-cut leads to skip the LLM call
- Exact phrasing of the missing-data hard-constraint instruction to the
  LLM

Everything else in this document (flow, stage responsibilities, data
shapes, error handling, report schema) should be treated as settled and
implementable now.
