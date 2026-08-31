"""Live intake extraction against the real Base44 gateway, on sample_raw_brief.md.

This is the test that actually proves intake works. The mocked tests in test_intake.py pin the
guardrails against a canned response; only this one shows the model reading a real free-text
brief and producing a grounded, structured result.

Makes real calls and spends Base44 credits. Marked `live`; skipped without credentials.
Deselect with:  pytest -m "not live"
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.intake import extract_brief, quote_is_grounded
from backend.app.schemas import RawBrief

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.getenv('BASE44_FN_URL') and os.getenv('BASE44_SHARED_SECRET')),
        reason='BASE44_FN_URL / BASE44_SHARED_SECRET not set; skipping live intake tests.',
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PATH = REPO_ROOT / 'sample_raw_brief.md'
SAMPLE_BRIEF = SAMPLE_PATH.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def result():
    """One live extraction shared by the assertions below, to spend credits once."""
    return extract_brief(RawBrief(text=SAMPLE_BRIEF, source_ref='sample_raw_brief.md'))


def test_live_extracts_tier(result):
    assert result.brief.tier == 'III', f'expected Tier III, got {result.brief.tier!r}'


def test_live_extracts_it_load(result):
    assert result.brief.it_load_mw == 20.0, f'expected 20 MW, got {result.brief.it_load_mw!r}'


def test_live_extracts_gensets_as_owner_furnished(result):
    """The OFE case. Conflating it with 'subcontract' would misplace the delivery constraint."""
    modes = result.brief.delivery_mode_by_discipline
    assert modes.get('gensets') == 'owner-furnished', (
        f'expected gensets owner-furnished, got {modes.get("gensets")!r} (all: {modes})'
    )


def test_live_cites_provenance_for_every_extracted_field(result):
    """PRODUCT_SPEC.md §3.1: a citation per field, and every citation really in the source."""
    assert result.field_provenance, 'no provenance recorded at all'

    for name, value in result.brief.model_dump().items():
        if name == 'delivery_mode_by_discipline':
            for discipline in value:
                assert f'delivery_mode.{discipline}' in result.field_provenance
        elif value is not None:
            assert name in result.field_provenance, f'{name} has a value but no citation'

    for key, prov in result.field_provenance.items():
        assert prov.grounded is True, f'{key} provenance not grounded'
        assert quote_is_grounded(prov.quote, SAMPLE_BRIEF), (
            f'{key} cites {prov.quote!r}, which is not in sample_raw_brief.md'
        )


def test_live_provenance_quotes_match_the_expected_source_spans(result):
    """The citations should point at the sentences a human would point at."""
    assert '20 MW' in result.field_provenance['it_load_mw'].quote
    assert 'III' in result.field_provenance['tier'].quote
    assert 'owner-furnished' in result.field_provenance['delivery_mode.gensets'].quote.lower()


def test_live_raises_questions_for_what_the_brief_never_says(result):
    """sample_raw_brief.md says 'a client' but never names one, and gives no project name."""
    asked = {q.field for q in result.questions}
    assert asked, 'a brief this incomplete must raise questions'
    assert 'client' in asked, f'client is unnamed in the brief but was not asked. Asked: {asked}'
    assert result.brief.client is None, 'client name must not be invented'

    # Questions remain, so the brief is not fully extracted...
    assert result.is_complete is False
    # ...but nothing blocking is outstanding: tier, load and city are all grounded, so the
    # simulation could start while the client name is still being chased.
    assert result.can_proceed is True
    assert result.blocking_questions == []


def test_live_does_not_invent_an_exact_rfs_date(result):
    """The brief says 'Q1 2027' - an exact ISO date would be fabrication."""
    date = result.brief.target_rfs_date
    if date and len(date) == 10 and date[4] == '-':
        pytest.fail(f'invented an exact RFS date {date!r} from a quarter-level statement')


def test_live_extracts_the_richer_context_fields(result):
    """Not required fields, but the brief states them plainly and they drive routing."""
    assert result.brief.city and 'navi mumbai' in result.brief.city.lower()
    if result.brief.site_context:
        assert 'greenfield' in result.brief.site_context.lower()
    if result.brief.redundancy_topology:
        assert 'n+1' in result.brief.redundancy_topology.lower()


def test_live_extracts_multiple_delivery_modes(result):
    """The brief states four distinct delivery modes; conflating them loses the fork."""
    modes = result.brief.delivery_mode_by_discipline
    assert len(modes) >= 3, f'expected several disciplines, got {modes}'
    assert set(modes.values()) <= {'self-perform', 'turnkey', 'subcontract', 'owner-furnished'}
    if 'civil' in modes:
        assert modes['civil'] == 'self-perform'
