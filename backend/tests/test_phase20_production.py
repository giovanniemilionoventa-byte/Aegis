"""Phase 20 -- production mode: refuse unsafe starts, close the doors a public
service must not have open.

Development stays the default so the existing suite and the local demo keep
working. Everything here is opt-in through AEGIS_ENV=production, and each test
that needs it flips the switch on the config module rather than the process
environment, so nothing leaks into the rest of the suite.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app import main as main_module
from app.main import create_app
from app.security_posture import InsecureConfiguration, assert_secrets_configured
from app.seed import DEMO_EMAIL, DEMO_PASSWORD

STRONG = "Zq8v2LmT9xRw4KpN7sHd1FbC6yGa3JeU5tQo0WiX"  # 40 chars, no placeholder
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(config, "ALLOW_DEFAULT_SECRETS", False)
    for name in ("SECRET_KEY", "EAT_KEY", "EVIDENCE_SECRET_KEY"):
        monkeypatch.setattr(config, name, STRONG)
    monkeypatch.setattr(config, "BROKER_URL", "")
    monkeypatch.setattr(config, "ENABLE_GMAIL", False)
    monkeypatch.setattr(config, "SEED_DEMO", False)
    monkeypatch.setattr(config, "AUTH_RATE_PER_MIN", 0)
    monkeypatch.setattr(config, "CORS_ORIGINS", [])
    return monkeypatch


def _paths(application) -> set[str]:
    return {getattr(route, "path", "") for route in application.routes}


# ---------------------------------------------------------------------------
# Refusing to start unsafe
# ---------------------------------------------------------------------------


def test_production_refuses_shipped_defaults_even_when_told_to_allow_them(monkeypatch):
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(config, "ALLOW_DEFAULT_SECRETS", True)
    with pytest.raises(InsecureConfiguration):
        assert_secrets_configured("control-plane")


def test_production_refuses_placeholder_secret(production):
    production.setattr(config, "SECRET_KEY", "change-me-" + STRONG[:32])
    with pytest.raises(InsecureConfiguration, match="AEGIS_SECRET_KEY"):
        assert_secrets_configured("control-plane")


def test_production_refuses_a_short_secret(production):
    production.setattr(config, "EVIDENCE_SECRET_KEY", STRONG[:31])
    with pytest.raises(InsecureConfiguration, match="AEGIS_EVIDENCE_SECRET_KEY"):
        assert_secrets_configured("control-plane")


def test_production_accepts_strong_secrets(production):
    assert_secrets_configured("control-plane")
    assert_secrets_configured("enforcement-gateway")


def test_production_refuses_role_all(production):
    with pytest.raises(InsecureConfiguration, match="all"):
        assert_secrets_configured("all")


def test_development_still_allows_the_shipped_defaults(monkeypatch):
    monkeypatch.setattr(config, "ENV", "development")
    monkeypatch.setattr(config, "ALLOW_DEFAULT_SECRETS", True)
    assert_secrets_configured("all")


def test_app_does_not_start_in_production_on_defaults(monkeypatch):
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(config, "ALLOW_DEFAULT_SECRETS", True)
    with pytest.raises(InsecureConfiguration):
        with TestClient(create_app("control-plane")):
            pass


def test_production_token_lifetime_defaults_to_an_hour():
    """Read in a clean interpreter: the value is fixed when config is imported."""
    code = "from app import config; print(config.ACCESS_TOKEN_EXPIRE_MINUTES)"
    env = {"AEGIS_ENV": "production", "PATH": ""}
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "60"


# ---------------------------------------------------------------------------
# What production does not expose
# ---------------------------------------------------------------------------


def test_production_does_not_seed_the_demo_organization(production):
    calls: list[str] = []
    production.setattr(main_module, "seed_if_empty", lambda db: calls.append("demo"))
    production.setattr(
        main_module, "seed_builtin_patterns", lambda db: calls.append("patterns")
    )
    with TestClient(create_app("control-plane")):
        pass
    assert "demo" not in calls
    assert "patterns" in calls, "built-in behaviour patterns are not demo data"


def test_development_still_seeds_the_demo_organization(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(config, "ENV", "development")
    monkeypatch.setattr(config, "SEED_DEMO", True)
    monkeypatch.setattr(main_module, "seed_if_empty", lambda db: calls.append("demo"))
    with TestClient(create_app("control-plane")):
        pass
    assert "demo" in calls


def test_production_hides_the_interactive_api_docs(production):
    with TestClient(create_app("control-plane")) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path


def test_development_keeps_the_interactive_api_docs():
    with TestClient(create_app("all")) as client:
        assert client.get("/docs").status_code == 200


def test_production_health_does_not_name_weak_secrets(production):
    with TestClient(create_app("control-plane")) as client:
        body = client.get("/api/health").json()
    assert body["posture"] == {"secure": True}


def test_control_plane_in_production_omits_the_agent_test_harness(production):
    paths = _paths(create_app("control-plane"))
    assert "/api/agents" in paths
    assert not any(p.startswith("/api/verification") for p in paths)
    assert not any("verification-runs" in p for p in paths)
    assert not any(p.startswith("/api/gmail") for p in paths), "Gmail is opt-in"


def test_gmail_routes_return_only_when_explicitly_enabled(production):
    production.setattr(config, "ENABLE_GMAIL", True)
    paths = _paths(create_app("control-plane"))
    assert any(p.startswith("/api/gmail") for p in paths)


def test_gateway_in_production_omits_agentctl_and_needs_a_broker_for_tools(production):
    paths = _paths(create_app("enforcement-gateway"))
    assert "/api/authorize" in paths
    assert not any(p.startswith("/api/agentctl") for p in paths)
    assert not any(p.startswith("/api/gateway") for p in paths), (
        "no broker configured: the in-process tool harness must stay unreachable"
    )


def test_gateway_tools_come_back_when_a_broker_is_configured(production):
    production.setattr(config, "BROKER_URL", "http://credential-broker:8000/api")
    paths = _paths(create_app("enforcement-gateway"))
    assert any(p.startswith("/api/gateway") for p in paths)


# ---------------------------------------------------------------------------
# CORS and request size
# ---------------------------------------------------------------------------


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/auth/login",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )


def test_production_cors_answers_only_listed_origins(production):
    production.setattr(config, "CORS_ORIGINS", ["https://app.pilot.test"])
    with TestClient(create_app("control-plane")) as client:
        stranger = _preflight(client, "https://evil.test")
        friend = _preflight(client, "https://app.pilot.test")
    assert "access-control-allow-origin" not in stranger.headers
    assert friend.headers["access-control-allow-origin"] == "https://app.pilot.test"
    assert "access-control-allow-credentials" not in friend.headers


def test_cors_never_sends_credentials_even_in_development():
    with TestClient(create_app("all")) as client:
        response = _preflight(client, "https://anywhere.test")
    assert "access-control-allow-credentials" not in response.headers


def test_oversized_request_body_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "MAX_BODY_BYTES", 1000)
    with TestClient(create_app("all")) as client:
        response = client.post(
            "/api/auth/login",
            content=b"x" * 2000,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413


def test_a_small_body_still_gets_through(monkeypatch):
    monkeypatch.setattr(config, "MAX_BODY_BYTES", 1000)
    with TestClient(create_app("all")) as client:
        response = client.post(
            "/api/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
        )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# List endpoints cannot be asked for everything
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dev_client():
    with TestClient(create_app("all")) as instance:
        yield instance


def _operator(client: TestClient) -> dict:
    token = client.post(
        "/api/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", ["/api/events", "/api/executions"])
@pytest.mark.parametrize("value", ["-1", "0", "501"])
def test_list_limit_is_bounded(dev_client, path, value):
    response = dev_client.get(f"{path}?limit={value}", headers=_operator(dev_client))
    assert response.status_code == 422, (path, value)


@pytest.mark.parametrize("path", ["/api/events", "/api/executions"])
def test_list_limit_still_accepts_sane_values(dev_client, path):
    response = dev_client.get(f"{path}?limit=5", headers=_operator(dev_client))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# One URL: the dashboard is served by the control plane
# ---------------------------------------------------------------------------


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('aegis')")
    (tmp_path / "index.html").write_text("<!doctype html><title>Aegis</title>")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path.parent / "outside-secret.txt").write_text("do not serve")
    monkeypatch.setattr(config, "STATIC_DIR", str(tmp_path))
    return tmp_path


def test_dashboard_is_served_from_the_root(static_dir):
    with TestClient(create_app("control-plane")) as client:
        root = client.get("/")
        assert root.status_code == 200 and "<title>Aegis</title>" in root.text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/favicon.svg").status_code == 200


def test_unknown_page_falls_back_to_the_dashboard(static_dir):
    with TestClient(create_app("control-plane")) as client:
        page = client.get("/agents/some-id")
    assert page.status_code == 200 and "<title>Aegis</title>" in page.text


def test_unknown_api_path_stays_a_json_404(static_dir):
    with TestClient(create_app("control-plane")) as client:
        response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_static_serving_cannot_escape_its_directory(static_dir):
    with TestClient(create_app("control-plane")) as client:
        for path in ("/../outside-secret.txt", "/%2e%2e/outside-secret.txt"):
            response = client.get(path)
            assert "do not serve" not in response.text, path


def test_gateway_role_never_serves_the_dashboard(static_dir, production):
    with TestClient(create_app("enforcement-gateway")) as client:
        assert client.get("/").status_code == 404
