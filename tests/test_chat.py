"""Tests of the experiment chat (F9.39).

The model is replaced by a script of answers; everything else is real: the
context read from SQLite, the tools, the fenced API connection. What matters
most is not that the model answers but that the conversation **cannot reach the
experiment**: it writes only its own tables and the history does not move.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.deps import ApiConfig
from api.guard import history_tables
from api.main import create_app
from src import chat
from src.config import Infra
from src.db import Database
from src.llm import LLMError, LLMResponse, ToolCall
from src.profile_settings import resolve_settings


class ScriptedLLM:
    """Answers each `complete_chat` with the next response of the script and
    keeps what it was sent. A response that is an exception is raised."""

    def __init__(self, *script: LLMResponse | Exception) -> None:
        self.script = list(script)
        self.model = "scripted"
        self.calls: list[dict] = []

    def complete_chat(self, *, messages, tools=None, max_tokens=None, reasoning_effort=None):
        self.calls.append({
            "messages": json.loads(json.dumps(messages)), "tools": tools,
            "effort": reasoning_effort,
        })
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None


def text(content: str) -> LLMResponse:
    return LLMResponse(
        content=content, parsed=None, model="scripted", latency_ms=5,
        prompt_tokens=100, completion_tokens=20,
    )


def asks(name: str, **arguments) -> LLMResponse:
    return LLMResponse(
        content="", parsed=None, model="scripted", latency_ms=5,
        prompt_tokens=100, completion_tokens=10,
        tool_calls=[ToolCall(id="c1", name=name, arguments=json.dumps(arguments))],
    )


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "chat.db")


@pytest.fixture
def seeded(db_path):
    """A European profile with one cycle: a hold on SAP and an approved buy on SAN."""
    with Database(path=db_path) as db:
        profile_id = db.create_profile(name="eu-test", settings={
            "market": "eu", "llm_provider": "openai", "llm_model": "gpt-test",
            "llm_api_key": "sk-test",
        })
        db.set_profile_universe(profile_id, ["SAN.MC", "SAP.DE"])
        portfolio_id = db.get_profile(profile_id)["portfolio_id"]
        cycle_id = db.start_cycle(
            portfolio_id=portfolio_id, equity_start=10_000.0, cash_start=10_000.0,
            market_open=True, symbols=["SAN.MC", "SAP.DE"], llm_model="gpt-test",
            settings={"market": "eu"},
        )
        db.finish_cycle(cycle_id, status="completed", equity_end=10_000.0)
        db.execute(
            "insert into market_snapshots (id, cycle_id, symbol, as_of, price, "
            "indicators_json, created_at) values (7, ?, 'SAP.DE', "
            "'2026-09-29T08:00:00+00:00', 185.2, '{\"rsi_14\": 48.1}', "
            "'2026-09-29T08:00:00+00:00')",
            (cycle_id,),
        )
        for decision_id, symbol, action, thesis, snapshot in (
            ("d-sap", "SAP.DE", "hold", "El objetivo exigido supera el máximo anual.", 7),
            ("d-san", "SAN.MC", "buy", "Tendencia sobre las medias.", None),
        ):
            db.execute(
                "insert into decisions (id, cycle_id, portfolio_id, snapshot_id, symbol, "
                "kind, action, conviction, thesis, llm_model, created_at) values "
                "(?, ?, ?, ?, ?, 'entry', ?, 70, ?, 'gpt-test', "
                "'2026-09-29T08:01:00+00:00')",
                (decision_id, cycle_id, portfolio_id, snapshot, symbol, action, thesis),
            )
        db.execute(
            "insert into risk_events (id, cycle_id, portfolio_id, decision_id, symbol, "
            "verdict, rule, reason, created_at) values ('r1', ?, ?, 'd-san', 'SAN.MC', "
            "'approved', 'position_size', 'ok', '2026-09-29T08:01:01+00:00')",
            (cycle_id, portfolio_id),
        )
        db.execute(
            "insert into news_items (cycle_id, symbol, ref, title, provider, created_at) "
            "values (?, null, 'M1', 'El BCE mantiene los tipos', 'test', "
            "'2026-09-29T08:00:00+00:00')",
            (cycle_id,),
        )
    return {"profile_id": profile_id, "portfolio_id": portfolio_id, "cycle_id": cycle_id}


def settings_of(db_path, profile_id):
    with Database(path=db_path) as db:
        return resolve_settings(db, profile_id, infra=Infra.load())


@pytest.fixture
def client(db_path, seeded):
    app = create_app(ApiConfig(db_path=db_path, controls=False))
    with TestClient(app) as test_client:
        test_client.app_ref = app
        yield test_client


def script_client(client, *script):
    llm = ScriptedLLM(*script)
    client.app_ref.state.chat_client = lambda settings: llm
    return llm


def history_counts(db_path):
    with Database(path=db_path, read_only=True) as db:
        return {
            table: db.query(f"select count(1) as n from {table}")[0]["n"]
            for table in history_tables(db)
        }


# ======================================================================
# The reply
# ======================================================================

def test_the_context_carries_the_last_cycle_and_the_rule_of_no_memory(db_path, seeded):
    llm = ScriptedLLM(text("LISTO"), text("Mantuve SAP porque…"))
    settings = settings_of(db_path, seeded["profile_id"])
    with Database(path=db_path, read_only=True) as db:
        answer = chat.reply(
            llm, db, settings=settings, portfolio_id=seeded["portfolio_id"],
            history=[], question="¿Por qué no compraste SAP?",
        )

    assert answer.content == "Mantuve SAP porque…"
    system = llm.calls[1]["messages"][0]["content"]
    assert "NO RECUERDAS" in system
    assert "El objetivo exigido supera el máximo anual." in system
    assert "El BCE mantiene los tipos" in system
    assert llm.calls[1]["messages"][-1] == {
        "role": "user", "content": "¿Por qué no compraste SAP?",
    }


def test_the_lookup_goes_without_reasoning_and_the_answer_with_the_profiles(
    db_path, seeded
):
    """GPT-6 refuses tools while it reasons on /chat/completions, so the two
    halves are asked for separately (see the module's docstring)."""
    llm = ScriptedLLM(text("LISTO"), text("Respuesta."))
    settings = settings_of(db_path, seeded["profile_id"])
    with Database(path=db_path, read_only=True) as db:
        answer = chat.reply(
            llm, db, settings=settings, portfolio_id=seeded["portfolio_id"],
            history=[], question="¿Cómo ves el mercado?",
        )

    lookup, final = llm.calls
    assert lookup["tools"] and lookup["effort"] == "none"
    assert final["tools"] is None and final["effort"] is None
    # What the lookup wrote instead of a tool call is not the answer.
    assert answer.content == "Respuesta."
    assert "FASE DE CONSULTA" not in final["messages"][0]["content"]


def test_a_tool_result_goes_back_to_the_model_before_it_answers(db_path, seeded):
    llm = ScriptedLLM(
        asks("decision_data", decision_id="d-sap"), text("LISTO"), text("Vi un RSI de 48."),
    )
    settings = settings_of(db_path, seeded["profile_id"])
    with Database(path=db_path, read_only=True) as db:
        answer = chat.reply(
            llm, db, settings=settings, portfolio_id=seeded["portfolio_id"],
            history=[], question="¿Qué datos viste en SAP?",
        )

    assert answer.content == "Vi un RSI de 48."
    assert answer.tools == [{"name": "decision_data", "arguments": {"decision_id": "d-sap"}}]
    assert answer.prompt_tokens == 300
    tool_message = llm.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert json.loads(tool_message["content"])["indicators"] == {"rsi_14": 48.1}
    # The answer sees the lookup as data in its context, not as tool messages.
    final = llm.calls[2]["messages"]
    assert "consultas_hechas_para_esta_pregunta" in final[0]["content"]
    assert '"rsi_14": 48.1' in final[0]["content"]
    assert all(m["role"] != "tool" for m in final)


def test_the_lookups_have_a_ceiling_and_then_the_model_answers(
    db_path, seeded, monkeypatch
):
    monkeypatch.setattr(chat, "MAX_TOOL_ROUNDS", 1)
    llm = ScriptedLLM(asks("cycles"), text("Con lo que tengo: dos ciclos."))
    settings = settings_of(db_path, seeded["profile_id"])
    with Database(path=db_path, read_only=True) as db:
        answer = chat.reply(
            llm, db, settings=settings, portfolio_id=seeded["portfolio_id"],
            history=[], question="¿Cuántos ciclos llevas?",
        )

    assert answer.content == "Con lo que tengo: dos ciclos."
    assert llm.calls[0]["tools"] and llm.calls[1]["tools"] is None


def test_a_tool_cannot_read_another_experiments_decision(db_path, seeded):
    settings = settings_of(db_path, seeded["profile_id"])
    call = ToolCall(id="c", name="decision_data", arguments='{"decision_id": "d-sap"}')
    with Database(path=db_path, read_only=True) as db:
        assert "error" in chat.run_tool(db, "otra-cartera", settings, call)
        assert chat.run_tool(db, seeded["portfolio_id"], settings, call)["symbol"] == "SAP.DE"


def test_a_malformed_tool_call_comes_back_as_an_error_the_model_can_read(db_path, seeded):
    settings = settings_of(db_path, seeded["profile_id"])
    with Database(path=db_path, read_only=True) as db:
        unknown = chat.run_tool(
            db, seeded["portfolio_id"], settings, ToolCall("c", "place_order", "{}")
        )
        broken = chat.run_tool(
            db, seeded["portfolio_id"], settings, ToolCall("c", "search_decisions", "{not json")
        )
    assert "error" in unknown
    assert "decisions" in broken


# ======================================================================
# The API
# ======================================================================

def test_a_question_opens_a_thread_and_stores_both_messages(client, db_path, seeded):
    script_client(client, text("LISTO"), text("Porque el suelo de una sigma era alto."))

    response = client.post("/api/chat/messages", json={
        "profile": "eu-test", "content": "¿Por qué no compraste SAP?",
        "decision_id": "d-sap",
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["thread"]["title"] == "¿Por qué no compraste SAP?"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["decision_id"] == "d-sap"
    assert body["messages"][1]["content"] == "Porque el suelo de una sigma era alto."
    threads = client.get("/api/chat/threads?profile=eu-test").json()
    assert [t["messages"] for t in threads] == [2]


def test_a_follow_up_keeps_the_decision_in_view(client, seeded):
    llm = script_client(
        client, text("LISTO"), text("Primera."), text("LISTO"), text("Segunda."),
    )
    first = client.post("/api/chat/messages", json={
        "profile": "eu-test", "content": "Háblame de SAP", "decision_id": "d-sap",
    }).json()
    client.post("/api/chat/messages", json={
        "profile": "eu-test", "content": "¿Y hoy?", "thread_id": first["thread"]["id"],
    })

    second_call = llm.calls[3]["messages"]
    assert "decisiones_por_las_que_se_pregunta" in second_call[0]["content"]
    assert [m["role"] for m in second_call[1:]] == ["user", "assistant", "user"]


def test_the_conversation_does_not_move_the_history(client, db_path, seeded):
    before = history_counts(db_path)
    script_client(client, text("LISTO"), text("Hola."))
    thread = client.post(
        "/api/chat/messages", json={"profile": "eu-test", "content": "Hola"}
    ).json()["thread"]
    assert client.delete(f"/api/chat/threads/{thread['id']}").status_code == 204

    assert history_counts(db_path) == before
    assert client.get("/api/chat/threads?profile=eu-test").json() == []


def test_a_failed_answer_is_stored_with_its_reason(client, seeded):
    script_client(client, LLMError("OpenAI respondió con un error 429"))

    body = client.post(
        "/api/chat/messages", json={"profile": "eu-test", "content": "¿Qué opinas?"}
    ).json()

    answer = body["messages"][-1]
    assert answer["content"] == "" and "429" in answer["error"]


def test_a_thread_belongs_to_one_experiment(client, db_path, seeded):
    with Database(path=db_path) as db:
        other = db.create_profile(name="eu-otro", settings={"market": "eu"})
        thread_id = db.create_chat_thread(other, title="Ajena")
    script_client(client, text("No debería llegar aquí."))

    response = client.post("/api/chat/messages", json={
        "profile": "eu-test", "content": "Hola", "thread_id": thread_id,
    })

    assert response.status_code == 409


def test_deleting_the_profile_takes_its_conversations(db_path, seeded):
    with Database(path=db_path) as db:
        thread_id = db.create_chat_thread(seeded["profile_id"], title="Hola")
        db.add_chat_message(thread_id, role="user", content="Hola")
        db.delete_profile(seeded["profile_id"])
        assert db.query("select count(1) as n from chat_messages")[0]["n"] == 0
