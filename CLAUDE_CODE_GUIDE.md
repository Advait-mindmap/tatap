# Driving Claude Code — step by step (full product)

Build from an empty folder to the delivered product. Paste prompts in order, one task at a time,
from the 20-task backlog in `docs/ARCHITECTURE_AND_BUILD.md` §4. Let each finish, run its test,
review, then continue. Do not paste the whole backlog at once.

## 0. Setup (once)

1. Put the whole bundle into the folder, keeping `/docs`.
2. Read `INPUTS.md` (what you provide, where) and copy `.env.example` to `.env`.
3. Open the folder in VSCode with Claude Code.

## 1. Kickoff prompt (paste first, verbatim)

```
Read CLAUDE.md and every file in /docs fully before doing anything. This is a delivered
product (an expert data-centre build simulator and planner), not a demo. Data centres only.

Tell me back, in your own words:
- what "simulate, then plan" means here and why the LLM must not free-generate the schedule,
- how stop-and-ask works and why it is the core differentiator,
- what the three linked views are (2D flow, 3D/4D build) and how they relate to the simulation,
- what the admin console must contain at the same depth as the planner,
- the Tier-1 safety rule.

Do NOT write code yet. We build strictly in the order of docs/ARCHITECTURE_AND_BUILD.md section 4,
one numbered task at a time, stopping after each for my review. Confirm you will not reduce scope:
if something is hard (3D/4D, scale), you build it as its own task, you do not cut it. Acknowledge.
```

Only proceed once its summary is right. If it says the model "generates the schedule," or it
proposes skipping the 3D/4D or admin, correct it before any code.

## 2. Build prompts

Give one per task, in order. Template for each:

```
Do Task N only, from docs/ARCHITECTURE_AND_BUILD.md section 4. Follow the relevant /docs spec
(named there). End with a passing test and show me the diff. Do not start Task N+1.
```

Task-specific notes to add:

- **Task 3 (LLM layer):** "Also give me the Base44 backend-function file to paste into Base44.
  Default provider Base44; OpenAI/Anthropic as adapters. All return schema-valid JSON."
- **Task 4 (corpus + libraries):** "Load the seed corpus and libraries. Flag clearly anything you
  invented (durations, lead times, city pathway) so my domain/compliance team verifies it in admin.
  Do not treat model-generated domain data as truth."
- **Task 5 (intake):** "Test on sample_raw_brief.md: assert it extracts tier, load, gensets=OFE,
  cites provenance, and asks for anything missing."
- **Task 6–8 (reasoning, engine, simulator):** "The LLM reasons within the corpus/libraries only;
  the engine instances activities. The simulator must raise decision points (curated + dynamic) and
  stop rather than guess. Emit the event stream over /ws."
- **Task 9 (SimulationOutput):** "Add a golden test asserting the output structure and that the
  expected decision points fire for the seed brief."
- **Task 10 (P6):** "Build against a PUBLIC XER fixture at /samples/reference.xer (recommend one to
  download). Add a P6 XML fallback. Write trail_ref into UDFs. Later I will drop a version-matched
  real XER for final validation."
- **Tasks 11–16 (2D then 3D/4D):** build in the order in VISUALIZATION_SPEC.md section 6. Verify
  progressive draw, hover-highlight, decision prompts, the 4D scrubber, and the linked 3D<->2D highlight.
- **Tasks 17–18 (admin):** full depth per ADMIN_SPEC.md — fragnet editor, decision-point + compliance
  management, safety sign-off gating export, reasoning-trail audit, roles.
- **Tasks 19–20 (scale + hardening):** full discipline + commissioning depth in the libraries (this is
  what produces the large activity counts), streamed XER, virtualised 2D, instanced 3D, reproducible
  mode, performance pass, end-to-end on the headline project.

## 3. Working habits

- One task at a time; make it show the diff and a passing test before you accept.
- Periodically audit the core rule: "Show me every place an activity/duration/logic link is created,
  and confirm each comes from /app/libraries or the corpus, not model free-text."
- Audit stop-and-ask: "Show me the decision points that fired for this brief and confirm none were
  auto-answered below CONF_THRESHOLD."
- Domain data (corpus, city pathway, lead times, fragnet durations) is human-verified in admin. Have
  your DC/compliance people check it.
- Do not let it de-scope the 3D/4D, the admin, or the simulation to "save time." Those are the product.
- Secrets and client data never get committed.

## 4. What only you can provide (see INPUTS.md)

- Base44 URL + shared secret (or an OpenAI/Anthropic key); Railway account + Postgres.
- A public XER fixture for task 10; a version-matched real XER before final delivery.
- The real-execution corpus (your/Tata historical DC schedules and actuals) — this is what makes it
  a genuine expert; the sooner it is loaded, the better every simulation gets.
- Domain sign-off on the library and compliance data.
