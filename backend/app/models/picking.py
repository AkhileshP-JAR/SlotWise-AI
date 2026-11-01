"""Picker workforce and PickEvent execution trace models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base

if TYPE_CHECKING:
    from backend.app.models.location import Location
    from backend.app.models.orders import Order, OrderLine
    from backend.app.models.sku import SKU
    from backend.app.models.warehouse import Warehouse


class Picker(Base):
    """Warehouse picking associate executing order fulfillment tours."""

    __tablename__ = "pickers"
    __table_args__ = (
        CheckConstraint("walk_speed_mps > 0", name="ck_pickers_walk_speed_positive"),
        Index("ix_pickers_warehouse_id", "warehouse_id"),
    )

    picker_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    warehouse_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("warehouses.warehouse_id", ondelete="CASCADE"), nullable=False
    )
    picker_name: Mapped[str] = mapped_column(String(100), nullable=False)
    shift: Mapped[str] = mapped_column(String(24), nullable=False, default="DAY")
    walk_speed_mps: Mapped[float] = mapped_column(Float, nullable=False, default=1.25)
    pick_rate_per_hour: Mapped[float] = mapped_column(Float, nullable=False, default=65.0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")

    warehouse: Mapped[Warehouse] = relationship("Warehouse", back_populates="pickers")
    pick_events: Mapped[list[PickEvent]] = relationship("PickEvent", back_populates="picker")


class PickEvent(Base):
    """Operational pick execution record linking an order line to a physical storage location."""

    __tablename__ = "pick_events"
    __table_args__ = (
        CheckConstraint("quantity_picked >= 0", name="ck_pick_events_qty_nonneg"),
        CheckConstraint("pick_duration_seconds >= 0", name="ck_pick_events_duration_nonneg"),
        Index("ix_pick_events_order_id", "order_id"),
        Index("ix_pick_events_sku_location", "sku_id", "location_id"),
        Index("ix_pick_events_status", "event_status"),
    )

    pick_event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False
    )
    order_line_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("order_lines.order_line_id", ondelete="CASCADE"), nullable=False
    )
    sku_id: Mapped[str] = mapped_column(String(16), ForeignKey("skus.sku_id", ondelete="RESTRICT"), nullable=False)
    location_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("locations.location_id", ondelete="RESTRICT"), nullable=False
    )
    quantity_picked: Mapped[int] = mapped_column(Integer, nullable=False)
    picker_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("pickers.picker_id", ondelete="RESTRICT"), nullable=False
    )
    pick_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    pick_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    travel_distance_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    aisle_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_status: Mapped[str] = mapped_column(String(24), nullable=False)

    order: Mapped[Order] = relationship("Order", back_populates="pick_events")
    order_line: Mapped[OrderLine] = relationship("OrderLine", back_populates="pick_events")
    sku: Mapped[SKU] = relationship("SKU", back_populates="pick_events")
    location: Mapped[Location] = relationship("Location", back_populates="pick_events")
    picker: Mapped[Picker] = relationship("Picker", back_populates="pick_events")
