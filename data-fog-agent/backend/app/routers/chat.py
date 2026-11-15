"""Chat with the agent. Answers stream back as server-sent events."""

import json
from datetime import datetime

import openai
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agent, config
from app.auth import get_current_user
from app.db import SessionLocal, get_db
from app.models import Conversation, User
from app.rate_limit import check_rate_limit

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None


class ConversationSummary(BaseModel):
    id: int
    title: str
    updated_at: datetime


class ConversationOut(ConversationSummary):
    turns: list[dict]


def get_own_conversation(db: Session, user: User, conversation_id: int) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(404, "Conversation not found")
    return conversation


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    ).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return get_own_conversation(db, user, conversation_id)


@router.post("")
def chat(body: ChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Every message costs LLM calls, so one user can't run up the bill.
    check_rate_limit("chat", str(user.id), per_minute=30)
    if not config.LLM_API_KEY:
        raise HTTPException(503, "Chat needs LLM_API_KEY to be set on the server.")

    if body.conversation_id is not None:
        get_own_conversation(db, user, body.conversation_id)  # a 404 before the stream starts

    events = stream_answer(user.org_id, user.id, body.conversation_id, body.message)
    # no-transform stops the Next.js proxy from gzipping, which would buffer the stream.
    headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
    return StreamingResponse(events, media_type="text/event-stream", headers=headers)


def stream_answer(org_id: int, user_id: int, conversation_id: int | None, message: str):
    # The request's session may be closed while the answer is still streaming, so this
    # opens its own one.
    with SessionLocal(info={"org_id": org_id}) as db:
        user = db.get(User, user_id)
        if conversation_id is None:
            # Saved only once it has an answer, so a failed answer or a closed tab doesn't
            # leave an empty conversation in the list.
            conversation = Conversation(
                org_id=org_id, user_id=user_id, title=message[:200], messages=[], turns=[]
            )
        else:
            conversation = db.get(Conversation, conversation_id)

        # ponytail: the whole conversation is sent every time. A very long one will outgrow
        # the model's context and fail; the person starts a new chat. Summarise old turns
        # if that ever gets in the way.
        history = list(conversation.messages)
        text, done = "", None
        try:
            for event in agent.answer(db, user, history, message):
                if event["type"] == "text":
                    text += event["text"]
                if event["type"] == "done":
                    done = event
                yield sse(event)
        except openai.APIError as error:
            # Nothing is saved, so the conversation stays valid for the next question.
            db.rollback()
            yield sse({"type": "error", "message": f"The AI service failed: {error.message}"})
            return

        conversation.messages = history
        conversation.turns = conversation.turns + [
            {"role": "user", "text": message},
            {"role": "assistant", "text": text, "sources": done["sources"], "table": done["table"]},
        ]
        db.add(conversation)
        db.commit()
        yield sse({"type": "conversation", "id": conversation.id})


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"
