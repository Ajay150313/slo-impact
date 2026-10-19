---
name: slo-impact
description: Measure user-impact-weighted uptime with slo-impact. Use when raw availability hides who was affected and when, for example an outage at peak traffic hitting all users versus a 3 AM blip hitting a few. Covers the API, the calculator, and remaining SLO headroom.
---

# slo-impact

Raw availability counts every minute of downtime the same. slo-impact weights each incident by
the fraction of users affected and the traffic level at the time.

Run the service: `slo-impact` (FastAPI, demo collector by default; Prometheus collector via config).

## API

| Route | Purpose |
|---|---|
| `GET /api/v1/dashboard?window=24h\|7d\|1m\|3m\|12m` | all services, composite uptime |
| `GET /api/v1/services` | list services |
| `GET /api/v1/services/{id}` | one service with incidents and history |
| `GET /health`, `GET /ready` | liveness and readiness |

## Calculator

```python
from slo_impact.calculator import calculate_service_uptime, remaining_slo_headroom_seconds
from slo_impact.models import WindowLabel

result = calculate_service_uptime(service, incidents, history, WindowLabel.MONTHS_12)
result.raw_uptime            # time-based
result.user_impact_uptime    # weighted by users affected and traffic
result.slo_met

remaining_slo_headroom_seconds(service, incidents, WindowLabel.MONTHS_12)
```

`remaining_slo_headroom_seconds` is how much full-user, peak-traffic downtime the target still
allows. At 99.99% that is seconds, not hours.

## Guidance

- Record `user_fraction` and `traffic_weight` for every incident; the metric is only as good as these inputs.
- Report both raw and impact-weighted uptime; the gap is the finding.
- Tests: `pytest -q` after `pip install -e ".[dev]"`. API tests start the app lifespan themselves.
