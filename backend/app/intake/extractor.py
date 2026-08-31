"""Free text / documents -> structured Brief, with a citation per field and questions.

Two guardrails do the real work here, and both exist because a confident wrong brief poisons
every downstream stage:

1. **Quote grounding.** Every field the model returns must cite a verbatim span of the source.
   We check that span really is in the source. A citation that is not literally present means
   the model reconstructed it from expectation rather than reading, so the field is discarded
   and asked about instead of trusted.
2. **Confidence gating.** A field below CONF_THRESHOLD becomes a question rather than a value
   (CLAUDE.md rule 3, applied at the front door).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.app.intake.prompt import (
    DELIVERY_MODES,
    SYSTEM_PROMPT,
    TARGET_DISCIPLINES,
    TARGET_FIELDS,
    build_user_prompt,
    extraction_schema,
)
from backend.app.llm import LLMError, get_adapter
from backend.app.schemas import (
    ExtractedBrief,
    FieldProvenance,
    IntakeQuestion,
    IntakeResult,
    RawBrief,
)

#: Fields the plan cannot be built without. A gap here is blocking.
REQUIRED_FIELDS = ('city', 'tier', 'it_load_mw')

_WS = re.compile(r'\s+')

_TIER_RE = re.compile(r'\b(?:tier\s*)?(IV|III|II|I|[1-4])\b', re.IGNORECASE)
_TIER_FROM_ARABIC = {'1': 'I', '2': 'II', '3': 'III', '4': 'IV'}

_MODE_ALIASES = {
    'ofe': 'owner-furnished',
    'owner furnished': 'owner-furnished',
    'owner-furnished': 'owner-furnished',
    'owner supplied': 'owner-furnished',
    'owner-supplied': 'owner-furnished',
    'client supplied': 'owner-furnished',
    'client-supplied': 'owner-furnished',
    'free issue': 'owner-furnished',
    'self perform': 'self-perform',
    'self-perform': 'self-perform',
    'selfperform': 'self-perform',
    'in-house': 'self-perform',
    'turnkey': 'turnkey',
    'turnkey package': 'turnkey',
    'epc': 'turnkey',
    'subcontract': 'subcontract',
    'subcontracted': 'subcontract',
    'sub-contract': 'subcontract',
}

_TRUEISH = {'true', 'yes', 'y', '1'}
_FALSEISH = {'false', 'no', 'n', '0'}


def conf_threshold() -> float:
    try:
        return float(os.getenv('CONF_THRESHOLD', '0.7'))
    except ValueError:
        return 0.7


def _normalise(text: str) -> str:
    """Collapse whitespace and case so quote matching survives re-wrapping."""
    return _WS.sub(' ', (text or '')).strip().lower()


def quote_is_grounded(quote: str, source: str, min_chars: int = 8) -> bool:
    """Is this citation really present in the source text?

    Whitespace-insensitive, because the model may re-wrap lines. Very short quotes are rejected:
    a two-word fragment matches by luck and proves nothing.
    """
    q = _normalise(quote)
    if len(q) < min_chars:
        return False
    return q in _normalise(source)


def normalise_tier(value: str) -> Optional[str]:
    match = _TIER_RE.search(value or '')
    if not match:
        return None
    token = match.group(1).upper()
    return _TIER_FROM_ARABIC.get(token, token)


def normalise_mode(value: str) -> Optional[str]:
    v = _normalise(value)
    if v in _MODE_ALIASES:
        return _MODE_ALIASES[v]
    for alias, mode in _MODE_ALIASES.items():
        if alias in v:
            return mode
    return v if v in DELIVERY_MODES else None


def _coerce(name: str, raw_value: str) -> Tuple[Any, Optional[str]]:
    """Coerce a string value to its typed form. Returns (value, error)."""
    value = (raw_value or '').strip()
    if not value:
        return None, 'empty value'

    if name == 'it_load_mw':
        match = re.search(r'(\d+(?:\.\d+)?)', value)
        if not match:
            return None, f'could not read a number from {value!r}'
        return float(match.group(1)), None

    if name == 'tier':
        tier = normalise_tier(value)
        if tier is None:
            return None, f'could not read a tier from {value!r}'
        return tier, None

    if name == 'in_dc_park_or_sez':
        low = value.lower()
        if low in _TRUEISH:
            return True, None
        if low in _FALSEISH:
            return False, None
        return None, f'could not read a boolean from {value!r}'

    return value, None


def extract_brief(
    raw: RawBrief | str,
    adapter: Any = None,
    *,
    threshold: Optional[float] = None,
) -> IntakeResult:
    """Read a raw brief into a structured Brief with per-field provenance and questions."""
    raw_brief = RawBrief(text=raw) if isinstance(raw, str) else raw
    if not raw_brief.text.strip():
        raise ValueError('Cannot run intake on an empty brief.')

    adapter = adapter or get_adapter()
    threshold = conf_threshold() if threshold is None else threshold

    response = adapter.invoke(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(raw_brief.text),
        schema=extraction_schema(),
    )
    return build_result(response, raw_brief, threshold)


def build_result(
    response: Dict[str, Any], raw_brief: RawBrief, threshold: float
) -> IntakeResult:
    """Turn a raw gateway response into a validated IntakeResult.

    Separated from the call so the grounding and gating logic is testable without a gateway.
    """
    source = raw_brief.text
    brief = ExtractedBrief()
    provenance: Dict[str, FieldProvenance] = {}
    questions: List[IntakeQuestion] = []
    warnings: List[str] = []
    unresolved: List[str] = []
    accepted: set[str] = set()

    # ---- scalar fields -------------------------------------------------------------
    for item in response.get('fields') or []:
        name = (item.get('name') or '').strip()
        if name not in TARGET_FIELDS:
            continue

        quote = item.get('quote') or ''
        confidence = float(item.get('confidence') or 0.0)
        grounded = quote_is_grounded(quote, source)

        if not grounded:
            warnings.append(
                f'DISCARDED {name}: cited quote is not present in the source text '
                f'({quote[:60]!r}). The citation was not grounded, so the value was not trusted.'
            )
            unresolved.append(name)
            questions.append(IntakeQuestion(
                field=name,
                question=f'What is the {name.replace("_", " ")} for this project?',
                why_needed='The extractor cited a quote that does not appear in the brief, so '
                           'the value could not be trusted and was discarded.',
                blocking=name in REQUIRED_FIELDS,
            ))
            continue

        if confidence < threshold:
            unresolved.append(name)
            questions.append(IntakeQuestion(
                field=name,
                question=f'Please confirm the {name.replace("_", " ")} for this project.',
                why_needed=f'Extracted with confidence {confidence:.2f}, below the '
                           f'{threshold:.2f} threshold, so it is asked rather than assumed.',
                blocking=name in REQUIRED_FIELDS,
            ))
            continue

        value, error = _coerce(name, item.get('value') or '')
        if error:
            warnings.append(f'DISCARDED {name}: {error}')
            unresolved.append(name)
            questions.append(IntakeQuestion(
                field=name,
                question=f'What is the {name.replace("_", " ")} for this project?',
                why_needed=f'The extracted value could not be read as the expected type ({error}).',
                blocking=name in REQUIRED_FIELDS,
            ))
            continue

        setattr(brief, name, value)
        accepted.add(name)
        provenance[name] = FieldProvenance(
            field=name,
            quote=quote.strip(),
            confidence=confidence,
            source_ref=raw_brief.source_ref,
            grounded=True,
        )

    # ---- delivery mode per discipline ---------------------------------------------
    for item in response.get('delivery_modes') or []:
        discipline = _normalise(item.get('discipline') or '')
        if discipline not in TARGET_DISCIPLINES:
            continue

        quote = item.get('quote') or ''
        confidence = float(item.get('confidence') or 0.0)
        mode = normalise_mode(item.get('mode') or '')

        if mode is None:
            warnings.append(
                f'DISCARDED delivery mode for {discipline}: unrecognised mode '
                f'{item.get("mode")!r}.'
            )
            continue
        if not quote_is_grounded(quote, source):
            warnings.append(
                f'DISCARDED delivery mode for {discipline}: cited quote is not present in the '
                f'source text ({quote[:60]!r}).'
            )
            continue
        if confidence < threshold:
            questions.append(IntakeQuestion(
                field=f'delivery_mode.{discipline}',
                question=f'Is {discipline} self-performed, turnkey, subcontracted or '
                         'owner-furnished?',
                why_needed=f'Extracted with confidence {confidence:.2f}, below the '
                           f'{threshold:.2f} threshold. Delivery mode changes the activity '
                           'structure entirely (DOMAIN_KNOWLEDGE.md §6).',
                blocking=True,
            ))
            continue

        brief.delivery_mode_by_discipline[discipline] = mode
        key = f'delivery_mode.{discipline}'
        provenance[key] = FieldProvenance(
            field=key,
            quote=quote.strip(),
            confidence=confidence,
            source_ref=raw_brief.source_ref,
            grounded=True,
        )

    # ---- questions raised by the model ---------------------------------------------
    asked = {q.field for q in questions}
    for item in response.get('questions') or []:
        field = (item.get('field') or '').strip()
        if field in accepted or field in asked:
            continue
        questions.append(IntakeQuestion(
            field=field,
            question=item.get('question') or f'Please provide {field}.',
            why_needed=item.get('why_needed') or 'Not stated in the brief.',
            blocking=field in REQUIRED_FIELDS,
        ))
        asked.add(field)
        if field in TARGET_FIELDS and field not in unresolved:
            unresolved.append(field)

    # ---- required fields must never be silently absent -----------------------------
    for field in REQUIRED_FIELDS:
        if field in accepted or field in asked:
            continue
        questions.append(IntakeQuestion(
            field=field,
            question=f'What is the {field.replace("_", " ")} for this project?',
            why_needed='Required to build the plan and not present in the brief.',
            blocking=True,
        ))
        asked.add(field)
        if field not in unresolved:
            unresolved.append(field)

    conflicts = [c for c in (response.get('conflicts') or []) if c]
    if conflicts:
        warnings.append(
            f'{len(conflicts)} conflict(s) in the brief were flagged rather than resolved.'
        )

    return IntakeResult(
        brief=brief,
        field_provenance=provenance,
        questions=questions,
        unresolved_fields=unresolved,
        flagged_conflicts=conflicts,
        extraction_confidence_overall=float(response.get('overall_confidence') or 0.0),
        warnings=warnings,
        raw_brief_ref=raw_brief.source_ref,
        attachments=list(raw_brief.attachments),
    )


__all__ = [
    'LLMError',
    'REQUIRED_FIELDS',
    'build_result',
    'conf_threshold',
    'extract_brief',
    'normalise_mode',
    'normalise_tier',
    'quote_is_grounded',
]
