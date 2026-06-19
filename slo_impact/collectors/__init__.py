"""Collector protocol — all data sources implement this interface."""

from __future__ import annotations

from typing import Protocol, Sequence

from slo_impact.models import Incident, Service, UptimePoint, WindowLabel


class Collector(Protocol):
    """
    Abstract data-source interface.

    Implement this protocol to plug in Prometheus, Datadog, PagerDuty,
    or any custom data source without touching the rest of the codebase.
    """

    async def get_incidents(
        self,
        service: Service,
        window: WindowLabel,
    ) -> Sequence[Incident]:
        """Return all incidents that overlap with the given window."""
        ...

    async def get_history(
        self,
        service: Service,
        window: WindowLabel,
    ) -> Sequence[UptimePoint]:
        """Return time-series availability points for sparkline rendering."""
        ...
