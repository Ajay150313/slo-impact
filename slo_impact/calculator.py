"""
slo-impact: user-impact-weighted uptime calculator.

─────────────────────────────────────────────────────────────────────────────
WHY THIS EXISTS
─────────────────────────────────────────────────────────────────────────────
Raw availability (total_time - downtime) / total_time doesn't capture what
engineers actually care about: how many users were affected, for how long, at
what traffic level?

A 5-minute database outage at 3 AM with 3% of peak traffic has a dramatically
different customer impact than a 5-minute outage at noon.  Traditional SLOs
treat them identically.

USER-IMPACT UPTIME
─────────────────────────────────────────────────────────────────────────────
For each incident we record two additional dimensions:

  user_fraction   — what fraction of the user base experienced degradation
                    (e.g. 0.60 = 60% of users were affected)

  traffic_weight  — traffic volume during the incident as a ratio of the
                    rolling baseline
                    (e.g. 0.08 = incident occurred at 8% of peak traffic)

User-impact downtime for a single incident:

  impact_seconds = duration_seconds × user_fraction × traffic_weight

Aggregate across all incidents in the window:

  total_impact_seconds = Σ impact_seconds

User-impact uptime:

  user_impact_uptime = 1 − (total_impact_seconds / window_seconds)

Because user_fraction ≤ 1 and traffic_weight can be < 1, user-impact uptime
is always ≥ raw uptime.  The gap tells you how efficiently your incidents are
distributed across user exposure — it is the metric your customers experience.

SLO TRUTH
─────────────────────────────────────────────────────────────────────────────
We evaluate SLO targets against user_impact_uptime, not raw availability.
"If your SLO has never been breached, it isn't an SLO. It's a slogan."
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Sequence

from slo_impact.models import (
    DashboardSummary,
    Incident,
    IncidentCategory,
    Service,
    ServiceStatus,
    ServiceUptimeResult,
    UptimePoint,
    WindowLabel,
)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def calculate_service_uptime(
    service: Service,
    incidents: Sequence[Incident],
    history: Sequence[UptimePoint],
    window: WindowLabel,
) -> ServiceUptimeResult:
    """
    Compute raw and user-impact uptime for a single service.

    Parameters
    ----------
    service:   Service definition including SLO target.
    incidents: All incidents that overlap with the measurement window.
    history:   Pre-computed time-series points (from collector).
    window:    Measurement window enum (drives window_seconds).
    """
    window_seconds = float(window.seconds)

    raw_downtime = _sum_raw_downtime(incidents, window_seconds)
    impact_downtime = _sum_impact_downtime(incidents, window_seconds)

    raw_uptime = max(0.0, 1.0 - raw_downtime / window_seconds)
    user_impact_uptime = max(0.0, 1.0 - impact_downtime / window_seconds)

    customer_impact_minutes = impact_downtime / 60.0

    return ServiceUptimeResult(
        service=service,
        window_label=window,
        window_seconds=window_seconds,
        raw_uptime=raw_uptime,
        user_impact_uptime=user_impact_uptime,
        total_downtime_seconds=raw_downtime,
        user_impact_downtime_seconds=impact_downtime,
        total_customer_impact_minutes=customer_impact_minutes,
        incidents=list(incidents),
        history=list(history),
    )


def build_dashboard(
    service_results: Sequence[ServiceUptimeResult],
    window: WindowLabel,
) -> DashboardSummary:
    """
    Aggregate per-service results into the top-level dashboard response.

    Composite uptime is a weighted average using Service.weight.
    """
    total_weight = sum(r.service.weight for r in service_results)
    if total_weight == 0:
        total_weight = 1.0  # guard against divide-by-zero

    composite = sum(
        r.user_impact_uptime * r.service.weight for r in service_results
    ) / total_weight

    total_impact = sum(r.total_customer_impact_minutes for r in service_results)

    overall_status = _aggregate_status(service_results)
    categories = _categorise_incidents(service_results)

    return DashboardSummary(
        overall_status=overall_status,
        composite_uptime=composite,
        total_customer_impact_minutes=total_impact,
        window_label=window,
        services=list(service_results),
        incident_categories=categories,
        last_updated=datetime.now(tz=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sum_raw_downtime(incidents: Sequence[Incident], window_cap: float) -> float:
    """
    Sum incident durations, capped at window_cap so a long-running incident
    does not produce negative uptime.
    """
    return min(window_cap, sum(i.duration_seconds for i in incidents))


def _sum_impact_downtime(incidents: Sequence[Incident], window_cap: float) -> float:
    """
    Sum user-impact-weighted downtime seconds, capped at window_cap.

    Each incident contributes:
        duration × user_fraction × traffic_weight

    This means an incident at 5% traffic that affects 60% of users contributes
    only 3% of its duration to the impact bucket.
    """
    total = sum(i.user_impact_seconds for i in incidents)
    return min(window_cap, total)


def _aggregate_status(results: Sequence[ServiceUptimeResult]) -> ServiceStatus:
    """
    Return the worst current status across all services.
    Priority: MAJOR_OUTAGE > PARTIAL_OUTAGE > DEGRADED > OPERATIONAL
    """
    priority = {
        ServiceStatus.MAJOR_OUTAGE: 3,
        ServiceStatus.PARTIAL_OUTAGE: 2,
        ServiceStatus.DEGRADED: 1,
        ServiceStatus.OPERATIONAL: 0,
    }
    worst = ServiceStatus.OPERATIONAL
    for r in results:
        if priority[r.service.current_status] > priority[worst]:
            worst = r.service.current_status
    return worst


def _categorise_incidents(
    results: Sequence[ServiceUptimeResult],
) -> list[IncidentCategory]:
    """
    Group incidents by category and sum their user-impact minutes.
    Returns categories sorted descending by duration.
    """
    palette = [
        "#ef4444",  # red
        "#f97316",  # orange
        "#eab308",  # yellow
        "#8b5cf6",  # violet
        "#06b6d4",  # cyan
    ]

    bucket: dict[str, float] = defaultdict(float)
    for result in results:
        for incident in result.incidents:
            bucket[incident.category] += incident.user_impact_seconds / 60.0

    sorted_cats = sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)

    categories = []
    for idx, (label, minutes) in enumerate(sorted_cats):
        categories.append(
            IncidentCategory(
                label=label,
                duration_minutes=round(minutes, 1),
                hex_color=palette[idx % len(palette)],
            )
        )
    return categories
