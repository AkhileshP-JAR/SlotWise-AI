"""Supplier and SKU master entity models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base

if TYPE_CHECKING:
    from backend.app.models.assignment import SlotAssignment
    from backend.app.models.orders import OrderLine
    from backend.app.models.picking import PickEvent
    from backend.app.models.replenishment import Replenishment
    from backend.app.models.sku_profile import SKUProfile
    from backend.app.models.zone import HazardClass


class Supplier(Base):
    """Upstream vendor supplying SKUs with lead-time parameters."""

    __tablename__ = "suppliers"
    __table_args__ = (CheckConstraint("average_lead_time_days > 0", name="ck_suppliers_lead_time_positive"),)

    supplier_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    supplier_name: Mapped[str] = mapped_column(String(120), nullable=False)
    country: Mapped[str] = mapped_column(String(80), nullable=False)
    average_lead_time_days: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    reliability_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.95)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")

    skus: Mapped[list[SKU]] = relationship("SKU", back_populates="supplier")


class SKU(Base):
    """Stock Keeping Unit master record with physical dimensions and handling constraints."""

    __tablename__ = "skus"
    __table_args__ = (
        CheckConstraint("unit_price > 0", name="ck_skus_price_positive"),
        CheckConstraint("unit_weight_kg > 0", name="ck_skus_weight_positive"),
        CheckConstraint("width_cm > 0", name="ck_skus_width_positive"),
        CheckConstraint("height_cm > 0", name="ck_skus_height_positive"),
        CheckConstraint("depth_cm > 0", name="ck_skus_depth_positive"),
        Index("ix_skus_category", "category"),
        Index("ix_skus_hazard_temp", "hazard_class", "temperature_requirement"),
        Index("ix_skus_status", "status"),
    )

    sku_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    sku_name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    unit_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    width_cm: Mapped[float] = mapped_column(Float, nullable=False)
    height_cm: Mapped[float] = mapped_column(Float, nullable=False)
    depth_cm: Mapped[float] = mapped_column(Float, nullable=False)
    is_fragile: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hazard_class: Mapped[str] = mapped_column(
        String(32), ForeignKey("hazard_classes.hazard_class_id", ondelete="RESTRICT"), nullable=False
    )
    temperature_requirement: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("suppliers.supplier_id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")

    supplier: Mapped[Supplier | None] = relationship("Supplier", back_populates="skus")
    hazard_ref: Mapped[HazardClass] = relationship("HazardClass", back_populates="skus")
    profiles: Mapped[list[SKUProfile]] = relationship(
        "SKUProfile", back_populates="sku", cascade="all, delete-orphan"
    )
    order_lines: Mapped[list[OrderLine]] = relationship("OrderLine", back_populates="sku")
    pick_events: Mapped[list[PickEvent]] = relationship("PickEvent", back_populates="sku")
    replenishments: Mapped[list[Replenishment]] = relationship("Replenishment", back_populates="sku")
    slot_assignments: Mapped[list[SlotAssignment]] = relationship("SlotAssignment", back_populates="sku")

    @property
    def unit_volume_m3(self) -> float:
        """Exact unit volume in cubic meters converted from cubic centimeters."""
        return (self.width_cm * self.height_cm * self.depth_cm) / 1_000_000.0
