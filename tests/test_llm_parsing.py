"""Tests of the defensive parsing of the model's responses.

Every case here is a real shape in which a NIM model returns "almost" clean JSON.
Without this layer, a whole cycle falls over because the model decided to explain
itself before answering.
"""

from __future__ import annotations

from src.analyst import _coerce_action, _coerce_conviction, _coerce_price
from src.llm import extract_json_object, strip_reasoning


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
