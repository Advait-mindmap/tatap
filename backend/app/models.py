from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    """A document in the retrieval corpus.

    `kind` matters for grounding. DOMAIN_KNOWLEDGE.md §1 requires the simulation to prefer
    REAL project precedent over generic norms and to cite which precedent it used, so a
    document that is not a real execution must never be cited as though it were. See
    `backend.app.rag.CorpusKind`.
    """

    __tablename__ = 'corpus_docs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(1536), nullable=True)
    embed_status: Mapped[str] = mapped_column(String(50), nullable=False)
    tags: Mapped[List[str]] = mapped_column(JSON, nullable=False)

    # --- added in Task 4 (corpus ingestion + retrieval) ---
    title: Mapped[str] = mapped_column(String(500), nullable=False, default='')
    content: Mapped[str] = mapped_column(Text, nullable=False, default='')
    # 'real_execution' | 'standard' | 'project_documentation' | 'synthetic_placeholder'
    kind: Mapped[str] = mapped_column(String(50), nullable=False, default='real_execution')
    # Human-verified in admin (ADMIN_SPEC.md §1). Never set true by an ingestion run.
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    corpus_version: Mapped[str] = mapped_column(String(50), nullable=False, default='v1')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    chunks: Mapped[List['CorpusChunk']] = relationship(
        back_populates='doc', cascade='all, delete-orphan'
    )


class CorpusChunk(Base):
    """An embedded slice of a CorpusDoc. Retrieval happens over these, not whole documents."""

    __tablename__ = 'corpus_chunks'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey('corpus_docs.id'), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(1536), nullable=True)
    embed_status: Mapped[str] = mapped_column(String(50), nullable=False, default='pending')
    embed_model: Mapped[str] = mapped_column(String(100), nullable=False, default='')
    corpus_version: Mapped[str] = mapped_column(String(50), nullable=False, default='v1')

    doc: Mapped['CorpusDoc'] = relationship(back_populates='chunks')


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


class PersistedRun(Base):
    """An in-flight simulation run, stored so it survives a process restart.

    The existing tables model a *finished* simulation projected into flow_nodes/activities/
    trail_entries. None of them hold what a half-walked run needs — which stages are done, which
    forks are open, the sequence number, and the per-stage reasoning already paid for. So this
    table exists rather than being forced into `simulations`, which requires a project row and
    describes a completed artefact.

    `state` is a serialised `RunState`; `reasonings` maps stage -> serialised `StageReasoning`.
    Both are plain data because the simulator was deliberately built as a data state machine
    (simulator/runner.py) rather than a suspended coroutine.

    Storing the reasoning matters as much as storing the state: without it, resuming would
    re-reason every completed stage and spend the model budget a second time for a plan the
    run already had.
    """

    __tablename__ = 'run_states'

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: running | halted | complete | error — for operators reading the table, not for the walk,
    #: which derives its own status from `state`.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='running')
    state: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    reasonings: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    stages: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class UsageCounter(Base):
    """A counted, capped resource for one UTC day.

    In the database rather than in memory on purpose. An in-process counter resets on every
    deploy and every container restart, so the cap it enforces is only ever as strong as the
    uptime — which on a platform that restarts freely is no cap at all.

    `scope` is the thing being counted: 'llm_calls' for the global provider budget, or
    'runs:<client>' for one caller's simulations.
    """

    __tablename__ = 'usage_counters'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    day: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint('day', 'scope', name='uq_usage_day_scope'),)
