from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = 'projects'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    client: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    sims: Mapped[List['SimulationRun']] = relationship(back_populates='project')


class Brief(Base):
    __tablename__ = 'briefs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey('projects.id'), nullable=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[str] = mapped_column(String(100), nullable=False)
    it_load_mw: Mapped[float] = mapped_column(Float, nullable=False)
    client: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    questions: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Decision(Base):
    __tablename__ = 'decisions'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey('projects.id'), nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class LibraryVersion(Base):
    __tablename__ = 'library_versions'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    libraries: Mapped[List['Library']] = relationship(back_populates='version_ref')
    simulations: Mapped[List['SimulationRun']] = relationship(back_populates='library_version_ref')


class Library(Base):
    __tablename__ = 'libraries'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    library_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey('library_versions.id'), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version_ref: Mapped[Optional['LibraryVersion']] = relationship(back_populates='libraries')


class SimulationRun(Base):
    __tablename__ = 'simulations'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    library_version: Mapped[str] = mapped_column(String(50), nullable=False)
    library_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey('library_versions.id'), nullable=True)
    corpus_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    project: Mapped['Project'] = relationship(back_populates='sims')
    library_version_ref: Mapped[Optional['LibraryVersion']] = relationship(back_populates='simulations')


class FlowNode(Base):
    __tablename__ = 'flow_nodes'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey('simulations.id'), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    dept: Mapped[str] = mapped_column(String(100), nullable=True)
    zone_id: Mapped[str] = mapped_column(String(100), nullable=True)
    trail_ref: Mapped[str] = mapped_column(String(255), nullable=True)
    start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finish: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class FlowEdge(Base):
    __tablename__ = 'flow_edges'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey('simulations.id'), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(50), nullable=False)
    lag_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Zone(Base):
    __tablename__ = 'zones'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey('simulations.id'), nullable=False)
    zone_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    geometry_ref: Mapped[str] = mapped_column(String(255), nullable=True)


class Activity(Base):
    __tablename__ = 'activities'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey('simulations.id'), nullable=False)
    wbs_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    calendar: Mapped[str] = mapped_column(String(100), nullable=False)
    dept_code: Mapped[str] = mapped_column(String(100), nullable=False)
    delivery_mode: Mapped[str] = mapped_column(String(100), nullable=False)
    zone_id: Mapped[str] = mapped_column(String(100), nullable=True)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    safety_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trail_ref: Mapped[str] = mapped_column(String(255), nullable=True)


class TrailEntry(Base):
    __tablename__ = 'trail_entries'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    simulation_id: Mapped[int] = mapped_column(ForeignKey('simulations.id'), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[List[str]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(100), nullable=True)
    hitl_tier: Mapped[str] = mapped_column(String(50), nullable=False)


class CorpusDoc(Base):
    __tablename__ = 'corpus_docs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(1536), nullable=True)
    embed_status: Mapped[str] = mapped_column(String(50), nullable=False)
    tags: Mapped[List[str]] = mapped_column(JSON, nullable=False)


class ComplianceRegister(Base):
    __tablename__ = 'compliance_registers'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    authority: Mapped[str] = mapped_column(String(255), nullable=False)
    gate_stage: Mapped[str] = mapped_column(String(100), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class Signoff(Base):
    __tablename__ = 'signoffs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    signed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = 'audit_log'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class CacheEntry(Base):
    __tablename__ = 'cache_entries'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    cache_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
