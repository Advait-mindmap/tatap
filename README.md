# DC Build Simulator & Planner (end product)

An expert data-centre construction planning product. It ingests a project brief (free text
or documents), **simulates the build the way an experienced team would execute it**, pausing
at the real decision points to ask the user, then turns that simulation into a Primavera P6
plan. The simulation is watchable: a 2D process-flow view and a 3D/4D model that build up
stage by stage as planning proceeds, with hover-to-highlight of the specific flows. It ships
with an **admin console of equal depth** for the knowledge base, libraries, compliance,
decision points, sign-offs and audit.

Scope: **data centres only**, for now. This is the product being delivered to the client to
use, not a demo.

## What makes it an expert, not a text generator

- **Grounded in real executions.** Reasoning is retrieved from a corpus of real DC project
  schedules, actuals and lessons, and every output cites its source.
- **It simulates, then plans.** It walks the build department by department, stage by stage,
  dependency by dependency, and the plan is the product of that simulation.
- **It knows where thought breaks and stops.** At genuine forks (self-perform vs subcontract,
  owner-furnished equipment, grid position, tier ambiguity) it pauses and asks, instead of
  bulldozing past with an invented answer. This is the core differentiator.
- **It is visual.** The flow and the build are viewable and cross-verifiable in 2D and 3D/4D,
  not just a list of activities.

## Documentation set

Read in this order.

| File | What it covers |
|------|----------------|
| `docs/PRODUCT_SPEC.md` | The product: users, the end-to-end experience, success criteria |
| `docs/DOMAIN_KNOWLEDGE.md` | DC domain from real executions: stages, compliances, equipment, commissioning, decision points, safety |
| `docs/SIMULATION_AND_REASONING.md` | The simulation model, stop-and-ask, the expert prompt, the reasoning trail, I/O contracts |
| `docs/VISUALIZATION_SPEC.md` | 2D process-flow, 3D/4D build simulation, progressive animation, hover-highlight |
| `docs/ADMIN_SPEC.md` | The admin console, at the same depth as the planner |
| `docs/ARCHITECTURE_AND_BUILD.md` | Technical architecture, data model, Railway deploy, phased build plan + task backlog |
| `CLAUDE.md` | Repo rules Claude Code must hold to |
| `INPUTS.md` | Exactly what you provide, how, and where |
| `CLAUDE_CODE_GUIDE.md` | The ordered prompts to drive the build |
| `.env.example`, `sample_raw_brief.md`, `sample_brief.json` | Config template and seed inputs |

## Build order

Full-product build, not a cut-down demo. Follow the phased plan and task backlog in
`docs/ARCHITECTURE_AND_BUILD.md`, driven by the prompts in `CLAUDE_CODE_GUIDE.md`.

## Hosting & LLM

Railway. LLM behind a provider-agnostic layer (default Base44 credits; swappable to OpenAI /
Anthropic). See `INPUTS.md`.
