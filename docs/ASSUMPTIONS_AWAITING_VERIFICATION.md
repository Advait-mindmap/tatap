# ASSUMPTIONS AWAITING VERIFICATION

Things the build assumed that **no spec in `/docs` states** and **no domain expert has confirmed**.
Written for the DC delivery / compliance team, not for developers.

Everything here is currently in use. It works, it produces sensible-looking output, and it may be
wrong in ways the output will not reveal. That is exactly why it is written down.

---

## 1. Which decision points fire at which stage (`DECISION_TAGS_BY_STAGE`)

**Status: my assumption. Not from any spec. Needs your sign-off.**
Code: `backend/app/reasoning/stages.py`. Introduced in Task 6.

### The problem it solves

Two libraries describe stages in two different vocabularies, and nothing reconciles them:

- The **decision-point library** (`DOMAIN_KNOWLEDGE.md` §6) tags each fork with departments —
  `statutory`, `design`, `planning`, `procurement`, `civil`, `mep`, `fit_out`, `commissioning`,
  `handover`, `enabling`.
- The **fragnet library** and the simulator walk **construction stages** — `approvals`,
  `enabling`, `substructure`, `superstructure`, `envelope`, `mep_power`, `mep_cooling`,
  `fire_bms`, `fit_out`, `commissioning`, `handover`.

The simulator walks stage by stage and must decide, at each one, which forks to raise. I wrote a
mapping to bridge the two. **I chose it by reading the tables; nobody with delivery experience
checked it.**

### Why this matters

Stopping at the right fork, and only the right fork, is the product's sharpest differentiator
(`PRODUCT_SPEC.md` §4). This mapping decides *when* each fork is put to the planner. A wrong
mapping produces one of two failures, neither of which looks like an error in the output:

- **A genuine fork surfaces at the wrong stage** — asked too late to act on. A procurement
  decision raised after construction has been sequenced is a decision already made by default.
- **A genuine fork is missed entirely** — if a tag maps to no stage, or to a stage that never
  runs for a given project, the simulation walks straight past it and *silently assumes an
  answer*. The plan looks complete and confident. This is the failure the whole stop-and-ask
  design exists to prevent.

### The mapping, tag by tag

| Decision-point tag | Mapped to stage(s) | My reasoning — please check |
|---|---|---|
| `statutory` | `approvals` | Statutory pathway questions gate the approvals stage, which is where consents are obtained. |
| `design` | `approvals` | There is no design stage (see §1.1). Approvals is the first stage that runs, so design-basis forks are raised as early as possible. |
| `planning` | `approvals` | Same reasoning — planning/WBS forks need answering before anything is sequenced. |
| `enabling` | `enabling` | Direct match. |
| `civil` | `substructure`, `superstructure`, `envelope` | The three stages civil/structure owns per the `DOMAIN_KNOWLEDGE.md` §3 table. |
| `mep` | `mep_power`, `mep_cooling`, `fire_bms` | The three MEP-owned stages. Fire/BMS included because §3 lists it under services. |
| `procurement` | `mep_power`, `mep_cooling` | **Weakest assumption — see §1.1.** There is no procurement stage, so procurement forks are attached to the stages that consume long-lead plant. |
| `fit_out` | `fire_bms`, `fit_out` | Fit-out plus fire/BMS, which are typically subcontracted fit-out-adjacent packages. |
| `commissioning` | `commissioning` | Direct match. |
| `handover` | `handover` | Direct match. |

No tag is currently unmapped. If you add a tag in admin that this table does not cover, the forks
carrying it will **never be raised** — the mapping should move into the admin decision-point
editor (`ADMIN_SPEC.md` §3) so that cannot happen silently.

### What this means in practice

The resulting behaviour — the eight curated forks and the stages each will be raised at:

| Decision point | Raised at these stages |
|---|---|
| `dp.delivery_mode` | substructure, superstructure, envelope, mep_power, mep_cooling, fire_bms, fit_out |
| `dp.ofe` | mep_power, mep_cooling, fire_bms |
| `dp.grid_position` | approvals, mep_power, mep_cooling, fire_bms |
| `dp.tier_topology` | approvals, mep_power, mep_cooling, fire_bms, commissioning |
| `dp.greenfield_brownfield` | enabling, substructure, superstructure, envelope, mep_power, mep_cooling, fire_bms, commissioning |
| `dp.phasing` | approvals, commissioning, handover |
| `dp.long_lead_unconfirmed` | mep_power, mep_cooling |
| `dp.city_pathway_unconfirmed` | approvals |

### 1.1 Three specific things I want challenged

**a) There is no `procurement` stage, and procurement drives the critical path.**
`DOMAIN_KNOWLEDGE.md` §2 lists Procurement/SCM as its own department, and §4 says long-lead gear
"usually drives RFS. Front-load it." But my stage list has no procurement stage, so every
procurement fork — including `dp.long_lead_unconfirmed`, the one about the lead times that set
RFS — is only raised once `mep_power` is reached. **That may be far too late.** If procurement
should be its own early stage, this is a structural change, not a mapping tweak.

**b) `dp.long_lead_unconfirmed` fires only at `mep_power` and `mep_cooling`.**
So long-lead items outside MEP — structural steel, for instance — would never trigger it. Is that
correct, or does civil have long-lead exposure that should stop the flow too?

**c) `dp.delivery_mode` is raised at seven stages.**
Self-perform vs subcontract is genuinely per-discipline, so repetition may be right. But a planner
answering the same-sounding question seven times will start clicking through them, which defeats
the purpose. Should it be asked once per discipline, up front, instead?

### How to correct it

Tell us the right mapping and we change the table. It is a small, contained change — but only if
someone who has delivered a data centre says what it should be.

---

## 2. The invented library data (Task 4)

Separately from the mapping above, **30 of 44 library entries were invented by the model** and are
awaiting your verification: equipment lead times (8/8), city pathway timings (8/8), productivity
norms (6/6), fragnet durations and logic (5/5), tier sizing rules (3/4).

They are quarantined — `assert_usable_in_live_plan()` refuses them, and any reasoning resting on
them is capped at confidence 0.5 and flagged Tier-2 — but they are what the engine currently
instances from.

To list exactly what needs checking:

```python
from backend.app.libraries import verification_report
verification_report()
```

Each entry carries a `provenance` block in `backend/app/libraries/data/*.json` naming its origin
(`model_generated` = invented; `spec_transcribed` = copied from `DOMAIN_KNOWLEDGE.md`) and its
verification status. Verification is recorded per entry, so you can sign off the lead times
without signing off the norms.

---

## 3. Known gaps that are not assumptions

Recorded here so they are not mistaken for verified behaviour:

- **Corpus has no real executions.** The seed holds only this repo's own documentation and a
  standards pointer index. No simulation can cite real project precedent until historical DC
  schedules and actuals are loaded (`INPUTS.md` §4).
- **Retrieval is lexical, not semantic.** No embedding API is configured, so the corpus matches
  shared vocabulary rather than shared meaning, and will miss paraphrased precedent.
- **The pgvector SQL retrieval path is untested.** Tests run on sqlite against an equivalent
  Python implementation; the Postgres path has no coverage.
