# ADMIN SPEC — the management console (same depth as the planner)

The admin side is where the domain expertise is curated and governed. It is not a settings page;
it carries the same domain depth as the planner, because the quality of every simulation depends on
what lives here. Role-gated (admin, reviewer). Same backend, same data model.

## 1. Knowledge base & real-execution corpus

- Ingest and manage the corpus that makes the product an expert: real DC project schedules,
  execution records/actuals, lessons learned, method statements, standards.
- Per document: source, project, tier/load, city, date, tags, and embedding status (pgvector).
- Review what a simulation retrieved and cited, and curate the corpus (promote good precedent,
  retire stale). This is the lever that raises expertise over time.

## 2. Template & fragnet libraries (the depth lives here)

- Edit the work-package **fragnet templates** at full structural depth: activities, logic
  (FS/SS/FF with lag), durations rules, resource and cost loading, materials consumed, quality
  hold points. This is the same structure the engine instances, exposed for editing.
- Manage the productivity norms and the equipment lead-time library.
- Everything is **versioned** (`LIBRARY_VERSION`); a simulation records the version it used, so a
  plan can always be traced to the library that produced it.

## 3. Decision-point management

- Curate the decision-point library (`DOMAIN_KNOWLEDGE.md` §6): the question text, why it stops the
  flow, the options, and the impact each option has on the plan.
- Tune the dynamic detection: the confidence threshold and the conflict rules that raise a
  decision point. This directly controls the stop-and-ask behaviour.

## 4. Compliance registers (per city)

- Manage the city statutory pathways: approvals, authorities, the stage each gates, and how it maps
  into P6. Confirm/verify entries (this is the human-verified data). Mark a register as
  compliance-approved before it can be used in a live plan.

## 5. Governance: HITL, safety, sign-off

- Manage the Tier-1 safety register (`DOMAIN_KNOWLEDGE.md` §7). Tier-1 rules are HSE-owned; changes
  require an HSE-role sign-off.
- Review and sign off Tier-1 safety items on a specific plan; export to P6 is blocked until signed.
- Review Tier-2 flags and the decision log.

## 6. Projects, versions, audit

- List and manage projects and their simulation runs; compare runs; re-run in reproducible mode.
- Full **audit of the reasoning trail**: for any element, what/why/source/confidence/decided-by,
  and which corpus, library and prompt versions produced it. This is what lets a reviewer defend the
  plan to the client.

## 7. Users & roles

- Roles: admin, reviewer, planner. Permissions gate corpus/library edits, compliance approval,
  safety sign-off and export.

## 8. Admin UI

- Same React app, role-gated admin routes (or a sibling admin app on the same API). CRUD over the
  above with the fragnet/decision editors carrying real domain structure, not flat forms. Reuses the
  2D flow component to preview a template's fragnet and the reasoning-trail viewer for audit.
