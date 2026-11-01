"""Warehouse master entity model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base

if TYPE_CHECKING:
    from backend.app.models.assignment import SlotAssignment
    from backend.app.models.location import Aisle
    from backend.app.models.orders import Order
    from backend.app.models.picking import Picker
    from backend.app.models.replenishment import Replenishment
    from backend.app.models.zone import Zone


class Warehouse(Base):
    """Physical warehouse facility containing storage zones, aisles, and docks."""

    __tablename__ = "warehouses"
    __table_args__ = (
        CheckConstraint("total_area_sqft > 0", name="ck_warehouses_area_positive"),
        CheckConstraint("daily_labor_minutes_capacity > 0", name="ck_warehouses_labor_positive"),
        Index("ix_warehouses_status", "status"),
    )

    warehouse_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    warehouse_name: Mapped[str] = mapped_column(String(120), nullable=False)
    warehouse_type: Mapped[str] = mapped_column(String(40), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(80), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=False)
    total_area_sqft: Mapped[float] = mapped_column(Float, nullable=False)
    operating_hours: Mapped[str] = mapped_column(String(40), nullable=False)
    daily_labor_minutes_capacity: Mapped[float] = mapped_column(Float, nullable=False, default=2400.0)
    depot_x: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    depot_y: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    zones: Mapped[list[Zone]] = relationship("Zone", back_populates="warehouse", cascade="all, delete-orphan")
    aisles: Mapped[list[Aisle]] = relationship("Aisle", back_populates="warehouse", cascade="all, delete-orphan")
    orders: Mapped[list[Order]] = relationship("Order", back_populates="warehouse")
    pickers: Mapped[list[Picker]] = relationship("Picker", back_populates="warehouse")
    replenishments: Mapped[list[Replenishment]] = relationship("Replenishment", back_populates="warehouse")
    slot_assignments: Mapped[list[SlotAssignment]] = relationship("SlotAssignment", back_populates="warehouse")
