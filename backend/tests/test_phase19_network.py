"""Phase 19 — the agent cannot reach Gmail, and this is why.

The claim "the agent has no route to Gmail" is worth exactly as much as the
evidence behind it. There are three independent pieces here, and the docstrings
say which is which, because they prove different strengths of statement:

  1. The compose file. Asserted below by parsing docker-compose.yml: the agent
     is attached only to an internal network, the Gmail connector is not on
     that network, and the sealed credential volume is mounted by two services
     and no others. This is a configuration check and it runs everywhere.

  2. The egress proxy's allow-list. Asserted below by calling the real host
     matcher: Google hosts are refused, including lookalikes. This is a unit
     check of the code that makes the runtime decision.

  3. The running containers. infra/boundary/boundary_proof.py probes from
     inside the containers and needs a Docker daemon; those tests skip when
     there is none. Only that third piece is a runtime proof, and nothing here
     claims otherwise.
"""

from __future__ import annotations

import os
import sys

import pytest
import yaml

from app import network_policy

PROXY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "infra",
    "egress-proxy",
)


@pytest.fixture(scope="module")
def compose() -> dict:
    with open(network_policy.compose_path(), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _networks(compose: dict, service: str) -> set[str]:
    return set(compose["services"][service].get("networks") or [])


# -- 1. the deployment ------------------------------------------------------


def test_agent_networks_are_all_internal(compose):
    """The AI agent has no non-internal attachment. No route off the host."""
    declared = compose["networks"]
    for network in _networks(compose, "ai-agent"):
        assert declared[network].get("internal") is True, network


def test_agent_is_not_on_any_network_the_gmail_connector_is_on(compose):
    """There is no shared bridge, so the connector is not even addressable."""
    assert not (_networks(compose, "ai-agent") & _networks(compose, "gmail-connector"))


def test_agent_shares_no_network_with_broker_or_control_plane(compose):
    agent_nets = _networks(compose, "ai-agent")
    for peer in ("credential-broker", "control-plane", "protected-tool"):
        assert not (agent_nets & _networks(compose, peer)), peer


def test_agent_reaches_only_the_gateway_and_the_proxy(compose):
    """Everything the agent CAN address, enumerated."""
    agent_nets = _networks(compose, "ai-agent")
    reachable = {
        name
        for name, service in compose["services"].items()
        if name != "ai-agent" and (set(service.get("networks") or []) & agent_nets)
    }
    assert reachable == {"enforcement-gateway", "egress-proxy", "agent"}


def test_only_two_services_have_an_external_route(compose):
    """Being on a non-internal network is the privilege; two services have it.

    control-plane is a third, and deliberately so: it must be reachable from
    the operator's browser. It holds no agent-facing route.
    """
    declared = compose["networks"]
    external = {
        name
        for name, service in compose["services"].items()
        if any(
            not declared[net].get("internal", False)
            for net in (service.get("networks") or [])
        )
    }
    assert external == {"gmail-connector", "egress-proxy", "control-plane"}


def test_oauth_store_is_mounted_by_exactly_two_services(compose):
    """The sealed Google credential reaches two containers. Which two matters."""
    holders = {
        name
        for name, service in compose["services"].items()
        if any(str(v).startswith("aegis-oauth:") for v in (service.get("volumes") or []))
    }
    assert holders == {"control-plane", "gmail-connector"}


def test_the_connector_mounts_the_credential_store_read_only(compose):
    mounts = [
        str(v)
        for v in compose["services"]["gmail-connector"]["volumes"]
        if str(v).startswith("aegis-oauth:")
    ]
    assert mounts == ["aegis-oauth:/oauth:ro"]


def test_no_agent_container_holds_a_google_or_internal_secret(compose):
    """The agent's environment, enumerated and checked.

    Anything matching a protected-credential name in the agent container would
    be a Phase 19 failure regardless of what the network allows.
    """
    forbidden = (
        "AEGIS_GOOGLE_CLIENT_SECRET",
        "AEGIS_GOOGLE_CLIENT_ID",
        "AEGIS_OAUTH_ENCRYPTION_KEY",
        "AEGIS_CRM_SECRET",
        "AEGIS_EAT_KEY",
        "AEGIS_INTERNAL_TOOL_TOKEN",
        "AEGIS_INTERNAL_GATEWAY_TOKEN",
        "AEGIS_SECRET_KEY",
        "AEGIS_EVIDENCE_SECRET_KEY",
    )
    for container in ("ai-agent", "agent"):
        environment = compose["services"][container].get("environment") or {}
        for key in forbidden:
            assert key not in environment, f"{container} holds {key}"


def test_agent_proxy_points_at_the_egress_proxy(compose):
    environment = compose["services"]["ai-agent"]["environment"]
    assert environment["HTTPS_PROXY"] == "http://egress-proxy:8888"
    # The gateway is reached directly, not through the proxy: it is a peer on
    # the internal network, and proxying it would be both pointless and a way
    # to make the proxy a single point of failure for enforcement.
    assert "enforcement-gateway" in environment["NO_PROXY"]


def test_declared_matrix_matches_the_compose_file(compose):
    """network_policy.py is documentation unless it agrees with the deployment."""
    for (source, destination), verdict in network_policy.MATRIX.items():
        if destination == "db" or source not in compose["services"]:
            continue
        if destination not in compose["services"]:
            continue
        shared = bool(_networks(compose, source) & _networks(compose, destination))
        assert shared is (verdict == "ALLOW"), f"{source} -> {destination}"


# -- 2. the egress allow-list -----------------------------------------------


@pytest.fixture(scope="module")
def proxy_module():
    os.environ["AEGIS_EGRESS_ALLOWLIST"] = "api.deepseek.com,api.anthropic.com"
    sys.path.insert(0, PROXY_DIR)
    import proxy  # noqa: PLC0415

    return proxy


@pytest.mark.parametrize(
    "host",
    [
        "gmail.googleapis.com",
        "www.googleapis.com",
        "oauth2.googleapis.com",
        "accounts.google.com",
        "mail.google.com",
        "smtp.gmail.com",
    ],
)
def test_proxy_refuses_every_google_host(proxy_module, host):
    assert proxy_module._host_allowed(host) is False


@pytest.mark.parametrize(
    "host",
    [
        "api.deepseek.com.evil.test",
        "notapi.deepseek.com",
        "api.deepseek.com.gmail.googleapis.com",
        "evil.test",
    ],
)
def test_proxy_refuses_lookalike_hosts(proxy_module, host):
    """The check is on label boundaries, not substrings."""
    assert proxy_module._host_allowed(host) is False


def test_proxy_allows_the_model_provider(proxy_module):
    assert proxy_module._host_allowed("api.deepseek.com") is True
    assert proxy_module._host_allowed("api.anthropic.com") is True


def test_default_allowlist_contains_no_google_host():
    """A default that leaked a Google host would undo the boundary silently."""
    with open(
        os.path.join(os.path.dirname(PROXY_DIR), "..", "docker-compose.yml"),
        "r",
        encoding="utf-8",
    ) as handle:
        compose = yaml.safe_load(handle)
    default = compose["services"]["egress-proxy"]["environment"]["AEGIS_EGRESS_ALLOWLIST"]
    assert "google" not in default.lower()
    assert "gmail" not in default.lower()
