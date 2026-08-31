"""The seed corpus.

DELIBERATE OMISSION: there are no seeded "real project" documents here.

It would be easy to generate a handful of plausible historical DC schedules with realistic
durations and slippage narratives, and the corpus would look impressively full. It would also be
actively dangerous. The reasoning trail cites the precedent it used and surfaces that citation to
a planner, so an invented project schedule in the corpus would appear in the output as evidence
that a real delivered project supports the plan. That is the one failure mode this product
cannot have — DOMAIN_KNOWLEDGE.md §1 defines the expertise as preferring real precedent and
saying which was used.

So the seed contains only material that genuinely exists: this repo's own domain documentation,
and a register naming the applicable public standards. Neither is `CorpusKind.REAL_EXECUTION`,
so neither can be cited as precedent, and retrieval warns when no real-execution hit is found.

The real corpus is the client's to load (INPUTS.md §4): historical DC schedules, actuals,
lessons learned, method statements. Every simulation gets more grounded as that grows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from backend.app.rag.ingest import CorpusKind

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = REPO_ROOT / 'docs'

#: Repo documentation worth retrieving over. Read from disk at ingestion time, so the corpus
#: always reflects the real file rather than a copy that can drift.
_SEED_DOC_FILES = (
    ('DOMAIN_KNOWLEDGE.md', 'Data centre domain knowledge (stages, gates, decision points, safety)'),
    ('SIMULATION_AND_REASONING.md', 'Simulation model, expert reasoning loop and stop-and-ask'),
    ('PRODUCT_SPEC.md', 'Product specification and success criteria'),
    ('ARCHITECTURE_AND_BUILD.md', 'Architecture, data model and build plan'),
    ('VISUALIZATION_SPEC.md', '2D flow and 3D/4D visualisation specification'),
    ('ADMIN_SPEC.md', 'Admin console specification'),
)

#: A pointer index, NOT the text of any standard. Named so a query mentioning a code retrieves
#: something, while making unmistakable that the standard itself is not held here.
_STANDARDS_REGISTER = """\
STANDARDS AND STATUTORY REFERENCE REGISTER (POINTER INDEX ONLY)

This document names the standards and statutes that govern data centre delivery in India, as
listed in docs/DOMAIN_KNOWLEDGE.md. It does NOT contain the text of any standard, and nothing
here may be quoted as the content or requirement of a standard. Obtain the standards themselves
and have the compliance team confirm applicability per project.

Tier and design: Uptime Institute Tier classification; TIA-942 telecommunications
infrastructure standard for data centres.

Building and fire: National Building Code of India 2016 (NBC 2016), Part 4 covering fire and
life safety; state fire services NOC.

Structural and civil: IS 456 (plain and reinforced concrete); IS 800 (general construction in
steel); IS 2911 (design and construction of pile foundations).

Energy: Energy Conservation Building Code (ECBC), Bureau of Energy Efficiency (BEE).

Electrical: Central Electricity Authority (CEA) regulations; state Electrical Inspectorate /
Chief Electrical Inspector to Government (CEIG) approval for HT installation and permission to
energise.

Environmental and utilities: EIA Notification 2006 and State Environment Impact Assessment
Authority (SEIAA) environmental clearance; State Pollution Control Board consent to establish
and consent to operate; Central Ground Water Authority or municipal water approvals.

Hazardous storage: Petroleum and Explosives Safety Organisation (PESO) licensing for high speed
diesel (HSD) storage serving generators.

Labour and site: Building and Other Construction Workers Act (BOCW); Contract Labour
(Regulation and Abolition) Act (CLRA).

Other: Airports Authority of India (AAI) height and aviation clearance; Department of
Telecommunications (DoT) infrastructure registration.
"""


def _build_seed_documents() -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []

    for filename, description in _SEED_DOC_FILES:
        path = DOCS_DIR / filename
        if not path.is_file():
            continue
        docs.append(
            {
                'title': f'{filename} - {description}',
                'content': path.read_text(encoding='utf-8'),
                'source': f'docs/{filename}',
                'kind': CorpusKind.PROJECT_DOCUMENTATION,
                'tags': ['project_documentation', 'data_centre', path.stem.lower()],
            }
        )

    docs.append(
        {
            'title': 'Standards and statutory reference register (pointer index)',
            'content': _STANDARDS_REGISTER,
            'source': 'docs/DOMAIN_KNOWLEDGE.md#5 (compiled pointer index)',
            'kind': CorpusKind.STANDARD,
            'tags': ['standards', 'statutory', 'india', 'pointer_only'],
        }
    )
    return docs


#: Evaluated at import so callers get a stable list; content is read from disk here.
SEED_DOCUMENTS: List[Dict[str, Any]] = _build_seed_documents()
