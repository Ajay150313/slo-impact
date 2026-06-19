"""
slo-impact: Prometheus collector.

Reads availability metrics from a Prometheus HTTP API and converts them into
the slo-impact domain model.  Incident detection is based on dips in the
availability metric below the configured threshold.

Prerequisites
─────────────
Your Prometheus must expose an availability metric for each service.
The simplest form is an instant-vector expression that returns 0–1:

    # e.g. multi-window availability (1 − error_ratio)
    1 - (
      rate(http_requests_total{status=~"5..",job="my-service"}[5m])
      /
      rate(http_requests_total{job="my-service"}[5m])
    )

Set this as Service.prometheus_availability_query in your services config.

Configuration
─────────────
    SLO_IMPACT_PROMETHEUS_URL=http://prometheus:9090
    SLO_IMPACT_DEMO_MODE=false
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import httpx

from slo_impact.models import (
    Incident,
    Service,
    ServiceStatus,
    SeverityLevel,
    UptimePoint,
    WindowLabel,
)

logger = logging.getLogger(__name__)

# Availability below this ratio within a bucket is considered an incident.
_INCIDENT_THRESHOLD = 0.995
# Min gap between two incidents to be treated as separate events.
_MERGE_GAP_SECONDS = 300


class PrometheusCollector:
    """Fetch availability data from a live Prometheus instance."""

    def __init__(self, base_url: str, timeout: float = 10.0, step: str = "5m") -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._step = step
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_incidents(
        self,
        service: Service,
        window: WindowLabel,
    ) -> Sequence[Incident]:
        if not service.prometheus_availability_query:
            logger.warning(
                "Service %s has no prometheus_availability_query — skipping",
                service.id,
            )
            return []

        points = await self._query_range(
            service.prometheus_availability_query, window
        )
        if not points:
            return []

        return _detect_incidents(service, points)

    async def get_history(
        self,
        service: Service,
        window: WindowLabel,
    ) -> Sequence[UptimePoint]:
        if not service.prometheus_availability_query:
            return []

        points = await self._query_range(
            service.prometheus_availability_query, window
        )
        return [
            UptimePoint(
                timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                raw_availability=max(0.0, min(1.0, float(val))),
                # User-impact availability can be further refined if you
                # have a traffic-weighted metric; otherwise raw is used.
                user_impact_availability=max(0.0, min(1.0, float(val))),
            )
            for ts, val in points
        ]

    async def current_status(self, service: Service) -> ServiceStatus:
        """Query instant availability and map to ServiceStatus."""
        if not service.prometheus_availability_query:
            return ServiceStatus.OPERATIONAL

        result = await self._instant_query(service.prometheus_availability_query)
        if result is None:
            return ServiceStatus.OPERATIONAL

        val = float(result)
        if val >= 0.999:
            return ServiceStatus.OPERATIONAL
        if val >= 0.990:
            return ServiceStatus.DEGRADED
        if val >= 0.900:
            return ServiceStatus.PARTIAL_OUTAGE
        return ServiceStatus.MAJOR_OUTAGE

    # ------------------------------------------------------------------
    # Prometheus HTTP API helpers
    # ------------------------------------------------------------------

    async def _query_range(
        self,
        query: str,
        window: WindowLabel,
    ) -> list[tuple[float, float]]:
        now = datetime.now(tz=timezone.utc)
        start = now - timedelta(seconds=window.seconds)

        params = {
            "query": query,
            "start": start.timestamp(),
            "end": now.timestamp(),
            "step": self._step,
        }
        try:
            resp = await self._client.get(
                f"{self._base_url}/api/v1/query_range", params=params
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("Prometheus query_range failed: %s", exc)
            return []

        return _extract_range_values(data)

    async def _instant_query(self, query: str) -> Optional[float]:
        try:
            resp = await self._client.get(
                f"{self._base_url}/api/v1/query",
                params={"query": query},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("Prometheus instant query failed: %s", exc)
            return None

        results = data.get("data", {}).get("result", [])
        if not results:
            return None
        _, val = results[0]["value"]
        return float(val)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_range_values(data: dict[str, Any]) -> list[tuple[float, float]]:
    """Extract [(timestamp, value)] from a Prometheus query_range response."""
    results: list[tuple[float, float]] = []
    for series in data.get("data", {}).get("result", []):
        for ts, val in series.get("values", []):
            try:
                results.append((float(ts), float(val)))
            except (TypeError, ValueError):
                continue
    results.sort(key=lambda t: t[0])
    return results


def _detect_incidents(
    service: Service,
    points: list[tuple[float, float]],
) -> list[Incident]:
    """
    Walk the time-series and group consecutive below-threshold buckets into
    incident objects.  Adjacent incidents within MERGE_GAP_SECONDS are merged.
    """
    incidents: list[Incident] = []
    in_incident = False
    incident_start: Optional[float] = None
    min_val_in_window = 1.0

    for i, (ts, val) in enumerate(points):
        if val < _INCIDENT_THRESHOLD:
            if not in_incident:
                in_incident = True
                incident_start = ts
                min_val_in_window = val
            else:
                min_val_in_window = min(min_val_in_window, val)
        else:
            if in_incident:
                # Check whether the next point is also low (gap merge)
                if i + 1 < len(points):
                    next_ts, next_val = points[i + 1]
                    if (
                        next_val < _INCIDENT_THRESHOLD
                        and next_ts - ts <= _MERGE_GAP_SECONDS
                    ):
                        continue  # stay in incident across the gap

                # Close the incident
                severity = _severity_from_availability(min_val_in_window)
                inc = Incident(
                    id=f"{service.id}-prom-{int(incident_start or ts)}",
                    service_id=service.id,
                    title=f"Availability dip detected — {service.name}",
                    category="Detected Degradation",
                    started_at=datetime.fromtimestamp(
                        incident_start or ts, tz=timezone.utc
                    ),
                    ended_at=datetime.fromtimestamp(ts, tz=timezone.utc),
                    severity=severity,
                    # Conservative defaults — override via PagerDuty integration
                    user_fraction=severity.default_user_fraction,
                    traffic_weight=1.0,
                )
                incidents.append(inc)
                in_incident = False
                min_val_in_window = 1.0

    return incidents


def _severity_from_availability(availability: float) -> SeverityLevel:
    if availability >= 0.990:
        return SeverityLevel.DEGRADED
    if availability >= 0.900:
        return SeverityLevel.PARTIAL
    return SeverityLevel.OUTAGE
