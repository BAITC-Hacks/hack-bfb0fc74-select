"""Deterministic contractor loading, filtering, and ranking.

Only structured CSV fields decide eligibility. Each category member receives at
most one exclusion reason, in this order: busy, budget, format, language,
duration. Ranking is a documented heuristic, never a quality rating.
"""

import csv
from datetime import date
from pathlib import Path

from models import MatchResult, Profile, Request


FIRST_DATE = date(2026, 9, 23)
LAST_DATE = date(2026, 12, 31)
FIELDS = (
    "id", "anon_name", "categories", "city", "city_imputed", "synthetic",
    "price_from_kzt", "price_imputed", "event_formats", "languages",
    "max_hours", "busy_dates", "description",
)
EXCLUSION_ORDER = ("busy", "budget", "format", "language", "duration")
FORMAT_TERMS = {
    "свадьба": ("свадьб", "свадеб"),
    "той": ("той", "тоя", "тойлар"),
    "корпоратив": ("корпорат",),
    "конференция": ("конференц",),
    "юбилей": ("юбиле",),
    "день рождения": ("день рождения", "дня рождения"),
}


def _list(value: str, field: str, row_number: int) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in value.split("|"))
    if not parts or any(not part for part in parts) or len(parts) != len(set(parts)):
        raise ValueError(f"CSV row {row_number}: invalid {field}")
    return parts


def _flag(value: str, field: str, row_number: int) -> bool:
    if value not in ("True", "False"):
        raise ValueError(f"CSV row {row_number}: invalid {field}")
    return value == "True"


def _positive_int(value: str, field: str, row_number: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CSV row {row_number}: invalid {field}") from exc
    if number <= 0:
        raise ValueError(f"CSV row {row_number}: invalid {field}")
    return number


def load_profiles(path: str | Path = "data/contractors.csv") -> tuple[Profile, ...]:
    """Parse the supplied dataset, rejecting malformed records and calendar data."""
    source = Path(path)
    if source == Path("data/contractors.csv") and not source.exists():
        source = Path(__file__).resolve().parent / source
    profiles: list[Profile] = []
    ids: set[str] = set()
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or tuple(reader.fieldnames) != FIELDS:
            raise ValueError("CSV headers differ from expected contractor schema")
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV row {row_number}: malformed columns")
            for field in ("id", "anon_name", "city", "description"):
                if not row[field].strip():
                    raise ValueError(f"CSV row {row_number}: missing {field}")
            profile_id = row["id"].strip()
            if profile_id in ids:
                raise ValueError(f"CSV row {row_number}: duplicate id {profile_id}")
            ids.add(profile_id)
            date_tokens = _list(row["busy_dates"], "busy_dates", row_number)
            try:
                dates = tuple(date.fromisoformat(token) for token in date_tokens)
            except ValueError as exc:
                raise ValueError(f"CSV row {row_number}: invalid busy_dates") from exc
            if any(day < FIRST_DATE or day > LAST_DATE for day in dates):
                raise ValueError(f"CSV row {row_number}: busy date outside calendar")
            if list(dates) != sorted(dates):
                raise ValueError(f"CSV row {row_number}: busy dates out of order")
            max_hours = (
                _positive_int(row["max_hours"], "max_hours", row_number)
                if row["max_hours"] else None
            )
            profiles.append(Profile(
                id=profile_id,
                anon_name=row["anon_name"].strip(),
                categories=_list(row["categories"], "categories", row_number),
                city=row["city"].strip(),
                city_imputed=_flag(row["city_imputed"], "city_imputed", row_number),
                synthetic=_flag(row["synthetic"], "synthetic", row_number),
                price_from_kzt=_positive_int(row["price_from_kzt"], "price_from_kzt", row_number),
                price_imputed=_flag(row["price_imputed"], "price_imputed", row_number),
                event_formats=_list(row["event_formats"], "event_formats", row_number),
                languages=_list(row["languages"], "languages", row_number),
                max_hours=max_hours,
                busy_dates=frozenset(dates),
                description=row["description"].strip(),
            ))
    if not profiles:
        raise ValueError("CSV has no contractor profiles")
    return tuple(profiles)


def _format_mentioned(profile: Profile, event_format: str) -> bool:
    text = profile.description.casefold()
    return any(term in text for term in FORMAT_TERMS.get(event_format, (event_format.casefold(),)))


def match(profiles: tuple[Profile, ...], request: Request) -> MatchResult:
    """Return at most three profiles, with mutually exclusive exclusion counts."""
    if not isinstance(request.date, date) or not FIRST_DATE <= request.date <= LAST_DATE:
        raise ValueError("Date must be within 2026-09-23..2026-12-31")
    if not isinstance(request.budget_kzt, int) or request.budget_kzt <= 0:
        raise ValueError("Budget must be a positive number of tenge")
    if request.duration_hours is not None and (
        not isinstance(request.duration_hours, int) or request.duration_hours <= 0
    ):
        raise ValueError("Duration must be a positive number of hours")
    if not all((request.city, request.category, request.event_format)):
        raise ValueError("City, category, and event format are required")

    in_category = [
        profile for profile in profiles
        if profile.city == request.city and request.category in profile.categories
    ]
    exclusions = dict.fromkeys(EXCLUSION_ORDER, 0)
    eligible: list[Profile] = []
    for profile in in_category:
        if request.date in profile.busy_dates:
            reason = "busy"
        elif profile.price_from_kzt > request.budget_kzt:
            reason = "budget"
        elif request.event_format not in profile.event_formats:
            reason = "format"
        elif request.language and request.language not in profile.languages:
            reason = "language"
        elif (request.duration_hours is not None and profile.max_hours is not None
              and profile.max_hours < request.duration_hours):
            reason = "duration"
        else:
            eligible.append(profile)
            continue
        exclusions[reason] += 1

    eligible.sort(key=lambda profile: (
        not _format_mentioned(profile, request.event_format),
        profile.price_from_kzt,
        profile.id,
    ))
    status = "matched" if eligible else ("no_category" if not in_category else "no_eligible")
    return MatchResult(
        status=status,
        cards=tuple(eligible[:3]),
        eligible_count=len(eligible),
        category_count=len(in_category),
        exclusion_counts=exclusions,
    )
