"""Aisle geometry and storage Location ORM models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base

if TYPE_CHECKING:
    from backend.app.models.assignment import SlotAssignment
    from backend.app.models.picking import PickEvent
    from backend.app.models.warehouse import Warehouse
    from backend.app.models.zone import Zone


class Aisle(Base):
    """Physical aisle corridor within a warehouse zone used for route modeling and blockage tracking."""

    __tablename__ = "aisles"
    __table_args__ = (
        CheckConstraint("width_m > 0", name="ck_aisles_width_positive"),
        CheckConstraint("congestion_factor >= 1.0", name="ck_aisles_congestion_ge_one"),
        Index("ix_aisles_warehouse_zone", "warehouse_id", "zone_id"),
    )

    aisle_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    zone_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("zones.zone_id", ondelete="CASCADE"), nullable=False
    )
    warehouse_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("warehouses.warehouse_id", ondelete="CASCADE"), nullable=False
    )
    aisle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    x_start: Mapped[float] = mapped_column(Float, nullable=False)
    y_start: Mapped[float] = mapped_column(Float, nullable=False)
    x_end: Mapped[float] = mapped_column(Float, nullable=False)
    y_end: Mapped[float] = mapped_column(Float, nullable=False)
    width_m: Mapped[float] = mapped_column(Float, nullable=False, default=3.0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    congestion_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")

    warehouse: Mapped[Warehouse] = relationship("Warehouse", back_populates="aisles")
    zone: Mapped[Zone] = relationship("Zone", back_populates="aisles")
    locations: Mapped[list[Location]] = relationship("Location", back_populates="aisle")


class Location(Base):
    """Discrete addressable storage slot (bin, shelf, or pallet bay) inside a zone."""

    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("width_cm > 0", name="ck_locations_width_positive"),
        CheckConstraint("height_cm > 0", name="ck_locations_height_positive"),
        CheckConstraint("depth_cm > 0", name="ck_locations_depth_positive"),
        CheckConstraint("max_weight_kg > 0", name="ck_locations_max_weight_positive"),
        CheckConstraint("max_volume_m3 > 0", name="ck_locations_max_volume_positive"),
        CheckConstraint("current_weight_kg >= 0", name="ck_locations_curr_weight_nonneg"),
        CheckConstraint("current_volume_m3 >= 0", name="ck_locations_curr_volume_nonneg"),
        Index("ix_locations_zone_id", "zone_id"),
        Index("ix_locations_aisle_id", "aisle_id"),
        Index("ix_locations_status_blocked", "status", "is_blocked"),
    )

    location_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    zone_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("zones.zone_id", ondelete="CASCADE"), nullable=False
    )
    aisle_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("aisles.aisle_id", ondelete="RESTRICT"), nullable=False
    )
    rack_id: Mapped[str] = mapped_column(String(24), nullable=False)
    shelf_level: Mapped[int] = mapped_column(Integer, nullable=False)
    bin_id: Mapped[str] = mapped_column(String(24), nullable=False)
    x_coordinate: Mapped[float] = mapped_column(Float, nullable=False)
    y_coordinate: Mapped[float] = mapped_column(Float, nullable=False)
    width_cm: Mapped[float] = mapped_column(Float, nullable=False)
    height_cm: Mapped[float] = mapped_column(Float, nullable=False)
    depth_cm: Mapped[float] = mapped_column(Float, nullable=False)
    max_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    max_volume_m3: Mapped[float] = mapped_column(Float, nullable=False)
    current_weight_kg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_volume_m3: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    accessibility_type: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="DEDICATED_SINGLE_SKU")
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")

    zone: Mapped[Zone] = relationship("Zone", back_populates="locations")
    aisle: Mapped[Aisle] = relationship("Aisle", back_populates="locations")
    slot_assignments: Mapped[list[SlotAssignment]] = relationship("SlotAssignment", back_populates="location")
    pick_events: Mapped[list[PickEvent]] = relationship("PickEvent", back_populates="location")
