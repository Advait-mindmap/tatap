"""Zone generation for the 3D/4D model.

VISUALIZATION_SPEC.md §2: a schematic model generated parametrically from the equipment counts
and layout rules, not a photoreal BIM. DOMAIN_KNOWLEDGE.md §4: "Size from the load" — IT load plus
tier and topology determine hall, electrical/UPS room, generator and chiller counts, which is
what drives the activity count.

The counts come from `tier_rules`, which is unverified industry-estimate data. The zone records
carry that through rather than presenting a count as fact.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from backend.app.engine.ids import zone_id
from backend.app.libraries.provenance import rests_on_estimated_data

#: Which stage first brings each zone kind into existence, for the 4D build-up.
ZONE_FIRST_STAGE = {
    'site': 'enabling',
    'shell': 'substructure',
    'data_hall': 'superstructure',
    'electrical_room': 'mep_power',
    'ups_room': 'mep_power',
    'generator_yard': 'mep_power',
    'cooling_plant': 'mep_cooling',
}


def _rule(entries: List[Dict[str, Any]], rule_id: str) -> Optional[Dict[str, Any]]:
    for entry in entries:
        if entry.get('id') == rule_id:
            return entry
    return None


def generate_zones(brief: Dict[str, Any], tier_rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derive the zone set from IT load, topology and the sizing rules.

    Deterministic: same brief and rules in, same zones out, in the same order.
    """
    load_mw = float(brief.get('it_load_mw') or 0.0)
    topology = str(brief.get('redundancy_topology') or 'N+1').upper().replace(' ', '')

    hall_rule = _rule(tier_rules, 'tier.hall_sizing')
    mult_rule = _rule(tier_rules, 'tier.redundancy_multipliers')
    plant_rule = _rule(tier_rules, 'tier.plant_per_mw')

    mw_per_hall = float((hall_rule or {}).get('mw_per_hall') or 2.5)
    multipliers = (mult_rule or {}).get('multipliers') or {}
    multiplier = float(multipliers.get(topology, 1.0))
    per_mw = (plant_rule or {}).get('per_mw') or {}

    # Any sizing rule that is unverified stand-in data taints every count derived from it.
    unverified = sorted({
        r['id'] for r in (hall_rule, mult_rule, plant_rule)
        if r is not None and rests_on_estimated_data(r)
    })
    # A stated hall count does not rest on the hall-sizing estimate, because it did not come
    # from it. Leaving the dependency on would understate the confidence of a fact the client
    # gave us.
    if brief.get('data_hall_count') and hall_rule is not None:
        unverified = [dep for dep in unverified if dep != hall_rule.get('id')]

    def counted(kind: str, per_mw_key: str) -> int:
        rate = float(per_mw.get(per_mw_key) or 0.0)
        return max(1, math.ceil(load_mw * rate * multiplier)) if rate and load_mw else 0

    # THE BRIEF WINS. `mw_per_hall` is unverified industry-estimate data, so dividing the load
    # by it is a guess; a brief that states "four data halls" has told us the answer. Deriving
    # it anyway meant a stated four-hall campus was planned as however many halls the estimate
    # implied, and fit-out, fire and power were then multiplied across the wrong number.
    stated_halls = brief.get('data_hall_count')
    if stated_halls:
        halls = max(1, int(stated_halls))
    else:
        halls = max(1, math.ceil(load_mw / mw_per_hall)) if load_mw else 0

    plan: List[tuple] = [
        ('site', 'Site and external works', 1),
        ('shell', 'Building shell and core', 1),
        ('data_hall', 'Data hall', halls),
        ('electrical_room', 'Electrical room', halls),
        ('ups_room', 'UPS and battery room', counted('ups_room', 'ups_modules') and halls or halls),
        ('generator_yard', 'Generator yard', 1),
        ('cooling_plant', 'Cooling plant', 1),
    ]

    zones: List[Dict[str, Any]] = []
    for kind, label, count in plan:
        for index in range(1, count + 1):
            name = label if count == 1 else f'{label} {index:02d}'
            zones.append({
                'id': zone_id(kind, index),
                'name': name,
                'kind': kind,
                'stage': ZONE_FIRST_STAGE.get(kind, 'substructure'),
                'geometry_ref': f'schematic:{kind}',
                'derived_from': {
                    'it_load_mw': load_mw,
                    'topology': topology,
                    'mw_per_hall': mw_per_hall,
                    'redundancy_multiplier': multiplier,
                    # Which way the hall count was decided, so a reader can tell a number the
                    # brief gave from one the sizing rules produced.
                    'hall_count_source': 'brief' if stated_halls else 'mw_per_hall_formula',
                    'stated_data_hall_count': stated_halls,
                },
                'unverified_dependencies': unverified,
            })

    equipment_counts = [
        {'item': key, 'count': counted(key, key), 'basis': f'{per_mw.get(key)}/MW x {multiplier}',
         'unverified_dependencies': unverified}
        for key in sorted(per_mw)
    ]
    for zone in zones:
        zone['equipment_counts'] = equipment_counts
    return zones
