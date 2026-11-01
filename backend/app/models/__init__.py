"""Unified export of all SQLAlchemy 2.0 ORM models for SlotWise AI."""

from backend.app.database import Base
from backend.app.models.assignment import AssignmentReason, OptimizationRun, Scenario, SlotAssignment
from backend.app.models.audit import AuditEvent, ExceptionRecord
from backend.app.models.location import Aisle, Location
from backend.app.models.orders import Order, OrderLine
from backend.app.models.picking import Picker, PickEvent
from backend.app.models.replenishment import Replenishment
from backend.app.models.sku import SKU, Supplier
from backend.app.models.sku_profile import SKUProfile
from backend.app.models.warehouse import Warehouse
from backend.app.models.zone import CompatibilityRule, HazardClass, Zone, ZoneHazardAllowance

__all__ = [
    "Aisle",
    "AssignmentReason",
    "AuditEvent",
    "Base",
    "CompatibilityRule",
    "ExceptionRecord",
    "HazardClass",
    "Location",
    "OptimizationRun",
    "Order",
    "OrderLine",
    "PickEvent",
    "Picker",
    "Replenishment",
    "SKU",
    "SKUProfile",
    "Scenario",
    "SlotAssignment",
    "Supplier",
    "Warehouse",
    "Zone",
    "ZoneHazardAllowance",
]
