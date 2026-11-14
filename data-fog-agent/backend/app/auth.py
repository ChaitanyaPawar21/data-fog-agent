from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import SessionLocal, get_db, system_session
from app.models import User
from app.rate_limit import check_rate_limit

ACCESS_TOKEN_LIFETIME = timedelta(minutes=15)
REFRESH_TOKEN_LIFETIME = timedelta(days=7)
REFRESH_COOKIE = "refresh_token"

password_hasher = PasswordHasher()

# Checked when the email doesn't exist, so a wrong email takes as long as a wrong
# password and login can't be used to find out which emails are registered.
DUMMY_PASSWORD_HASH = password_hasher.hash("not-a-real-password")

router = APIRouter(prefix="/api/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_token(user: User, token_type: str, lifetime: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "org": user.org_id,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def read_token(token: str, token_type: str) -> dict:
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid or expired token") from None
    if payload.get("type") != token_type:
        raise HTTPException(401, "Invalid or expired token")
    return payload


def set_refresh_cookie(response: Response, user: User):
    response.set_cookie(
        REFRESH_COOKIE,
        create_token(user, "refresh", REFRESH_TOKEN_LIFETIME),
        max_age=int(REFRESH_TOKEN_LIFETIME.total_seconds()),
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="strict",
        path="/api/auth",
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(401, "Not logged in")
    payload = read_token(credentials.credentials, "access")

    # From here on this request's session only sees rows of the user's org.
    db.info["org_id"] = payload["org"]
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "Not logged in")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admins only")
    return user


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    org_id: int
    email: str
    name: str
    role: str


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    # Slows down password guessing. In production the IP comes from Caddy's X-Forwarded-For.
    check_rate_limit("login", request.client.host, per_minute=10)
    # The org isn't known until we find the user, so this lookup has to see every org.
    with system_session() as db:
        user = db.scalar(select(User).where(User.email == body.email.strip().lower()))

    password_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    if not verify_password(password_hash, body.password) or user is None:
        raise HTTPException(401, "Wrong email or password")

    set_refresh_cookie(response, user)
    return {"access_token": create_token(user, "access", ACCESS_TOKEN_LIFETIME)}


@router.post("/refresh")
def refresh(response: Response, refresh_token: str | None = Cookie(None)):
    if refresh_token is None:
        raise HTTPException(401, "Not logged in")
    payload = read_token(refresh_token, "refresh")

    with SessionLocal(info={"org_id": payload["org"]}) as db:
        user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "Not logged in")

    set_refresh_cookie(response, user)
    return {"access_token": create_token(user, "access", ACCESS_TOKEN_LIFETIME)}


@router.post("/logout")
def logout(response: Response):
    # ponytail: refresh tokens are stateless, so logout only clears the cookie.
    # A stolen refresh token stays valid until it expires.
    # Store token ids in the DB if that matters.
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
