"""Acceptance checks for the dataset and deterministic filtering."""

from dataclasses import replace
from datetime import date

import pytest

from matcher import FIRST_DATE, LAST_DATE, load_profiles, match
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
