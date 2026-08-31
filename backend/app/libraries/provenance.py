"""Provenance and verification status for domain data.

CLAUDE.md: compliance and city pathways are versioned data, human-verified in admin — never
trusted from the model. DOMAIN_KNOWLEDGE.md §5 says the exact statutory set "varies by
city/parcel/project and is confirmed with the client's compliance team".

Every library entry therefore carries an explicit origin and verification state. Nothing in the
seed libraries is verified: the seed exists so the engine has a shape to instance from, not so
anyone can rely on its numbers. `assert_usable_in_live_plan()` is the gate that enforces that.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Iterable, List


class Origin(str, Enum):
    """Where a piece of domain data came from. Determines how much it can be trusted."""

    #: Copied from this repo's own /docs (e.g. the decision-point table in DOMAIN_KNOWLEDGE.md
    #: §6). Traceable to a document the client's team wrote or approved, but still unverified
    #: as engineering data.
    SPEC_TRANSCRIBED = 'spec_transcribed'

    #: INVENTED by the model. Durations, lead times, productivity norms, equipment counts and
    #: city pathway specifics. Plausible-looking and grounded in nothing. Highest risk: these
    #: are exactly the values a planner would otherwise assume were researched.
    MODEL_GENERATED = 'model_generated'

    #: A defensible industry-typical figure for the Indian DC market, standing in until real
    #: project actuals are loaded. Better than a model guess — it sits in the right range and can
    #: be argued for — but it is STILL NOT EVIDENCE. No project ran to these numbers, and a
    #: plausible number is the easiest kind to stop questioning. Treated as unverified.
    INDUSTRY_ESTIMATE = 'industry_estimate'

    #: Supplied by the client (real execution records, actuals, method statements). Nothing in
    #: the seed uses this — it is the target state once the real corpus is loaded.
    CLIENT_SUPPLIED = 'client_supplied'

    #: Named public standard or statute (IS 456, NBC 2016, CEA regs). The citation is
    #: checkable; the interpretation still is not.
    PUBLIC_STANDARD = 'public_standard'


class VerificationStatus(str, Enum):
    UNVERIFIED = 'unverified'
    #: Set only by a named human in the admin console (ADMIN_SPEC.md §4, §5).
    VERIFIED = 'verified'
    REJECTED = 'rejected'


#: Origins that must never reach a live plan without a human having verified them.
#: INDUSTRY_ESTIMATE is deliberately included: a defensible estimate is still not an actual.
REQUIRES_VERIFICATION = frozenset({
    Origin.MODEL_GENERATED,
    Origin.SPEC_TRANSCRIBED,
    Origin.INDUSTRY_ESTIMATE,
})

#: Origins that stand in for real data rather than record it. Reasoning that rests on one of
#: these must disclose it and have its confidence capped (SIMULATION_AND_REASONING.md section 4).
#: SINGLE SOURCE OF TRUTH - the reasoning loop reads this rather than naming origins itself, so
#: adding an origin cannot silently switch the anti-laundering cap off.
NOT_REAL_DATA = frozenset({Origin.MODEL_GENERATED, Origin.INDUSTRY_ESTIMATE})


class UnverifiedDomainDataError(RuntimeError):
    """Raised when unverified domain data would be used in a live plan."""


def provenance(
    origin: Origin,
    note: str,
    source_ref: str = '',
    status: VerificationStatus = VerificationStatus.UNVERIFIED,
) -> Dict[str, Any]:
    """Build the provenance block attached to every library entry."""
    block: Dict[str, Any] = {
        'origin': origin.value,
        'verification_status': status.value,
        'verified_by': None,
        'verified_on': None,
        'note': note,
        'source_ref': source_ref,
    }
    if status is VerificationStatus.UNVERIFIED:
        if origin is Origin.MODEL_GENERATED:
            block['warning'] = (
                'INVENTED BY THE MODEL. Not researched, not from any real project. '
                'Must be verified against real project data before use in a live plan.'
            )
        elif origin is Origin.INDUSTRY_ESTIMATE:
            block['warning'] = (
                'INDUSTRY ESTIMATE, NOT A PROJECT ACTUAL. A defensible typical figure for the '
                'Indian DC market, standing in until real execution data is loaded. No project '
                'ran to this number. Must be verified before use in a live plan.'
            )
    return block


def rests_on_estimated_data(entry: Dict[str, Any]) -> bool:
    """Is this entry a stand-in (invented or estimated) rather than a record of real execution?

    The reasoning loop calls this to decide whether to cap confidence and name the dependency.
    """
    prov = entry.get('provenance') or {}
    return prov.get('origin') in {o.value for o in NOT_REAL_DATA} and not is_verified(entry)


def is_verified(entry: Dict[str, Any]) -> bool:
    prov = entry.get('provenance') or {}
    return prov.get('verification_status') == VerificationStatus.VERIFIED.value


def unverified_entries(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every entry a human still has to sign off before it can drive a plan."""
    out = []
    for entry in entries:
        prov = entry.get('provenance') or {}
        origin = prov.get('origin')
        if origin in {o.value for o in REQUIRES_VERIFICATION} and not is_verified(entry):
            out.append(entry)
    return out


def assert_usable_in_live_plan(entries: Iterable[Dict[str, Any]], context: str = '') -> None:
    """Refuse to let unverified domain data drive a live plan.

    The engine may load and instance the seed libraries freely for development and tests. This
    gate is what a live planning run calls first, so unverified invented numbers cannot silently
    become a client's schedule.
    """
    pending = unverified_entries(entries)
    if not pending:
        return
    sample = ', '.join(str(e.get('id', '?')) for e in pending[:5])
    raise UnverifiedDomainDataError(
        f'{len(pending)} unverified domain entries would be used'
        f'{" in " + context if context else ""} (e.g. {sample}). '
        'Verify them in the admin console first (ADMIN_SPEC.md §2, §4).'
    )
