# CLAUDE.md

Repo guide for Claude Code. Read this and ALL of `/docs` before writing any code. This is a
delivered product, not a demo. Data centres only.

## What this is

An expert data-centre construction planner that **simulates the build**, stops at real decision
points to ask the user, shows the simulation as 2D process flow and a 3D/4D model that builds up
stage by stage, and turns it into a Primavera P6 plan. Ships with an admin console of equal depth.
Full product spec: `docs/PRODUCT_SPEC.md`.

## The rules everything depends on

1. **Simulate, then plan.** The core is a build simulation (a process graph walked stage by
   stage). The P6 schedule and the 2D/3D views are all projections of that one simulation.
   See `docs/SIMULATION_AND_REASONING.md`.
2. **The LLM reasons within real executions; it never free-generates the schedule.** It
   classifies and reasons over the corpus and libraries; a deterministic engine instances the
   activities, logic, durations and dates. It must never emit an activity/duration/logic/count/
   compliance not present in the libraries.
3. **Stop where thought breaks.** At genuine forks (self-perform vs subcontract, owner-furnished
   equipment, grid position, tier/topology, phasing, unconfirmed long-lead or statutory pathway),
   or when confidence < `CONF_THRESHOLD`, it raises a decision point and asks. It never guesses
   past a genuine fork. This is the product's differentiator — do not weaken it.
4. **Grounded in real executions.** Reasoning retrieves from a corpus of real DC projects and
   cites the precedent used. `docs/DOMAIN_KNOWLEDGE.md`.
5. **Tier-1 safety blocks export** until a human signs off (`docs/DOMAIN_KNOWLEDGE.md` §7).
6. **Depth in admin too.** The admin console carries the same domain depth as the planner
   (`docs/ADMIN_SPEC.md`).

## Stack

- Backend: Python 3.12, FastAPI, WebSocket/SSE for the simulation stream, pytest.
- Frontend: React + TypeScript (Vite). 2D flow: React Flow. 3D/4D: react-three-fiber + three.js + drei.
- LLM: provider-agnostic (`app/llm`), default `Base44Adapter` (backend-function webhook wrapping
  `Core.InvokeLLM`, using Base44 credits); alternates OpenAI / Anthropic. All return schema-valid JSON.
- DB / RAG: Railway Postgres + `pgvector`.
- Deploy: Railway via Dockerfile, bind `$PORT` on `0.0.0.0`, `/health`.

## Repo map

See `docs/ARCHITECTURE_AND_BUILD.md` §1 for the full backend/frontend/data layout.

## Determinism stance

Default mode maximises detail and realism. Reproducible mode (temp 0 + caching keyed by
`sha256(brief + decisions + prompt_version + library_version + corpus_version)`) is an optional
per-project setting, not the default. The engine's assembly is deterministic either way.

## Guardrails — do NOT

- Do NOT let the model output activities/durations/logic/counts/compliances directly. The engine
  instances them from `/app/libraries` and the corpus.
- Do NOT guess past a genuine decision fork. Raise a decision point and ask.
- Do NOT hardcode compliance or city pathways; they are versioned data, verified in admin.
- Do NOT finalise Tier-1 safety activities without a human sign-off; block export.
- Do NOT reduce scope. If something is hard (3D/4D, scale), build it as a first-class task per the
  build plan, do not cut it.
- Do NOT commit secrets, `/samples`, or `*.xer` (git-ignored).

## Build order

Follow the phased plan and the ordered task backlog in `docs/ARCHITECTURE_AND_BUILD.md` §3–§4,
driven by the prompts in `CLAUDE_CODE_GUIDE.md`. Stop and verify each task.
