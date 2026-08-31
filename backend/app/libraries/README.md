# Domain libraries

Versioned **data**, not code. The deterministic engine instances activities, logic, durations and
counts from here; the LLM only reasons over these entries and may never emit them itself
(CLAUDE.md rule 2). Everything is keyed to `LIBRARY_VERSION`, and every simulation records the
version it used so a plan can always be traced to the library that produced it.

| File | What it holds |
|---|---|
| `fragnets.json` | Work-package templates: activities, logic (FS/SS/FF + lag), durations, materials, hold points |
| `equipment_lead_times.json` | Long-lead plant, ex-works to site delivery |
| `productivity_norms.json` | Output rates used to derive durations from quantities |
| `tier_rules.json` | Sizing: hall count, redundancy multipliers, plant per MW |
| `decision_points.json` | The eight curated forks the simulator stops at |
| `safety_register.json` | Tier-1 safety-critical activities that block export until signed off |
| `city_pathways/*.json` | Per-city statutory approval pathways |

---

## These numbers are estimates, not evidence

**Read this before trusting any figure in these files.**

The lead times, productivity norms, fragnet durations, statutory timings and sizing ratios are
**industry-typical estimates for the Indian data-centre market**. They are defensible: each sits
in a realistic range and carries a note arguing for it. They are chosen so the engine has
sensible values to instance and so a generated programme looks like a real programme.

**None of them is a measurement of a delivered project.** No vendor quotation, no purchase order,
no historical schedule, no set of actuals informed a single one. They are marked
`origin: "industry_estimate"` and `verification_status: "unverified"`.

Realistic estimates are in one specific way **more dangerous** than obviously invented ones: a
number that looks wrong invites challenge, and a number that looks right stops being questioned.
A schedule built from these will be internally consistent, professionally plausible, and
potentially wrong in ways nothing in the output reveals. That is why the guardrails stay on even
though the values improved.

### What the guardrails do

- `assert_usable_in_live_plan()` **raises** on any unverified entry. A live planning run cannot
  use these as they stand.
- The reasoning loop **caps confidence at 0.5** for any conclusion resting on stand-in data,
  names the specific dependency in the reasoning trail, and raises a Tier-2 flag. An estimate
  cannot re-emerge wearing the model's confident voice.
- Compliance registers are created `approved: false` and must be approved in admin
  (`ADMIN_SPEC.md` §4) before driving a live plan.
- `verification_report()` lists exactly what is outstanding.

Marking an entry `verification_status: "verified"` is a deliberate human act, recorded per entry.
Nothing in the seed ships verified, and no ingestion or reasoning path can set it.

---

## What makes this product actually grounded

Loading **real project schedules and actuals** is the difference between a planner that produces
a plausible programme and one that produces a defensible one.

`DOMAIN_KNOWLEDGE.md` §1 puts it directly: *"The expert is only as good as its corpus."* Expertise
here means preferring real project precedent over generic norms **and saying which precedent was
used**. Everything in these files is a generic norm. Until the corpus holds delivered-project
data, the reasoning trail has nothing real to cite — retrieval will warn `NO REAL-EXECUTION
PRECEDENT`, and every conclusion rests on estimates.

What to load (`INPUTS.md` §4), in rough order of value:

1. **Historical DC schedules** — the coding, logic and durations that actually ran.
2. **Actuals against plan** — planned vs achieved, and *why* the difference. Slippage causes are
   worth more than durations, because they encode judgement rather than numbers.
3. **Lessons learned and method statements** — how the team actually sequences work.
4. **Procurement records** — real lead times against real POs, which replaces the highest-risk
   data in these files.

Each replaces estimates with evidence, and each simulation gets more defensible as the corpus
grows. Start with two or three real projects; it compounds from there.

---

## Provenance vocabulary

Every entry declares its origin. A file whose entry lacks a `provenance` block fails to load.

| Origin | Meaning | Needs verification |
|---|---|---|
| `client_supplied` | Real execution records from delivered projects | No — this is the evidence |
| `public_standard` | Named standard or statute (IS 456, NBC 2016, CEA regs) | No |
| `industry_estimate` | Defensible typical figure, standing in for real data | **Yes** |
| `model_generated` | Invented by the model, grounded in nothing | **Yes** |
| `spec_transcribed` | Copied from this repo's own `/docs` | **Yes** |

`industry_estimate` and `model_generated` both count as stand-in data: reasoning that rests on
either is capped and disclosed. The single source of truth is `NOT_REAL_DATA` in
`provenance.py` — the reasoning loop reads it rather than naming origins itself, so adding an
origin cannot silently switch the anti-laundering cap off.

Open items and the decisions behind the current shape are recorded in
`docs/ASSUMPTIONS_AWAITING_VERIFICATION.md`.
