"""Zone, HazardClass, ZoneHazardAllowance bridge table, and CompatibilityRule models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base

if TYPE_CHECKING:
    from backend.app.models.location import Aisle, Location
    from backend.app.models.sku import SKU
    from backend.app.models.warehouse import Warehouse


class HazardClass(Base):
    """Standardized hazard classification taxonomy."""

    __tablename__ = "hazard_classes"

    hazard_class_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    class_name: Mapped[str] = mapped_column(String(80), nullable=False)
    requires_segregation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    severity_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    zone_allowances: Mapped[list[ZoneHazardAllowance]] = relationship(
        "ZoneHazardAllowance", back_populates="hazard_class", cascade="all, delete-orphan"
    )
    skus: Mapped[list[SKU]] = relationship("SKU", back_populates="hazard_ref")


class ZoneHazardAllowance(Base):
    """Many-to-many bridge entity linking storage zones to permitted hazard classes."""

    __tablename__ = "zone_hazard_allowances"

    zone_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("zones.zone_id", ondelete="CASCADE"), primary_key=True
    )
    hazard_class_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("hazard_classes.hazard_class_id", ondelete="CASCADE"), primary_key=True
    )

    zone: Mapped[Zone] = relationship("Zone", back_populates="hazard_allowances")
    hazard_class: Mapped[HazardClass] = relationship("HazardClass", back_populates="zone_allowances")


class Zone(Base):
    """Logical and environmental storage zone within a warehouse."""

    __tablename__ = "zones"
    __table_args__ = (
        CheckConstraint("max_weight_kg > 0", name="ck_zones_max_weight_positive"),
        CheckConstraint("max_volume_m3 > 0", name="ck_zones_max_volume_positive"),
        Index("ix_zones_warehouse_id", "warehouse_id"),
        Index("ix_zones_type_temp", "zone_type", "temperature_type"),
    )

    zone_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    warehouse_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("warehouses.warehouse_id", ondelete="CASCADE"), nullable=False
    )
    zone_name: Mapped[str] = mapped_column(String(100), nullable=False)
    zone_type: Mapped[str] = mapped_column(String(40), nullable=False)
    temperature_type: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_hazard_classes: Mapped[str] = mapped_column(String(255), nullable=False)
    max_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    max_volume_m3: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")

    warehouse: Mapped[Warehouse] = relationship("Warehouse", back_populates="zones")
    hazard_allowances: Mapped[list[ZoneHazardAllowance]] = relationship(
        "ZoneHazardAllowance", back_populates="zone", cascade="all, delete-orphan"
    )
    aisles: Mapped[list[Aisle]] = relationship("Aisle", back_populates="zone", cascade="all, delete-orphan")
    locations: Mapped[list[Location]] = relationship(
        "Location", back_populates="zone", cascade="all, delete-orphan"
    )


class CompatibilityRule(Base):
    """Explicit co-location and environmental compatibility rules."""

    __tablename__ = "compatibility_rules"
    __table_args__ = (Index("ix_compatibility_rules_type", "rule_type"),)

    rule_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    rule_type: Mapped[str] = mapped_column(String(48), nullable=False)
    primary_attribute: Mapped[str] = mapped_column(String(64), nullable=False)
    conflicting_attribute: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="SAME_LOCATION")
    severity: Mapped[str] = mapped_column(String(24), nullable=False, default="CRITICAL")
    is_hard_constraint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
