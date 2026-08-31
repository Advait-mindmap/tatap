from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Brief(BaseModel):
    project_name: str
    city: str
    tier: str
    it_load_mw: float
    client: str
    questions: List[str] = Field(default_factory=list)
    raw_text: Optional[str] = None


class Decision(BaseModel):
    id: str
    question: str
    answer: str
    impact: str


class FlowNodeSchema(BaseModel):
    id: str
    kind: str
    stage: str
    label: str
    dept: Optional[str] = None
    trail_ref: Optional[str] = None
    zone_id: Optional[str] = None
    start: Optional[str] = None
    finish: Optional[str] = None


class FlowEdgeSchema(BaseModel):
    from_id: str = Field(alias='from')
    to_id: str = Field(alias='to')
    type: str
    lag: int = 0
    kind: str


class SimulationOutput(BaseModel):
    project_meta: Dict[str, Any]
    questions: List[str] = Field(default_factory=list)
    decisions: List[Decision] = Field(default_factory=list)
    flow: Dict[str, List[Dict[str, Any]]] = Field(default_factory={'nodes': [], 'edges': []})
    statutory_pathway: List[Dict[str, Any]] = Field(default_factory=list)
    equipment_counts: List[Dict[str, Any]] = Field(default_factory=list)
    long_lead_register: List[Dict[str, Any]] = Field(default_factory=list)
    activities: List[Dict[str, Any]] = Field(default_factory=list)
    commissioning: List[Dict[str, Any]] = Field(default_factory=list)
    zones: List[Dict[str, Any]] = Field(default_factory=list)
    reasoning_trail: List[Dict[str, Any]] = Field(default_factory=list)
    quality: Dict[str, Any]
    flags: List[str] = Field(default_factory=list)
