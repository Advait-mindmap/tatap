# BASE44 GATEWAY — the verified contract

The default LLM provider is a Base44 backend function wrapping `Core.InvokeLLM`, called over
HTTPS by [`backend/app/llm.py`](../backend/app/llm.py).

> **The source of truth for this function is the Base44 dashboard, not this repo.**
> Its code lives in the Base44 app and is edited there. This repo previously carried a Python
> file (`backend/app/base44_function.py`) that claimed to be that function; it was not — it was
> never deployed, and its contract disagreed with the live endpoint. It has been deleted rather
> than corrected, because nothing in this repo can be trusted to mirror code we cannot see.
>
> Everything below was established **empirically, from the endpoint's observed responses**. It
> records behaviour, not implementation. If the function is edited in Base44, this document can
> go stale without warning — the live tests in
> [`backend/tests/test_llm_live.py`](../backend/tests/test_llm_live.py) are what will catch that.

Verified 2026-08-31 against the deployed function.

## Endpoint

| | |
|---|---|
| URL | `BASE44_FN_URL` (`.env`) — an app-scoped path of the form `https://<app>.base44.app/functions/<functionName>`; ours is the `llmClassify` function |
| Method | `POST` |
| Auth | shared secret in the `x-shared-secret` request header |
| Edge | Cloudflare, in front of `base44-dispatcher-production.base44.workers.dev` |

## Request

```jsonc
{
  "prompt": "…",        // string,  REQUIRED
  "schema": { … },      // object,  REQUIRED — JSON Schema for the reply
  "model":  "…"         // string,  optional (gateway applies its own default if omitted)
}
```

Required headers: `Content-Type: application/json`, `x-shared-secret: <secret>`, and a
`User-Agent` that is **not** `Python-urllib` (see [Cloudflare](#cloudflare-edge-filtering)).

Verified details:

- **There is no `system`/`user` split.** A body carrying `system` and `user` instead of `prompt`
  is rejected. `llm.py` folds system+user into one `prompt` string, joined by a blank line.
- **The key is `schema`, not `response_json_schema`.** A body using the latter is rejected with
  `{"error":"Missing or invalid \"schema\" (object)"}`.
- **Nested schemas work.** Arrays of objects, `required`, and `minItems` were all honoured in the
  returned JSON.

## Response

`200` with the schema-conforming JSON object **bare in the body** — not wrapped in any envelope,
no `ok`/`data`/`content` fields:

```json
{"msg": "hello"}
```

## Observed failure modes

| Condition | Status | Body |
|---|---|---|
| `prompt` missing or not a string | `400` | `{"error":"Missing or invalid \"prompt\" (string)"}` |
| `schema` missing or not an object | `400` | `{"error":"Missing or invalid \"schema\" (object)"}` |
| `x-shared-secret` missing or wrong | `401` | `{"error":"Unauthorized"}` |
| `model` not in the valid set | `500` | `{"error":"… Validation Error … Invalid model '<x>'. Valid options are: …"}` |
| `User-Agent: Python-urllib/*` | `403` | Cloudflare error 1010 HTML — *the function never runs* |

Note that Base44 reports **validation** failures as `500`, not `4xx`. `llm.py` therefore does not
retry `500`; retrying an invalid model or malformed schema only spends credits. Only `429`, `502`,
`503`, `504` and network errors are retried.

## Models

The gateway's own `500` enumerated the valid set:

```
automatic, gpt_5_mini, gemini_3_flash, gpt_5_4, gpt_5_6_sol, gpt_5_6_luna,
gemini_3_1_pro, claude_sonnet_4_6, claude_opus_4_6, claude_opus_4_7,
claude_opus_4_8, claude-sonnet-5
```

Mirrored as `BASE44_MODELS` in `llm.py`. **`gpt_5` is not valid** despite appearing in the
original `.env.example` comment; it returns `500`. The project default is `claude_opus_4_8`.

## Cloudflare edge filtering

Cloudflare fingerprints the **client**, and blocks the literal `Python-urllib` User-Agent with
error 1010 — a `403` returned at the edge, before the function executes. This is not a Base44 app
setting, and not an auth problem.

A/B against the live endpoint, identical URL, secret and body, varying only `User-Agent`:

| User-Agent | Result |
|---|---|
| `Python-urllib/3.12` | **403** Cloudflare 1010 |
| `python-requests/2.32.3` | 200 |
| `curl/8.4.0` | 200 |
| `httpx/0.27.0` | 200 |
| `Mozilla/5.0 … Chrome/126` | 200 |
| `dc-planner/1.0` | 200 |

Only the `Python-urllib` token is filtered. No browser impersonation is needed or used: `llm.py`
sends the honest `dc-planner/1.0` (`USER_AGENT`).

## Not verified

Deliberately unrecorded, because we have not tested it: streaming, token/context limits, rate
limits, concurrency behaviour, per-call cost, retry semantics inside the function, and whether the
function forwards any parameter beyond `prompt`/`schema`/`model`. Observed latency was roughly
4–9s for small prompts, which is not a guarantee. Check the Base44 dashboard for anything here.
