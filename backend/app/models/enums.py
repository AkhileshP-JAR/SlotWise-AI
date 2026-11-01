"""Domain enumerations and explicit lifecycle transition rules for SlotWise AI."""

from __future__ import annotations

from enum import StrEnum


class WarehouseType(StrEnum):
    REGIONAL_DC = "REGIONAL_DC"
    FULFILLMENT_CENTER = "FULFILLMENT_CENTER"
    COLD_STORAGE_HUB = "COLD_STORAGE_HUB"
    HAZMAT_FACILITY = "HAZMAT_FACILITY"


class EntityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    MAINTENANCE = "MAINTENANCE"
    RESTRICTED = "RESTRICTED"
    BLOCKED = "BLOCKED"
    INACTIVE = "INACTIVE"


class ZoneType(StrEnum):
    FAST_PICK = "FAST_PICK"
    GENERAL = "GENERAL"
    BULK_RESERVE = "BULK_RESERVE"
    COLD_CHAIN = "COLD_CHAIN"
    HAZMAT = "HAZMAT"
    OVERSIZE = "OVERSIZE"


class TemperatureType(StrEnum):
    AMBIENT = "AMBIENT"
    CONTROLLED_ROOM = "CONTROLLED_ROOM"
    CHILLED = "CHILLED"
    FROZEN = "FROZEN"


class HazardCode(StrEnum):
    NONE = "NONE"
    FLAMMABLE = "FLAMMABLE"
    CORROSIVE = "CORROSIVE"
    OXIDIZER = "OXIDIZER"
    TOXIC = "TOXIC"
    LITHIUM_BATTERY = "LITHIUM_BATTERY"


class AccessibilityType(StrEnum):
    GOLDEN_ZONE = "GOLDEN_ZONE"
    STANDARD_REACH = "STANDARD_REACH"
    FLOOR_PALLET = "FLOOR_PALLET"
    HIGH_RACK_FORKLIFT = "HIGH_RACK_FORKLIFT"


class StoragePolicy(StrEnum):
    DEDICATED_SINGLE_SKU = "DEDICATED_SINGLE_SKU"
    SHARED_COMPATIBLE = "SHARED_COMPATIBLE"


class StorageType(StrEnum):
    BIN = "BIN"
    SHELF = "SHELF"
    PALLET = "PALLET"
    OVERSIZE_FLOOR = "OVERSIZE_FLOOR"
    HAZMAT_CABINET = "HAZMAT_CABINET"


class SKUCategory(StrEnum):
    ELECTRONICS = "ELECTRONICS"
    CONSUMER_GOODS = "CONSUMER_GOODS"
    FOOD_BEVERAGE = "FOOD_BEVERAGE"
    PHARMACEUTICAL = "PHARMACEUTICAL"
    INDUSTRIAL_HARDWARE = "INDUSTRIAL_HARDWARE"
    CHEMICAL_CLEANING = "CHEMICAL_CLEANING"
    APPAREL = "APPAREL"
    COLD_PRODUCE = "COLD_PRODUCE"


class SKUStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    DISCONTINUED = "DISCONTINUED"


class DemandVelocityClass(StrEnum):
    FAST = "FAST"
    MEDIUM = "MEDIUM"
    SLOW = "SLOW"


class ABCClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class XYZClass(StrEnum):
    X = "X"
    Y = "Y"
    Z = "Z"


class OrderPriority(StrEnum):
    SAME_DAY = "SAME_DAY"
    EXPRESS = "EXPRESS"
    STANDARD = "STANDARD"
    BULK = "BULK"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    PICKING = "PICKING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PickEventStatus(StrEnum):
    COMPLETED = "COMPLETED"
    SHORT_PICK = "SHORT_PICK"
    BLOCKED_REROUTE = "BLOCKED_REROUTE"
    EXCEPTION = "EXCEPTION"


class ReplenishmentPriority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class ReplenishmentStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    OVERLOADED = "OVERLOADED"
    CANCELLED = "CANCELLED"


class AssignmentStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class OptimizationRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ExceptionCategory(StrEnum):
    BUSINESS_RULE_VIOLATION = "BUSINESS_RULE_VIOLATION"
    DATA_QUALITY_VIOLATION = "DATA_QUALITY_VIOLATION"
    OPERATIONAL_WARNING = "OPERATIONAL_WARNING"
    OPTIMIZATION_FAILURE = "OPTIMIZATION_FAILURE"


class ExceptionIssueType(StrEnum):
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    INCOMPATIBLE_COLOCATION = "INCOMPATIBLE_COLOCATION"
    DUPLICATE_ACTIVE_ASSIGNMENT = "DUPLICATE_ACTIVE_ASSIGNMENT"
    BLOCKED_AISLE_PICK_PATH = "BLOCKED_AISLE_PICK_PATH"
    REPLENISHMENT_LABOR_OVERLOAD = "REPLENISHMENT_LABOR_OVERLOAD"
    NO_COMPATIBLE_LOCATION = "NO_COMPATIBLE_LOCATION"
    CAPACITY_NEARLY_EXHAUSTED = "CAPACITY_NEARLY_EXHAUSTED"
    INVENTORY_EXCEEDS_ASSIGNED_CAPACITY = "INVENTORY_EXCEEDS_ASSIGNED_CAPACITY"
    MISSING_SKU_PROFILE = "MISSING_SKU_PROFILE"
    ORPHAN_FOREIGN_KEY = "ORPHAN_FOREIGN_KEY"
    INVALID_LIFECYCLE_TRANSITION = "INVALID_LIFECYCLE_TRANSITION"
    EXCESSIVE_REPLENISHMENT_FREQUENCY = "EXCESSIVE_REPLENISHMENT_FREQUENCY"


class ExceptionSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ExceptionResolutionStatus(StrEnum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


VALID_ASSIGNMENT_TRANSITIONS: dict[AssignmentStatus, set[AssignmentStatus]] = {
    AssignmentStatus.PROPOSED: {AssignmentStatus.ACTIVE, AssignmentStatus.REJECTED},
    AssignmentStatus.ACTIVE: {AssignmentStatus.SUPERSEDED},
    AssignmentStatus.SUPERSEDED: set(),
    AssignmentStatus.REJECTED: set(),
}

VALID_EXCEPTION_TRANSITIONS: dict[ExceptionResolutionStatus, set[ExceptionResolutionStatus]] = {
    ExceptionResolutionStatus.OPEN: {
        ExceptionResolutionStatus.IN_REVIEW,
        ExceptionResolutionStatus.RESOLVED,
        ExceptionResolutionStatus.DISMISSED,
    },
    ExceptionResolutionStatus.IN_REVIEW: {
        ExceptionResolutionStatus.RESOLVED,
        ExceptionResolutionStatus.DISMISSED,
        ExceptionResolutionStatus.OPEN,
    },
    ExceptionResolutionStatus.RESOLVED: {ExceptionResolutionStatus.IN_REVIEW},
    ExceptionResolutionStatus.DISMISSED: {ExceptionResolutionStatus.IN_REVIEW},
}

VALID_REPLENISHMENT_TRANSITIONS: dict[ReplenishmentStatus, set[ReplenishmentStatus]] = {
    ReplenishmentStatus.PENDING: {
        ReplenishmentStatus.IN_PROGRESS,
        ReplenishmentStatus.OVERLOADED,
        ReplenishmentStatus.CANCELLED,
    },
    ReplenishmentStatus.OVERLOADED: {
        ReplenishmentStatus.IN_PROGRESS,
        ReplenishmentStatus.CANCELLED,
    },
    ReplenishmentStatus.IN_PROGRESS: {
        ReplenishmentStatus.COMPLETED,
        ReplenishmentStatus.CANCELLED,
    },
    ReplenishmentStatus.COMPLETED: set(),
    ReplenishmentStatus.CANCELLED: set(),
}
