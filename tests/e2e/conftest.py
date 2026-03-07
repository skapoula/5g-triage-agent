"""E2E test configuration and fixtures.

Two modes:
  Live server  — pass --alert-webhook http://localhost:8000/webhook
                 Requires the full stack (Memgraph, Prometheus, Loki) to be running.
  In-process   — no flag needed; uses FastAPI TestClient with the workflow mocked.
"""
from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--alert-webhook",
        default=None,
        help="Base URL of a running TriageAgent server, e.g. http://localhost:8000",
    )


@pytest.fixture(scope="session")
def server_base_url(request: pytest.FixtureRequest) -> str | None:
    """Return the live-server base URL, or None when running in-process."""
    raw: str | None = request.config.getoption("--alert-webhook")
    if raw is None:
        return None
    # Accept either the full webhook URL or just the base URL.
    return raw.rstrip("/").removesuffix("/webhook")
