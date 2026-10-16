from datetime import datetime, timedelta, timezone

from slo_impact.calculator import remaining_slo_headroom_seconds
from slo_impact.models import Incident, Service, SeverityLevel, WindowLabel


def test_headroom_shrinks_with_impact() -> None:
    service = Service(id="test-svc", name="Test Service", slo_target=0.9999, weight=1.0)
    now = datetime.now(tz=timezone.utc)
    empty = remaining_slo_headroom_seconds(service, [], WindowLabel.MONTHS_1)
    incident = Incident(
        id="inc-1",
        service_id="test-svc",
        title="Test incident",
        started_at=now - timedelta(minutes=10),
        ended_at=now,
        severity=SeverityLevel.PARTIAL,
        user_fraction=1.0,
        traffic_weight=1.0,
    )
    after = remaining_slo_headroom_seconds(service, [incident], WindowLabel.MONTHS_1)
    assert empty > after
    assert after >= 0
