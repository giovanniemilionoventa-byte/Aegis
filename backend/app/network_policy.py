"""Declarative trust-domain contract for Compose and tests.

Docker network attachments enforce the frozen matrix below.
internal:true blocks Internet, not peers on the same bridge.

PHASE 19 CHANGES THE SHAPE OF THIS FILE, because Phase 19 is the first time
anything in the deployment legitimately needs the internet.

Two components now have an external route, and only two:

  * gmail-connector reaches Google, because that is its whole job. It is the
    only container that mounts the OAuth store, and the only one holding a
    Google credential.
  * egress-proxy reaches the model provider, on behalf of the agent, through a
    host allow-list that does not contain Google.

The agent gets neither. It stays on an internal network whose only reachable
peer is the enforcement gateway, plus the egress proxy for its model traffic.
"agent -> gmail" is DENY at the network layer and DENY again at the proxy, and
tests/test_phase19_network.py asserts both from the compose file, with
infra/boundary/boundary_proof.py asserting it from inside the containers when a
Docker daemon is available.
"""

from pathlib import Path

AGENT_NETWORK = "agent_net"
BROKER_NETWORK = "broker_net"
TOOL_NETWORK = "tool_net"
PUBLIC_NETWORK = "public_net"
# Phase 19. Non-internal, and attached to exactly the two services that need
# to leave the host.
GOOGLE_NETWORK = "google_net"
EGRESS_NETWORK = "egress_net"

NETWORKS = {
    AGENT_NETWORK: {"internal": True, "purpose": "agent to gateway and egress proxy only"},
    BROKER_NETWORK: {
        "internal": True,
        "purpose": "gateway to credential broker",
    },
    TOOL_NETWORK: {
        "internal": True,
        "purpose": "broker to protected tools",
    },
    PUBLIC_NETWORK: {"internal": False, "purpose": "host access to control plane"},
    GOOGLE_NETWORK: {
        "internal": False,
        "purpose": "gmail connector to the Google API, and nothing else",
    },
    EGRESS_NETWORK: {
        "internal": False,
        "purpose": "egress proxy to the model provider, host allow-listed",
    },
}

SERVICES = {
    "ai-agent": {AGENT_NETWORK},
    "agent": {AGENT_NETWORK},
    "egress-proxy": {AGENT_NETWORK, EGRESS_NETWORK},
    "enforcement-gateway": {AGENT_NETWORK, BROKER_NETWORK},
    "control-plane": {PUBLIC_NETWORK},
    "credential-broker": {BROKER_NETWORK, TOOL_NETWORK},
    "protected-tool": {TOOL_NETWORK},
    "gmail-connector": {TOOL_NETWORK, GOOGLE_NETWORK},
}

DB_VOLUMES = {
    "ai-agent": set(),
    "agent": set(),
    "egress-proxy": set(),
    "enforcement-gateway": {"aegis-data"},
    "control-plane": {"aegis-data"},
    "credential-broker": set(),
    "protected-tool": set(),
    "gmail-connector": set(),
}

# Phase 19. Who may hold the sealed Google refresh tokens. Exactly two: the
# control plane writes the store when a human consents, the connector reads it
# to call Google. Anything else appearing here is a finding.
OAUTH_VOLUMES = {
    "ai-agent": set(),
    "agent": set(),
    "egress-proxy": set(),
    "enforcement-gateway": set(),
    "control-plane": {"aegis-oauth"},
    "credential-broker": set(),
    "protected-tool": set(),
    "gmail-connector": {"aegis-oauth"},
}

MATRIX = {
    ("agent", "enforcement-gateway"): "ALLOW",
    ("agent", "protected-tool"): "DENY",
    ("agent", "credential-broker"): "DENY",
    ("agent", "control-plane"): "DENY",
    ("agent", "db"): "DENY",
    # Phase 19: the real AI agent, and the thing it must never reach.
    ("ai-agent", "enforcement-gateway"): "ALLOW",
    ("ai-agent", "egress-proxy"): "ALLOW",
    ("ai-agent", "gmail-connector"): "DENY",
    ("ai-agent", "protected-tool"): "DENY",
    ("ai-agent", "credential-broker"): "DENY",
    ("ai-agent", "control-plane"): "DENY",
    ("ai-agent", "db"): "DENY",
    ("enforcement-gateway", "credential-broker"): "ALLOW",
    ("enforcement-gateway", "protected-tool"): "DENY",
    ("enforcement-gateway", "gmail-connector"): "DENY",
    ("enforcement-gateway", "control-plane"): "DENY",
    ("enforcement-gateway", "db"): "ALLOW",
    ("credential-broker", "protected-tool"): "ALLOW",
    ("credential-broker", "gmail-connector"): "ALLOW",
    ("credential-broker", "db"): "DENY",
    ("control-plane", "db"): "ALLOW",
    ("control-plane", "protected-tool"): "DENY",
    ("control-plane", "gmail-connector"): "DENY",
    ("control-plane", "credential-broker"): "DENY",
    ("egress-proxy", "enforcement-gateway"): "ALLOW",
    ("egress-proxy", "gmail-connector"): "DENY",
    ("egress-proxy", "db"): "DENY",
}

# Which services may route off the host at all.
EXTERNAL_ROUTE = {
    "ai-agent": False,
    "agent": False,
    "egress-proxy": True,
    "enforcement-gateway": False,
    "credential-broker": False,
    "protected-tool": False,
    "gmail-connector": True,
    "control-plane": True,
}


def reachable(source: str, destination: str) -> bool:
    if destination == "db":
        return bool(DB_VOLUMES.get(source))
    src = SERVICES.get(source, set())
    dst = SERVICES.get(destination, set())
    return bool(src & dst)


def holds_oauth_store(service: str) -> bool:
    return bool(OAUTH_VOLUMES.get(service))


def has_external_route(service: str) -> bool:
    attachments = SERVICES.get(service, set())
    return any(not NETWORKS.get(name, {}).get("internal", True) for name in attachments)


def expected_verdict(source: str, destination: str) -> str:
    return MATRIX.get((source, destination), "UNKNOWN")


def compose_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docker-compose.yml"
