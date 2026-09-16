import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DATABASE_URL = _env(
    "AEGIS_DATABASE_URL",
    "sqlite:///" + str(BASE_DIR / "aegis.db"),
)
SECRET_KEY = _env("AEGIS_SECRET_KEY", "aegis-dev-secret-change-in-production")
EAT_KEY = _env("AEGIS_EAT_KEY", "aegis-dev-eat-key-change-in-production")
EVIDENCE_SECRET_KEY = _env(
    "AEGIS_EVIDENCE_SECRET_KEY",
    "aegis-dev-evidence-key-change-in-production",
)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(_env("AEGIS_TOKEN_TTL_MINUTES", str(60 * 12)))
AGENT_TOKEN_PREFIX = "aegis_"
CORS_ORIGINS = [
    origin.strip()
    for origin in _env(
        "AEGIS_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

AEGIS_ROLE = _env("AEGIS_ROLE", "all")
BROKER_URL = _env("AEGIS_BROKER_URL", "")
TOOL_URL = _env("AEGIS_TOOL_URL", "")
INTERNAL_GATEWAY_TOKEN = _env("AEGIS_INTERNAL_GATEWAY_TOKEN", "")
INTERNAL_TOOL_TOKEN = _env("AEGIS_INTERNAL_TOOL_TOKEN", "")
CRM_SECRET = _env("AEGIS_CRM_SECRET", "aegis-internal-crm-secret-do-not-export")
EAT_TTL_SECONDS = int(_env("AEGIS_EAT_TTL_SECONDS", "10"))
REMOTE_TIMEOUT_SECONDS = float(_env("AEGIS_REMOTE_TIMEOUT_SECONDS", "3"))


def _flag(name: str, default: bool) -> bool:
    raw = _env(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# Phase 17: an agent with no ACTIVE runtime contract is denied.
#
# Until Phase 17 the enforcement path treated "this agent has no contract at
# all" as a pass-through, as a documented Phase 10 compatibility allowance
# (PHASE_13F_FAIL_CLOSED_VALIDATION.md). The practical effect was that the
# Runtime Contract — the product's main differentiator — was inert in every
# deployment and in every benchmark, because nothing ever created one.
#
# The default is now fail-closed. Setting this to false restores the legacy
# pass-through for a pre-Phase-11 deployment that has not provisioned contracts
# yet; it weakens enforcement and is logged as such by the API.
REQUIRE_RUNTIME_CONTRACT = _flag("AEGIS_REQUIRE_RUNTIME_CONTRACT", True)

# Phase 17: allow the shipped development secrets. Off by default; the app
# refuses to start on a default key unless this is set. Tests set it in
# conftest.py, deliberately and visibly.
ALLOW_DEFAULT_SECRETS = _flag("AEGIS_ALLOW_DEFAULT_SECRETS", False)

# Phase 17: an execution with events but no evidence hashes is treated as
# tampered. Set true only to read a pre-Phase-15 database that was never sealed.
EVIDENCE_ALLOW_UNSEALED = _flag("AEGIS_EVIDENCE_ALLOW_UNSEALED", False)

# Phase 17: agent credentials expire. The Credential model has always carried
# expires_at and get_agent_from_token has always checked it, but no creation
# path ever set it, so in practice every agent token issued was eternal. A
# leaked token stayed valid until someone noticed and revoked it by hand.
# 0 restores the previous never-expires behaviour.
AGENT_TOKEN_TTL_DAYS = int(_env("AEGIS_AGENT_TOKEN_TTL_DAYS", "90"))

# Phase 18: the runtime-verification agent's own credential.
#
# The agent container needs an identity before an operator has logged in, and
# the operator must not have to edit infrastructure to give it one. So
# scripts/init-env.sh generates this token, compose hands it to the agent
# container, and the seed registers an agent whose credential hash matches. It
# is an ordinary agent token, enforced exactly like any other -- it is not a
# tool credential, an EAT key or an internal service token.
VERIFY_AGENT_TOKEN = _env("AEGIS_VERIFY_AGENT_TOKEN", "")

# Approval grants are single-use and short-lived (Phase 17).
APPROVAL_TTL_SECONDS = int(_env("AEGIS_APPROVAL_TTL_SECONDS", "900"))
