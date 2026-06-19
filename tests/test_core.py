"""
Basic tests — cover the calculator's core logic and the API endpoints.
Run with:  pytest
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from slo_impact.main import app
from slo_impact.models import Incident, Service, SeverityLevel, WindowLabel
from slo_impact.calculator import calculate_service_uptime, build_dashboard


# ---------------------------------------------------------------------------
# Calculator unit tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def service():
    return Service(id="test-svc", name="Test Service", slo_target=0.9999, weight=1.0)


def make_incident(duration_minutes: float, user_fraction: float, traffic_weight: float) -> Incident:
    now = datetime.now(tz=timezone.utc)
    return Incident(
        id="inc-1",
        service_id="test-svc",
        title="Test incident",
        started_at=now - timedelta(minutes=duration_minutes),
        ended_at=now,
        severity=SeverityLevel.PARTIAL,
        user_fraction=user_fraction,
        traffic_weight=traffic_weight,
    )


def test_user_impact_uptime_less_than_raw(service):
    """user_impact_uptime ≥ raw_uptime (impact weighting can only reduce downtime)."""
    inc = make_incident(60, user_fraction=0.5, traffic_weight=0.5)
    result = calculate_service_uptime(service, [inc], [], WindowLabel.MONTHS_12)
    assert result.user_impact_uptime >= result.raw_uptime


def test_full_outage_all_users_peak_traffic(service):
    """user_impact_uptime == raw_uptime when user_fraction=1 and traffic_weight=1."""
    inc = make_incident(60, user_fraction=1.0, traffic_weight=1.0)
    result = calculate_service_uptime(service, [inc], [], WindowLabel.MONTHS_12)
    assert abs(result.user_impact_uptime - result.raw_uptime) < 1e-9


def test_offpeak_3am_outage_lower_impact(service):
    """A low-traffic, low-fraction incident contributes far less to impact than raw."""
    inc = make_incident(60, user_fraction=0.05, traffic_weight=0.08)
    result = calculate_service_uptime(service, [inc], [], WindowLabel.MONTHS_12)
    assert result.user_impact_downtime_seconds < result.total_downtime_seconds * 0.1


def test_no_incidents(service):
    result = calculate_service_uptime(service, [], [], WindowLabel.MONTHS_12)
    assert result.raw_uptime == 1.0
    assert result.user_impact_uptime == 1.0
    assert result.total_customer_impact_minutes == 0.0


def test_slo_met_when_uptime_above_target(service):
    inc = make_incident(1, user_fraction=0.01, traffic_weight=0.01)
    result = calculate_service_uptime(service, [inc], [], WindowLabel.MONTHS_12)
    assert result.slo_met  # tiny incident should not breach a 99.99% target


def test_slo_breached_on_major_outage(service):
    # 12-hour outage, 100% users, peak traffic — will easily breach 99.99%
    inc = make_incident(720, user_fraction=1.0, traffic_weight=1.0)
    result = calculate_service_uptime(service, [inc], [], WindowLabel.MONTHS_12)
    assert not result.slo_met


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_dashboard_default_window():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "services" in data
    assert "composite_uptime" in data
    assert len(data["services"]) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["24h", "7d", "1m", "3m", "12m"])
async def test_dashboard_all_windows(window):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/dashboard?window={window}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_invalid_window():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/dashboard?window=bad")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_service_detail():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        list_resp = await client.get("/api/v1/services")
        services = list_resp.json()["services"]
        svc_id = services[0]["id"]
        resp = await client.get(f"/api/v1/services/{svc_id}")
    assert resp.status_code == 200
    assert resp.json()["service"]["id"] == svc_id


@pytest.mark.asyncio
async def test_service_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/services/does-not-exist")
    assert resp.status_code == 404
