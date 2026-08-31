"""Cross-stage gate machinery.

A fragnet describes work *within* a stage. The dependencies that actually shape a data centre
programme run *between* stages, and DOMAIN_KNOWLEDGE.md §4 names two of them:

- **Engineering drives procurement.** You cannot order to a specification that is not fixed, so
  design completion (IFC drawings issued) gates procurement.
- **"Front-load it; tie construction to delivery."** Long-lead plant usually drives RFS, so each
  long-lead item's delivery is a milestone that gates the construction activity consuming it.

Both are expressed here as DATA, not as code paths:

- `CROSS_STAGE_GATES` is a table of `GateRule`s — producer stage, consumer stages, why.
- Delivery gates are derived from the `material_links` a fragnet already declares.

That matters for a specific reason. `frag.design.*` and `frag.procurement.*` do not exist yet, so
the design stage instances no activities today. A milestone is emitted anyway, *unanchored*, and
still gates its consumers — so the programme carries the constraint now, and the moment those
fragnets are written the milestone simply acquires a predecessor. Nothing here needs rewriting
when the library fills out; that is the whole point of putting the rule in a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from backend.app.engine.ids import gate_id


@dataclass(frozen=True)
class GateRule:
    """One cross-stage constraint: a milestone produced by one stage that gates others."""

    id: str
    label: str
    #: The stage whose completion the milestone represents. If that stage instanced activities,
    #: the milestone hangs off its last one; if not, the milestone stands alone.
    producer_stage: str
    #: Stages whose activities must not start before the milestone.
    consumer_stages: Tuple[str, ...]
    kind: str
    why: str
    #: When true, only the consumer activities that declare a matching material_link are gated,
    #: rather than every activity in the consumer stages.
    per_material_link: bool = False


#: The declarative gate table. Add a row here, not a code path.
CROSS_STAGE_GATES: Tuple[GateRule, ...] = (
    GateRule(
        id='ifc_issued',
        label='Design complete - IFC drawings issued',
        producer_stage='design',
        consumer_stages=('procurement',),
        kind='design_release',
        why=(
            'Engineering drives procurement (DOMAIN_KNOWLEDGE.md §4). The equipment schedule is '
            'a design output, so ordering before the design is fixed risks buying to a '
            'specification that then changes.'
        ),
    ),
)

#: Delivery gates are not listed above because there is one per long-lead item actually selected;
#: they are generated from the fragnets' material_links. This rule carries their shared metadata.
DELIVERY_GATE = GateRule(
    id='delivery',
    label='Delivery to site',
    producer_stage='procurement',
    consumer_stages=(),  # resolved per material link
    kind='delivery',
    why=(
        'Long-lead gear usually drives RFS (DOMAIN_KNOWLEDGE.md §4): "Front-load it; tie '
        'construction to delivery." The installing activity cannot start before the plant '
        'arrives, so delivery is a hard predecessor rather than an assumption.'
    ),
    per_material_link=True,
)


def material_link_index(fragnets: Sequence[Dict]) -> Dict[str, List[Tuple[str, str]]]:
    """lead_id -> [(fragnet_id, activity_id), ...] for every declared material link.

    Read straight from library data, so a fragnet added later is picked up with no code change.
    Sorted for deterministic assembly.
    """
    index: Dict[str, List[Tuple[str, str]]] = {}
    for frag in fragnets:
        for link in frag.get('material_links', []) or []:
            lead = link.get('requires_delivery_of')
            activity = link.get('activity')
            if lead and activity:
                index.setdefault(lead, []).append((frag['id'], activity))
    return {lead: sorted(pairs) for lead, pairs in sorted(index.items())}


def delivery_gate_id(lead_id: str) -> str:
    return gate_id(f'delivery.{lead_id}')


def cross_stage_gate_id(rule: GateRule) -> str:
    return gate_id(rule.id)
