# slo-impact

**User-impact-weighted SLO dashboard.**  
Measure *true* 99.99% uptime by actual customer exposure — not just raw availability.

[![CI](https://github.com/Ajay150313/slo-impact/actions/workflows/ci.yml/badge.svg)](https://github.com/Ajay150313/slo-impact/actions)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## The problem with raw availability

Every SRE knows the formula: `(total_time − downtime) / total_time`.  
But that metric treats a **3 AM outage affecting 3% of users** identically to a
**noon outage affecting all users at peak load**.

When your oncall platform showed `99.99%`, did it mean your *customers* experienced
99.99% availability? Or just that your infrastructure was up 99.99% of the time?

**slo-impact answers the real question.**

---

## How it works

For each incident we capture two extra dimensions:

| Dimension | Meaning |
|---|---|
| `user_fraction` | Fraction of the user base actually affected (0–1) |
| `traffic_weight` | Traffic during incident ÷ baseline traffic |

**User-impact downtime per incident:**

```
impact_seconds = duration × user_fraction × traffic_weight
```

**User-impact uptime:**

```
user_impact_uptime = 1 − (Σ impact_seconds) / window_seconds
```

A 3 AM outage at 8% traffic affecting 5% of users contributes only
**0.4%** of what the same outage would contribute at peak.
That gap — between what your infrastructure reports and what customers feel — is the
number that matters for SLO integrity.

> *"If your SLO has never been breached, it isn't an SLO. It's a slogan."*

---

## Quick start

### Option 1 — Docker (recommended, zero setup)

```bash
git clone https://github.com/Ajay150313/slo-impact.git
cd slo-impact
docker-compose up
```

Open **http://localhost:8080** — the dashboard runs with built-in synthetic data
that mirrors a realistic digital banking platform. No Prometheus required.

### Option 2 — pip

```bash
pip install slo-impact          # once published to PyPI
# or from source:
pip install -e .

slo-impact                      # starts on http://0.0.0.0:8080
```

### Option 3 — from source (development)

```bash
git clone https://github.com/Ajay150313/slo-impact.git
cd slo-impact
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn slo_impact.main:app --reload --port 8080
```

---

## Prometheus integration

Point slo-impact at your real Prometheus instance and define a per-service
availability query:

```bash
SLO_IMPACT_PROMETHEUS_URL=http://prometheus:9090 \
SLO_IMPACT_DEMO_MODE=false \
SLO_IMPACT_SERVICES_CONFIG_PATH=./examples/config.example.yaml \
slo-impact
```

In your `services.yaml`, add the query for each service:

```yaml
services:
  - id: payments
    name: Payments API
    slo_target: 0.9999
    weight: 3.0
    prometheus_availability_query: |
      1 - (
        rate(http_requests_total{status=~"5..",job="payments"}[5m])
        / rate(http_requests_total{job="payments"}[5m])
      )
```

See [`examples/config.example.yaml`](examples/config.example.yaml) for the full template.

---

## Configuration reference

All settings can be provided as environment variables (prefix `SLO_IMPACT_`) or in `.env`:

| Variable | Default | Description |
|---|---|---|
| `SLO_IMPACT_HOST` | `0.0.0.0` | Server bind address |
| `SLO_IMPACT_PORT` | `8080` | Server port |
| `SLO_IMPACT_PROMETHEUS_URL` | *(empty)* | Prometheus base URL; leave blank for demo mode |
| `SLO_IMPACT_DEMO_MODE` | `true` | Force demo mode even if Prometheus URL is set |
| `SLO_IMPACT_DEFAULT_WINDOW` | `12m` | Default time window |
| `SLO_IMPACT_SERVICES_CONFIG_PATH` | *(empty)* | Path to custom services YAML |
| `SLO_IMPACT_LOG_LEVEL` | `info` | Logging level |

---

## REST API

The full OpenAPI spec is at **`/docs`** (Swagger UI) or **`/redoc`** once the server is running.

| Endpoint | Description |
|---|---|
| `GET /api/v1/dashboard?window=12m` | Full dashboard data (primary endpoint) |
| `GET /api/v1/services` | List configured services |
| `GET /api/v1/services/{id}?window=7d` | Single-service uptime detail |
| `GET /health` | Health check |
| `GET /ready` | Readiness probe |

### Example response

```json
{
  "overall_status": "operational",
  "composite_uptime": 0.9997,
  "total_customer_impact_minutes": 221.4,
  "window_label": "12m",
  "incident_categories": [
    { "label": "Patch Incident",       "duration_minutes": 75.2, "hex_color": "#ef4444" },
    { "label": "Auth Provider Outage", "duration_minutes": 26.1, "hex_color": "#f97316" }
  ],
  "services": [ ... ]
}
```

---

## Project structure

```
slo-impact/
├── slo_impact/
│   ├── __init__.py
│   ├── main.py          # FastAPI application + CLI entrypoint
│   ├── config.py        # Settings (env / YAML)
│   ├── models.py        # Pydantic domain models
│   ├── calculator.py    # User-impact uptime algorithm
│   └── collectors/
│       ├── __init__.py  # Collector protocol
│       ├── demo.py      # Synthetic data (default)
│       └── prometheus.py# Prometheus HTTP API
├── static/
│   └── index.html       # Dashboard UI (single file, no build step)
├── tests/
│   └── test_core.py
├── examples/
│   └── config.example.yaml
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## Running tests

```bash
pip install pytest pytest-asyncio httpx
pytest -v
```

---

## Related projects

| Repo | What it does |
|---|---|
| [agentsre](https://github.com/Ajay150313/agentsre) | SRE reliability instrumentation for agentic AI |
| [agentsre-langchain](https://github.com/Ajay150313/agentsre-langchain) | LangChain integration for agentsre |
| [slo-burn](https://github.com/Ajay150313/slo-burn) | SLO burn-rate alerting for Prometheus |

**slo-burn** tells you *when* your error budget is burning.  
**slo-impact** shows you *what it means for users*.

---

## Contributing

Issues and pull requests welcome.  
See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

[MIT](LICENSE) © Ajay Devineni
