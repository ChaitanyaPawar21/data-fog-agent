"""Personal API tokens. An MCP client sends one and acts as the user who made it."""

import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import get_current_user
from app.db import get_db, system_session
from app.models import ApiToken, User

router = APIRouter(prefix="/api/tokens", tags=["tokens"])

MAX_TOKENS_PER_USER = 20


def hash_token(token: str) -> str:
    # The token is 32 random bytes, so a plain fast hash is enough: there is nothing to guess.
    return hashlib.sha256(token.encode()).hexdigest()


def user_for_token(token: str) -> tuple[int, int] | None:
    """(user id, org id) of the token's owner, or None if the token isn't valid."""
    # The org isn't known until the token is found, so this lookup has to see every org.
    with system_session() as db:
        api_token = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
        if api_token is None:
            return None
        api_token.last_used_at = datetime.now(UTC)
        db.commit()
        return api_token.user_id, api_token.org_id


class NewTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None


class NewToken(TokenOut):
    token: str


@router.get("", response_model=list[TokenOut])
def list_tokens(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc())
    ).all()


@router.post("", response_model=NewToken, status_code=201)
def create_token(
    body: NewTokenRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    count = db.scalar(select(func.count()).where(ApiToken.user_id == user.id))
    if count >= MAX_TOKENS_PER_USER:
        raise HTTPException(409, "You have too many tokens. Revoke one you no longer use.")

    token = "dfa_" + secrets.token_urlsafe(32)
    api_token = ApiToken(
        org_id=user.org_id, user_id=user.id, name=body.name.strip(), token_hash=hash_token(token)
    )
    db.add(api_token)
    db.flush()
    audit(db, user, "user", "create_api_token", f"api_token:{api_token.id}", {"name": body.name})
    db.commit()
    # The only time the token is ever shown.
    return NewToken(
        id=api_token.id,
        name=api_token.name,
        created_at=api_token.created_at,
        last_used_at=None,
        token=token,
    )


@router.delete("/{token_id}", status_code=204)
def revoke_token(
    token_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    api_token = db.get(ApiToken, token_id)
    if api_token is None or api_token.user_id != user.id:
        raise HTTPException(404, "Token not found")
    audit(db, user, "user", "revoke_api_token", f"api_token:{token_id}", {"name": api_token.name})
    db.delete(api_token)
    db.commit()
