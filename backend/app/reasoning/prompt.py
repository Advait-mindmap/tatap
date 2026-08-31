"""The expert reasoning prompt.

Transcribed from SIMULATION_AND_REASONING.md §6, which gives it verbatim with bracketed tokens
filled at runtime. The additions below the spec text are the closed-vocabulary rules: the model
selects library IDs from lists we supply and cites source IDs we supply, so an invented activity
or a fabricated citation is detectable rather than persuasive.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

PROMPT_VERSION = os.getenv('PROMPT_VERSION', 'v1')

#: DC work-package taxonomy injected as [DC_TAXONOMY]. From DOMAIN_KNOWLEDGE.md §2.
DC_TAXONOMY = (
    'Client/BD; Design/Engineering; Statutory/Liaison; Procurement/SCM; Planning/Controls; '
    'Civil/Structure; MEP/Services (power train, cooling, fire, BMS); Commissioning (L1-L5, IST); '
    'QA/QC; HSE; Contracts; Client Cx/Certification.'
)

SYSTEM_PROMPT = """\
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

CLOSED VOCABULARY - THIS IS CHECKED
You do not write activities, durations, logic links or lags. You SELECT identifiers from the
lists supplied below and explain why each applies.
- Every fragnet_id, gate_id, lead_id and decision_point_id MUST be copied exactly from the
  AVAILABLE lists in this message. An identifier not in those lists will be rejected.
- Every entry in `sources` MUST be copied exactly from the RETRIEVED SOURCES list. Do not
  compose, guess or abbreviate a source id. A citation that was not supplied to you will be
  rejected and the element discarded.
- If nothing in the supplied lists fits this stage, return an empty list and say so in `notes`.
  An empty, honest answer is worth more than a fabricated one.

UNVERIFIED DATA - DISCLOSE, DO NOT LAUNDER
Library entries are marked with an origin and a verification status. Much of the current library
is STAND-IN DATA that no human has verified: durations, lead times, productivity norms and
statutory timings that are either model-generated or industry-typical estimates. An entry marked
INDUSTRY-ESTIMATE is a defensible figure for the Indian market - it is NOT a measurement of any
project, and being plausible makes it easier to over-trust, not safer.

If your reasoning rests on such an entry, say so plainly in your `why` and lower your
`confidence` accordingly. Do NOT present a conclusion drawn from stand-in data in confident
language - that turns an estimate into an apparent fact, which is the single most damaging thing
you can do here.

PER-STAGE LOOP
- Retrieve precedent for this stage from the corpus + libraries (already done; see below).
- Determine which work packages apply, how they sequence, which materials and gates attach,
  citing precedent by source id.
- Screen for decision points; if any is unresolved, raise it and stop this branch.
- Hand the expanded packages to the engine (you do not expand them yourself).
"""

USER_TEMPLATE = """\
STAGE TO REASON ABOUT: {stage}   (owning department: {dept})

CONFIRMED BRIEF
{brief_json}

RESOLVED DECISIONS SO FAR
{decisions_json}

DC WORK-PACKAGE TAXONOMY
{taxonomy}

CONFIDENCE THRESHOLD
{conf_threshold} - below this, raise a decision point rather than proceeding.

AVAILABLE FRAGNETS FOR THIS STAGE (select fragnet_id from here only)
{fragnets}

AVAILABLE GATES FOR THIS STAGE (select gate_id from here only)
{gates}

AVAILABLE LONG-LEAD ITEMS (select lead_id from here only)
{long_lead}

AVAILABLE DECISION POINTS FOR THIS STAGE (select decision_point_id from here only)
{decision_points}

RETRIEVED SOURCES (cite by these ids only)
{sources}
"""


def _fmt_entries(entries: List[Dict[str, Any]], id_key: str, fields: List[str]) -> str:
    if not entries:
        return '  (none available for this stage)'
    lines = []
    for entry in entries:
        bits = ' | '.join(str(entry.get(f, '')) for f in fields if entry.get(f) not in (None, ''))
        prov = entry.get('provenance') or {}
        status = prov.get('origin', 'unknown')
        verified = prov.get('verification_status', 'unknown')
        if verified != 'verified' and status in ('model_generated', 'industry_estimate'):
            # The model must see that a value is a stand-in, not a measured figure, so it can
            # disclose that rather than reasoning over it as though it were evidence.
            kind = 'MODEL-GENERATED' if status == 'model_generated' else 'INDUSTRY-ESTIMATE'
            marker = f' [UNVERIFIED {kind} STAND-IN, NOT A PROJECT ACTUAL]'
        else:
            marker = f' [{status}/{verified}]'
        lines.append(f'  - {entry.get(id_key)}: {bits}{marker}')
    return '\n'.join(lines)


def _fmt_sources(hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return '  (nothing retrieved - you have no precedent for this stage; say so in notes)'
    lines = []
    for hit in hits:
        kind = hit.get('kind', '?')
        precedent = 'REAL EXECUTION PRECEDENT' if hit.get('citable_as_precedent') else (
            f'{kind} - NOT citable as real-execution precedent'
        )
        text = ' '.join(str(hit.get('text', '')).split())[:400]
        lines.append(f'  - {hit.get("ref")} ({precedent})\n      {text}')
    return '\n'.join(lines)


def build_user_prompt(
    *,
    stage: str,
    dept: str,
    brief: Dict[str, Any],
    decisions: List[Dict[str, Any]],
    fragnets: List[Dict[str, Any]],
    gates: List[Dict[str, Any]],
    long_lead: List[Dict[str, Any]],
    decision_points: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    conf_threshold: float,
) -> str:
    return USER_TEMPLATE.format(
        stage=stage,
        dept=dept,
        brief_json=json.dumps(brief, indent=2, default=str),
        decisions_json=json.dumps(decisions, indent=2, default=str) if decisions else '  (none)',
        taxonomy=DC_TAXONOMY,
        conf_threshold=conf_threshold,
        fragnets=_fmt_entries(fragnets, 'id', ['name', 'dept', 'materials', 'gates']),
        gates=_fmt_entries(gates, 'id', ['approval', 'authority', 'gates_stage']),
        long_lead=_fmt_entries(long_lead, 'id', ['equipment', 'typical_weeks', 'drives_rfs']),
        decision_points=_fmt_entries(decision_points, 'id', ['title', 'question', 'impact']),
        sources=_fmt_sources(sources),
    )


def reasoning_schema() -> Dict[str, Any]:
    """The OUTPUT SCHEMA. Note it has no field in which an activity or duration could be put."""
    selection = {
        'type': 'object',
        'properties': {
            'why': {'type': 'string'},
            'confidence': {'type': 'number'},
            'sources': {'type': 'array', 'items': {'type': 'string'}},
            'rests_on_unverified_data': {'type': 'boolean'},
        },
        'required': ['why', 'confidence', 'sources'],
    }

    def with_id(name: str, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
        props = {name: {'type': 'string'}, **selection['properties'], **(extra or {})}
        return {
            'type': 'object',
            'properties': props,
            'required': [name] + selection['required'],
        }

    return {
        'type': 'object',
        'properties': {
            'stage': {'type': 'string'},
            'packages': {
                'type': 'array',
                'items': with_id(
                    'fragnet_id',
                    {'predecessors': {'type': 'array', 'items': {'type': 'string'}}},
                ),
            },
            'gates': {'type': 'array', 'items': with_id('gate_id')},
            'long_lead': {'type': 'array', 'items': with_id('lead_id')},
            'decision_points': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'decision_point_id': {'type': 'string'},
                        'why_stuck': {'type': 'string'},
                        'confidence': {'type': 'number'},
                    },
                    'required': ['decision_point_id', 'why_stuck'],
                },
            },
            'notes': {'type': 'string'},
        },
        'required': ['stage', 'packages', 'gates', 'long_lead', 'decision_points'],
    }
