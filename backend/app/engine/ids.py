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


def activity_id(stage: str, fragnet_id: str, activity_id_: str) -> str:
    """Stable id for one instanced fragnet activity."""
    return f'{stage}.{fragnet_id}.{activity_id_}'


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
