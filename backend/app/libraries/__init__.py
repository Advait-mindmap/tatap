"""Versioned domain libraries.

These are DATA, not code (CLAUDE.md: "compliance and city pathways are versioned data, verified
in admin"). The deterministic engine instances activities, logic, durations and counts from
here; the LLM only reasons over them and must never emit them itself.

Every entry carries a `provenance` block. Nothing in the seed set is verified — see
`verification_report()` for what a domain reviewer still has to sign off, and
`backend.app.libraries.provenance.assert_usable_in_live_plan` for the gate that keeps
unverified data out of a live plan.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from backend.app.libraries.provenance import (
    Origin,
    UnverifiedDomainDataError,
    VerificationStatus,
    assert_usable_in_live_plan,
    is_verified,
    unverified_entries,
)

DATA_DIR = Path(__file__).parent / 'data'
CITY_PATHWAY_DIR = DATA_DIR / 'city_pathways'

#: Libraries the engine expects to exist. A missing one is a bug, not an empty default.
REQUIRED_LIBRARIES = (
    'fragnets',
    'productivity_norms',
    'equipment_lead_times',
    'tier_rules',
    'decision_points',
    'safety_register',
)


class LibraryError(RuntimeError):
    """A library file is missing, malformed, or version-mismatched."""


def library_version() -> str:
    return os.getenv('LIBRARY_VERSION', 'v1')


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise LibraryError(f'Library file not found: {path.name}') from exc
    except json.JSONDecodeError as exc:
        raise LibraryError(f'Library file {path.name} is not valid JSON: {exc}') from exc

    for field in ('library', 'library_version', 'entries'):
        if field not in data:
            raise LibraryError(f'Library file {path.name} is missing required field "{field}"')

    # Every entry must declare where it came from. Domain data with no provenance is exactly
    # the "plausible-looking filler" the product spec rules out.
    for entry in data['entries']:
        if 'provenance' not in entry:
            raise LibraryError(
                f'Entry {entry.get("id", "<no id>")} in {path.name} has no provenance block. '
                'Every library entry must declare its origin and verification status.'
            )
        origin = entry['provenance'].get('origin')
        if origin not in {o.value for o in Origin}:
            raise LibraryError(
                f'Entry {entry.get("id", "<no id>")} in {path.name} has unknown origin {origin!r}'
            )
    return data


@lru_cache(maxsize=None)
def load_library(name: str) -> Dict[str, Any]:
    """Load one library by name (without .json)."""
    data = _read(DATA_DIR / f'{name}.json')
    expected = library_version()
    if data['library_version'] != expected:
        raise LibraryError(
            f'Library "{name}" is version {data["library_version"]!r} but LIBRARY_VERSION is '
            f'{expected!r}. A simulation records the library version it used, so these must agree.'
        )
    return data


def load_all() -> Dict[str, Dict[str, Any]]:
    """Load every required library plus all city pathways."""
    libs = {name: load_library(name) for name in REQUIRED_LIBRARIES}
    libs['city_pathways'] = {
        'library': 'city_pathways',
        'library_version': library_version(),
        'entries': [e for city in available_cities() for e in load_city_pathway(city)['entries']],
        'cities': available_cities(),
    }
    return libs


def available_cities() -> List[str]:
    if not CITY_PATHWAY_DIR.is_dir():
        return []
    return sorted(p.stem for p in CITY_PATHWAY_DIR.glob('*.json'))


@lru_cache(maxsize=None)
def load_city_pathway(city: str) -> Dict[str, Any]:
    """Load one city's statutory pathway. `city` is the file stem, e.g. 'navi_mumbai'."""
    slug = city.strip().lower().replace(' ', '_').replace('-', '_')
    path = CITY_PATHWAY_DIR / f'{slug}.json'
    if not path.is_file():
        raise LibraryError(
            f'No statutory pathway for city {city!r}. Available: {available_cities()}. '
            'DOMAIN_KNOWLEDGE.md section 5: the pathway is per-city data confirmed with the '
            "client's compliance team, never inferred."
        )
    return _read(path)


def all_entries() -> List[Dict[str, Any]]:
    """Every entry across every library, for verification reporting."""
    out: List[Dict[str, Any]] = []
    for name in REQUIRED_LIBRARIES:
        for entry in load_library(name)['entries']:
            out.append({**entry, '_library': name})
    for city in available_cities():
        for entry in load_city_pathway(city)['entries']:
            out.append({**entry, '_library': f'city_pathways/{city}'})
    return out


def verification_report() -> Dict[str, Any]:
    """What the domain team still has to verify, and how risky each item is.

    This is the report ADMIN_SPEC.md sections 2 and 4 govern. `model_generated` entries are the
    dangerous ones: durations, lead times and city-pathway specifics that were invented and will
    look researched once they are inside a schedule.
    """
    entries = all_entries()
    by_origin: Dict[str, int] = {}
    by_library: Dict[str, Dict[str, int]] = {}

    for entry in entries:
        origin = entry['provenance'].get('origin', 'unknown')
        by_origin[origin] = by_origin.get(origin, 0) + 1
        lib = entry.get('_library', 'unknown')
        bucket = by_library.setdefault(lib, {'total': 0, 'invented': 0, 'verified': 0})
        bucket['total'] += 1
        if origin == Origin.MODEL_GENERATED.value:
            bucket['invented'] += 1
        if is_verified(entry):
            bucket['verified'] += 1

    pending = unverified_entries(entries)
    invented = [e for e in entries if e['provenance'].get('origin') == Origin.MODEL_GENERATED.value]

    return {
        'library_version': library_version(),
        'total_entries': len(entries),
        'by_origin': by_origin,
        'by_library': by_library,
        'unverified_count': len(pending),
        'invented_count': len(invented),
        'all_verified': not pending,
        'invented_ids': sorted(e.get('id', '?') for e in invented),
        'summary': (
            f'{len(invented)} of {len(entries)} entries were INVENTED BY THE MODEL '
            f'(durations, lead times, norms, city-pathway timings). '
            f'{len(pending)} entries are unverified and must not drive a live plan.'
        ),
    }


__all__ = [
    'DATA_DIR',
    'LibraryError',
    'Origin',
    'REQUIRED_LIBRARIES',
    'UnverifiedDomainDataError',
    'VerificationStatus',
    'all_entries',
    'assert_usable_in_live_plan',
    'available_cities',
    'is_verified',
    'library_version',
    'load_all',
    'load_city_pathway',
    'load_library',
    'unverified_entries',
    'verification_report',
]
