"""Order and OrderLine transaction models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base

if TYPE_CHECKING:
    from backend.app.models.picking import PickEvent
    from backend.app.models.sku import SKU
    from backend.app.models.warehouse import Warehouse


class Order(Base):
    """Customer outbound fulfillment order routed to a warehouse."""

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_warehouse_date", "warehouse_id", "order_date"),
        Index("ix_orders_status_priority", "order_status", "order_priority"),
    )

    order_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    warehouse_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("warehouses.warehouse_id", ondelete="RESTRICT"), nullable=False
    )
    order_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    order_priority: Mapped[str] = mapped_column(String(24), nullable=False)
    order_status: Mapped[str] = mapped_column(String(24), nullable=False)
    customer_region: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    warehouse: Mapped[Warehouse] = relationship("Warehouse", back_populates="orders")
    order_lines: Mapped[list[OrderLine]] = relationship(
        "OrderLine", back_populates="order", cascade="all, delete-orphan"
    )
    pick_events: Mapped[list[PickEvent]] = relationship("PickEvent", back_populates="order")


class OrderLine(Base):
    """Line-item SKU demand within an outbound order."""

    __tablename__ = "order_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_lines_quantity_positive"),
        CheckConstraint("unit_price > 0", name="ck_order_lines_price_positive"),
        CheckConstraint("line_value >= 0", name="ck_order_lines_value_nonneg"),
        Index("ix_order_lines_order_id", "order_id"),
        Index("ix_order_lines_sku_id", "sku_id"),
    )

    order_line_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        String(24), ForeignKey("orders.order_id", ondelete="CASCADE"), nullable=False
    )
    sku_id: Mapped[str] = mapped_column(String(16), ForeignKey("skus.sku_id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    line_value: Mapped[float] = mapped_column(Float, nullable=False)

    order: Mapped[Order] = relationship("Order", back_populates="order_lines")
    sku: Mapped[SKU] = relationship("SKU", back_populates="order_lines")
    pick_events: Mapped[list[PickEvent]] = relationship("PickEvent", back_populates="order_line")
