# SIMULATION & REASONING

How the product simulates the build, reasons like an expert, stops where thought breaks, and
turns the simulation into a plan. This is the core engine. Read with `DOMAIN_KNOWLEDGE.md`.

## 1. The principle

The product does not ask a model to "write a 40k-line schedule." It **runs a simulation of the
build** as a process graph, using a domain-expert reasoning loop grounded in real executions.
The plan (the P6 schedule) and the visuals (2D flow, 3D/4D model) are all projections of that
one simulation. The LLM reasons and classifies within the corpus and libraries; a deterministic
engine assembles activities, logic and dates. The simulation is the product; the schedule is one
of its outputs.

## 2. The simulation model

The build is represented as a directed graph:
- **Nodes:** stages, work packages, activities, milestones, compliance gates, quality hold
  points, and **decision points**.
- **Edges:** dependencies (FS/SS/FF/SF with lag), material-delivery links, gate constraints.

The **simulator walks the graph** in execution order (engineering → procurement → construction →
commissioning), expanding each stage from the domain libraries and the corpus, emitting an event
stream as it goes:
`stage_started`, `package_expanded`, `activity_added`, `gate_inserted`, `decision_needed`,
`decision_resolved`, `stage_completed`, `simulation_completed`.

That event stream is what makes the flow **watchable one by one**: the 2D flow draws each node as
it is emitted, and the 3D model builds the corresponding zone/system as its stage starts (see
`VISUALIZATION_SPEC.md`). The same completed graph is what exports to P6.

## 3. The expert reasoning loop (per stage)

For each stage the simulator runs this loop:
1. **Retrieve** the relevant precedent from the real-execution corpus and the domain libraries
   (templates, norms, lead times, city pathway).
2. **Reason** (LLM, grounded, schema-constrained): which work packages apply, how they sequence,
   which materials and gates attach, what the durations are, citing the precedent used.
3. **Check for a decision point** (Section 4). If found and unresolved, **pause and ask**.
4. **Expand** the packages into activities via the deterministic engine (instance fragnets, wire
   logic, apply durations/calendars, attach department/materials/gates/hold-points).
5. **Emit** the events and record a **reasoning-trail entry** per element.

## 4. Stop-and-ask: where the flow of thought breaks

This is the differentiator. The simulator detects decision points two ways:
- **Curated:** the decision-point library in `DOMAIN_KNOWLEDGE.md` §6 (self-perform vs
  subcontract, OFE, grid position, tier ambiguity, phasing, unconfirmed long-lead/pathway).
- **Dynamic:** whenever a required input is missing, two library precedents conflict, or the
  model's confidence is below `CONF_THRESHOLD`, it raises a decision point rather than guessing.

When a decision point fires, the simulator **halts that branch**, emits `decision_needed` with:
`{ id, question, why_stuck, options[], impact, blocking }`, and surfaces it to the user (live in
the UI, and visible as a highlighted node in the flow). It does **not** invent an answer. On the
user's answer it records the decision, resumes the branch, and every downstream element cites the
decision it depended on.

Non-blocking uncertainties (a durations estimate, a soft assumption) are not full stops; they are
flagged Tier-2 for later confirmation so the simulation can continue. Only genuine forks halt.

## 5. The reasoning trail

Every node carries a trail entry so the simulation is cross-verifiable:
`{ ref_id, stage, decision, why, sources:[corpus/library ids], confidence, decided_by, hitl_tier }`.
`sources` cites the real-execution precedent or the standard used. Trail entries travel into P6 as
UDFs and drive the hover-to-explain in the UI.

## 6. The expert system prompt (verbatim; fill bracketed tokens at runtime)

```
ROLE
You are a senior data centre delivery planner with 20+ years of real project execution in
India. You think in how projects are actually built and commissioned, grounded in the retrieved
corpus of real executions. Experts in: Uptime Tier / TIA-942; NBC 2016; ECBC; CEA/CEIG; PESO;
state statutory pathways; DC commissioning L1-L5 and integrated systems test; Primavera P6.

PRIME DIRECTIVE
Simulate the build of this data centre the way an experienced team would execute it, stage by
stage, and reason each step so a senior planner would judge it correct and defensible. Prefer
real-execution precedent from the corpus over generic norms, and say which precedent you used.

HARD BOUNDARIES
1. You REASON and CLASSIFY within the retrieved corpus and libraries. You DO NOT invent
   activities, durations, logic, equipment counts or compliances. Those come from the libraries;
   the engine instances them.
2. When the flow of thought genuinely cannot continue without a human decision (delivery mode,
   owner-furnished equipment, grid position, tier/topology, phasing, unconfirmed long-lead or
   statutory pathway), or confidence is below [CONF_THRESHOLD], STOP and raise a decision_point
   with {question, why_stuck, options, impact}. Never guess past a genuine fork.
3. Output ONLY the JSON in the OUTPUT SCHEMA. Every element carries a reasoning-trail entry with a
   citation to a retrieved source and a confidence.

PER-STAGE LOOP (repeat for each stage in execution order)
- Retrieve precedent for this stage from the corpus + libraries.
- Determine the work packages, sequence, materials, gates and durations, citing precedent.
- Screen for decision points; if any is unresolved, emit decision_needed and stop this branch.
- Hand the expanded packages to the engine and emit the stage's events + trail entries.

CONTEXT (injected at runtime)
Confirmed brief: [BRIEF_JSON]
Resolved decisions so far: [DECISIONS_JSON]
Retrieved corpus + libraries: [RAG_CHUNKS_WITH_IDS]
DC work-package taxonomy: [DC_TAXONOMY]
Client coding standard / EPS: [CODING_STANDARD]
```

A smaller extraction prompt runs at intake: same role and boundaries, its only job is to read the
raw brief/documents into the Brief schema with a provenance citation per field and questions[] for
anything missing.

## 7. Output contract (one simulation → many projections)

```
{
  "project_meta": {...}, "questions": [], "decisions": [ { id, question, answer, impact } ],
  "flow": { "nodes": [ { id, kind, stage, label, dept, trail_ref, zone_id, start, finish } ],
            "edges": [ { from, to, type, lag, kind } ] },
  "statutory_pathway": [...], "equipment_counts": [...], "long_lead_register": [...],
  "activities": [ { id, wbs_id, name, type, duration_days, calendar, dept_code, delivery_mode,
                    predecessors:[{id,type,lag}], resources:[...], hold_points:[...],
                    zone_id, stage, hitl_tier, safety_flag, trail_ref } ],
  "commissioning": [ { level, name, predecessors, is_IST } ],
  "zones": [ { id, name, kind, stage, geometry_ref } ],   // for the 3D model
  "reasoning_trail": [ { ref_id, stage, decision, why, sources, confidence, decided_by, hitl_tier } ],
  "quality": { dcma_summary, governance_complete }, "flags": [...]
}
```

`flow` drives the 2D view; `zones` + activity `stage`/`zone_id` drive the 3D/4D model; `activities`
export to P6; `reasoning_trail` powers hover-to-explain. `decisions` records every stop-and-ask.

## 8. Determinism as an optional mode

Default mode maximises detail and realism (temperature up, richer exploration). An optional
**reproducible mode** (temperature 0 where supported + caching keyed by
`sha256(confirmed_brief + decisions + prompt_version + library_version + corpus_version)`) yields
identical output on re-run, for when a client asks "why did this change." Set per project in admin.
The engine's assembly is deterministic either way; the variability is only in the reasoning step.
