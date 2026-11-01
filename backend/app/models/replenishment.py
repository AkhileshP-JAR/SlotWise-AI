"""Replenishment task and labor workload model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base

if TYPE_CHECKING:
    from backend.app.models.location import Location
    from backend.app.models.sku import SKU
    from backend.app.models.warehouse import Warehouse


class Replenishment(Base):
    """Internal stock transfer task replenishing forward pick locations from reserve or receiving."""

    __tablename__ = "replenishments"
    __table_args__ = (
        CheckConstraint("required_quantity > 0", name="ck_replenishments_req_qty_positive"),
        CheckConstraint("replenishment_quantity >= 0", name="ck_replenishments_rep_qty_nonneg"),
        CheckConstraint("labor_minutes_required >= 0", name="ck_replenishments_labor_req_nonneg"),
        CheckConstraint("assigned_labor_minutes >= 0", name="ck_replenishments_labor_assn_nonneg"),
        Index("ix_replenishments_warehouse_status", "warehouse_id", "replenishment_status"),
        Index("ix_replenishments_sku_id", "sku_id"),
    )

    replenishment_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    warehouse_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("warehouses.warehouse_id", ondelete="CASCADE"), nullable=False
    )
    sku_id: Mapped[str] = mapped_column(String(16), ForeignKey("skus.sku_id", ondelete="RESTRICT"), nullable=False)
    source_location_id: Mapped[str | None] = mapped_column(
        String(24), ForeignKey("locations.location_id", ondelete="SET NULL"), nullable=True
    )
    destination_location_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("locations.location_id", ondelete="RESTRICT"), nullable=False
    )
    required_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    replenishment_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[str] = mapped_column(String(24), nullable=False, default="NORMAL")
    labor_minutes_required: Mapped[float] = mapped_column(Float, nullable=False)
    assigned_labor_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    replenishment_status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    warehouse: Mapped[Warehouse] = relationship("Warehouse", back_populates="replenishments")
    sku: Mapped[SKU] = relationship("SKU", back_populates="replenishments")
    source_location: Mapped[Location | None] = relationship("Location", foreign_keys=[source_location_id])
    destination_location: Mapped[Location] = relationship("Location", foreign_keys=[destination_location_id])
