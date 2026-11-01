"""OptimizationRun, SlotAssignment, AssignmentReason, and Scenario persistence models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base

if TYPE_CHECKING:
    from backend.app.models.location import Location
    from backend.app.models.sku import SKU
    from backend.app.models.warehouse import Warehouse


class OptimizationRun(Base):
    """Immutable execution trace record for each slotting optimization invocation."""

    __tablename__ = "optimization_runs"
    __table_args__ = (
        Index("ix_optimization_runs_warehouse_scope", "warehouse_scope"),
        Index("ix_optimization_runs_created_at", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    warehouse_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    zone_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm_name: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    scoring_weights_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    sku_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assignment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unassigned_sku_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    exception_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kpi_results_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    execution_duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    run_status: Mapped[str] = mapped_column(String(24), nullable=False, default="COMPLETED")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    assignments: Mapped[list[SlotAssignment]] = relationship("SlotAssignment", back_populates="optimization_run")
    candidate_reasons: Mapped[list[AssignmentReason]] = relationship(
        "AssignmentReason", back_populates="optimization_run", cascade="all, delete-orphan"
    )


class SlotAssignment(Base):
    """Versioned SKU-to-location slotting decision. Historical records are never overwritten."""

    __tablename__ = "slot_assignments"
    __table_args__ = (
        CheckConstraint("assigned_quantity >= 0", name="ck_slot_assignments_qty_nonneg"),
        CheckConstraint("decision_version >= 1", name="ck_slot_assignments_version_pos"),
        Index("ix_slot_assignments_sku_status", "sku_id", "assignment_status"),
        Index("ix_slot_assignments_location_status", "location_id", "assignment_status"),
        Index("ix_slot_assignments_warehouse_status", "warehouse_id", "assignment_status"),
        Index("ix_slot_assignments_run_id", "optimization_run_id"),
    )

    slot_assignment_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    optimization_run_id: Mapped[str | None] = mapped_column(
        String(24), ForeignKey("optimization_runs.run_id", ondelete="SET NULL"), nullable=True
    )
    sku_id: Mapped[str] = mapped_column(String(16), ForeignKey("skus.sku_id", ondelete="RESTRICT"), nullable=False)
    location_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("locations.location_id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("warehouses.warehouse_id", ondelete="RESTRICT"), nullable=False
    )
    assignment_status: Mapped[str] = mapped_column(String(24), nullable=False)
    assigned_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_weight_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    allocated_volume_m3: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    assignment_score: Mapped[float] = mapped_column(Float, nullable=False)
    distance_score: Mapped[float] = mapped_column(Float, nullable=False)
    compatibility_score: Mapped[float] = mapped_column(Float, nullable=False)
    capacity_score: Mapped[float] = mapped_column(Float, nullable=False)
    velocity_score: Mapped[float] = mapped_column(Float, nullable=False)
    pick_frequency_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    replenishment_score: Mapped[float] = mapped_column(Float, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False)

    optimization_run: Mapped[OptimizationRun | None] = relationship(
        "OptimizationRun", back_populates="assignments"
    )
    sku: Mapped[SKU] = relationship("SKU", back_populates="slot_assignments")
    location: Mapped[Location] = relationship("Location", back_populates="slot_assignments")
    warehouse: Mapped[Warehouse] = relationship("Warehouse", back_populates="slot_assignments")


class AssignmentReason(Base):
    """Auditability record storing evaluated candidate scores, constraints checked, and rejection reasons."""

    __tablename__ = "assignment_reasons"
    __table_args__ = (
        Index("ix_assignment_reasons_run_sku", "optimization_run_id", "sku_id"),
        Index("ix_assignment_reasons_sku_loc", "sku_id", "candidate_location_id"),
    )

    reason_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    optimization_run_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("optimization_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    slot_assignment_id: Mapped[str | None] = mapped_column(
        String(24), ForeignKey("slot_assignments.slot_assignment_id", ondelete="SET NULL"), nullable=True
    )
    sku_id: Mapped[str] = mapped_column(String(16), ForeignKey("skus.sku_id", ondelete="CASCADE"), nullable=False)
    candidate_location_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=False
    )
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_feasible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rank_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_breakdown_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    hard_constraints_checked_json: Mapped[dict[str, bool]] = mapped_column(JSON, nullable=False)
    rejection_reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    optimization_run: Mapped[OptimizationRun] = relationship("OptimizationRun", back_populates="candidate_reasons")


class Scenario(Base):
    """What-if scenario comparison contrasting baseline slotting against an optimization run."""

    __tablename__ = "scenarios"
    __table_args__ = (Index("ix_scenarios_warehouse_id", "warehouse_id"),)

    scenario_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    scenario_name: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    warehouse_id: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("warehouses.warehouse_id", ondelete="SET NULL"), nullable=True
    )
    optimization_run_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("optimization_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    baseline_metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    optimized_metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    comparison_delta_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
