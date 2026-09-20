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

# Phase 20 -- one switch between "runs on my laptop" and "faces the internet".
# Development is the default so the test suite and the local demo keep working.
# Production refuses to start unsafe (see security_posture.py) and closes what a
# public service must not expose. Read it through is_production() at call time.
ENV = _env("AEGIS_ENV", "development").strip().lower()


def is_production() -> bool:
    return ENV == "production"


# A dashboard session lasts an hour in production: it is a bearer token that a
# static page keeps in the browser.
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    _env("AEGIS_TOKEN_TTL_MINUTES", "60" if ENV == "production" else str(60 * 12))
)
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


# ---------------------------------------------------------------------------
# Phase 19 — real Gmail as the first real protected service.
#
# Every value here is server-side. None of it is ever handed to an agent, put
# in a model prompt, returned by an API, or written to the evidence chain.
# ---------------------------------------------------------------------------

# Google OAuth client. Created by a human in Google Cloud Console; see
# docs/PHASE_19_GMAIL.md. The client secret lives in .env, which is gitignored.
GOOGLE_OAUTH_CLIENT_ID = _env("AEGIS_GOOGLE_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = _env("AEGIS_GOOGLE_CLIENT_SECRET", "")
GOOGLE_OAUTH_REDIRECT_URI = _env(
    "AEGIS_GOOGLE_REDIRECT_URI", "http://localhost:8000/api/gmail/oauth/callback"
)

# Google endpoints. Overridable so tests can point the connector at a local
# stand-in without editing code; production leaves them alone.
GOOGLE_AUTH_ENDPOINT = _env(
    "AEGIS_GOOGLE_AUTH_ENDPOINT", "https://accounts.google.com/o/oauth2/v2/auth"
)
GOOGLE_TOKEN_ENDPOINT = _env(
    "AEGIS_GOOGLE_TOKEN_ENDPOINT", "https://oauth2.googleapis.com/token"
)
GMAIL_API_BASE_URL = _env("AEGIS_GMAIL_API_BASE_URL", "https://gmail.googleapis.com")

# Where sealed refresh tokens are kept. Mounted on the control plane (writes on
# consent) and the Gmail connector (reads to call Google) and nowhere else.
GMAIL_OAUTH_STORE_PATH = _env("AEGIS_GMAIL_STORE_PATH", "/oauth/gmail_connections.json")
GMAIL_OAUTH_ENCRYPTION_KEY = _env("AEGIS_OAUTH_ENCRYPTION_KEY", "")

# The scopes Aegis asks Google for. gmail.modify covers search, read, drafting
# and sending. Aegis does NOT request https://mail.google.com/, which would add
# permanent-delete rights it has no canonical operation for: the connector
# cannot perform an operation Google never granted, which is a second, external
# floor under the DENY that policy already applies to gmail.delete.
GMAIL_OAUTH_SCOPES = [
    scope.strip()
    for scope in _env(
        "AEGIS_GMAIL_SCOPES", "https://www.googleapis.com/auth/gmail.modify"
    ).split(",")
    if scope.strip()
]

GMAIL_TIMEOUT_SECONDS = float(_env("AEGIS_GMAIL_TIMEOUT_SECONDS", "20"))

# Phase 19: the AI agent's own model configuration. Read by the agent runtime
# in infra/ai-agent, never by the backend. The agent holds this and its Aegis
# token; it holds nothing belonging to Gmail.
AGENT_LLM_BASE_URL = _env("AEGIS_AGENT_LLM_BASE_URL", "")
AGENT_LLM_MODEL = _env("AEGIS_AGENT_LLM_MODEL", "")

# Phase 19: the identity of the AI agent that drives Gmail. Same shape as
# VERIFY_AGENT_TOKEN: generated by scripts/init-env.sh, handed to the agent
# container, matched by a seeded credential. It is an ordinary agent token,
# enforced like any other, and it is the *only* credential that agent holds.
GMAIL_AGENT_TOKEN = _env("AEGIS_GMAIL_AGENT_TOKEN", "")

# Phase 19: the Gmail connector runs in its own container, because it is the
# only component that needs a route to the internet and the only one that
# mounts the OAuth store. When this is set the broker sends gmail there instead
# of to the CRM protected-tool.
GMAIL_TOOL_URL = _env("AEGIS_GMAIL_TOOL_URL", "")


# ---------------------------------------------------------------------------
# Phase 20 -- hosted pilot.
#
# Every default below is the safe one for a service on the public internet when
# AEGIS_ENV=production, and the convenient one for a laptop otherwise. Callers
# read these as `config.NAME` at request time, so tests can move them.
# ---------------------------------------------------------------------------

_PRODUCTION = ENV == "production"


def _csv(name: str) -> list[str]:
    return [item.strip() for item in _env(name, "").split(",") if item.strip()]


# Registration by invitation only. Empty in production means registration is
# closed, which is the right failure: nobody signs up by accident.
INVITE_CODES = _csv("AEGIS_INVITE_CODES")

# Sliding-window limit per client address on login and register; 0 disables it.
# In-process, so each worker counts alone: a ceiling, not a guarantee.
# ponytail: the edge (Cloudflare) does the real limiting; a shared store if needed.
AUTH_RATE_PER_MIN = int(_env("AEGIS_AUTH_RATE_PER_MIN", "10" if _PRODUCTION else "0"))

# Requests declaring a larger body are refused with 413; 0 disables the check.
# Chunked bodies carry no length: the reverse proxy enforces the same cap.
MAX_BODY_BYTES = int(
    _env("AEGIS_MAX_BODY_BYTES", str(64 * 1024) if _PRODUCTION else "0")
)

# A production secret shorter than this, or containing a placeholder, is refused.
MIN_SECRET_LENGTH = 32

# Demo organization, demo admin and demo agents. Never in production.
SEED_DEMO = _flag("AEGIS_SEED_DEMO", not _PRODUCTION)

# The Gmail pilot (OAuth routes on the control plane). Opt-in in production.
ENABLE_GMAIL = _flag("AEGIS_ENABLE_GMAIL", not _PRODUCTION)

# Send-like actions are judged on the recipients in the request, not on the
# destination the agent declares. Strict mode (production's default) applies this
# to every organization -- one that never listed its domains sees every recipient
# as external -- and BLOCKs a send that names no recipient. Outside strict mode
# the derivation applies once an organization has set its domains.
REQUIRE_DERIVED_DESTINATION = _flag("AEGIS_REQUIRE_DERIVED_DESTINATION", _PRODUCTION)

# Pending approvals one agent may hold open. A stuck or hostile agent cannot
# fill the operator's queue past this.
MAX_PENDING_APPROVALS_PER_AGENT = int(_env("AEGIS_MAX_PENDING_APPROVALS", "25"))

# The reviewer's preview of what they approve is deleted this long after the
# decision (or after the request expired undecided).
APPROVAL_PREVIEW_RETENTION_DAYS = int(
    _env("AEGIS_APPROVAL_PREVIEW_RETENTION_DAYS", "7")
)

# Public addresses, used in notification links and connection snippets.
PUBLIC_APP_URL = _env("AEGIS_PUBLIC_APP_URL", "").rstrip("/")
PUBLIC_GATEWAY_URL = _env("AEGIS_PUBLIC_GATEWAY_URL", "").rstrip("/")

# Where the built dashboard lives. Empty: /app/static (the image), then frontend/dist.
STATIC_DIR = _env("AEGIS_STATIC_DIR", "")

# Approval notifications by email. No host: the dashboard still shows everything.
SMTP_HOST = _env("AEGIS_SMTP_HOST", "")
SMTP_PORT = int(_env("AEGIS_SMTP_PORT", "587"))
SMTP_USER = _env("AEGIS_SMTP_USER", "")
SMTP_PASSWORD = _env("AEGIS_SMTP_PASSWORD", "")
SMTP_FROM = _env("AEGIS_SMTP_FROM", "")
SMTP_STARTTLS = _flag("AEGIS_SMTP_STARTTLS", True)

# Database pool. Bounded, and it fails fast (503) instead of holding a request
# for the SQLAlchemy default of 30 s while every worker thread piles up behind it.
DB_POOL_SIZE = int(_env("AEGIS_DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(_env("AEGIS_DB_MAX_OVERFLOW", "10"))
DB_POOL_TIMEOUT = float(_env("AEGIS_DB_POOL_TIMEOUT", "5"))
