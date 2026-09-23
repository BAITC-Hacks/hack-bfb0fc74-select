"""Acceptance checks for the dataset and deterministic filtering."""

from dataclasses import replace
from datetime import date

import pytest

from matcher import FIRST_DATE, LAST_DATE, build_match_reasons, load_profiles, match
from models import Request


@pytest.fixture(scope="module")
def profiles():
    return load_profiles()


def query(day, *, city="Алматы", category="Ведущий", event_format="свадьба",
          budget=2_000_000, duration=None, language=None):
    return Request(city=city, date=date.fromisoformat(day), event_format=event_format,
                   category=category, budget_kzt=budget, duration_hours=duration,
                   language=language)


def test_dataset_schema_and_calendar(profiles):
    assert len(profiles) == 66
    assert len({p.id for p in profiles}) == 66
    assert all(FIRST_DATE <= day <= LAST_DATE for p in profiles for day in p.busy_dates)
    assert sum(p.synthetic for p in profiles) == 13
    assert sum(p.max_hours is None for p in profiles) == 9


def test_dense_date_shift_is_real_and_deterministic(profiles):
    october_10 = match(profiles, query("2026-10-10"))
    october_11 = match(profiles, query("2026-10-11"))
    assert october_10.status == october_11.status == "matched"
    assert october_10.eligible_count == 3
    assert october_11.eligible_count == 5
    assert tuple(p.id for p in october_10.cards) != tuple(p.id for p in october_11.cards)
    assert october_10 == match(tuple(reversed(profiles)), query("2026-10-10"))
    assert october_11 == match(profiles, query("2026-10-11"))


def test_sparse_and_empty_states(profiles):
    sparse = match(profiles, query("2026-10-10", category="Флорист", budget=300_000))
    assert sparse.status == "matched"
    assert sparse.eligible_count == len(sparse.cards) == 1
    absent = match(profiles, query("2026-10-10", city="Астана", category="Декоратор",
                                   budget=3_000_000))
    assert absent.status == "no_category"
    assert absent.category_count == 0
    assert all(value == 0 for value in absent.exclusion_counts.values())
    filtered = match(profiles, query("2026-10-10", budget=100_000))
    assert filtered.status == "no_eligible"
    assert filtered.category_count > 0
    assert sum(filtered.exclusion_counts.values()) == filtered.category_count


def test_venue_busy_dates_are_enforced(profiles):
    venue = next(p for p in profiles if "Банкетный зал" in p.categories)
    day = min(venue.busy_dates)
    result = match(profiles, Request(venue.city, day, venue.event_formats[0],
                                     "Банкетный зал", 10_000_000))
    assert venue.id not in {p.id for p in result.cards}
    assert result.exclusion_counts["busy"] >= 1


def test_optional_filters_and_exclusive_reasons(profiles):
    source = next(p for p in profiles if p.max_hours is not None
                  and (FIRST_DATE not in p.busy_dates or LAST_DATE not in p.busy_dates))
    free_day = next(day for day in (FIRST_DATE, LAST_DATE)
                    if day not in source.busy_dates)
    base = Request(source.city, free_day, source.event_formats[0],
                   source.categories[0], source.price_from_kzt)
    # A single profile isolates the first failing rule for every field.
    assert match((source,), base).eligible_count == 1
    assert match((source,), replace(base, language="несуществующий")).exclusion_counts["language"] == 1
    assert match((source,), replace(base, duration_hours=source.max_hours + 1)).exclusion_counts["duration"] == 1
    assert match((source,), replace(base, budget_kzt=source.price_from_kzt - 1,
                                    language="несуществующий")).exclusion_counts["budget"] == 1
    no_hours = next(p for p in profiles if p.max_hours is None
                    and (FIRST_DATE not in p.busy_dates or LAST_DATE not in p.busy_dates))
    free_day = next(day for day in (FIRST_DATE, LAST_DATE)
                    if day not in no_hours.busy_dates)
    request = Request(no_hours.city, free_day, no_hours.event_formats[0],
                      no_hours.categories[0], no_hours.price_from_kzt, 100)
    assert match((no_hours,), request).eligible_count == 1


@pytest.mark.parametrize("day", ["2026-09-22", "2027-01-01"])
def test_unknown_calendar_dates_are_rejected(profiles, day):
    with pytest.raises(ValueError, match="Date"):
        match(profiles, query(day))


def test_invalid_csv_rejected(tmp_path):
    source = tmp_path / "bad.csv"
    source.write_text("id,wrong\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="headers"):
        load_profiles(source)


def test_structured_reasons_follow_cards_and_source_fields(profiles):
    request = query("2026-10-11", language="русский", duration=4)
    result = match(profiles, request)
    reasons = build_match_reasons(result, request)
    assert tuple(reasons) == tuple(card.id for card in result.cards)
    assert 0 < len(reasons) <= 3
    for card in result.cards:
        by_field = {check["field"]: check for check in reasons[card.id]}
        assert by_field["city"] == {"field": "city", "operator": "eq",
                                    "actual": card.city, "requested": request.city}
        assert by_field["categories"]["actual"] == card.categories
        assert by_field["busy_dates"] == {
            "field": "busy_dates", "operator": "not_contains", "actual": True,
            "requested": request.date.isoformat(),
        }
        assert by_field["price_from_kzt"]["actual"] <= request.budget_kzt
        assert request.event_format in by_field["event_formats"]["actual"]
        assert request.language in by_field["languages"]["actual"]
        assert by_field["max_hours"]["actual"] >= request.duration_hours


def test_reasons_handle_sparse_empty_and_synthetic(profiles):
    sparse_request = query("2026-10-10", category="Флорист", budget=300_000,
                           duration=100)
    sparse = match(profiles, sparse_request)
    assert len(sparse.cards) == 1
    assert "max_hours" not in {
        check["field"] for check in build_match_reasons(sparse, sparse_request)[sparse.cards[0].id]
    }
    empty_request = query("2026-10-10", budget=100_000)
    assert build_match_reasons(match(profiles, empty_request), empty_request) == {}

    synthetic = next(card for card in profiles if card.synthetic)
    free_day = next(day for day in (FIRST_DATE, LAST_DATE) if day not in synthetic.busy_dates)
    request = Request(synthetic.city, free_day, synthetic.event_formats[0],
                      synthetic.categories[0], synthetic.price_from_kzt)
    result = match((synthetic,), request)
    assert result.cards[0].synthetic is True
    assert build_match_reasons(result, request)[synthetic.id]


def test_reasons_reject_mismatched_request(profiles):
    request = query("2026-10-11")
    result = match(profiles, request)
    with pytest.raises(ValueError, match="does not satisfy"):
        build_match_reasons(result, replace(request, city="Астана"))


def test_empty_language_matches_unspecified_language_in_reasons(profiles):
    request = query("2026-10-11", language="")
    unspecified = replace(request, language=None)
    result = match(profiles, request)
    assert result == match(profiles, unspecified)
    assert build_match_reasons(result, request) == build_match_reasons(result, unspecified)
    assert all(check["field"] != "languages"
               for checks in build_match_reasons(result, request).values()
               for check in checks)


@pytest.mark.parametrize("city,category,event_format", [
    (" ", "Ведущий", "свадьба"),
    ("Алматы", " ", "свадьба"),
    ("Алматы", "Ведущий", " "),
])
def test_whitespace_required_fields_rejected(profiles, city, category, event_format):
    with pytest.raises(ValueError, match="required"):
        match(profiles, query("2026-10-11", city=city, category=category,
                              event_format=event_format))
