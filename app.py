"""Local Streamlit interface for the HackAlem contractor matcher."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import streamlit as st

from models import Request


DATA_PATH = Path(__file__).parent / "data" / "contractors.csv"
FIRST_DATE = date(2026, 9, 23)
LAST_DATE = date(2026, 12, 31)
EXCLUSION_LABELS = {
    "busy": "заняты на дату",
    "budget": "цена от выше бюджета",
    "format": "не работают с этим форматом",
    "language": "не работают на выбранном языке",
    "duration": "не подходят по длительности",
}


def contractor_count_phrase(count: int) -> str:
    word = "подрядчик" if count % 10 == 1 and count % 100 != 11 else (
        "подрядчика" if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14)
        else "подрядчиков"
    )
    return f"{count} {word}"


@st.cache_data(show_spinner=False)
def get_profiles():
    from matcher import load_profiles

    return load_profiles(DATA_PATH)


def money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₸"


def profile_flags(profile) -> list[str]:
    flags = []
    if profile.synthetic:
        flags.append("Синтетический профиль")
    if profile.city_imputed:
        flags.append("Город заполнен в датасете")
    if profile.price_imputed:
        flags.append("Цена заполнена в датасете")
    return flags


def show_result(result, request: Request, profiles, use_ai: bool) -> None:
    from explanations import build_explanations

    if result.status == "no_category":
        st.warning(
            f"В городе «{request.city}» нет подрядчиков категории «{request.category}» "
            "в этом каталоге. Попробуйте другую категорию или город."
        )
        show_date_comparison(result, request, profiles)
        return

    if result.status == "no_eligible":
        st.warning(
            f"В городе «{request.city}» есть {contractor_count_phrase(result.category_count)} "
            f"категории «{request.category}», но на {request.date:%d.%m.%Y} "
            "никто не проходит выбранные условия."
        )
        show_exclusions(result)
        show_date_comparison(result, request, profiles)
        return

    if result.status != "matched":
        st.error("Неизвестное состояние подбора. Попробуйте обновить страницу.")
        return

    try:
        explanations, mode = build_explanations(result, request, use_ai=use_ai)
    except Exception:
        # The selection must remain usable when the optional explanation service fails.
        explanations, mode = build_explanations(result, request, use_ai=False)

    st.success(
        f"Подобрано {len(result.cards)} из {result.eligible_count} подходящих "
        f"подрядчиков на {request.date:%d.%m.%Y}."
    )
    mode_label = {
        "ai": "AI",
        "openai": "AI",
        "local": "локальные",
        "fallback": "локальные (AI временно недоступен)",
    }.get(str(mode).lower(), "локальные")
    st.caption(
        f"Объяснения: {mode_label}. Состав и порядок определены проверяемыми правилами отбора."
    )
    for index, profile in enumerate(result.cards, start=1):
        with st.container(border=True):
            st.subheader(f"{index}. {profile.anon_name}")
            st.write(f"**Категории:** {', '.join(profile.categories)}")
            st.write(f"**Город:** {profile.city} · **Цена:** от {money(profile.price_from_kzt)}")
            st.write(explanations.get(profile.id, "Подходит по выбранным условиям."))
            flags = profile_flags(profile)
            if flags:
                st.caption(" · ".join(flags))

    if result.eligible_count < 3:
        if result.eligible_count == result.category_count:
            st.info(
                f"Показаны все {result.eligible_count} подходящих: в этом городе "
                "больше профилей данной категории нет."
            )
        else:
            st.info(
                f"Показаны все {result.eligible_count} подходящих: остальные профили этой "
                "категории не прошли ограничения запроса."
            )
        show_exclusions(result)

    show_date_comparison(result, request, profiles)


def show_exclusions(result) -> None:
    reasons = [
        f"{EXCLUSION_LABELS[key]} — {count}"
        for key, count in result.exclusion_counts.items()
        if count and key in EXCLUSION_LABELS
    ]
    if reasons:
        st.caption("Причины отсева в выбранной категории: " + "; ".join(reasons) + ".")


def show_date_comparison(result, request: Request, profiles) -> None:
    """Explain a changed selection when only the date changed between submissions."""
    signature = (
        request.city,
        request.category,
        request.event_format,
        request.budget_kzt,
        request.duration_hours,
        request.language,
    )
    previous = st.session_state.get("previous_match")
    current_ids = tuple(profile.id for profile in result.cards)
    if previous and previous["signature"] == signature and previous["date"] != request.date:
        removed = set(previous["ids"]) - set(current_ids)
        added = set(current_ids) - set(previous["ids"])
        if removed or added:
            profile_by_id = {profile.id: profile for profile in profiles}
            removed_names = [profile_by_id[id_].anon_name for id_ in previous["ids"] if id_ in removed]
            added_names = [profile_by_id[id_].anon_name for id_ in current_ids if id_ in added]
            now_busy = [
                profile_by_id[id_].anon_name
                for id_ in previous["ids"]
                if id_ in removed and request.date in profile_by_id[id_].busy_dates
            ]
            previously_busy = [
                profile_by_id[id_].anon_name
                for id_ in current_ids
                if id_ in added and previous["date"] in profile_by_id[id_].busy_dates
            ]
            details = []
            if removed_names:
                details.append("выбыли: " + ", ".join(removed_names))
            if added_names:
                details.append("появились: " + ", ".join(added_names))
            if now_busy:
                details.append("заняты на новую дату: " + ", ".join(now_busy))
            if previously_busy:
                details.append("были заняты на прежнюю дату: " + ", ".join(previously_busy))
            st.info(
                f"При смене даты с {previous['date']:%d.%m.%Y} на {request.date:%d.%m.%Y} "
                + "; ".join(details)
                + ". Система заново проверила занятость каждого подрядчика на выбранный день."
            )
        else:
            st.caption("При смене даты выбранная тройка не изменилась.")
    st.session_state["previous_match"] = {
        "signature": signature,
        "date": request.date,
        "ids": current_ids,
    }


def main() -> None:
    st.set_page_config(page_title="Select · Подбор подрядчиков", page_icon="✦", layout="centered")
    st.title("✦ Select")
    st.markdown("### Подрядчики для вашего события")
    st.write(
        "Задайте условия — покажем до трёх подходящих вариантов, проверим занятость "
        "и объясним выбор."
    )

    try:
        profiles = get_profiles()
    except Exception as exc:
        st.error(f"Не удалось открыть каталог: {exc}")
        st.stop()

    cities = sorted({profile.city for profile in profiles})
    categories = sorted({category for profile in profiles for category in profile.categories})
    formats = sorted({fmt for profile in profiles for fmt in profile.event_formats})
    languages = sorted({language for profile in profiles for language in profile.languages})
    if not all((cities, categories, formats)):
        st.error("Каталог пуст или содержит неполные данные.")
        st.stop()

    st.caption("Каталог: 66 анонимизированных профилей · даты доступности: 23.09–31.12.2026")
    with st.form("match_form"):
        city = st.selectbox("Город", cities, index=cities.index("Алматы") if "Алматы" in cities else 0)
        selected_date = st.date_input(
            "Дата мероприятия",
            value=date(2026, 10, 11),
            min_value=FIRST_DATE,
            max_value=LAST_DATE,
            format="DD.MM.YYYY",
        )
        event_format = st.selectbox("Тип мероприятия", formats, index=formats.index("свадьба") if "свадьба" in formats else 0)
        category = st.selectbox("Категория подрядчика", categories, index=categories.index("Ведущий") if "Ведущий" in categories else 0)
        budget = st.number_input("Бюджет, ₸", min_value=1, value=2_000_000, step=50_000)
        with st.expander("Дополнительные условия"):
            duration = st.number_input("Длительность, часы (0 — не учитывать)", min_value=0, max_value=48, value=0, step=1)
            language = st.selectbox("Язык работы (необязательно)", ["Не важно", *languages])
        use_ai = False
        if os.getenv("OPENAI_API_KEY"):
            use_ai = st.checkbox("Улучшить объяснения с AI", value=True)
        submitted = st.form_submit_button("Подобрать подрядчиков", type="primary", use_container_width=True)

    if not submitted:
        st.caption("Начальная цена может отличаться от итоговой сметы. Календарь известен только в указанном диапазоне.")
        return

    request = Request(
        city=city,
        date=selected_date,
        event_format=event_format,
        category=category,
        budget_kzt=int(budget),
        duration_hours=int(duration) if duration else None,
        language=None if language == "Не важно" else language,
    )
    from matcher import match

    try:
        result = match(profiles, request)
    except ValueError as exc:
        st.error(str(exc))
        return
    show_result(result, request, profiles, use_ai)


if __name__ == "__main__":
    main()
