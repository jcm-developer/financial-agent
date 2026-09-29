"""The conversation with each experiment's model (F9.39).

The only endpoints that call a model from inside the API. They can because the
call writes nothing the experiment reads: the question and the answer go to
`chat_threads` and `chat_messages`, which `guard.py` lets through, and the tools
the model uses read the history without being able to change it. Firing a
*cycle* is still a subprocess (`runner.py`); this is not one.

The endpoint is a plain `def`: FastAPI runs it in its thread pool, so the 10-30 s
the model takes block one worker and not the event loop that serves the SSE.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from src import chat
from src.config import ConfigError, Infra
from src.llm import LLMError
from src.profile_settings import resolve_settings

from ..deps import UNPROCESSABLE, ConfigDb, ProfileQuery, ReadDb, find_profile, portfolio_of
from ..models import ChatAsk, ChatThread, ChatThreadDetail

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["conversación"])

#: Characters of the first question kept as the thread's title.
TITLE_LENGTH = 70


@router.get("/threads", response_model=list[ChatThread])
def threads(db: ReadDb, profile: ProfileQuery):
    return db.list_chat_threads(find_profile(db, profile)["id"])


@router.get("/threads/{thread_id}", response_model=ChatThreadDetail)
def thread_detail(db: ReadDb, thread_id: str):
    _thread_or_404(db, thread_id)
    return _detail(db, thread_id)


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thread(db: ConfigDb, thread_id: str):
    _thread_or_404(db, thread_id)
    db.delete_chat_thread(thread_id)


@router.post("/messages", response_model=ChatThreadDetail)
def ask(db: ConfigDb, body: ChatAsk, request: Request):
    """Stores the question, asks the profile's model and stores the answer.

    A failed answer is stored too, with its reason, and the call still returns
    200: the thread is what the screen shows, and a question that vanished
    because the provider answered 429 would be harder to understand than the
    failure itself.
    """
    profile = find_profile(db, body.profile)
    portfolio_id = portfolio_of(profile)
    try:
        settings = resolve_settings(db, profile["id"], infra=Infra.load())
    except ConfigError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc

    if body.thread_id:
        thread = _thread_or_404(db, body.thread_id)
        if thread["profile_id"] != profile["id"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Esa conversación es de otro experimento.",
            )
        thread_id = thread["id"]
    else:
        thread_id = db.create_chat_thread(profile["id"], title=_title(body.content))

    history = db.chat_messages(thread_id)
    question = body.content.strip()
    db.add_chat_message(
        thread_id, role="user", content=question, decision_id=body.decision_id
    )
    focus = [m["decision_id"] for m in history if m.get("decision_id")]
    if body.decision_id:
        focus.append(body.decision_id)

    factory = getattr(request.app.state, "chat_client", None) or chat.build_client
    try:
        with factory(settings) as llm:
            answer = chat.reply(
                llm, db, settings=settings, portfolio_id=portfolio_id,
                history=history, question=question,
                focus_ids=list(dict.fromkeys(focus)),
            )
    except LLMError as exc:
        log.warning("La conversación de %s no obtuvo respuesta: %s", profile["name"], exc)
        db.add_chat_message(
            thread_id, role="assistant", content="", llm_model=settings.llm_model,
            error=str(exc),
        )
    else:
        db.add_chat_message(
            thread_id, role="assistant", content=answer.content,
            tools=answer.tools, llm_model=answer.model,
            latency_ms=answer.latency_ms, prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
        )
    return _detail(db, thread_id)


def _thread_or_404(db: Any, thread_id: str) -> dict[str, Any]:
    thread = db.get_chat_thread(thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No existe esa conversación.")
    return thread


def _detail(db: Any, thread_id: str) -> dict[str, Any]:
    messages = db.chat_messages(thread_id)
    return {"thread": {**db.get_chat_thread(thread_id), "messages": len(messages)},
            "messages": messages}


def _title(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= TITLE_LENGTH else title[: TITLE_LENGTH - 1].rstrip() + "…"
