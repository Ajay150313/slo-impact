"""
slo-impact: FastAPI application.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from slo_impact.calculator import build_dashboard, calculate_service_uptime
from slo_impact.collectors.demo import DemoCollector
from slo_impact.collectors.prometheus import PrometheusCollector
from slo_impact.config import settings
from slo_impact.models import (
    DashboardSummary,
    ServiceUptimeResult,
    WindowLabel,
)

# ---------------------------------------------------------------------------
# Logging setup (structlog for JSON in prod, colourful in dev)
# ---------------------------------------------------------------------------

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(settings.log_level.upper())
    )
)
log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Application lifespan — initialise / tear down the collector
# ---------------------------------------------------------------------------

_collector: DemoCollector | PrometheusCollector | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _collector
    if settings.use_prometheus:
        log.info("Using Prometheus collector", url=settings.prometheus_url)
        _collector = PrometheusCollector(
            base_url=settings.prometheus_url,  # type: ignore[arg-type]
            timeout=settings.prometheus_timeout_seconds,
            step=settings.prometheus_step,
        )
    else:
        log.info("Using demo/synthetic data collector")
        _collector = DemoCollector()

    yield

    if isinstance(_collector, PrometheusCollector):
        await _collector.close()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="slo-impact",
    description=(
        "User-impact-weighted SLO dashboard. "
        "Measures true 99.99% uptime by actual customer exposure, "
        "not just raw availability."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Static files & root
# ---------------------------------------------------------------------------

import importlib.resources as pkg_resources
from pathlib import Path

_STATIC_DIR = Path(__file__).parent.parent / "static"

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    html_file = _STATIC_DIR / "index.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text())
    return HTMLResponse(content="<h1>slo-impact</h1><p>Static dir not found.</p>")


# ---------------------------------------------------------------------------
# Health & readiness
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/ready", tags=["ops"])
async def ready():
    if _collector is None:
        raise HTTPException(status_code=503, detail="Collector not initialised")
    return {"status": "ready"}


# ---------------------------------------------------------------------------
# API v1
# ---------------------------------------------------------------------------

def _parse_window(w: str) -> WindowLabel:
    try:
        return WindowLabel(w)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid window '{w}'. Valid values: {[e.value for e in WindowLabel]}",
        )


@app.get("/api/v1/dashboard", response_model=DashboardSummary, tags=["slo"])
async def get_dashboard(
    window: str = Query(default="12m", description="Time window: 24h | 7d | 1m | 3m | 12m"),
):
    """
    Return the full dashboard summary — composite uptime, per-service results,
    incident attribution, and availability impact breakdown.

    This is the primary endpoint consumed by the web dashboard.
    """
    win = _parse_window(window)
    services = settings.load_services()

    results: list[ServiceUptimeResult] = []
    for svc in services:
        incidents = await _collector.get_incidents(svc, win)  # type: ignore[union-attr]
        history = await _collector.get_history(svc, win)      # type: ignore[union-attr]
        result = calculate_service_uptime(svc, incidents, history, win)
        results.append(result)

    return build_dashboard(results, win)


@app.get("/api/v1/services", tags=["slo"])
async def list_services():
    """Return the configured service list (no incident data)."""
    return {"services": settings.load_services()}


@app.get("/api/v1/services/{service_id}", response_model=ServiceUptimeResult, tags=["slo"])
async def get_service(
    service_id: str,
    window: str = Query(default="12m"),
):
    """Return detailed uptime analysis for a single service."""
    win = _parse_window(window)
    services = {s.id: s for s in settings.load_services()}
    if service_id not in services:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")

    svc = services[service_id]
    incidents = await _collector.get_incidents(svc, win)  # type: ignore[union-attr]
    history = await _collector.get_history(svc, win)      # type: ignore[union-attr]
    return calculate_service_uptime(svc, incidents, history, win)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def serve():
    """Entrypoint registered in pyproject.toml [project.scripts]."""
    uvicorn.run(
        "slo_impact.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=settings.reload,
    )


if __name__ == "__main__":
    serve()
