"""Phase 17 — refuse to run with the shipped development secrets.

The evidence chain is an HMAC. Its security rests entirely on the key being
unknown to whoever might tamper with the database. Before Phase 17,
AEGIS_EVIDENCE_SECRET_KEY appeared in no .env.example, in no compose file and in
no script: every process that ever ran, including the three benchmark
campaigns, used the constant written in config.py. Anyone who could write to the
events table could also recompute a valid chain, so the tamper-evidence detected
accidental corruption but not an adversary.

The same is true of the signing key, the EAT key and the internal service
tokens. A default secret in a security product is not a convenience, so the app
refuses to start on one unless the operator explicitly opts in.

Tests and local development opt in through AEGIS_ALLOW_DEFAULT_SECRETS=1, which
is what conftest.py sets. Deployments are expected to supply real values.

Phase 20 adds a stricter mode. With AEGIS_ENV=production the opt-in above no
longer works, a secret that merely looks like a placeholder or is too short to be
a key is refused as well, and the "all" role (every trust domain in one process)
is refused outright.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config

# The values shipped in config.py as defaults. Any of these in use means the
# secret was never configured.
DEVELOPMENT_DEFAULTS = {
    "AEGIS_SECRET_KEY": "aegis-dev-secret-change-in-production",
    "AEGIS_EAT_KEY": "aegis-dev-eat-key-change-in-production",
    "AEGIS_EVIDENCE_SECRET_KEY": "aegis-dev-evidence-key-change-in-production",
    "AEGIS_CRM_SECRET": "aegis-internal-crm-secret-do-not-export",
}

# Phase 20: in production a value is also refused when it looks like a
# placeholder somebody forgot to replace (.env.example ships several).
PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "change_me",
    "your-",
    "-dev-",
    "dev-secret",
    "do-not-export",
    "example",
)

# Which secrets each role actually relies on. A role is only blocked by a weak
# secret it uses.
ROLE_REQUIREMENTS = {
    "control-plane": ("AEGIS_SECRET_KEY", "AEGIS_EVIDENCE_SECRET_KEY"),
    "enforcement-gateway": (
        "AEGIS_SECRET_KEY",
        "AEGIS_EAT_KEY",
        "AEGIS_EVIDENCE_SECRET_KEY",
    ),
    "credential-broker": ("AEGIS_EAT_KEY",),
    "protected-tool": (),
    "all": (
        "AEGIS_SECRET_KEY",
        "AEGIS_EAT_KEY",
        "AEGIS_EVIDENCE_SECRET_KEY",
    ),
}

_CURRENT = {
    "AEGIS_SECRET_KEY": lambda: config.SECRET_KEY,
    "AEGIS_EAT_KEY": lambda: config.EAT_KEY,
    "AEGIS_EVIDENCE_SECRET_KEY": lambda: config.EVIDENCE_SECRET_KEY,
    "AEGIS_CRM_SECRET": lambda: config.CRM_SECRET,
    "AEGIS_INTERNAL_GATEWAY_TOKEN": lambda: config.INTERNAL_GATEWAY_TOKEN,
    "AEGIS_INTERNAL_TOOL_TOKEN": lambda: config.INTERNAL_TOOL_TOKEN,
    "AEGIS_OAUTH_ENCRYPTION_KEY": lambda: config.GMAIL_OAUTH_ENCRYPTION_KEY,
}


class InsecureConfiguration(RuntimeError):
    """Raised at startup when a required secret is still a shipped default."""


@dataclass(frozen=True)
class PostureFinding:
    variable: str
    issue: str


def _production_extras(role: str) -> tuple[str, ...]:
    """Secrets a production role needs beyond the base list, when it uses them."""
    extras: list[str] = []
    if role == "enforcement-gateway" and config.BROKER_URL:
        extras.append("AEGIS_INTERNAL_GATEWAY_TOKEN")
    if role == "credential-broker":
        extras += [
            "AEGIS_INTERNAL_GATEWAY_TOKEN",
            "AEGIS_INTERNAL_TOOL_TOKEN",
            "AEGIS_CRM_SECRET",
        ]
    if role == "protected-tool":
        extras += ["AEGIS_INTERNAL_TOOL_TOKEN", "AEGIS_CRM_SECRET"]
    if role == "control-plane" and config.ENABLE_GMAIL:
        extras.append("AEGIS_OAUTH_ENCRYPTION_KEY")
    return tuple(extras)


def _strength_problem(value: str) -> str | None:
    lowered = value.lower()
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return "looks like a placeholder"
    if len(value) < config.MIN_SECRET_LENGTH:
        return f"shorter than {config.MIN_SECRET_LENGTH} characters"
    return None


def weak_secrets(role: str) -> list[PostureFinding]:
    """Secrets this role depends on that are still at their shipped default."""
    variables = ROLE_REQUIREMENTS.get(role, ROLE_REQUIREMENTS["all"])
    production = config.is_production()
    if production:
        variables = variables + tuple(
            item for item in _production_extras(role) if item not in variables
        )
    findings: list[PostureFinding] = []
    for variable in variables:
        current = _CURRENT[variable]()
        if not current:
            findings.append(PostureFinding(variable, "missing"))
        elif current == DEVELOPMENT_DEFAULTS.get(variable):
            findings.append(PostureFinding(variable, "shipped development default"))
        elif production:
            problem = _strength_problem(current)
            if problem:
                findings.append(PostureFinding(variable, problem))
    return findings


def assert_secrets_configured(role: str) -> None:
    if config.is_production():
        # AEGIS_ALLOW_DEFAULT_SECRETS deliberately has no effect here.
        if role == "all":
            raise InsecureConfiguration(
                "Refusing role 'all' in production: it runs every trust domain in "
                "one process. Run control-plane and enforcement-gateway as "
                "separate services."
            )
        findings = weak_secrets(role)
        if findings:
            detail = ", ".join(f"{item.variable} ({item.issue})" for item in findings)
            raise InsecureConfiguration(
                f"Refusing to start in production with weak secrets: {detail}. "
                "Generate real values with scripts/init-env.sh."
            )
        return
    if config.ALLOW_DEFAULT_SECRETS:
        return
    findings = weak_secrets(role)
    if not findings:
        return
    detail = ", ".join(f"{item.variable} ({item.issue})" for item in findings)
    raise InsecureConfiguration(
        "Refusing to start with development secrets: "
        f"{detail}. Set real values, or set AEGIS_ALLOW_DEFAULT_SECRETS=1 to "
        "accept a development posture explicitly."
    )


def posture_report(role: str) -> dict:
    findings = weak_secrets(role)
    return {
        "role": role,
        "environment": config.ENV,
        "default_secrets_allowed": config.ALLOW_DEFAULT_SECRETS,
        "weak_secrets": [item.variable for item in findings],
        "secure": not findings,
    }
