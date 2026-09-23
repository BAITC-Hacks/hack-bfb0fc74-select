"""Acceptance of the full form → matcher → explanations → visible cards flow."""
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py", default_timeout=10).run()


def submit(app, *, city="Алматы", category="Ведущий", budget=2_000_000,
           day=date(2026, 10, 10)):
    app.selectbox[0].set_value(city)
    app.selectbox[2].set_value(category)
    app.number_input[0].set_value(budget)
    app.date_input[0].set_value(day)
    app.button[0].click().run()
    assert not app.exception
    return app


def test_three_cards_and_date_change(app):
    submit(app, day=date(2026, 10, 11))
    first = [item.value for item in app.subheader]
    assert len(first) == 3
    assert "из 5" in app.success[0].value
    text = " ".join(item.value for item in app.markdown)
    assert "11.10.2026" in text and "укладывается в бюджет" in text
    submit(app)
    assert len(app.subheader) == 3
    assert first != [item.value for item in app.subheader]
    assert any("заняты" in item.value for item in app.info)


def test_sparse_result(app):
    submit(app, category="Флорист", budget=300_000)
    assert len(app.subheader) == 1
    assert any("Показан 1 подходящий вариант" in item.value for item in app.info)
    assert any("Найден 1 подходящий подрядчик" in item.value for item in app.success)


def test_two_results_use_natural_russian(app):
    submit(app, category="Банкетный зал", budget=6_000_000, day=date(2026, 11, 14))
    assert len(app.subheader) == 2
    assert any("Показаны 2 подходящих варианта" in item.value for item in app.info)
    assert any("Найдены 2 подходящих подрядчика" in item.value for item in app.success)


@pytest.mark.parametrize("city,category,budget,message", [
    ("Астана", "Декоратор", 3_000_000, "нет подрядчиков категории"),
    ("Алматы", "Ведущий", 100_000, "никто не проходит"),
])
def test_distinct_empty_states(app, city, category, budget, message):
    submit(app, city=city, category=category, budget=budget)
    assert len(app.subheader) == 0
    assert any(message in item.value for item in app.warning)
    assert not any("подрядчик(ов)" in item.value for item in app.warning)


def test_synthetic_profile_is_visibly_marked(app):
    from matcher import FIRST_DATE, LAST_DATE, load_profiles
    from datetime import timedelta

    # Use the actual catalog and real matching; find a query which selects a synthetic card.
    from matcher import match
    from models import Request
    profiles = load_profiles()
    for profile in (p for p in profiles if p.synthetic):
        for offset in range((LAST_DATE - FIRST_DATE).days + 1):
            day = FIRST_DATE + timedelta(days=offset)
            request = Request(profile.city, day, profile.event_formats[0],
                              profile.categories[0], profile.price_from_kzt)
            result = match(profiles, request)
            if any(p.synthetic for p in result.cards):
                app.selectbox[1].set_value(request.event_format)
                submit(app, city=request.city, category=request.category,
                       budget=request.budget_kzt, day=day)
                assert any("Синтетический профиль" in x.value for x in app.caption)
                return
    pytest.fail("No demonstrable synthetic card in the source catalog")
