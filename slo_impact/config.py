"""
slo-impact: configuration.

Settings are loaded from environment variables or a YAML config file.
Environment variables take precedence.

Example env usage:
    SLO_IMPACT_PROMETHEUS_URL=http://prometheus:9090 slo-impact serve
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from slo_impact.models import Service, WindowLabel


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SLO_IMPACT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"
    reload: bool = False

    # Data source — leave blank to use built-in demo data
    prometheus_url: Optional[str] = Field(
        default=None,
        description="Base URL of the Prometheus HTTP API (e.g. http://prometheus:9090).",
    )
    prometheus_timeout_seconds: float = 10.0
    prometheus_step: str = "5m"   # query_range step for history calls

    # Dashboard behaviour
    default_window: WindowLabel = WindowLabel.MONTHS_12
    demo_mode: bool = Field(
        default=True,
        description=(
            "When True and prometheus_url is not set, synthetic realistic data "
            "is generated so the dashboard works out of the box."
        ),
    )

    # Path to an optional YAML services file (see examples/config.example.yaml).
    # If not set, the built-in demo services are used.
    services_config_path: Optional[Path] = None

    @field_validator("prometheus_url", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    @property
    def use_prometheus(self) -> bool:
        return self.prometheus_url is not None

    def load_services(self) -> list[Service]:
        """
        Load service definitions from the YAML config file (if provided),
        otherwise return the built-in demo services.
        """
        path = self.services_config_path
        if path is None:
            env_path = os.environ.get("SLO_IMPACT_SERVICES_CONFIG_PATH")
            if env_path:
                path = Path(env_path)

        if path and path.exists():
            with path.open() as fh:
                raw = yaml.safe_load(fh)
            return [Service(**svc) for svc in raw.get("services", [])]

        return _default_demo_services()


def _default_demo_services() -> list[Service]:
    """
    Sensible demo services that mirror a typical digital banking platform.
    Intentionally generic — no employer names or internal service identifiers.
    """
    return [
        Service(
            id="core-banking",
            name="Core Banking Platform",
            description="Primary transaction ledger and account management API",
            slo_target=0.9999,
            weight=3.0,
        ),
        Service(
            id="auth",
            name="Authentication Service",
            description="OAuth 2.0 / OIDC token issuance and session management",
            slo_target=0.9999,
            weight=2.5,
        ),
        Service(
            id="payments",
            name="Payments API",
            description="ACH, wire, and card payment initiation gateway",
            slo_target=0.9999,
            weight=3.0,
        ),
        Service(
            id="kyc",
            name="KYC Service",
            description="Identity verification and compliance screening",
            slo_target=0.9995,
            weight=1.5,
        ),
        Service(
            id="transaction-db",
            name="Transaction DB",
            description="Primary relational store for financial records",
            slo_target=0.9999,
            weight=3.0,
        ),
        Service(
            id="notifications",
            name="Notification Service",
            description="Real-time push, SMS, and email delivery pipeline",
            slo_target=0.9990,
            weight=1.0,
        ),
        Service(
            id="reporting",
            name="Reporting Service",
            description="Regulatory and customer statement generation",
            slo_target=0.9990,
            weight=1.0,
        ),
        Service(
            id="file-storage",
            name="File Storage",
            description="Document and media object store (S3-compatible)",
            slo_target=0.9999,
            weight=0.8,
        ),
    ]


# Module-level singleton — import this everywhere else.
settings = Settings()
