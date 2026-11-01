"""SKUProfile demand velocity, ABC/XYZ classification, and inventory parameter model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base

if TYPE_CHECKING:
    from backend.app.models.sku import SKU


class SKUProfile(Base):
    """Calculated demand velocity, variability, ABC/XYZ classes, and reorder parameters for a SKU."""

    __tablename__ = "sku_profiles"
    __table_args__ = (
        CheckConstraint("average_daily_demand >= 0", name="ck_sku_profiles_adv_nonneg"),
        CheckConstraint("demand_std_dev >= 0", name="ck_sku_profiles_std_nonneg"),
        CheckConstraint("pick_frequency >= 0", name="ck_sku_profiles_pick_freq_nonneg"),
        CheckConstraint("safety_stock >= 0", name="ck_sku_profiles_safety_stock_nonneg"),
        CheckConstraint("reorder_point >= 0", name="ck_sku_profiles_rop_nonneg"),
        Index("ix_sku_profiles_sku_id", "sku_id"),
        Index("ix_sku_profiles_velocity_abc", "demand_velocity", "abc_class"),
    )

    profile_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sku_id: Mapped[str] = mapped_column(String(16), ForeignKey("skus.sku_id", ondelete="CASCADE"), nullable=False)
    average_daily_demand: Mapped[float] = mapped_column(Float, nullable=False)
    demand_std_dev: Mapped[float] = mapped_column(Float, nullable=False)
    demand_velocity: Mapped[str] = mapped_column(String(16), nullable=False)
    velocity_percentile: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pick_frequency: Mapped[float] = mapped_column(Float, nullable=False)
    total_units_sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_order_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    abc_class: Mapped[str] = mapped_column(String(4), nullable=False)
    xyz_class: Mapped[str] = mapped_column(String(4), nullable=False)
    turnover_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    safety_stock: Mapped[float] = mapped_column(Float, nullable=False)
    reorder_point: Mapped[float] = mapped_column(Float, nullable=False)
    target_days_of_supply: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    current_on_hand_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replenishment_frequency: Mapped[float] = mapped_column(Float, nullable=False)
    peak_demand_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    effective_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sku: Mapped[SKU] = relationship("SKU", back_populates="profiles")
