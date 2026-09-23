import json
import re
import sys
import types
from datetime import date

from explanations import _compose, _local_evidence, _normalize, build_explanations
from matcher import load_profiles, match
from models import MatchResult, Profile, Request


def profile(identifier: str, description: str, price: int = 200_000) -> Profile:
    return Profile(
        id=identifier,
        anon_name=f"Имя {identifier}",
        categories=("Флорист",),
        city="Алматы",
        city_imputed=False,
        synthetic=False,
        price_from_kzt=price,
        price_imputed=False,
        event_formats=("свадьба",),
        languages=("русский",),
        max_hours=None,
        busy_dates=frozenset(),
        description=description,
    )


REQUEST = Request("Алматы", date(2026, 10, 10), "свадьба", "Флорист", 500_000)
FIRST = profile("A", "Создаём букеты из сезонных цветов для камерных свадеб. Работаем в Алматы.")
SECOND = profile("B", "Оформляем свадебные арки живыми цветами и подбираем композиции под площадку.", 300_000)


def matched(cards: tuple[Profile, ...] = (FIRST, SECOND)) -> MatchResult:
    return MatchResult("matched", cards, len(cards), len(cards), {})


def test_local_explanations_are_specific_factual_and_stable():
    result = matched()
    first, mode = build_explanations(result, REQUEST)
    again, repeat_mode = build_explanations(result, REQUEST)

    assert mode == repeat_mode == "local"
    assert first == again
    assert list(first) == ["A", "B"]
    assert "сезонных цветов" in first["A"]
    assert "свадебные арки" in first["B"]
    assert "200 000 ₸" in first["A"]
    assert "300 000 ₸" in first["B"]
    assert "10.10.2026" in first["A"]
    assert "В описании профиля указано" in first["A"]


def test_empty_result_never_calls_ai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    assert build_explanations(matched(()), REQUEST, use_ai=True) == ({}, "local")


def test_ai_selects_only_literal_evidence_and_preserves_order(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            items = [
                {"id": "A", "evidence": "Создаём букеты из сезонных цветов для камерных свадеб"},
                {"id": "B", "evidence": "Оформляем свадебные арки живыми цветами"},
            ]
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps({"items": items})))]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] <= 6
            assert kwargs["max_retries"] == 0
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    result = matched()
    explanations, mode = build_explanations(result, REQUEST, use_ai=True)
    assert mode == "ai"
    assert len(calls) == 1
    assert list(explanations) == ["A", "B"]
    assert "сезонных цветов" in explanations["A"]
    assert result.cards == (FIRST, SECOND)


def test_invalid_ai_evidence_falls_back_for_entire_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    class FakeCompletions:
        def create(self, **kwargs):
            content = json.dumps(
                {"items": [
                    {"id": "B", "evidence": "Неизвестная награда и опыт 20 лет"},
                    {"id": "A", "evidence": "Создаём букеты из сезонных цветов"},
                ]}
            )
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    explanations, mode = build_explanations(matched(), REQUEST, use_ai=True)
    assert mode == "fallback"
    assert "сезонных цветов" in explanations["A"]
    assert "свадебные арки" in explanations["B"]


def test_requested_ai_without_key_uses_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    explanations, mode = build_explanations(matched(), REQUEST, use_ai=True)
    assert mode == "fallback"
    assert len(explanations) == 2


def test_equal_price_bands_get_distinct_source_facts():
    profiles = load_profiles()
    request = Request("Алматы", date(2026, 9, 23), "юбилей", "Лайв-бэнд", 1_150_000)
    result = match(profiles, request)
    texts, mode = build_explanations(result, request)

    assert mode == "local"
    assert [card.id for card in result.cards] == ["HK-23752", "HK-31819", "HK-83709"]
    assert "два вокалиста" in texts["HK-23752"]
    assert "4 вокалиста, струнный квартет" in texts["HK-83709"]
    assert texts["HK-23752"] != texts["HK-83709"]


def test_demo_host_uses_service_detail_instead_of_introduction():
    profiles = load_profiles()
    request = Request("Алматы", date(2026, 10, 11), "свадьба", "Ведущий", 2_000_000)
    result = match(profiles, request)
    texts, _ = build_explanations(result, request)

    assert "HK-44923" in texts
    assert "разработаем ОРИГИНАЛЬНЫЙ сценарий" in texts["HK-44923"]
    assert "Меня зовут" not in texts["HK-44923"]


def test_source_excerpt_is_literal_and_not_clipped_for_all_profiles():
    profiles = load_profiles()
    assert len(profiles) == 66
    for card in profiles:
        excerpt = _local_evidence(card)
        assert excerpt in _normalize(card.description), card.id
        assert not excerpt.endswith("…"), card.id
    by_id = {card.id: card for card in profiles}
    assert "Финалист премии" in _local_evidence(by_id["HK-26808"])
    assert "Меня зовут" not in _local_evidence(by_id["HK-61323"])


def test_nested_source_quotes_remain_readable_and_two_sentences():
    card = profile("QUOTED", "«Rurouni Sound» – группа с двумя вокалистами и саксофоном.")
    request = Request("Алматы", date(2026, 10, 10), "свадьба", "Флорист", 500_000)
    text = _compose(card, request, "«Rurouni Sound» – группа с двумя вокалистами и саксофоном")
    assert "„«Rurouni Sound» – группа" in text
    assert text.endswith("саксофоном“.")
    assert len(re.findall(r"(?<!\d)\.(?!\d)", text)) == 2
