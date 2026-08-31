# DOMAIN KNOWLEDGE — Data Centres (from real executions)

What the product must know to be an expert. This is the substance behind the simulation. It is
held as **versioned data and a real-execution corpus** (managed in the admin console), not
hardcoded. Grounded in how DC projects are actually executed in India.

## 1. Grounding in real executions

The expert is only as good as its corpus. The knowledge base holds:
- Real DC project schedules (client historical), with their coding, durations and logic.
- Execution records and actuals (planned vs actual durations, slippage causes, rework).
- Lessons learned and method statements from delivered projects.
- Standards and codes (Uptime Tier, TIA-942, NBC 2016, ECBC, CEA regs, PESO).
- Per-city statutory pathways and authority maps.
- Equipment lead-time data for the Indian market.
Every simulation decision retrieves from this corpus and cites the source in its reasoning
trail. "Expert based on real executions" means: prefer real project precedent over generic
norms, and say which precedent was used.

## 2. The build, department by department

The simulation walks these in order; each hands to the next (full detail of the flow model is
in `SIMULATION_AND_REASONING.md`).

1. Client / BD — scope, tier, IT load (MW), city, RFS date, contract mode.
2. Design / Engineering — basis of design, redundancy topology, equipment schedule, IFC drawings.
3. Statutory / Liaison — the city approval pathway.
4. Procurement / SCM — long-lead strategy, owner-furnished vs contractor.
5. Planning / Controls — WBS, logic, durations, calendars.
6. Civil / Structure — shell and core.
7. MEP / Services — power train, cooling, fire, BMS.
8. Commissioning — L1 to L5, integrated systems test.
9. QA/QC — inspection and test plans, hold points.
10. HSE — permits, safety-critical method.
11. Contracts — delivery mode per discipline.
12. Client Cx / Certification — Uptime constructed-facility certification, handover.

## 3. Stages, materials, compliance (each activity is self-describing)

For every stage the simulation attaches: owning department, materials consumed, statutory and
quality gates, and the P6 representation.

> **Reconciliation note.** This table originally listed neither design nor procurement, while §2
> above lists Design/Engineering as department 2 and Procurement/SCM as department 4, and
> `SIMULATION_AND_REASONING.md` §2 walks "engineering → procurement → construction →
> commissioning". The three disagreed, and the build followed this table — so procurement forks
> (including the long-lead one that sets RFS) were not raised until the MEP stages, and design
> forks such as tier/redundancy topology were folded into approvals. **Design & engineering** and
> **Procurement & long-lead** rows have been added so all three sources agree.
>
> Both stages' activities span the programme; their position here reflects when they are planned
> and reasoned about, not when the work finishes. Design is placed after approvals on explicit
> instruction — note that §2 orders Design (2) *before* Statutory/Liaison (3), and in practice the
> basis of design usually must precede the consent applications, since drawings are what gets
> submitted for sanction. Flagged for confirmation in
> `docs/ASSUMPTIONS_AWAITING_VERIFICATION.md` §1.5.
>
> Neither stage has fragnets yet (`frag.design.*`, `frag.procurement.*` are future library data).
> Design completion — IFC drawings issued — should eventually **gate procurement**: you cannot
> order to a specification that is not fixed. See `ASSUMPTIONS_AWAITING_VERIFICATION.md` §1.1
> and §1.3.

| Stage | Owning dept | Key materials | Key gates |
|-------|-------------|---------------|-----------|
| Approvals & clearances | Liaison | — | EC (SEIAA), SPCB CTE, building sanction, commencement |
| Design & engineering | Design/Engineering | — | basis of design frozen, equipment schedule, IFC drawings issued |
| Procurement & long-lead | Procurement/SCM | (orders the long-lead plant consumed below) | vendor award, L1 factory acceptance test, delivery milestones |
| Enabling & site setup | Construction, HSE | temp works, fuel | BOCW, CLRA, PESO (HSD) |
| Substructure | Civil, QA/QC | cement, aggregates, rebar, formwork | IS 456, IS 2911, geotech |
| Superstructure | Civil/Struct, QA/QC | RMC, rebar, structural steel | IS 456, IS 800, cube test |
| Envelope | Construction | blocks, waterproofing, cladding | fire-rating, warranties |
| MEP power train | MEP, Procurement | transformers, switchgear, UPS, busway | CEA regs, CEIG energisation |
| MEP cooling | MEP, Procurement | chillers, CRAH, piping | pressure testing |
| Fire & BMS | Subcontract | suppression, VESDA, BMS | fire NOC, NBC 2016 |
| Fit-out & cabling | Construction | containment, structured cabling | TIA-942 |
| Commissioning | Cx, MEP, HSE | consumables | CTO, CEIG, final fire NOC |
| Handover & DLP | PM, QA/QC | — | occupancy certificate, warranties |

## 4. Expert moves the simulation must make

- **Size from the load.** IT load (MW) + tier + topology → number of data halls, electrical/UPS
  rooms, generators, chillers, CRAH — which drives the activity count.
- **Procurement-led critical path.** Long-lead gear (transformers, HV switchgear, generators,
  UPS, chillers, busway) usually drives RFS. Front-load it; tie construction to delivery.
- **Sequence for the tier.** Tier IV fault tolerance / concurrent maintainability (2N) constrains
  the build and commissioning sequence into independent paths.
- **Protect commissioning to L5.** L1 factory test → L2 site verification → L3 pre-functional →
  L4 functional → L5 integrated systems test (load banks, black-building, failure-mode). IST is
  the longest-pole risk; RFS ties to a passed IST.
- **Apply the exact city pathway.** Energisation cannot precede CEIG approval; occupancy cannot
  precede the final fire NOC.

## 5. India / city statutory pathway (data, configurable, not legal advice)

Held per city (`libraries/city_pathways/*.json`). National frame: state DC/IT policy status;
Environmental Clearance (SEIAA, EIA 2006) for large campuses; Consent to Establish/Operate
(State PCB); building plan sanction (city / industrial-area authority — CIDCO/MIDC, SIPCOT,
TSIIC, YEIDA, KIADB); fire NOC (State Fire Services, NBC 2016 Part 4); HT installation &
energisation (CEA regs; State Electrical Inspectorate / CEIG); diesel (HSD) storage (PESO);
water/groundwater (CGWA/municipal); height/aviation (AAI); telecom/infra registration (DoT);
energy code (ECBC/BEE); Uptime Tier certification (non-statutory quality gate). Each becomes a
gating milestone with a finish-to-start constraint. Exact set varies by city/parcel/project and
is confirmed with the client's compliance team.

## 6. The decision-point library (where thought breaks)

These are the forks the simulation must recognise and stop at. Curated from real executions,
plus dynamic detection when input is missing or confidence is low (see `SIMULATION_AND_REASONING.md`).

| Decision point | Why it stops the flow | What it changes |
|----------------|-----------------------|-----------------|
| Self-perform vs subcontract (per discipline) | Changes the activity structure entirely | Full fragnet vs interface package |
| Owner-furnished equipment (OFE) or contractor-supplied | Changes who controls delivery dates | Delivery milestone as constraint vs procurement fragnet |
| Grid / power position | Substation build vs feeder-ready shifts the critical path | Energisation sequence and long-lead |
| Tier / redundancy topology ambiguity | N+1 vs 2N changes equipment counts and sequencing | Counts + concurrent-maintainability logic |
| Greenfield vs brownfield (live facility) | Live-hall constraints and permits | Safety holds, concurrent-ops sequencing |
| Phasing (single vs hall-by-hall) | Changes handover and commissioning structure | RFS milestones, phased Cx |
| Long-lead assumption unconfirmed | Lead time drives RFS; a guess is dangerous | Procurement placement, critical path |
| City statutory pathway unconfirmed | Wrong gate = wrong plan | Compliance milestones |

## 7. HITL and safety (Tier-1 = mandatory sign-off, blocks export)

| Safety-critical activity | Why |
|--------------------------|-----|
| HV/MV energisation & live electrical testing | Arc flash / electrocution; lock-out/tag-out |
| Integrated systems test under load / on generator | Live power + rotating plant; fire/burn |
| Gas fire-suppression discharge test | Asphyxiation in sealed rooms |
| Generator & fuel-system commissioning | Fire / explosion |
| Work in or next to live data halls (brownfield) | Concurrent operations beside energised IT |

The engine inserts safety hold points and permit-to-work predecessors, marks these activities,
and blocks P6 export without a named sign-off (managed in admin).
