# DOMAIN DECISIONS AND OUTSTANDING ASSUMPTIONS

Modelling choices the build made that **no spec in `/docs` stated**, split into two kinds:

- **§0 Decided** — settled by the project owner, who is the domain authority here. There is no
  separate domain team to sign these off. They are final and the code implements them; treat
  them as requirements, not as open questions.
- **§2 onward** — still outstanding: data that has not been checked, and library content that
  does not exist yet. These are genuinely unresolved.

Everything described here is in use. The outstanding items work and produce sensible-looking
output, and may be wrong in ways the output will not reveal. That is why they stay written down.

---

## 0. Decided

Final. Recorded with the reasoning so the *why* survives, not just the *what*.

### 0.1 Design sits before approvals

**Walk order: `design -> approvals -> procurement -> ...`**

The basis of design precedes the consent applications, because drawings are what gets submitted
for sanction. This matches `DOMAIN_KNOWLEDGE.md` §2, which orders Design/Engineering (2) ahead of
Statutory/Liaison (3).

**Approvals is NOT split.** Some early approvals — land acquisition and title, environmental
clearance on a large campus, DC-park or SEZ plot allotment — do in reality begin before design is
meaningfully advanced. That overlap is handled as **logic links in the engine**, not by splitting
`approvals` into early and late stages. The walk order governs the sequence in which stages are
*reasoned about*; genuine calendar overlap between an early land/EC track and design is expressed
as dependencies between activities, which is what a dependency graph is for. Splitting the stage
would push a scheduling concern into the stage list, where it does not belong.

### 0.2 `dp.delivery_mode` fires per discipline

The fork is raised at every stage whose discipline it governs — `procurement`, `substructure`,
`superstructure`, `envelope`, `mep_power`, `mep_cooling`, `fire_bms`, `fit_out` — not once
globally. `DOMAIN_KNOWLEDGE.md` §6 defines it as "self-perform vs subcontract (**per
discipline**)", and one answer applied to every discipline would lose the granularity that makes
the fork worth stopping for.

The procurement firing is where overall delivery strategy is set; the per-discipline firings
remain. Being asked a similar-sounding question at several stages is a decision-*resolution*
concern — remember the answer per discipline and stop re-asking — not a reason to collapse the
fork.

### 0.3 Planning has no stage of its own

`dp.phasing` fires at `approvals`, and that is correct. Planning/Controls is a department
(`DOMAIN_KNOWLEDGE.md` §2, 5) rather than a construction stage: it produces the WBS, logic,
durations and calendars, which are the engine's outputs rather than a phase of work to be
sequenced. Phasing must be settled before anything is sequenced, so an early stage is the right
home for the fork.

---

## 1. Which decision points fire at which stage (`DECISION_TAGS_BY_STAGE`)

**Status: implemented, and the decisions in §0 are baked into it.** The rows below that follow
from §0 are final. The remainder is a mapping I derived from the two tables; it stands as
implemented and can be changed at any time, but nothing is waiting on it.
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
mapping to bridge the two, by reading both tables.

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

| Decision-point tag | Mapped to stage(s) | Reasoning |
|---|---|---|
| `statutory` | `approvals` | Statutory pathway questions gate the approvals stage, which is where consents are obtained. |
| `design` | `design` | **Decided (§0.1).** The design stage leads the walk and owns these forks. |
| `planning` | `approvals` | **Decided (§0.3).** Planning is a department, not a construction stage; `dp.phasing` must be settled before anything is sequenced. |
| `enabling` | `enabling` | Direct match. |
| `civil` | `substructure`, `superstructure`, `envelope` | The three stages civil/structure owns per the `DOMAIN_KNOWLEDGE.md` §3 table. |
| `mep` | `mep_power`, `mep_cooling`, `fire_bms` | The three MEP-owned stages. Fire/BMS included because §3 lists it under services. |
| `procurement` | `procurement` | The procurement stage owns these forks, so they are raised before construction is sequenced (§1.1). |
| `fit_out` | `fire_bms`, `fit_out` | Fit-out plus fire/BMS, which are typically subcontracted fit-out-adjacent packages. |
| `commissioning` | `commissioning` | Direct match. |
| `handover` | `handover` | Direct match. |

No tag is currently unmapped. **If a tag is added in admin that this table does not cover, the
forks carrying it will never be raised** — the mapping should move into the admin decision-point
editor (`ADMIN_SPEC.md` §3) so that cannot happen silently. That remains outstanding.

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

### 1.2 Outstanding: does `dp.long_lead_unconfirmed` belong outside MEP?

It now fires at `procurement` only. Long-lead items outside MEP — structural steel, for instance —
therefore still do not trigger it independently. Is the single early procurement firing enough, or
does civil have long-lead exposure that should stop the flow on its own terms?

### 1.3 Outstanding: `frag.procurement.*` is future library data

The procurement stage has **no fragnets**, so the engine currently has nothing to instance for it.
It is consistent with the six other stages that also have none (`approvals`, `enabling`,
`envelope`, `fire_bms`, `fit_out`, `handover`), and full discipline depth is scheduled work — but
until a procurement fragnet exists, a procurement chain (RFQ -> award -> manufacture -> FAT ->
ship -> deliver) will not appear in the exported schedule, and long-lead plant will still appear
to arrive without a visible procurement path.

### 1.4 `dp.delivery_mode` fires per discipline — **decided, see §0.2**

Raised at every stage whose discipline it governs, not once globally. Final.

### 1.5 Design leads the walk — **decided, see §0.1**

`design -> approvals -> procurement -> ...`. Design forks, notably `dp.tier_topology` where N+1
vs 2N sets the equipment counts, are raised at `design` before procurement reasons about what to
order.

Early approvals that genuinely precede design (land, EC, DC-park allotment) are handled as
**logic links in the engine**, not by splitting the `approvals` stage. Decided; not revisited.

### 1.6 Outstanding: `frag.design.*`, and the IFC gate

The design stage has **no fragnets**, so nothing is instanced for it — same position as
procurement (§1.3). When they are written, the important part is not the activities but the
**gate**: design completion (IFC drawings issued) should gate procurement, because ordering to an
unfixed specification is how projects buy the wrong equipment. That is a cross-stage logic link
the engine will own, not something the stage list can express.

### Changing the mapping

The rows fixed by §0 are final. The rest is a contained change — one table in
`backend/app/reasoning/stages.py` — if a stage's forks turn out to be raised at the wrong point.
Longer term it belongs in the admin decision-point editor (`ADMIN_SPEC.md` §3) rather than in
code.

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
