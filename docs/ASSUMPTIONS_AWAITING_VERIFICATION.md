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
- The **fragnet library** and the simulator walk **construction stages** — `design`,
  `approvals`, `procurement`, `enabling`, `substructure`, `superstructure`, `envelope`, `mep_power`,
  `mep_cooling`, `fire_bms`, `fit_out`, `commissioning`, `handover`.

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
| `design` | `design` | Resolved — see §1.5. A design/engineering stage now exists and owns these forks. |
| `planning` | `approvals` | Planning/Controls is its own department (§2, 5) but has no stage. Its fork (`dp.phasing`) needs answering before anything is sequenced, so the earliest stage is the right home. Flag it if planning needs its own stage. |
| `enabling` | `enabling` | Direct match. |
| `civil` | `substructure`, `superstructure`, `envelope` | The three stages civil/structure owns per the `DOMAIN_KNOWLEDGE.md` §3 table. |
| `mep` | `mep_power`, `mep_cooling`, `fire_bms` | The three MEP-owned stages. Fire/BMS included because §3 lists it under services. |
| `procurement` | `procurement` | Resolved — see §1.1. A procurement stage now exists and owns these forks, so they are raised before construction is sequenced. |
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
| `dp.delivery_mode` | **procurement**, substructure, superstructure, envelope, mep_power, mep_cooling, fire_bms, fit_out |
| `dp.ofe` | **procurement**, mep_power, mep_cooling, fire_bms |
| `dp.grid_position` | approvals, **procurement**, mep_power, mep_cooling, fire_bms |
| `dp.tier_topology` | **design**, **procurement**, mep_power, mep_cooling, fire_bms, commissioning |
| `dp.greenfield_brownfield` | enabling, substructure, superstructure, envelope, mep_power, mep_cooling, fire_bms, commissioning |
| `dp.phasing` | approvals, commissioning, handover |
| `dp.long_lead_unconfirmed` | **procurement** |
| `dp.city_pathway_unconfirmed` | approvals |

### 1.1 Resolved: a procurement stage now exists

**Originally flagged here:** there was no `procurement` stage, so every procurement fork — including
`dp.long_lead_unconfirmed`, the one about the lead times that set RFS — was not raised until
`mep_power`, by which point construction was already being sequenced.

**Resolved.** `procurement` is now a stage in the walk, positioned `approvals -> procurement ->
enabling -> ...` per `DOMAIN_KNOWLEDGE.md` §2 (Statutory 3 -> Procurement/SCM 4 -> Civil 6) and
`SIMULATION_AND_REASONING.md` §2 (engineering -> procurement -> construction -> commissioning).

Its **walk position** is early because that is when procurement strategy and long-lead exposure
must be reasoned about. Its **activities** — RFQ, tender, award, manufacture, FAT, ship, deliver —
span the programme and land as delivery milestones that gate construction. Walk position governs
when it is *reasoned*, not when its work *happens*.

The stage owns the long-lead register directly rather than deriving it from fragnets, because it
has no fragnets yet (see 1.3).

### 1.2 Still open: does `dp.long_lead_unconfirmed` belong outside MEP?

It now fires at `procurement` only. Long-lead items outside MEP — structural steel, for instance —
therefore still do not trigger it independently. Is the single early procurement firing enough, or
does civil have long-lead exposure that should stop the flow on its own terms?

### 1.3 Still open: `frag.procurement.*` is future library data

The procurement stage has **no fragnets**, so the engine currently has nothing to instance for it.
It is consistent with the six other stages that also have none (`approvals`, `enabling`,
`envelope`, `fire_bms`, `fit_out`, `handover`), and full discipline depth is scheduled work — but
until a procurement fragnet exists, a procurement chain (RFQ -> award -> manufacture -> FAT ->
ship -> deliver) will not appear in the exported schedule, and long-lead plant will still appear
to arrive without a visible procurement path.

### 1.4 Decided: `dp.delivery_mode` still fires per discipline

Adding the procurement stage gives this fork an early firing point where the overall delivery
strategy is set. It was a deliberate decision **not** to remove the per-discipline firings:
`DOMAIN_KNOWLEDGE.md` §6 defines it as "Self-perform vs subcontract (**per discipline**)", and
collapsing it to one question would apply a single answer to every discipline.

The cost is that a planner may be asked a similar-sounding question at eight stages. That is a
decision-*resolution* problem — remember the answer per discipline and stop re-asking — not a
reason to lose the granularity. Say if you disagree; it is one line.

### 1.5 Design/engineering is a stage, and now leads the walk

**Originally folded into approvals.** Design forks — notably `dp.tier_topology`, where N+1 vs 2N
sets the equipment counts — were raised at the approvals stage because no design stage existed.

**Resolved.** A `design` stage exists and owns them, and it now **leads the walk**:

```
design -> approvals -> procurement -> enabling -> substructure -> ... -> handover
```

This matches `DOMAIN_KNOWLEDGE.md` §2, which orders Design/Engineering (2) ahead of
Statutory/Liaison (3) and Procurement/SCM (4). The reasoning: the basis of design normally must
precede the consent applications, because drawings are what gets submitted for building sanction;
and the equipment schedule is a design output, so procurement cannot sensibly order ahead of it.

### 1.5.1 Still open: approvals that legitimately precede design

**The walk is a single ordered sequence, and reality is not.** Some early approvals genuinely can
and do run before design is meaningfully advanced:

- land acquisition and title
- environmental clearance (SEIAA) on a large campus
- DC-park / SEZ plot allotment (CIDCO, MIDC, SIPCOT, TSIIC, YEIDA, KIADB)

Placing `design` first means the simulation reasons about design before it reasons about any of
these, which understates how early the land and environmental track really starts. In a real
programme the two run in parallel, with different approvals attaching to different points.

This is a **modelling limitation, not a claim that design always comes first**. If it matters for
your programmes, the options are roughly: split `approvals` into an early track (land, EC,
allotment) and a late track (building sanction, commencement) that sits after design; or let the
engine express the overlap through logic links rather than through walk order. Tell us which
reflects how you actually run it.

### 1.6 Still open: `frag.design.*`, and the IFC gate

The design stage has **no fragnets**, so nothing is instanced for it — same position as
procurement (§1.3). When they are written, the important part is not the activities but the
**gate**: design completion (IFC drawings issued) should gate procurement, because ordering to an
unfixed specification is how projects buy the wrong equipment. That is a cross-stage logic link
the engine will own, not something the stage list can express.

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
