"""A deterministic stub LLM provider, for local demos and CI.

WHY THIS EXISTS
The full path — intake, per-stage reasoning, simulation — makes a real gateway call per stage.
That is correct in use, but it makes the browser tests need credentials and spend credits on
every CI run, and it makes a local demo cost money. This provider answers the same schemas
deterministically so the whole product can be driven end to end offline.

WHAT IT IS NOT
It is not a model, and it is not evidence of anything. It reads a few obvious facts out of the
brief with regular expressions and otherwise selects whatever the libraries offer for the stage.
Everything it produces is stamped so it cannot be mistaken for reasoning:

  - selections carry `stub: true` in their `why`
  - it is only reachable via LLM_PROVIDER=stub, never by default, and `get_adapter` warns
  - a plan produced this way still fails `assert_usable_in_live_plan`, because the library data
    it selects is unverified regardless of who selected it

Use it to exercise the machinery. Never to judge the output.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List

STUB_NOTE = 'STUB PROVIDER - selected mechanically, not reasoned. Not evidence.'


def _first(pattern: str, text: str, group: int = 1) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(group).strip() if match else ''


def _brief_body(prompt: str) -> str:
    """The user's brief alone, without the prompt scaffolding around it.

    The prompt fences the brief between `---` markers. Quoting across that fence produces text
    that exists in the PROMPT but not in the BRIEF, so grounding rejects it - which is the
    check working correctly, on a citation the stub had no business making.
    """
    after = prompt.split('RAW BRIEF', 1)[-1]
    parts = after.split('---')
    return parts[1] if len(parts) >= 2 else after


def _window(body: str, match: re.Match, pad: int = 45) -> str:
    """The enclosing span of a match, verbatim.

    The stub cites the surrounding text, exactly as it appears, rather than the bare token it
    matched on. That is no longer required for the quote to pass grounding - the check now asks
    whether a citation is SPECIFIC (a number, or two real words) rather than how long it is, so
    "12 MW" stands on its own. A little context still makes the citation more useful to read.
    """
    start = max(0, match.start() - pad)
    end = min(len(body), match.end() + pad)
    return body[start:end].strip()


def _extract_fields(prompt: str) -> List[Dict[str, Any]]:
    """Pull the handful of fields a regex can honestly find, and quote the source verbatim."""
    body = _brief_body(prompt)
    fields: List[Dict[str, Any]] = []

    def add(name: str, value: str, quote: str, confidence: float = 0.9) -> None:
        if value and quote:
            fields.append(
                {'name': name, 'value': value, 'quote': quote, 'confidence': confidence}
            )

    tier = re.search(r'\bTier\s+(IV|III|II|I)\b', body, re.IGNORECASE)
    if tier:
        add('tier', tier.group(1).upper(), _window(body, tier))

    load = re.search(r'(\d+(?:\.\d+)?)\s*MW', body, re.IGNORECASE)
    if load:
        add('it_load_mw', load.group(1), _window(body, load))

    topology = re.search(r'\b(2N\+1|2N|N\+1)\b', body)
    if topology:
        add('redundancy_topology', topology.group(1), _window(body, topology))

    city = re.search(
        r'\b(Navi Mumbai|Mumbai|Chennai|Hyderabad|Bengaluru|Bangalore|Pune|Noida|Delhi|'
        r'Kolkata|Ahmedabad|Jaipur|Chandigarh)\b',
        body,
    )
    if city:
        add('city', city.group(1), _window(body, city))

    site = re.search(r'\b(greenfield|brownfield)\b', body, re.IGNORECASE)
    if site:
        add('site_context', site.group(1).lower(), _window(body, site))

    if re.search(r'hall[- ]by[- ]hall|phased', body, re.IGNORECASE):
        quote = _first(r'([^.]*\b(?:hall[- ]by[- ]hall|phased)[^.]*)', body)
        add('phasing', 'phased-by-hall', quote, 0.8)

    scope = re.search(r'\b(turnkey|design-build|construction-only|fit-out-only)\b', body,
                      re.IGNORECASE)
    if scope:
        add('scope', scope.group(1).lower(), _window(body, scope))

    return fields


def _extract_delivery_modes(prompt: str) -> List[Dict[str, Any]]:
    body = _brief_body(prompt)
    modes: List[Dict[str, Any]] = []
    patterns = [
        ('gensets', r'([^.]*\bgenset[^.]*\b(?:owner[- ]furnished|owner[- ]supplied|free issue)[^.]*)',
         'owner-furnished'),
        ('civil', r'([^.]*\bself[- ]perform[^.]*\bcivil[^.]*|[^.]*\bcivil[^.]*\bself[- ]perform[^.]*)',
         'self-perform'),
        ('electrical', r'([^.]*\belectrical[^.]*\bturnkey[^.]*)', 'turnkey'),
        ('mechanical', r'([^.]*\bmechanical[^.]*\bturnkey[^.]*)', 'turnkey'),
        ('fire', r'([^.]*\bfire[^.]*\bsubcontract[^.]*)', 'subcontract'),
        ('bms', r'([^.]*\bBMS[^.]*\bsubcontract[^.]*)', 'subcontract'),
    ]
    for discipline, pattern, mode in patterns:
        quote = _first(pattern, body)
        if quote:
            modes.append({'discipline': discipline, 'mode': mode, 'quote': quote,
                          'confidence': 0.85})
    return modes


def _intake_response(prompt: str) -> Dict[str, Any]:
    fields = _extract_fields(prompt)
    found = {f['name'] for f in fields}
    questions = [
        {'field': name,
         'question': f'What is the {name.replace("_", " ")} for this project?',
         'why_needed': 'Not found in the brief by the stub extractor.'}
        for name in ('client', 'project_name', 'target_rfs_date', 'power_position')
        if name not in found
    ]
    return {
        'fields': fields,
        'delivery_modes': _extract_delivery_modes(prompt),
        'questions': questions,
        'conflicts': [],
        'overall_confidence': 0.75,
    }


def _reasoning_response(prompt: str) -> Dict[str, Any]:
    """Select everything the stage offers. Mechanical, and labelled as such."""
    def ids(section: str) -> List[str]:
        block = prompt.split(section, 1)[-1].split('AVAILABLE', 1)[0].split('RETRIEVED', 1)[0]
        return re.findall(r'^\s*-\s+([\w.\-/]+):', block, re.MULTILINE)

    sources = re.findall(r'^\s*-\s+(corpus:\d+#\d+)', prompt, re.MULTILINE)
    cite = sources[:1]

    stage = _first(r'STAGE TO REASON ABOUT:\s*(\w+)', prompt)
    packages = ids('AVAILABLE FRAGNETS FOR THIS STAGE')
    gates = ids('AVAILABLE GATES FOR THIS STAGE')
    leads = ids('AVAILABLE LONG-LEAD ITEMS')
    forks = ids('AVAILABLE DECISION POINTS FOR THIS STAGE')

    def selection(key: str, value: str) -> Dict[str, Any]:
        return {key: value, 'why': f'{STUB_NOTE} Offered for the {stage} stage.',
                'confidence': 0.8, 'sources': cite}

    return {
        'stage': stage,
        'packages': [selection('fragnet_id', p) for p in packages],
        'gates': [selection('gate_id', g) for g in gates],
        'long_lead': [selection('lead_id', l) for l in leads],
        # Raise every curated fork the stage offers: the stub cannot judge which are genuine,
        # and over-asking is the safe direction for a thing that cannot reason.
        'decision_points': [
            {'decision_point_id': f, 'why_stuck': f'{STUB_NOTE} Raised because the stage offers it.'}
            for f in forks
        ],
        'notes': STUB_NOTE,
    }


class StubAdapter:
    """Answers the intake and reasoning schemas deterministically. Never the default."""

    provider = 'stub'
    is_stub = True

    def __init__(self, **_: Any) -> None:
        self.model = 'stub'

    def invoke(
        self, system: str = '', user: str = '', schema: Dict[str, Any] | None = None, **kwargs: Any
    ) -> Dict[str, Any]:
        prompt = f'{system}\n\n{user}'
        if 'STAGE TO REASON ABOUT' in prompt:
            return _reasoning_response(prompt)
        return _intake_response(prompt)


def stub_enabled() -> bool:
    return os.getenv('LLM_PROVIDER', '').lower() == 'stub'
