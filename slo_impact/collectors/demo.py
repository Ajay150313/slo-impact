"""
slo-impact: demo / synthetic data collector.

Generates realistic-looking banking-platform incident history without
any real data source.  This is the default when prometheus_url is not set.

The generated data is deterministic (seeded by service id + window) so the
dashboard looks stable across page refreshes.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Sequence

from slo_impact.models import (
    Incident,
    Service,
    ServiceStatus,
    SeverityLevel,
    UptimePoint,
    WindowLabel,
)


# ---------------------------------------------------------------------------
# Incident templates (generic, no employer references)
# ---------------------------------------------------------------------------

_INCIDENT_TEMPLATES: list[dict] = [
    {
        "title": "Elevated error rates — upstream dependency timeout",
        "category": "Elevated Error Rates",
        "severity": SeverityLevel.DEGRADED,
        "duration_range": (20, 55),
        "user_fraction_range": (0.08, 0.20),
    },
    {
        "title": "Auth provider latency spike",
        "category": "Auth Provider Outage",
        "severity": SeverityLevel.PARTIAL,
        "duration_range": (15, 35),
        "user_fraction_range": (0.40, 0.70),
    },
    {
        "title": "Scheduled maintenance window — patch cycle",
        "category": "Patch Incident",
        "severity": SeverityLevel.DEGRADED,
        "duration_range": (45, 90),
        "user_fraction_range": (0.10, 0.25),
    },
    {
        "title": "DNS resolution failure — CDN edge node",
        "category": "Other Incidents",
        "severity": SeverityLevel.PARTIAL,
        "duration_range": (10, 25),
        "user_fraction_range": (0.20, 0.50),
    },
    {
        "title": "Database connection pool exhaustion",
        "category": "Elevated Error Rates",
        "severity": SeverityLevel.PARTIAL,
        "duration_range": (8, 20),
        "user_fraction_range": (0.30, 0.60),
    },
    {
        "title": "Certificate renewal automation gap",
        "category": "Other Incidents",
        "severity": SeverityLevel.DEGRADED,
        "duration_range": (5, 15),
        "user_fraction_range": (0.05, 0.15),
    },
]

# Services with higher criticality weight get proportionally fewer incidents.
_INCIDENTS_PER_YEAR_BY_WEIGHT = {
    3.0: 2,   # critical path → very low incident rate
    2.5: 3,
    1.5: 4,
    1.0: 5,
    0.8: 4,
}


class DemoCollector:
    """Synthetic data collector — no external dependencies required."""

    async def get_incidents(
        self,
        service: Service,
        window: WindowLabel,
    ) -> Sequence[Incident]:
        rng = _seeded_rng(service.id, window.value)
        now = datetime.now(tz=timezone.utc)
        window_start = now - timedelta(seconds=window.seconds)

        yearly_count = _INCIDENTS_PER_YEAR_BY_WEIGHT.get(
            round(service.weight, 1), 4
        )
        # Scale by window fraction of a year
        expected = max(1, round(yearly_count * window.seconds / 31_536_000))
        count = rng.randint(max(1, expected - 1), expected + 2)

        incidents: list[Incident] = []
        for i in range(count):
            tmpl = rng.choice(_INCIDENT_TEMPLATES)

            # Spread incidents across the window
            offset_frac = rng.uniform(0.01, 0.99)
            started_at = window_start + timedelta(
                seconds=window.seconds * offset_frac
            )
            duration_m = rng.uniform(*tmpl["duration_range"])  # type: ignore[arg-type]
            ended_at = started_at + timedelta(minutes=duration_m)
            if ended_at > now:
                ended_at = now

            # Traffic weight: lower at night, higher during business hours
            hour = started_at.hour
            traffic = _traffic_weight_for_hour(hour, rng)

            user_fraction = rng.uniform(*tmpl["user_fraction_range"])  # type: ignore[arg-type]

            incidents.append(
                Incident(
                    id=f"{service.id}-inc-{i}",
                    service_id=service.id,
                    title=tmpl["title"],
                    category=tmpl["category"],
                    started_at=started_at,
                    ended_at=ended_at,
                    severity=tmpl["severity"],
                    user_fraction=round(user_fraction, 3),
                    traffic_weight=round(traffic, 3),
                )
            )

        return incidents

    async def get_history(
        self,
        service: Service,
        window: WindowLabel,
    ) -> Sequence[UptimePoint]:
        """
        Generate sparkline data that visually matches what you'd see in a
        real production dashboard — mostly flat high availability with
        occasional dips corresponding to the generated incidents.
        """
        rng = _seeded_rng(service.id + "_hist", window.value)
        now = datetime.now(tz=timezone.utc)
        window_start = now - timedelta(seconds=window.seconds)
        step = window.resolution_seconds

        # Pre-compute incidents so dips line up with actual incident timestamps
        incidents = await self.get_incidents(service, window)

        points: list[UptimePoint] = []
        t = window_start
        while t <= now:
            raw_avail = _availability_at(t, step, incidents, rng, service.slo_target)
            impact_avail = min(raw_avail, raw_avail - rng.uniform(0, 0.0002))
            points.append(
                UptimePoint(
                    timestamp=t,
                    raw_availability=max(0.95, min(1.0, raw_avail)),
                    user_impact_availability=max(0.95, min(1.0, impact_avail)),
                )
            )
            t += timedelta(seconds=step)

        return points

    def current_status(self, service: Service) -> ServiceStatus:
        """All services nominal in demo mode — override if you need incidents."""
        return ServiceStatus.OPERATIONAL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seeded_rng(service_id: str, window: str) -> random.Random:
    seed_str = f"{service_id}:{window}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16) % (2**31)
    return random.Random(seed)


def _traffic_weight_for_hour(hour: int, rng: random.Random) -> float:
    """
    Sinusoidal daily traffic curve: peak at 14:00, trough at 03:00.
    Noise is added to make successive incidents look different.
    """
    base = 0.3 + 0.7 * (0.5 + 0.5 * math.cos(math.pi * (hour - 14) / 12))
    noise = rng.gauss(0, 0.05)
    return max(0.05, min(1.0, base + noise))


def _availability_at(
    ts: datetime,
    step: int,
    incidents: Sequence[Incident],
    rng: random.Random,
    baseline: float,
) -> float:
    """
    Return the availability for a single sparkline bucket.
    Drops significantly if an incident overlaps the bucket, otherwise
    stays very close to the SLO target with tiny random noise.
    """
    bucket_end = ts + timedelta(seconds=step)
    for inc in incidents:
        inc_start = inc.started_at
        inc_end = inc.ended_at or (inc_start + timedelta(hours=1))
        overlap = (
            min(bucket_end, inc_end) - max(ts, inc_start)
        ).total_seconds()
        if overlap > 0:
            drop = (overlap / step) * inc.user_fraction * rng.uniform(0.8, 1.2)
            return max(0.90, baseline - drop * 0.05)

    # Nominal: tiny jitter around baseline
    noise = rng.gauss(0, 0.00005)
    return max(baseline - 0.0005, min(1.0, baseline + noise))
