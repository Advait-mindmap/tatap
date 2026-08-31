# INPUTS — exactly what you provide, how, and where

Claude Code builds the product. This is the complete list of what **you** supply. Four points:
setup, build, run-time, and the ongoing input that makes it an expert (the corpus).

## Quick map

| Phase | What you provide | Where |
|-------|------------------|-------|
| Setup | doc bundle, env vars, Base44 function, Railway | folder, `.env`, Base44, Railway |
| Build | the prompts, a public XER sample, verify library & compliance data | Claude Code chat, `/samples`, admin |
| Run | the project brief (free text or docs), decision-point answers | intake screen; decision prompts |
| Ongoing | the real-execution corpus (historical DC schedules/actuals) | admin -> knowledge base |

## 1. Setup (once)

- **Docs:** all files in the folder, keeping `/docs`.
- **Env vars:** copy `.env.example` to `.env`, fill it; set the same in Railway -> Variables.
  Keys: `LLM_PROVIDER`, `LLM_MODEL`, `BASE44_FN_URL`, `BASE44_SHARED_SECRET` (or `OPENAI_API_KEY`/
  `ANTHROPIC_API_KEY`), `DATABASE_URL`, `CONF_THRESHOLD`, `LIBRARY_VERSION`, `PROMPT_VERSION`,
  `CORPUS_VERSION`.
- **Base44 function (if LLM_PROVIDER=base44):** yours to own. It lives in the Base44 dashboard,
  which is its source of truth; it is not generated into this repo. In Base44: create the backend
  function, set `SHARED_SECRET`, copy the URL into `BASE44_FN_URL`. It must take
  `{prompt, schema, model}` and return the schema-conforming JSON — the contract the backend relies
  on is documented in `docs/BASE44_GATEWAY.md`. This spends your Base44 credits.
- **Railway:** New Project -> add PostgreSQL (gives `DATABASE_URL`) -> set Variables -> connect repo
  or `railway up`.

## 2. During the build

- **Prompts:** from `CLAUDE_CODE_GUIDE.md`, one task at a time, into the Claude Code chat.
- **Public XER sample (task 10):** download a PyP6XER/xerparser fixture `.xer` to
  `/samples/reference.xer` (git-ignored). No real P6 file needed to start.
- **Verify domain data (task 4 onward):** in the admin console, have your DC/compliance people
  confirm the city statutory pathway, equipment lead times and fragnet durations, and mark the
  compliance register approved. This is the one place accuracy cannot be delegated to the model.

## 3. Run-time (using the product)

- **The brief:** paste free text or upload documents (RFP, basis-of-design) at intake; or submit a
  structured brief like `sample_brief.json`. See `sample_raw_brief.md` for the free-text shape.
- **Decision-point answers:** as the simulation runs, it pauses at genuine forks (self-perform vs
  subcontract, owner-furnished equipment, grid position, tier, phasing). Answer them; it resumes.
- **Output:** watch the 2D flow and 3D/4D build; review the reasoning trail; export the P6 plan
  (XER, or P6 XML fallback) and the preview.

## 4. Ongoing — the corpus (what makes it an expert)

- Load your real-execution data into admin -> knowledge base: historical DC schedules, actuals,
  lessons, method statements. The more real precedent, the more expert and defensible the output.
  Start with 2-3 real DC schedules; grow it. Every simulation cites what it used.

## 5. Before final delivery / demo

- Confirm the target P6 **version**; export a small dummy from that version to `/samples` and re-run
  the XER-validity check, so it validates against their environment.
- Confirm their **coding standard / EPS** so the plan comes out in their conventions.

## What you never do

- Never commit `.env`, `/samples`, or `*.xer`.
- Never hand-edit a generated plan to "fix" it. Fix the library/corpus or answer the decision point
  and re-run, so the plan stays explainable and grounded.
