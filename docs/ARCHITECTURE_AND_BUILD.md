# ARCHITECTURE & BUILD PLAN

Technical architecture, data model, deployment, and the phased build plan with an ordered task
backlog. Full product, data centres only. Hosting: Railway. LLM: provider-agnostic (default Base44).

## 1. Architecture

```
Frontend (React + TS, Vite)
  - Planner app: intake, live simulation, 2D flow (React Flow), 3D/4D (react-three-fiber),
                 decision-point prompts, reasoning-trail viewer, P6 export
  - Admin app (role-gated): corpus, libraries, decision points, compliance, sign-off, audit
  - Realtime: WebSocket/SSE for the simulation event stream

Backend (Python 3.12, FastAPI)
  /api        planner + admin REST, /ws simulation stream
  /schemas    pydantic: RawBrief, Brief, Decision, SimulationOutput, FlowNode/Edge, Zone,
              Activity, TrailEntry, Flag, corpus/library/user models
  /llm        provider-agnostic client + adapters (base44 default, openai, anthropic)
  /intake     free-text/doc extraction -> Brief (cited)
  /simulator  the build simulation: graph walk, per-stage expert loop, event stream,
              decision-point detection (curated + dynamic stop-and-ask)
  /engine     deterministic assembly: fragnet instancing, logic, CPM, durations, governance,
              Cx ladder, zone/geometry generation for the 3D model
  /rag        corpus ingestion + retrieval (pgvector)
  /xer        P6 XER read/write (public sample) + P6 XML fallback
  /libraries  versioned DATA: templates/fragnets, norms, lead-times, city pathways, tier rules,
              decision-point library, safety register
  /admin      corpus/library/decision/compliance/user/version/audit management

Data (Railway Postgres + pgvector)
  projects, briefs, decisions, simulations(runs), flow_nodes, flow_edges, zones, activities,
  trail_entries, libraries(versioned), compliance_registers, corpus_docs(+embeddings),
  users, roles, signoffs, audit_log, cache
```

The simulator is the heart: it produces one `SimulationOutput` that the 2D view, the 3D/4D view and
the P6 export all project from. The LLM reasons within the corpus/libraries; the engine assembles.

## 2. Deployment (Railway)

- Backend service: Dockerfile, uvicorn on `$PORT` host `0.0.0.0`, `/health`, WebSocket enabled.
- Frontend: built and served (static service or from the backend); point it at the API/WS URL.
- Postgres plugin with `pgvector` enabled via migration.
- Env vars in Railway Variables (see `.env.example` / `INPUTS.md`).
- Volume for generated XER/XML and the git-ignored public XER sample.
- Deploy from GitHub (auto) or `railway up`.

## 3. Phased build plan (the target is the full product, not a demo)

Build in this order because each phase de-risks the next. Each phase ends working and reviewable.

- **Phase 0 — Foundation.** Repo scaffold (backend + frontend), Dockerfile, railway.json, /health,
  Postgres + pgvector, schemas, the provider-agnostic LLM layer + Base44 backend function, CI.
- **Phase 1 — Intake & expert reasoning.** Free-text/doc intake with cited extraction; the corpus
  + libraries loaded; the per-stage expert reasoning loop grounded in the corpus.
- **Phase 2 — The simulation engine.** The build graph, the deterministic assembly, the event
  stream, and stop-and-ask decision-point detection (curated + dynamic). Produces a full
  `SimulationOutput` for a real DC brief.
- **Phase 3 — P6 output.** XER read/write against a public sample + P6 XML fallback; trail into UDFs;
  validity test. (Version-matched real XER for final validation later — `INPUTS.md`.)
- **Phase 4 — 2D flow visualization.** React Flow view: completed graph, then progressive draw from
  the live stream, hover-highlight, click-to-trail, decision-point prompts.
- **Phase 5 — 3D/4D visualization.** Schematic 3D from zones; 4D time scrubber and stage state; then
  linked hover-pick binding 3D and 2D; then progressive 3D build synced to the live simulation.
- **Phase 6 — Admin console.** Corpus, libraries (fragnet editor), decision points, compliance,
  safety sign-off, versioning, audit — at full depth.
- **Phase 7 — Scale & hardening.** Full discipline coverage and full commissioning depth in the
  libraries (this is what produces tens of thousands of activities); streamed XER; virtualised 2D;
  instanced 3D; reproducible-mode; performance pass.

## 4. Ordered task backlog

Execute top to bottom; stop and verify each. Give these to Claude Code one at a time
(`CLAUDE_CODE_GUIDE.md`).

1. Backend + frontend scaffold; Dockerfile; railway.json; /health; deploy hello-world to Railway.
2. Postgres + pgvector migration; the full data model (§1) as pydantic + tables.
3. Provider-agnostic LLM layer + Base44 adapter (+ backend function) + OpenAI/Anthropic stubs; smoke test.
4. Corpus ingestion + pgvector retrieval; load seed corpus + libraries (with LIBRARY_VERSION).
5. Intake: free-text/doc -> Brief with provenance + questions; /intake endpoint; test on sample_raw_brief.md.
6. Per-stage expert reasoning loop (LLM, grounded, schema-constrained), citing corpus.
7. Deterministic engine: fragnet instancing, logic, durations, governance, Cx ladder, zone generation.
8. Simulator: graph walk + event stream + decision-point detection (curated + dynamic stop-and-ask); /ws stream.
9. SimulationOutput assembled end to end for a real DC brief; golden test (structure + decisions).
10. XER read/write vs public sample + P6 XML fallback; trail into UDFs; validity test.
11. 2D flow (React Flow): completed graph; hover-highlight; click-to-trail.
12. 2D progressive draw from /ws; decision-point prompt panel; play/step/replay.
13. 3D schematic model from zones (react-three-fiber); stage colour-coding.
14. 4D time scrubber + per-zone stage state changes.
15. Linked hover: 3D pick <-> 2D highlight (shared highlight state).
16. Progressive 3D build synced to the live simulation.
17. Admin: corpus + library (fragnet editor) + decision-point + compliance management; versioning.
18. Admin: Tier-1 safety sign-off gating export; reasoning-trail audit; users/roles.
19. Scale: full discipline + commissioning depth in libraries; streamed XER; virtualised 2D; instanced 3D.
20. Reproducible mode; performance pass; end-to-end on the headline project.

## 5. Guardrails (unchanged, enforced throughout)

- The LLM reasons/classifies within the corpus and libraries; it never invents activities, durations,
  logic, counts or compliances — the engine instances those from data.
- Genuine decision forks stop and ask; they are never guessed past.
- Tier-1 safety items block export until signed off.
- Compliance and library data are human-verified in admin, not trusted from the model.
- Secrets, `/samples`, `*.xer` are git-ignored.
