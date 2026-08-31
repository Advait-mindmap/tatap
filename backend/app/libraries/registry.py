"""Register the on-disk libraries into the database against a LIBRARY_VERSION.

ADMIN_SPEC.md §2: everything is versioned, and a simulation records the version it used so a
plan can always be traced back to the library that produced it.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.libraries import (
    REQUIRED_LIBRARIES,
    available_cities,
    library_version,
    load_city_pathway,
    load_library,
    verification_report,
)
from backend.app.models import ComplianceRegister, Library, LibraryVersion


def register_libraries(session: Session, description: str = 'Seed libraries') -> Dict[str, Any]:
    """Upsert the LibraryVersion row and one Library row per library.

    Idempotent: re-running against the same LIBRARY_VERSION updates rather than duplicates.
    """
    version = library_version()

    version_row = session.execute(
        select(LibraryVersion).where(LibraryVersion.version == version)
    ).scalars().first()
    if version_row is None:
        version_row = LibraryVersion(version=version, description=description)
        session.add(version_row)
        session.flush()

    registered: List[str] = []
    for name in REQUIRED_LIBRARIES:
        data = load_library(name)
        row = session.execute(
            select(Library).where(Library.name == name, Library.version == version)
        ).scalars().first()
        if row is None:
            row = Library(
                name=name,
                category=name,
                version=version,
                library_version_id=version_row.id,
                description=data.get('description', ''),
                is_active=True,
            )
            session.add(row)
        else:
            row.description = data.get('description', '')
            row.library_version_id = version_row.id
            row.is_active = True
        registered.append(name)

    for city in available_cities():
        data = load_city_pathway(city)
        lib_name = f'city_pathways/{city}'
        row = session.execute(
            select(Library).where(Library.name == lib_name, Library.version == version)
        ).scalars().first()
        if row is None:
            session.add(
                Library(
                    name=lib_name,
                    category='city_pathways',
                    version=version,
                    library_version_id=version_row.id,
                    description=data.get('description', ''),
                    is_active=True,
                )
            )
        registered.append(lib_name)

    session.flush()
    report = verification_report()
    return {
        'library_version': version,
        'registered': registered,
        'verification': report,
        'warnings': _registry_warnings(report),
    }


def register_compliance_registers(session: Session) -> Dict[str, Any]:
    """Create ComplianceRegister rows from the city pathways, all UNAPPROVED.

    ADMIN_SPEC.md §4: a register must be marked compliance-approved before it can be used in a
    live plan. Ingestion never approves anything — approval is a human act.
    """
    created = 0
    for city in available_cities():
        data = load_city_pathway(city)
        city_name = data.get('city', city)
        for entry in data['entries']:
            existing = session.execute(
                select(ComplianceRegister).where(
                    ComplianceRegister.city == city_name,
                    ComplianceRegister.authority == entry['authority'],
                    ComplianceRegister.gate_stage == entry['gates_stage'],
                )
            ).scalars().first()
            if existing is None:
                session.add(
                    ComplianceRegister(
                        city=city_name,
                        authority=entry['authority'],
                        gate_stage=entry['gates_stage'],
                        approved=False,
                    )
                )
                created += 1
    session.flush()
    return {
        'created': created,
        'approved': 0,
        'warning': (
            'All compliance registers are UNAPPROVED. Their durations and sequencing were '
            'invented by the model. A live plan must not use them until the compliance team '
            'verifies and approves each register in admin (ADMIN_SPEC.md §4).'
        ),
    }


def _registry_warnings(report: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    if report['invented_count']:
        warnings.append(
            f'{report["invented_count"]} of {report["total_entries"]} library entries were '
            'INVENTED BY THE MODEL (durations, lead times, productivity norms, city-pathway '
            'timings). They are placeholders so the engine has a shape to instance, and will '
            'look researched once inside a schedule. Verify in admin before any live plan.'
        )
    if not report['all_verified']:
        warnings.append(
            f'{report["unverified_count"]} entries are unverified. '
            'assert_usable_in_live_plan() will refuse them.'
        )
    return warnings
