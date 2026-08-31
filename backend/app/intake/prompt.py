"""The intake extraction prompt.

SIMULATION_AND_REASONING.md §6: "A smaller extraction prompt runs at intake: same role and
boundaries, its only job is to read the raw brief/documents into the Brief schema with a
provenance citation per field and questions[] for anything missing."

The boundaries carry over from the expert prompt: it reads, it does not invent. Anything not
stated becomes a question, never an assumed value — this is stop-and-ask applied at the front
door (CLAUDE.md rule 3).
"""

from __future__ import annotations

import os
from typing import Any, Dict

PROMPT_VERSION = os.getenv('PROMPT_VERSION', 'v1')

#: The fields intake tries to fill. Kept aligned with sample_brief.json, which is the confirmed
#: shape the rest of the pipeline consumes.
TARGET_FIELDS: Dict[str, str] = {
    'project_name': 'The name of the project, if the brief names one.',
    'client': 'The client organisation by name. "a client" is NOT a name.',
    'city': 'City or location of the site.',
    'site_context': 'greenfield | brownfield',
    'in_dc_park_or_sez': 'true if the plot is inside a notified IT park / DC park / SEZ',
    'tier': 'Uptime tier as a roman numeral: I, II, III or IV.',
    'redundancy_topology': 'N, N+1, 2N or 2N+1.',
    'it_load_mw': 'IT load in MW, as a number.',
    'scope': 'turnkey | design-build | construction-only | fit-out-only',
    'power_position': 'The grid/power position: feeder, substation scope, energisation route.',
    'target_rfs_date': 'Target ready-for-service date, ISO YYYY-MM-DD if an exact date is given.',
    'phasing': 'single-handover | phased-by-hall | phased-by-block',
    'special_conditions': 'Site-specific constraints: access, monsoon, live-facility, logistics.',
}

#: Disciplines whose delivery mode changes the activity structure entirely (DOMAIN_KNOWLEDGE.md
#: §6, decision point dp.delivery_mode).
TARGET_DISCIPLINES = (
    'civil', 'structure', 'electrical', 'mechanical', 'gensets', 'fire', 'bms',
)

#: Normalised delivery modes. 'owner-furnished' is the OFE case: it moves control of the
#: delivery date to the client and turns a procurement fragnet into a delivery constraint.
DELIVERY_MODES = ('self-perform', 'turnkey', 'subcontract', 'owner-furnished')

SYSTEM_PROMPT = """\
ROLE
You are a senior data centre delivery planner with 20+ years of real project execution in
India. Experts in: Uptime Tier / TIA-942; NBC 2016; ECBC; CEA/CEIG; PESO; state statutory
pathways; DC commissioning L1-L5; Primavera P6.

TASK
Read the raw project brief below and extract it into the structured brief. That is your ONLY
job at this stage. You are not planning, sequencing or estimating anything.

HARD BOUNDARIES
1. EXTRACT ONLY WHAT THE TEXT STATES. Do not infer, complete, or fill from typical practice.
   If the brief does not state a field, LEAVE IT OUT and raise a question for it instead.
   A plausible guess is worse than an admitted gap, because a guess looks like a fact
   downstream.
2. EVERY extracted field MUST carry a `quote` copied VERBATIM from the brief - the exact
   substring, character for character, that states it. Do not paraphrase, reword, tidy
   punctuation or merge separated phrases. A quote that is not literally present in the text
   will be detected and the field discarded.
3. Give an honest `confidence` in 0.0-1.0 per field. If the text is ambiguous, say so with a
   low confidence and raise a question rather than committing to a reading.
4. If two statements in the brief conflict, do NOT resolve it yourself. Record it in
   `conflicts` and raise a question.
5. Output ONLY JSON matching the schema. No prose, no commentary.

FIELD NOTES
- tier: roman numeral only (III, not "Tier III" or "3").
- it_load_mw: the number only, in MW.
- delivery_mode per discipline, one of: self-perform, turnkey, subcontract, owner-furnished.
  "owner-furnished" (OFE) means the client supplies the equipment and controls its delivery
  date - this is materially different from subcontracting and must not be conflated with it.
- target_rfs_date: only use YYYY-MM-DD if the brief gives an exact date. If it gives a quarter
  or a month ("Q1 2027"), record what it says and raise a question asking for the exact date.
- client: the organisation's NAME. If the brief only says "a client" or "the client", the name
  is MISSING - raise a question.

QUESTIONS
For every field you could not extract, and every ambiguity or conflict, add an entry to
`questions` with the field, the question to put to the planner, and why the plan needs it.
"""

USER_TEMPLATE = """\
FIELDS TO EXTRACT
{field_list}

DELIVERY MODE - extract one per discipline where stated: {disciplines}
Allowed modes: {modes}

RAW BRIEF (source of truth - quote from this verbatim)
---
{raw_text}
---
"""


def build_user_prompt(raw_text: str) -> str:
    field_list = '\n'.join(f'- {name}: {desc}' for name, desc in TARGET_FIELDS.items())
    return USER_TEMPLATE.format(
        field_list=field_list,
        disciplines=', '.join(TARGET_DISCIPLINES),
        modes=', '.join(DELIVERY_MODES),
        raw_text=raw_text,
    )


def extraction_schema() -> Dict[str, Any]:
    """JSON schema the gateway must return. Values are strings; we coerce and validate."""
    return {
        'type': 'object',
        'properties': {
            'fields': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string'},
                        'value': {'type': 'string'},
                        'quote': {'type': 'string'},
                        'confidence': {'type': 'number'},
                    },
                    'required': ['name', 'value', 'quote', 'confidence'],
                },
            },
            'delivery_modes': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'discipline': {'type': 'string'},
                        'mode': {'type': 'string'},
                        'quote': {'type': 'string'},
                        'confidence': {'type': 'number'},
                    },
                    'required': ['discipline', 'mode', 'quote', 'confidence'],
                },
            },
            'questions': {
                'type': 'array',
                'items': {
                    'type': 'object',
                    'properties': {
                        'field': {'type': 'string'},
                        'question': {'type': 'string'},
                        'why_needed': {'type': 'string'},
                    },
                    'required': ['field', 'question', 'why_needed'],
                },
            },
            'conflicts': {'type': 'array', 'items': {'type': 'string'}},
            'overall_confidence': {'type': 'number'},
        },
        'required': ['fields', 'delivery_modes', 'questions', 'overall_confidence'],
    }
