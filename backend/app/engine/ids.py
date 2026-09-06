"""Deterministic identifier and WBS generation.

Every id is a pure function of the thing it names. Nothing here reads a clock, a random source,
or an insertion order, because SIMULATION_AND_REASONING.md section 8 requires that re-running the
engine over the same reasoning yields identical output - the assembly is deterministic even when
the reasoning step is not.
"""

from __future__ import annotations

import re

_SLUG = re.compile(r'[^a-z0-9]+')


def slug(text: str) -> str:
    return _SLUG.sub('-', (text or '').lower()).strip('-')


def activity_id(
    stage: str, fragnet_id: str, activity_id_: str, zone_index: int = 0,
) -> str:
    """Stable id for one instanced fragnet activity.

    `zone_index` is 1-based and names WHICH zone this instance belongs to, for work a fragnet
    repeats per data hall or per electrical room. Zero means the activity is instanced once for
    the whole project, which is both the default and the right answer for work that genuinely
    happens once - a bulk fuel farm is not built seven times because there are seven halls.

    The suffix is `.zNN` rather than the zone id itself: it stays inside the character set P6
    task codes allow, so the id survives the XER export without being hashed.
    """
    base = f'{stage}.{fragnet_id}.{activity_id_}'
    return f'{base}.z{zone_index:02d}' if zone_index else base


def zone_index_of(instanced_id: str) -> int:
    """The zone index an instanced id carries, or 0 for project-wide work."""
    tail = instanced_id.rsplit('.', 1)[-1]
    if len(tail) == 3 and tail[0] == 'z' and tail[1:].isdigit():
        return int(tail[1:])
    return 0


def gate_id(name: str) -> str:
    return f'gate.{slug(name)}'


def hold_point_id(owner_activity_id: str, name: str) -> str:
    return f'hold.{owner_activity_id}.{slug(name)}'


def zone_id(kind: str, index: int) -> str:
    return f'zone.{slug(kind)}.{index:02d}'


def wbs_id(stage_index: int, stage: str, package_index: int, activity_index: int) -> str:
    """WBS path: stage.package.activity, each 1-based and zero-padded for stable sorting."""
    return f'{stage_index + 1:02d}.{package_index + 1:02d}.{activity_index + 1:03d}'


def trail_ref(element_id: str) -> str:
    return f'trail.{element_id}'
