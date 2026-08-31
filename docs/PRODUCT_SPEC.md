# PRODUCT SPEC — DC Build Simulator & Planner

The product, its users, the end-to-end experience, and the success criteria. Data centres
only. This is the delivered product.

## 1. What it is

A domain-expert data-centre construction planner. A planner or PM gives it a project brief;
it simulates the build the way a seasoned team would execute it, stopping to ask at real
decision points; it shows that simulation live as 2D process flow and a 3D/4D model that
grows stage by stage; and it produces a Primavera P6 plan plus a reviewable preview. An admin
console of equal depth manages the domain knowledge that makes it expert.

## 2. Users and roles

- **Planner / PM (primary).** Starts a project, answers the decision-point questions, watches
  the simulation, reviews the flow and 3D build, exports the P6 plan.
- **Reviewer / lead.** Cross-verifies the simulation and the reasoning trail; signs off Tier-1
  safety items before export.
- **Admin.** Manages the knowledge base, the real-execution corpus, the template and decision
  libraries, the compliance registers, users and versions, and audits reasoning trails.
  (See `ADMIN_SPEC.md` — the admin side carries the same domain depth as the planner side.)

## 3. The end-to-end experience

1. **Brief intake.** Paste the brief or upload documents (RFP, basis-of-design). The system
   extracts a structured brief with a citation per field and lists what it still needs.
2. **Simulation begins and is watchable.** It walks the build department by department and
   stage by stage. As it proceeds, the **2D process-flow** draws itself node by node and the
   **3D model builds up** so you can see which construction stage it is in.
3. **It stops at the real decision points.** When the flow of thought genuinely cannot
   continue without a call (self-perform vs subcontract, owner-furnished equipment, grid
   position, tier/redundancy ambiguity, missing scope), it pauses, explains why it is stuck,
   asks the specific question, and resumes once answered. Every pause is visible in the flow.
4. **Review and cross-verify.** The completed simulation is a viewable flow diagram and a
   3D/4D model. Hovering a section highlights the specific flows tied to it (its predecessors,
   successors, critical path, compliance gates and material links). Every element has a
   reasoning trail: what, why, and the real-execution source it is grounded in.
5. **Plan out.** Export a Primavera P6 schedule (XER, with a P6 XML fallback) in the client's
   coding standard, and a shareable preview. The reasoning trail travels into P6 as UDFs.

## 4. Success criteria (how it is judged)

Not line count alone. The bar is expert-level fidelity:

- **Quality.** Every activity is correct and defensible: right sequence, right logic, right
  durations, no plausible-looking filler.
- **Depth of process understanding.** It reasons like someone who has delivered data centres:
  engineering drives procurement drives construction drives commissioning; long-lead gear bends
  the programme; energisation waits on the electrical inspectorate. The *why* is visible.
- **Faithful flow simulation.** Reading the output feels like watching the job get planned,
  department to department, decision to decision, not like reading a static list.
- **Stops at the right forks and asks.** It surfaces exactly the decisions a human must make,
  and only those, rather than inventing answers. This is the sharpest differentiator versus a
  raw model that never stops to ask.
- **Visible and cross-verifiable.** The simulation can be watched, replayed and interrogated in
  2D and 3D/4D, so a reviewer can trust it.
- **Depth carried through to admin.** The console that manages the domain knowledge is as
  detailed as the planner, not a thin settings page.

The line-count depth (tens of thousands of activities at full scale) is a natural output of
this fidelity across all disciplines and the full commissioning ladder, not the goal in itself.

## 5. Scope now / next

- **Now:** data centres, end to end (intake → simulation → decision points → 2D + 3D/4D →
  P6 plan → admin). Full discipline coverage and full commissioning depth.
- **Not now:** other asset types (road, bridge). The architecture keeps them possible later by
  swapping the domain libraries, but they are out of scope for this delivery.

## 6. Determinism stance

Default mode favours **maximum detail and realism**. Reproducibility (identical output on
re-run) is an **optional mode**, not the default, useful when a client later asks "why did
this change." It is a lever the admin can set per project, not a constraint on the product.
