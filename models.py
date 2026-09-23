"""Shared, dependency-free contracts for matching and presentation."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Profile:
    id: str
    anon_name: str
    categories: tuple[str, ...]
    city: str
    city_imputed: bool
    synthetic: bool
    price_from_kzt: int
    price_imputed: bool
    event_formats: tuple[str, ...]
    languages: tuple[str, ...]
    max_hours: int | None
    busy_dates: frozenset[date]
    description: str


@dataclass(frozen=True)
class Request:
    city: str
    date: date
    event_format: str
    category: str
    budget_kzt: int
    duration_hours: int | None = None
    language: str | None = None


@dataclass(frozen=True)
class MatchResult:
    status: str  # matched | no_category | no_eligible
    cards: tuple[Profile, ...]
    eligible_count: int
    category_count: int
    exclusion_counts: dict[str, int]  # busy, budget, format, language, duration
