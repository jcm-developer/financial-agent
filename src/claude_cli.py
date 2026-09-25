"""Claude through the `claude` command line, on the owner's subscription (F9.35).

**Why the CLI and not the API.** The API is billed per token and the `anthropic`
SDK would be a new dependency and a second HTTP dialect (F9.1). The CLI is what
the subscription pays for: with a token from `claude setup-token` in
`CLAUDE_CODE_OAUTH_TOKEN`, every call comes out of the same quota as interactive
use and nothing is billed apart. Anthropic documents that token for "CI
pipelines, scripts, or other environments where interactive browser login isn't
available". What is **not** allowed is lifting that token into the SDK, so the
CLI is the only door, not merely the cheaper one.

The key in the profile decides the billing, and the prefix says which is which:
an `sk-ant-oat…` goes out as the subscription token, anything else as
`ANTHROPIC_API_KEY`, which the CLI bills per token. ⚠️ **The CLI prefers an API
key over the token**, so any `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` in the
environment is removed from the child's: one inherited by accident would turn a
free call into a billed one without any error.

**The flags are the difference between 186 tokens and 8.389.** Measured on the
2026-09-25 with the same one-line prompt: run from the repo, the CLI loads
CLAUDE.md, the settings and the MCP servers by itself, and bills them as input on
every call. Hence the empty working directory, `--setting-sources ""`,
`--strict-mcp-config`, `--tools ""` and
`--exclude-dynamic-system-prompt-sections`; and
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, without which each call also made a
side call to Haiku of ~500 tokens.

**`--json-schema` was tried and discarded.** It validates the answer, which
would close F9.24 on this path, but it works through an internal tool, so every
call takes two turns and pays the prompt twice. The whole point of the profile is
to spend little, and Sonnet returns clean JSON in one turn without it.

The prompts go by file and stdin, not as arguments: a system prompt of several
thousand characters on the command line trips Windows' 32.767-character limit,
and nothing is gained by risking it on Linux either.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The subscription token issued by `claude setup-token`.
SUBSCRIPTION_TOKEN_PREFIX = "sk-ant-oat"

#: Credentials the CLI would pick before the subscription token.
_OVERRIDING_CREDENTIALS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

#: Phrases of the CLI's quota errors. A spent window lasts hours, so retrying it
#: within the cycle only burns the backoff.
_QUOTA_MARKERS = ("usage limit", "session limit", "weekly limit", "hit your")

#: The API's status as the CLI reports it: «API Error: 400 …». A 4xx other than
#: 429 is a request that will be refused again —measured on the 2026-09-26 with
#: «Claude Code 2.1.185 does not support this model», retried to no purpose.
_API_STATUS = re.compile(r"API Error:\s*(\d{3})")


class ClaudeCliError(RuntimeError):
    """The call failed. `retryable` says whether trying again can help."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class ClaudeCli:
    """One single-turn call per `complete`, with no tools and no project context."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float,
        effort: str | None = None,
        binary: str | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.effort = effort
        self.binary = binary or os.environ.get("CLAUDE_BIN") or "claude"
        self._env = child_env(api_key)
        # One empty directory for the whole session: the CLI reads CLAUDE.md
        # from its working directory upwards, and the project's would be billed
        # as input on every call.
        self._workdir = tempfile.TemporaryDirectory(prefix="claude-cli-")

    def close(self) -> None:
        self._workdir.cleanup()

    def command(self, system_prompt_file: Path) -> list[str]:
        cmd = [
            self.binary, "-p",
            "--model", self.model,
            "--system-prompt-file", str(system_prompt_file),
            "--output-format", "json",
            "--max-turns", "1",
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--exclude-dynamic-system-prompt-sections",
        ]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    def complete(self, *, system: str, user: str) -> tuple[dict[str, Any], int]:
        """Returns the CLI's result object and the latency in milliseconds."""
        if shutil.which(self.binary, path=self._env.get("PATH")) is None:
            raise ClaudeCliError(
                f"No se encuentra el ejecutable «{self.binary}» de Claude Code. "
                "En Docker lo instala la imagen; fuera, hay que instalarlo o "
                "indicar su ruta en CLAUDE_BIN.",
                retryable=False,
            )
        system_file = Path(self._workdir.name) / "system.txt"
        system_file.write_text(system, encoding="utf-8")

        started = time.monotonic()
        try:
            completed = subprocess.run(
                self.command(system_file),
                input=user,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=self._workdir.name,
                env=self._env,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliError(
                f"Claude no contestó en {self.timeout:g} s.", retryable=True
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        return parse_result(completed.stdout, completed.stderr, completed.returncode), latency_ms


def child_env(api_key: str) -> dict[str, str]:
    """The child's environment, with exactly one credential: the profile's."""
    env = {k: v for k, v in os.environ.items() if k not in _OVERRIDING_CREDENTIALS}
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    if api_key.startswith(SUBSCRIPTION_TOKEN_PREFIX):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = api_key
    else:
        env["ANTHROPIC_API_KEY"] = api_key
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def parse_result(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    """The CLI's JSON result, or a `ClaudeCliError` that says whether to retry.

    A failed call can still print a result object with `is_error`, so the JSON is
    looked at before the exit code: it carries the reason.
    """
    try:
        result = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        result = None

    if not isinstance(result, dict):
        detail = (stderr or stdout or "").strip()[:400]
        raise ClaudeCliError(
            f"Claude Code terminó con código {returncode} sin devolver un resultado: {detail}",
            retryable=not _is_quota(detail),
        )

    if result.get("is_error") or result.get("subtype") != "success":
        detail = str(result.get("result") or result.get("errors") or result.get("subtype"))
        if _is_quota(detail):
            raise ClaudeCliError(
                f"Se ha agotado el cupo de la suscripción de Claude: {detail[:300]}",
                retryable=False,
            )
        raise ClaudeCliError(
            f"Claude Code devolvió un error: {detail[:400]}",
            retryable=_is_transient(detail),
        )

    return result


def _is_transient(text: str) -> bool:
    """Whether trying again can help: a 429, a 5xx, or no status at all."""
    match = _API_STATUS.search(text)
    if match is None:
        return True
    status = int(match.group(1))
    return status == 429 or status >= 500


def _is_quota(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _QUOTA_MARKERS)


def usage_tokens(result: dict[str, Any]) -> tuple[int, int]:
    """(prompt, completion) tokens. Cached input counts as input: for the quota
    it is still context the model read."""
    usage = result.get("usage") or {}
    prompt = sum(
        int(usage.get(k) or 0)
        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )
    return prompt, int(usage.get("output_tokens") or 0)
