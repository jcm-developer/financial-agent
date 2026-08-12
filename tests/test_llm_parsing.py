"""Tests of the defensive parsing of the model's responses.

Every case here is a real shape in which a NIM model returns "almost" clean JSON.
Without this layer, a whole cycle falls over because the model decided to explain
itself before answering.
"""

from __future__ import annotations

from src.analyst import _coerce_action, _coerce_conviction, _coerce_price
from src.llm import _read_sse, extract_json_object, strip_reasoning


def sse(*events: str) -> list[str]:
    """The lines as httpx hands them over: no newline, and a blank one between
    events."""
    lines: list[str] = []
    for event in events:
        lines.extend([event, ""])
    return lines


# -- Reensamblado del stream (F9.22) -----------------------------------------

def test_the_fragments_are_joined_in_order():
    stream = _read_sse(sse(
        'data: {"choices":[{"delta":{"content":"{\\"act"}}]}',
        'data: {"choices":[{"delta":{"content":"ion\\": \\"buy\\"}"}}]}',
        "data: [DONE]",
    ))
    assert extract_json_object(stream.text) == {"action": "buy"}


def test_data_without_a_space_is_read_too():
    """NIM does not always put the space after `data:`."""
    stream = _read_sse(sse('data:{"choices":[{"delta":{"content":"ok"}}]}'))
    assert stream.text == "ok"


def test_keep_alive_comments_are_ignored():
    stream = _read_sse(sse(": ping", 'data: {"choices":[{"delta":{"content":"ok"}}]}'))
    assert stream.text == "ok"


def test_a_broken_chunk_does_not_lose_what_came_before():
    """Better a response with a hole —which the JSON parsing will catch— than
    throwing away everything that did arrive."""
    stream = _read_sse(sse(
        'data: {"choices":[{"delta":{"content":"ho"}}]}',
        "data: {esto no es json",
        'data: {"choices":[{"delta":{"content":"la"}}]}',
    ))
    assert stream.text == "hola"


def test_the_stream_running_out_is_an_ending():
    """`[DONE]` is not demanded: some deployments simply close."""
    stream = _read_sse(sse('data: {"choices":[{"delta":{"content":"{}"}}]}'))
    assert stream.text == "{}"
    assert stream.error == ""


def test_the_usage_of_the_last_chunk_is_kept():
    """It comes in a chunk with an empty `choices`, and it is the only place
    where the tokens of the decision are."""
    stream = _read_sse(sse(
        'data: {"choices":[{"delta":{"content":"x"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":22}}',
        "data: [DONE]",
    ))
    assert (stream.usage["prompt_tokens"], stream.usage["completion_tokens"]) == (11, 22)


def test_the_model_of_the_chunks_is_kept():
    stream = _read_sse(sse('data: {"model":"meta/llama-3.3-70b-instruct","choices":[]}'))
    assert stream.model == "meta/llama-3.3-70b-instruct"


def test_an_error_event_stops_the_stream():
    stream = _read_sse(sse(
        'data: {"choices":[{"delta":{"content":"a medias"}}]}',
        'data: {"error":{"message":"overloaded"}}',
        'data: {"choices":[{"delta":{"content":"esto ya no"}}]}',
    ))
    assert "overloaded" in stream.error
    assert stream.text == "a medias"


def test_with_no_content_the_reasoning_is_the_answer():
    """Reasoning models sometimes fill only `reasoning_content`, and then the
    JSON is in there."""
    stream = _read_sse(sse(
        'data: {"choices":[{"delta":{"reasoning_content":"{\\"action\\": \\"hold\\"}"}}]}'
    ))
    assert extract_json_object(stream.text) == {"action": "hold"}


def test_the_content_wins_over_the_reasoning():
    stream = _read_sse(sse(
        'data: {"choices":[{"delta":{"reasoning_content":"pensando","content":"{}"}}]}'
    ))
    assert stream.text == "{}"


def test_a_delta_in_blocks_is_flattened():
    stream = _read_sse(sse(
        'data: {"choices":[{"delta":{"content":[{"type":"text","text":"ok"}]}}]}'
    ))
    assert stream.text == "ok"


def test_an_empty_stream_brings_no_text():
    """Which is what tells a broken transport from a bad answer."""
    assert _read_sse([]).text == ""


# -- Extraccion de JSON ------------------------------------------------------

def test_plain_json_parses():
    assert extract_json_object('{"action": "buy"}') == {"action": "buy"}


def test_json_inside_a_code_fence_parses():
    text = 'Aqui tienes:\n```json\n{"action": "buy", "conviction": 80}\n```'
    assert extract_json_object(text) == {"action": "buy", "conviction": 80}


def test_json_inside_an_unlabelled_fence_parses():
    assert extract_json_object('```\n{"ok": true}\n```') == {"ok": True}


def test_reasoning_block_is_stripped():
    text = '<think>Veamos el RSI... esta alto.</think>\n{"action": "hold"}'
    assert extract_json_object(text) == {"action": "hold"}


def test_unclosed_reasoning_block_is_stripped():
    """Happens when the response is cut off by max_tokens."""
    assert strip_reasoning('{"a": 1}\n<think>me quede a medias') == '{"a": 1}'


def test_json_surrounded_by_prose_parses():
    text = 'Tras analizar los datos:\n{"action": "buy"}\nEspero que ayude.'
    assert extract_json_object(text) == {"action": "buy"}


def test_nested_braces_are_balanced_correctly():
    text = 'ruido {"a": {"b": {"c": 1}}, "d": 2} mas ruido'
    assert extract_json_object(text) == {"a": {"b": {"c": 1}}, "d": 2}


def test_braces_inside_strings_do_not_break_balancing():
    text = 'x {"thesis": "el patron {A} se confirma", "n": 1} y'
    assert extract_json_object(text) == {"thesis": "el patron {A} se confirma", "n": 1}


def test_escaped_quotes_inside_strings_are_handled():
    text = '{"thesis": "dijo \\"compra\\" ayer", "n": 1}'
    assert extract_json_object(text) == {"thesis": 'dijo "compra" ayer', "n": 1}


def test_malformed_json_returns_none():
    assert extract_json_object('{"action": buy,,}') is None


def test_no_json_at_all_returns_none():
    assert extract_json_object("No puedo ayudarte con eso.") is None


def test_empty_text_returns_none():
    assert extract_json_object("") is None


def test_an_object_wrapped_in_an_array_is_unwrapped():
    """Some models wrap the response in a one-element list. The object is
    extracted instead of failing: the schema is inside."""
    assert extract_json_object('[{"action": "buy"}]') == {"action": "buy"}


# -- Coercion de campos ------------------------------------------------------

def test_valid_action_passes_through():
    assert _coerce_action("buy", allowed={"buy", "hold"}) == "buy"


def test_action_is_case_and_space_insensitive():
    assert _coerce_action("  BUY  ", allowed={"buy", "hold"}) == "buy"


def test_unexpected_action_degrades_to_hold():
    """It never degrades to a trade: always to doing nothing."""
    assert _coerce_action("SHORT", allowed={"buy", "hold"}) == "hold"
    assert _coerce_action(None, allowed={"buy", "hold"}) == "hold"
    assert _coerce_action(42, allowed={"buy", "hold"}) == "hold"


def test_sell_is_not_allowed_in_an_entry_context():
    assert _coerce_action("sell", allowed={"buy", "hold"}) == "hold"


def test_conviction_is_clamped_to_its_range():
    assert _coerce_conviction(150) == 100
    assert _coerce_conviction(-20) == 0
    assert _coerce_conviction(72.6) == 73


def test_unparseable_conviction_becomes_zero():
    """Zero guarantees rejection by min_conviction, which is the safe failure."""
    assert _coerce_conviction("muy alta") == 0
    assert _coerce_conviction(None) == 0


def test_conviction_as_a_numeric_string_parses():
    assert _coerce_conviction("80") == 80


def test_price_rejects_non_positive_and_non_numeric_values():
    assert _coerce_price(0) is None
    assert _coerce_price(-10) is None
    assert _coerce_price("n/a") is None
    assert _coerce_price(None) is None
    assert _coerce_price(float("inf")) is None
    assert _coerce_price(float("nan")) is None


def test_price_rounds_to_four_decimals():
    assert _coerce_price("123.456789") == 123.4568
