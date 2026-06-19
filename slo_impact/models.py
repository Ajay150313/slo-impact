"""
slo-impact: data models.

All domain objects are Pydantic BaseModel so they serialize cleanly to/from
the REST API and can be validated at runtime.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SeverityLevel(str, Enum):
    """Incident impact severity."""

    DEGRADED = "degraded"  # Partial, reduced quality; most users unaffected
    PARTIAL = "partial"    # Significant impact; limited function for many users
    OUTAGE = "outage"      # Complete unavailability or severe data-path failure

    @property
    def default_user_fraction(self) -> float:
        """Conservative default user-fraction when not explicitly measured."""
        return {
            SeverityLevel.DEGRADED: 0.15,
            SeverityLevel.PARTIAL: 0.55,
            SeverityLevel.OUTAGE: 1.00,
        }[self]


class ServiceStatus(str, Enum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    PARTIAL_OUTAGE = "partial_outage"
    MAJOR_OUTAGE = "major_outage"


class WindowLabel(str, Enum):
    HOURS_24 = "24h"
    DAYS_7 = "7d"
    MONTHS_1 = "1m"
    MONTHS_3 = "3m"
    MONTHS_12 = "12m"

    @property
    def seconds(self) -> int:
        return {
            WindowLabel.HOURS_24: 86_400,
            WindowLabel.DAYS_7: 604_800,
            WindowLabel.MONTHS_1: 2_592_000,
            WindowLabel.MONTHS_3: 7_776_000,
            WindowLabel.MONTHS_12: 31_536_000,
        }[self]

    @property
    def resolution_seconds(self) -> int:
        """Recommended sparkline bucket size."""
        return {
            WindowLabel.HOURS_24: 3_600,       # 1h buckets → 24 points
            WindowLabel.DAYS_7: 21_600,        # 6h buckets → 28 points
            WindowLabel.MONTHS_1: 86_400,      # 1d buckets → 30 points
            WindowLabel.MONTHS_3: 259_200,     # 3d buckets → 30 points
            WindowLabel.MONTHS_12: 1_209_600,  # 2w buckets → 26 points
        }[self]


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------


class Incident(BaseModel):
    """A production incident and its user-impact metadata."""

    id: str
    service_id: str
    title: str
    category: str = "Other"
    started_at: datetime
    ended_at: Optional[datetime] = None
    severity: SeverityLevel = SeverityLevel.PARTIAL

    # User-impact dimensions — these are the key differentiators vs raw uptime
    user_fraction: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Fraction of the user base actually affected (0–1).",
    )
    traffic_weight: float = Field(
        default=1.0,
        ge=0.0,
        description=(
            "Traffic volume during incident as a ratio of baseline. "
            "A 3am incident with 10% of peak traffic → 0.1."
        ),
    )

    @model_validator(mode="after")
    def set_default_user_fraction(self) -> "Incident":
        # If caller left user_fraction at the Pydantic default (0.5) and
        # severity is provided, use the severity-based default.
        if self.user_fraction == 0.5:
            self.user_fraction = self.severity.default_user_fraction
        return self

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or datetime.utcnow()
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def user_impact_seconds(self) -> float:
        """Downtime weighted by user fraction and traffic volume."""
        return self.duration_seconds * self.user_fraction * self.traffic_weight

    @property
    def is_resolved(self) -> bool:
        return self.ended_at is not None


class Service(BaseModel):
    """A monitored service and its SLO configuration."""

    id: str
    name: str
    description: str = ""
    slo_target: float = Field(
        default=0.9999,
        ge=0.9,
        le=1.0,
        description="Target availability (e.g. 0.9999 = 99.99%).",
    )
    weight: float = Field(
        default=1.0,
        ge=0.0,
        description="Criticality weight for composite dashboard SLO.",
    )
    prometheus_availability_query: Optional[str] = Field(
        default=None,
        description=(
            "Prometheus instant query returning a 0–1 availability value. "
            "Leave null to use demo/synthetic data."
        ),
    )
    current_status: ServiceStatus = ServiceStatus.OPERATIONAL


class UptimePoint(BaseModel):
    """Single time-series data point for sparkline rendering."""

    timestamp: datetime
    raw_availability: float       # plain (downtime / window) availability
    user_impact_availability: float  # weighted by user_fraction * traffic_weight


class IncidentCategory(BaseModel):
    """Grouped incident cause for the bottom-panel attribution chart."""

    label: str
    duration_minutes: float
    hex_color: str = "#ef4444"


class ServiceUptimeResult(BaseModel):
    """Full uptime analysis for a single service over a time window."""

    service: Service
    window_label: WindowLabel
    window_seconds: float

    raw_uptime: float             # 0–1  e.g. 0.9997
    user_impact_uptime: float     # 0–1  always ≤ raw_uptime

    total_downtime_seconds: float
    user_impact_downtime_seconds: float
    total_customer_impact_minutes: float

    incidents: list[Incident]
    history: list[UptimePoint]

    @property
    def raw_uptime_pct(self) -> str:
        return f"{self.raw_uptime * 100:.2f}%"

    @property
    def user_impact_uptime_pct(self) -> str:
        return f"{self.user_impact_uptime * 100:.2f}%"

    @property
    def slo_met(self) -> bool:
        return self.user_impact_uptime >= self.service.slo_target

    @property
    def error_budget_total_seconds(self) -> float:
        return (1.0 - self.service.slo_target) * self.window_seconds

    @property
    def error_budget_consumed_pct(self) -> float:
        if self.error_budget_total_seconds <= 0:
            return 100.0
        return min(100.0, (self.user_impact_downtime_seconds / self.error_budget_total_seconds) * 100)

    @property
    def error_budget_remaining_seconds(self) -> float:
        return max(0.0, self.error_budget_total_seconds - self.user_impact_downtime_seconds)


class DashboardSummary(BaseModel):
    """Aggregated view across all services — the main API response."""

    overall_status: ServiceStatus
    composite_uptime: float          # weighted average across services
    total_customer_impact_minutes: float
    window_label: WindowLabel
    services: list[ServiceUptimeResult]
    incident_categories: list[IncidentCategory]
    last_updated: datetime

    @property
    def composite_uptime_pct(self) -> str:
        return f"{self.composite_uptime * 100:.2f}%"

    @property
    def total_impact_hours_minutes(self) -> str:
        h = int(self.total_customer_impact_minutes // 60)
        m = int(self.total_customer_impact_minutes % 60)
        return f"{h}h {m:02d}m" if h else f"{m}m"
