from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import config
from .database import SessionLocal, ensure_schema
from .routers import (
    agentctl,
    agents,
    approvals,
    auth,
    authorize,
    behavior_patterns,
    capabilities,
    contracts,
    evidence,
    gateway,
    gmail,
    policies,
    resources,
    simulate,
    verification,
)
from .routers import broker as broker_router
from .routers import tool as tool_router
from .security_posture import assert_secrets_configured, posture_report
from .seed import seed_builtin_patterns, seed_gmail_if_missing, seed_if_empty

CONTROL_ROUTERS = (
    auth,
    agents,
    policies,
    resources,
    approvals,
    behavior_patterns,
    capabilities,
    contracts,
    evidence,
    gmail,
    simulate,
    verification,
)
ENFORCEMENT_ROUTERS = (authorize, gateway, agentctl)
BROKER_ROUTERS = (broker_router,)
TOOL_ROUTERS = (tool_router,)

_ENFORCEMENT_PREFIXES = ("/api/authorize", "/api/gateway", "/api/agentctl")


def _filtered(routers):
    """Phase 20: what a role mounts depends on the environment.

    Gmail is opt-in (its OAuth routes are public surface). In production the
    agent test harness (verification runs, the agentctl job channel) is not
    mounted, and neither is the gateway's tool path unless a real broker stands
    behind it: without one it would run tools through the in-process harness,
    which has no EAT, no replay protection and holds every key in one process.
    """
    production = config.is_production()
    kept = []
    for router in routers:
        if router is gmail and not config.ENABLE_GMAIL:
            continue
        if production and router in (verification, agentctl):
            continue
        if production and router is gateway and not config.BROKER_URL:
            continue
        kept.append(router)
    return tuple(kept)


def _role_routers(role: str):
    if role == "control-plane":
        return _filtered(CONTROL_ROUTERS)
    if role == "enforcement-gateway":
        return _filtered(ENFORCEMENT_ROUTERS)
    if role == "credential-broker":
        return BROKER_ROUTERS
    if role == "protected-tool":
        return TOOL_ROUTERS
    return _filtered(CONTROL_ROUTERS + ENFORCEMENT_ROUTERS)


def _needs_database(role: str) -> bool:
    return role in {"all", "control-plane", "enforcement-gateway"}


def _needs_seed(role: str) -> bool:
    return role in {"all", "control-plane"}


def _dashboard_dir() -> Path | None:
    """The built dashboard, if there is one to serve."""
    if config.STATIC_DIR:
        candidates = [config.STATIC_DIR]
    else:
        candidates = [
            "/app/static",
            str(Path(__file__).resolve().parents[2] / "frontend" / "dist"),
        ]
    for raw in candidates:
        path = Path(raw)
        if (path / "index.html").is_file():
            return path.resolve()
    return None


def _mount_dashboard(application: FastAPI) -> None:
    """Serve the dashboard from the control plane: one URL, no CORS, no proxy.

    Registered last so every /api route matches first. Unknown paths fall back
    to index.html (the dashboard routes client-side) except under /api, which
    stays a JSON 404. A file is served only if it resolves inside the directory.
    """
    root = _dashboard_dir()
    if root is None:
        return
    index = root / "index.html"

    @application.get("/{full_path:path}", include_in_schema=False)
    def dashboard(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(role: str | None = None) -> FastAPI:
    selected = (role or config.AEGIS_ROLE or "all").strip() or "all"
    production = config.is_production()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        assert_secrets_configured(selected)
        if _needs_database(selected):
            ensure_schema()
        if _needs_seed(selected):
            db = SessionLocal()
            try:
                if config.SEED_DEMO:
                    seed_if_empty(db)
                    seed_gmail_if_missing(db)
                seed_builtin_patterns(db)
            finally:
                db.close()
        yield

    health_layer = "control-plane" if selected in {"all", "control-plane"} else selected
    application = FastAPI(
        title="Aegis — AI Security Control Layer",
        description=(
            "Model-agnostic control plane that governs identity, permissions, "
            "policy, risk, audit and human approval for AI agents."
        ),
        version="0.1.0",
        lifespan=lifespan,
        # A public deployment does not publish an interactive map of its API.
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    application.state.aegis_role = selected

    # Auth is a Bearer token in a header, never a cookie, so credentials are
    # never allowed cross-origin. The wildcard exists only outside production.
    origins = list(config.CORS_ORIGINS)
    if not production:
        origins.append("*")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = "/api"
    for router in _role_routers(selected):
        application.include_router(router.router, prefix=api)

    @application.middleware("http")
    async def reject_agent_tokens_on_control_plane(request, call_next):
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _ENFORCEMENT_PREFIXES):
            return await call_next(request)
        if selected in {"credential-broker", "protected-tool"}:
            return await call_next(request)
        if not path.startswith("/api/"):
            return await call_next(request)
        if path.startswith("/api/health"):
            return await call_next(request)
        agent_header = request.headers.get("x-agent-token")
        authorization = request.headers.get("authorization") or ""
        uses_agent = bool(agent_header) or authorization.lower().startswith("bearer aegis_")
        if uses_agent:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Agent credentials cannot access the control plane"
                },
            )
        return await call_next(request)

    @application.middleware("http")
    async def limit_request_body(request, call_next):
        # Outermost check: a declared oversize body is refused before it is read.
        # ponytail: chunked bodies carry no length; the reverse proxy caps those.
        limit = config.MAX_BODY_BYTES
        if limit > 0:
            declared = request.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > limit:
                return JSONResponse(
                    status_code=413, content={"detail": "Request body too large"}
                )
        return await call_next(request)

    @application.get("/api/health")
    def health():
        posture = posture_report(selected)
        if config.is_production():
            # An unauthenticated caller learns whether it is safe, not which
            # secret is weak.
            posture = {"secure": posture["secure"]}
        return {
            "status": "ok",
            "product": "aegis",
            "layer": health_layer,
            "posture": posture,
            "features": {"gmail": config.ENABLE_GMAIL, "demo": config.SEED_DEMO},
        }

    if selected in {"all", "control-plane"}:
        _mount_dashboard(application)

    return application


app = create_app()
