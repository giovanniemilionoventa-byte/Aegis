import hmac
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..ratelimit import enforce
from ..services import starter_pack
from ..database import get_db
from ..security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _slugify(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    return slug or "org"


MIN_PASSWORD_LENGTH = 12


def _require_invite(code: Optional[str]) -> None:
    """Registration by invitation. Every code is compared, so timing tells nothing."""
    supplied = (code or "").strip().encode()
    accepted = False
    for allowed in config.INVITE_CODES:
        accepted |= hmac.compare_digest(supplied, allowed.encode())
    if not accepted:
        raise HTTPException(status_code=403, detail="Registration is by invitation.")


@router.post("/register", response_model=schemas.TokenResponse)
def register(
    body: schemas.RegisterRequest, request: Request, db: Session = Depends(get_db)
):
    enforce(request, "register")
    if config.is_production():
        _require_invite(body.invite_code)
        if len(body.password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
            )
    email = body.email.lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    slug = _slugify(body.organization_name)
    base = slug
    n = 1
    while db.query(models.Organization).filter(models.Organization.slug == slug).first():
        n += 1
        slug = f"{base}-{n}"
    org = models.Organization(
        name=body.organization_name,
        slug=slug,
        # The admin's own mail domain is "inside" until they say otherwise.
        internal_domains=email.split("@", 1)[1],
    )
    db.add(org)
    db.flush()
    user = models.User(
        organization_id=org.id,
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role="admin",
    )
    db.add(user)
    starter_pack.install(db, org.id)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, org.id)
    return schemas.TokenResponse(access_token=token)


@router.post("/login", response_model=schemas.TokenResponse)
def login(body: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    enforce(request, "login")
    user = db.query(models.User).filter(models.User.email == body.email.lower()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user.id, user.organization_id)
    return schemas.TokenResponse(access_token=token)


@router.get("/me", response_model=schemas.MeResponse)
def me(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = (
        db.query(models.Organization)
        .filter(models.Organization.id == user.organization_id)
        .first()
    )
    return schemas.MeResponse(user=user, organization=org)
