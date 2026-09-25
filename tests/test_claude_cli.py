"""F9.35: Claude through the `claude` command, on the subscription.

Nothing here runs the real CLI: what gets tested is what would fail silently.
The credential that reaches the child decides whether a call is free or billed,
the flags decide whether each call carries 186 tokens of input or 8.389, and a
spent quota must not be retried for the length of the backoff.
"""

from __future__ import annotations

import json

import pytest

from src import claude_cli
from src.claude_cli import ClaudeCli, ClaudeCliError, child_env, parse_result, usage_tokens
from src.llm import LLMClient, LLMError

TOKEN = "sk-ant-oat01-subscription"


def _result(**overrides) -> str:
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": '{"action": "hold", "conviction": 55}',
        "usage": {
            "input_tokens": 3000,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 0,
            "output_tokens": 420,
        },
        "total_cost_usd": 0.0105,
    }
    base.update(overrides)
    return json.dumps(base)


# -- The credential ----------------------------------------------------------


def test_the_subscription_token_goes_as_oauth(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = child_env(TOKEN)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert "ANTHROPIC_API_KEY" not in env


def test_an_inherited_api_key_is_removed(monkeypatch):
    """The CLI prefers an API key over the token: one left in the environment
    would bill every call without any error."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-billed")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "other")
    env = child_env(TOKEN)
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_an_api_key_in_the_profile_is_passed_as_such(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "inherited")
    env = child_env("sk-ant-api03-profile")
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-api03-profile"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_the_side_call_to_haiku_is_switched_off():
    assert child_env(TOKEN)["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


# -- The command -------------------------------------------------------------


def test_the_command_isolates_the_call(tmp_path):
    """Without these flags the CLI loads CLAUDE.md, settings and MCP servers and
    bills them as input on every call: 8.389 tokens instead of 186."""
    cli = ClaudeCli(api_key=TOKEN, model="claude-sonnet-5", timeout=60)
    try:
        cmd = cli.command(tmp_path / "system.txt")
    finally:
        cli.close()
    joined = " ".join(cmd)
    for flag in (
        "--setting-sources", "--strict-mcp-config", "--no-session-persistence",
        "--exclude-dynamic-system-prompt-sections", "--tools",
    ):
        assert flag in cmd, flag
    assert cmd[cmd.index("--max-turns") + 1] == "1"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--json-schema" not in cmd  # two turns, the prompt paid twice
    assert "--effort" not in joined


def test_the_effort_is_passed_when_the_profile_sets_it(tmp_path):
    cli = ClaudeCli(api_key=TOKEN, model="m", timeout=60, effort="medium")
    try:
        cmd = cli.command(tmp_path / "s.txt")
    finally:
        cli.close()
    assert cmd[cmd.index("--effort") + 1] == "medium"


def test_the_call_does_not_run_in_the_project():
    cli = ClaudeCli(api_key=TOKEN, model="m", timeout=60)
    try:
        assert "claude-cli-" in cli._workdir.name
    finally:
        cli.close()


# -- The result --------------------------------------------------------------


def test_a_good_result_is_returned():
    assert parse_result(_result(), "", 0)["result"].startswith("{")


def test_cached_input_counts_as_input():
    assert usage_tokens(json.loads(_result())) == (3100, 420)


def test_an_error_result_is_retryable():
    with pytest.raises(ClaudeCliError) as caught:
        parse_result(_result(is_error=True, subtype="error_during_execution",
                             result="API Error: 529 overloaded"), "", 1)
    assert caught.value.retryable


def test_a_refused_request_is_not_retried():
    """A 400 will be refused again: the model the CLI version does not know."""
    with pytest.raises(ClaudeCliError) as caught:
        parse_result(_result(is_error=True, result=(
            "API Error: 400 Claude Code 2.1.185 does not support this model; "
            "version 2.1.280 or newer is required.")), "", 1)
    assert not caught.value.retryable


def test_a_rate_limit_is_retried():
    with pytest.raises(ClaudeCliError) as caught:
        parse_result(_result(is_error=True, result="API Error: 429 rate_limit_error"), "", 1)
    assert caught.value.retryable


def test_a_spent_quota_is_not_retried():
    """A window lasts hours: retrying it only burns the backoff."""
    with pytest.raises(ClaudeCliError, match="cupo") as caught:
        parse_result(_result(is_error=True, result="You've hit your session limit"), "", 1)
    assert not caught.value.retryable


def test_no_json_at_all_says_what_came_out():
    with pytest.raises(ClaudeCliError, match="sin devolver un resultado") as caught:
        parse_result("", "Invalid API key", 1)
    assert caught.value.retryable


# -- Through LLMClient -------------------------------------------------------


def _client(monkeypatch, outcomes):
    calls = iter(outcomes)

    def fake_complete(self, *, system, user):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return json.loads(outcome), 1234

    monkeypatch.setattr(ClaudeCli, "complete", fake_complete)
    monkeypatch.setattr(LLMClient, "_sleep_backoff", lambda self, attempt, override=None: None)
    return LLMClient(api_key=TOKEN, provider="anthropic", model="claude-sonnet-5",
                     max_retries=3)


def test_the_client_hands_the_call_to_the_cli(monkeypatch):
    with _client(monkeypatch, [_result()]) as client:
        assert client._client is None
        response = client.complete_json(system="s", user="u")
    assert response.parsed == {"action": "hold", "conviction": 55}
    assert (response.prompt_tokens, response.completion_tokens) == (3100, 420)
    assert response.raw["total_cost_usd"] == 0.0105
    assert response.latency_ms == 1234


def test_a_transient_failure_is_retried(monkeypatch):
    outcomes = [ClaudeCliError("529", retryable=True), _result()]
    with _client(monkeypatch, outcomes) as client:
        assert client.complete_json(system="s", user="u").parsed["action"] == "hold"


def test_a_spent_quota_fails_at_once(monkeypatch):
    outcomes = [ClaudeCliError("cupo agotado", retryable=False), _result()]
    with _client(monkeypatch, outcomes) as client:
        with pytest.raises(LLMError, match="cupo agotado"):
            client.complete_json(system="s", user="u")


def test_an_answer_without_json_is_refused(monkeypatch):
    with _client(monkeypatch, [_result(result="Lo siento, no puedo.")]) as client:
        with pytest.raises(LLMError, match="no devolvió un JSON válido"):
            client.complete_json(system="s", user="u")


def test_a_missing_binary_is_said_by_name(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda *a, **k: None)
    cli = ClaudeCli(api_key=TOKEN, model="m", timeout=60, binary="claude-no-existe")
    try:
        with pytest.raises(ClaudeCliError, match="claude-no-existe") as caught:
            cli.complete(system="s", user="u")
    finally:
        cli.close()
    assert not caught.value.retryable
