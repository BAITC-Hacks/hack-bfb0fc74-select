"""Local Streamlit interface for the HackAlem contractor matcher."""

from __future__ import annotations

from base64 import b64encode
from html import escape
from datetime import date
from pathlib import Path

import streamlit as st

from config import project_setting
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


def location_phrase(city: str) -> str:
    if city == "Зарубежье":
        return f"В направлении «{city}»"
    return f"В городе «{city}»"


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


def show_result(result, request: Request, profiles, use_ai: bool, prepared_explanations=None) -> None:
    from explanations import build_explanations

    if result.status == "no_category":
        other_place = "направление" if request.city == "Зарубежье" else "город"
        st.warning(
            f"{location_phrase(request.city)} нет подрядчиков категории «{request.category}» "
            f"в этом каталоге. Попробуйте другую категорию или {other_place}."
        )
        show_date_comparison(result, request, profiles)
        return

    if result.status == "no_eligible":
        st.warning(
            f"{location_phrase(request.city)} есть {contractor_count_phrase(result.category_count)} "
            f"категории «{request.category}», но на {request.date:%d.%m.%Y} "
            "никто не проходит выбранные условия."
        )
        show_exclusions(result)
        show_date_comparison(result, request, profiles)
        return

    if result.status != "matched":
        st.error("Неизвестное состояние подбора. Попробуйте обновить страницу.")
        return

    if prepared_explanations is not None:
        explanations, mode = prepared_explanations
    else:
        try:
            explanations, mode = build_explanations(result, request, use_ai=use_ai)
        except Exception:
            # The selection must remain usable when the optional explanation service fails.
            explanations, mode = build_explanations(result, request, use_ai=False)

    if result.eligible_count == 1:
        summary = f"Найден 1 подходящий подрядчик на {request.date:%d.%m.%Y}."
    elif result.eligible_count == 2:
        summary = f"Найдены 2 подходящих подрядчика на {request.date:%d.%m.%Y}."
    else:
        summary = (
            f"Подобрано {len(result.cards)} из {result.eligible_count} подходящих "
            f"подрядчиков на {request.date:%d.%m.%Y}."
        )
    st.success(summary)
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
            st.write(f"**{request.category}** · {profile.city}")
            st.write(f"**Цена от {money(profile.price_from_kzt)}** за мероприятие")
            st.caption(
                f"Дата {request.date:%d.%m.%Y} не отмечена занятой в каталоге. "
                "Это не подтверждение бронирования."
            )
            st.write(explanations.get(profile.id, "Подходит по выбранным условиям."))
            flags = profile_flags(profile)
            if flags:
                st.caption(" · ".join(flags))

    if result.eligible_count < 3:
        count_phrase = (
            "Показан 1 подходящий вариант"
            if result.eligible_count == 1
            else "Показаны 2 подходящих варианта"
        )
        if result.eligible_count == result.category_count:
            place = "в этом направлении" if request.city == "Зарубежье" else "в этом городе"
            st.info(
                f"{count_phrase}: {place} "
                "больше профилей данной категории нет."
            )
        else:
            st.info(
                f"{count_phrase}: остальные профили этой "
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
                details.append("не вошли в новую тройку: " + ", ".join(removed_names))
            if added_names:
                details.append("вошли в новую тройку: " + ", ".join(added_names))
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


def city_illustration_svg(city: str) -> str:
    """A small original vector scene that changes with the selected city."""
    if city == "Астана":
        landmarks = """
          <path d="M93 218 173 91 253 218Z" fill="#d8edf0" stroke="#5e9ba8" stroke-width="3"/>
          <path d="M125 218 173 91 221 218" fill="none" stroke="#83b8bf" stroke-width="2"/>
          <path d="M324 220V125h72v95M332 126h56M344 124V91a24 24 0 0 1 48 0v33" fill="#f4c878" stroke="#297083" stroke-width="4"/>
          <circle cx="368" cy="79" r="27" fill="#f5d27f" stroke="#297083" stroke-width="4"/>
          <path d="M520 218 568 124 616 218Z" fill="#b8dce6" stroke="#377f92" stroke-width="4"/>
          <circle cx="568" cy="152" r="24" fill="#e9f7f8" stroke="#377f92" stroke-width="5"/>
          <path d="M551 152h34M568 135v34M84 218h549" stroke="#377f92" stroke-width="4"/>
        """
    elif city == "Алматы":
        landmarks = """
          <path d="M58 218 192 82l76 78 77-102 157 160Z" fill="#cfe3e7" stroke="#688d9b" stroke-width="3"/>
          <path d="m155 121 37-39 30 31M309 98l36-40 36 49" fill="none" stroke="#fffdf7" stroke-width="7"/>
          <path d="M292 218v-82h57v82M365 218v-105h47v105M427 218v-67h60v67M504 218v-91h48v91" fill="#9bc2c8" stroke="#527e8b" stroke-width="3"/>
          <path d="M282 218h284" stroke="#527e8b" stroke-width="4"/>
        """
    else:
        landmarks = """
          <circle cx="360" cy="144" r="91" fill="#d9edf0" stroke="#4b8998" stroke-width="4"/>
          <path d="M269 144h182M360 53c-35 26-53 57-53 91s18 65 53 91M360 53c35 26 53 57 53 91s-18 65-53 91M286 103h148M286 185h148" fill="none" stroke="#6ba3ad" stroke-width="3"/>
          <path d="M123 220v-62h47v62M177 220v-98h59v98M486 220v-82h53v82M548 220v-54h43v54" fill="#b7d3d7" stroke="#638d96" stroke-width="3"/>
          <path d="M105 220h500" stroke="#638d96" stroke-width="4"/>
        """
    return f"""<svg class="city-art" viewBox="0 0 720 280" role="img" aria-label="Иллюстрация: {escape(city)}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="citySky" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#d8edf0"/><stop offset="1" stop-color="#f6f1e6"/></linearGradient>
        <linearGradient id="cityGround" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#98c8c3"/><stop offset="1" stop-color="#d9c995"/></linearGradient>
      </defs>
      <rect width="720" height="280" rx="22" fill="url(#citySky)"/>
      <circle cx="609" cy="53" r="24" fill="#f2c97b" opacity=".88"/>
      <path d="M0 224c108-18 168-10 252 0s167 8 246 0 146-13 222 0v56H0Z" fill="url(#cityGround)" opacity=".68"/>
      {landmarks}
      <path d="M0 238c115-13 214 8 328 0s246-11 392 1" fill="none" stroke="#fffdf7" stroke-width="3" opacity=".85"/>
    </svg>"""


def render_city_illustration(city: str) -> None:
    """Show generated city art when available, with an inline fallback for abroad."""
    image_names = {"Астана": "astana.png", "Алматы": "almaty.png"}
    image_name = image_names.get(city)
    image_path = Path(__file__).parent / "assets" / "cities" / image_name if image_name else None
    if image_path and image_path.is_file():
        source = b64encode(image_path.read_bytes()).decode("ascii")
        st.html(
            f'<img class="city-art" src="data:image/png;base64,{source}" '
            f'alt="Иллюстрация города {escape(city)}">'
        )
    else:
        source = b64encode(city_illustration_svg(city).encode("utf-8")).decode("ascii")
        st.html(
            f'<img class="city-art" src="data:image/svg+xml;base64,{source}" '
            f'alt="Иллюстрация: {escape(city)}">'
        )


def summary_icon(kind: str) -> str:
    """Consistent outline icons for the four request facts."""
    paths = {
        "format": '<path d="M12 20.5s-8-4.6-8-10a4.5 4.5 0 0 1 8-2.8 4.5 4.5 0 0 1 8 2.8c0 5.4-8 10-8 10Z"/>',
        "category": '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4m-4 0h8"/>',
        "date": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18m-13 4h3m3 0h3m-9 3h3"/>',
        "budget": '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    }
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="#377c7b" stroke-width="1.5" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths[kind]}</svg>'
    )
    source = b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="summary-icon" src="data:image/svg+xml;base64,{source}" alt="">'


def render_styles() -> None:
    st.markdown(
        """<style>
        :root { --ink:#142f3d; --muted:#687b82; --teal:#145968; --line:#e3e9e5; --paper:#f5f6f2; }
        .stApp { background: radial-gradient(ellipse at 82% 6%, #e5f1ed 0, transparent 38%), var(--paper); color:var(--ink); }
        header[data-testid="stHeader"] { background:transparent; }
        .block-container { max-width:1380px; padding-top:1.2rem; padding-bottom:3rem; }
        h1,h2,h3 { color:var(--ink); letter-spacing:-.025em; }
        h1 { font-size:clamp(2.2rem,4vw,3.5rem)!important; line-height:1.08!important; }
        [data-testid="stVerticalBlockBorderWrapper"] { background:rgba(255,255,255,.88); border:1px solid var(--line); border-radius:20px; box-shadow:0 12px 34px rgba(26,54,59,.055); }
        [data-testid="stSelectbox"] label, [data-testid="stDateInput"] label, [data-testid="stNumberInput"] label { color:#35525d; font-weight:650; }
        [data-testid="stSelectbox"] div[data-baseweb="select"] > div { background:#fff!important; color:#142f3d!important; border:1px solid #dce6e2!important; border-radius:12px; }
        [data-testid="stDateInput"] input, [data-testid="stNumberInput"] input { background:#fff!important; color:#142f3d!important; border:1px solid #dce6e2!important; border-radius:12px; }
        [data-testid="stNumberInput"] button { background:#fff!important; color:#145968!important; border-color:#dce6e2!important; }
        div.stButton > button[kind="primary"] { background:#145968; border:1px solid #145968; border-radius:13px; min-height:3.1rem; font-weight:700; }
        div.stButton > button[kind="primary"]:hover { background:#0e4856; border-color:#0e4856; }
        [data-testid="stExpander"] { border:1px solid var(--line); border-radius:12px; background:#fbfcfa; }
        .select-header { display:flex; align-items:center; justify-content:space-between; gap:24px; padding:5px 0 28px; border-bottom:1px solid rgba(20,47,61,.1); margin-bottom:38px; }
        .select-brand { color:#142f3d; font-size:1.55rem; font-weight:800; letter-spacing:-.05em; }
        .select-brand span { color:#4f9a91; }
        .select-nav { display:flex; gap:30px; }
        .select-nav a { color:#526b72; text-decoration:none; font-size:.94rem; font-weight:600; }
        .select-nav a:hover { color:#145968; }
        .hero-kicker,.eyebrow { color:#4f8e87; text-transform:uppercase; letter-spacing:.13em; font-size:.74rem; font-weight:800; }
        .hero-copy { max-width:720px; color:#60737a; font-size:1.08rem; line-height:1.65; margin:12px 0 27px; }
        .card-heading { font-size:1.35rem; font-weight:750; color:#142f3d; margin:0 0 2px; }
        .card-subtitle { color:#74858a; font-size:.9rem; margin-bottom:18px; }
        .form-label { color:#35525d; font-size:.91rem; font-weight:650; padding-top:.62rem; }
        .city-art { width:100%; aspect-ratio:1.82/1; object-fit:cover; display:block; border-radius:16px; }
        .city-preview-title { font-size:1.45rem; font-weight:780; color:#142f3d; margin:14px 0 3px; }
        .city-preview-copy { color:#667a80; font-size:.92rem; line-height:1.5; }
        .preview-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:20px; }
        .preview-item { padding:12px 13px; background:#f5f8f5; border:1px solid #e7eeea; border-radius:13px; min-width:0; }
        .summary-icon { width:21px; height:21px; color:#377c7b; flex:none; }
        .preview-item-head { display:flex; align-items:center; gap:8px; }
        .preview-label { color:#809095; font-size:.73rem; text-transform:uppercase; letter-spacing:.07em; }
        .preview-value { color:#213f49; font-weight:700; margin-top:4px; overflow-wrap:anywhere; }
        .trust-note { color:#718287; font-size:.82rem; line-height:1.5; padding-top:14px; }
        .feature-row { display:grid; grid-template-columns:repeat(3,1fr); gap:15px; margin-top:14px; }
        .feature-card { background:rgba(255,255,255,.72); border:1px solid var(--line); border-radius:16px; padding:19px; }
        .feature-number { color:#4f8e87; font-size:.76rem; font-weight:800; letter-spacing:.1em; }
        .feature-title { color:#183943; font-weight:750; margin:7px 0 4px; }
        .feature-copy { color:#718287; font-size:.88rem; line-height:1.5; }
        .catalog-strip { display:flex; flex-wrap:wrap; gap:10px; margin-top:13px; }
        .catalog-pill { border:1px solid var(--line); border-radius:999px; background:#fff; padding:8px 12px; color:#47636a; font-size:.85rem; }
        .section-heading { margin:48px 0 0; }
        @media (max-width:760px) { .block-container { padding-left:1rem; padding-right:1rem; } .select-header { align-items:flex-start; flex-direction:column; gap:12px; margin-bottom:25px; } .select-nav { gap:17px; flex-wrap:wrap; } .feature-row { grid-template-columns:1fr; } .form-label { padding-top:0; } }
        </style>""",
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Select · Подбор подрядчиков", page_icon="✦", layout="wide")
    render_styles()
    st.markdown(
        """<header class="select-header">
          <div class="select-brand">Select<span>✦</span></div>
          <nav class="select-nav" aria-label="Основная навигация">
            <a href="#builder">Подбор</a><a href="#how-it-works">Как выбираем</a><a href="#data">О данных</a>
          </nav>
          <div class="eyebrow">Подрядчики для событий</div>
        </header>
        <div id="builder" class="hero-kicker">Планируйте событие с уверенностью</div>
        <h1>Подберите команду<br>для вашего события</h1>
        <p class="hero-copy">Укажите дату, формат и бюджет — мы проверим каталог, исключим занятых и объясним, почему рекомендуем каждый вариант.</p>""",
        unsafe_allow_html=True,
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

    profile_counts = {city_name: sum(profile.city == city_name for profile in profiles) for city_name in cities}
    st.markdown('<div class="eyebrow">Настройте параметры</div>', unsafe_allow_html=True)
    left, right = st.columns([1.05, 0.95], gap="large")

    with left:
        with st.container(border=True):
            st.html('<h2 class="card-heading">Параметры события</h2><div class="card-subtitle">Чем точнее запрос, тем полезнее подбор.</div>')
            label_col, input_col = st.columns([0.8, 1.45], gap="small")
            with label_col:
                st.html('<div class="form-label">Город / направление</div>')
            with input_col:
                city = st.selectbox("Город / направление", cities, index=cities.index("Алматы") if "Алматы" in cities else 0, key="city_choice", label_visibility="collapsed")
            label_col, input_col = st.columns([0.8, 1.45], gap="small")
            with label_col:
                st.html('<div class="form-label">Дата мероприятия</div>')
            with input_col:
                selected_date = st.date_input(
                    "Дата мероприятия", value=date(2026, 10, 11), min_value=FIRST_DATE,
                    max_value=LAST_DATE, format="DD.MM.YYYY", key="event_date", label_visibility="collapsed",
                )
            label_col, input_col = st.columns([0.8, 1.45], gap="small")
            with label_col:
                st.html('<div class="form-label">Тип мероприятия</div>')
            with input_col:
                event_format = st.selectbox("Тип мероприятия", formats, index=formats.index("свадьба") if "свадьба" in formats else 0, key="event_format", label_visibility="collapsed")
            label_col, input_col = st.columns([0.8, 1.45], gap="small")
            with label_col:
                st.html('<div class="form-label">Категория подрядчика</div>')
            with input_col:
                category = st.selectbox("Категория подрядчика", categories, index=categories.index("Ведущий") if "Ведущий" in categories else 0, key="category_choice", label_visibility="collapsed")
            label_col, input_col = st.columns([0.8, 1.45], gap="small")
            with label_col:
                st.html('<div class="form-label">Бюджет, ₸</div>')
            with input_col:
                budget = st.number_input("Бюджет, ₸", min_value=1, value=2_000_000, step=50_000, key="event_budget", label_visibility="collapsed")
            with st.expander("Дополнительные условия", expanded=False):
                duration = st.number_input("Длительность, часы (0 — не учитывать)", min_value=0, max_value=48, value=0, step=1, key="event_duration")
                language = st.selectbox("Язык работы (необязательно)", ["Не важно", *languages], key="event_language")
            use_ai = False
            if project_setting("OPENAI_API_KEY"):
                use_ai = st.checkbox("Использовать AI для объяснений", value=False, key="ai_opt_in")
                st.caption(
                    "При включении в OpenAI передаются ID и описания до трёх выбранных "
                    "профилей, категория, формат и указанный язык. Имена, цены, даты "
                    "и календарь занятости не передаются."
                )
            submitted = st.button("Подобрать подрядчиков  →", type="primary", use_container_width=True, key="submit_match")
            st.caption("Цена указана «от». Отметка о свободной дате в каталоге не является подтверждением бронирования.")

    with right:
        with st.container(border=True):
            st.markdown('<div class="eyebrow">Выбранное направление</div>', unsafe_allow_html=True)
            render_city_illustration(city)
            safe_city = escape(city)
            st.markdown(
                f'<div class="city-preview-title">{safe_city}</div>'
                f'<div class="city-preview-copy">Профилей в исходном наборе: {profile_counts.get(city, 0)}. Превью меняется при выборе направления.</div>',
                unsafe_allow_html=True,
            )
            safe_format = escape(event_format)
            safe_category = escape(category)
            safe_date = selected_date.strftime("%d.%m.%Y")
            safe_budget = escape(money(int(budget)))
            language_value = "Любой язык" if language == "Не важно" else language
            duration_value = "не задана" if not duration else f"требуется {int(duration)} ч"
            st.html(
                f'''<div class="preview-grid">
                  <div class="preview-item"><div class="preview-item-head">{summary_icon("format")}<span class="preview-label">Формат</span></div><div class="preview-value">{safe_format}</div></div>
                  <div class="preview-item"><div class="preview-item-head">{summary_icon("category")}<span class="preview-label">Категория</span></div><div class="preview-value">{safe_category}</div></div>
                  <div class="preview-item"><div class="preview-item-head">{summary_icon("date")}<span class="preview-label">Дата</span></div><div class="preview-value">{safe_date}</div></div>
                  <div class="preview-item"><div class="preview-item-head">{summary_icon("budget")}<span class="preview-label">Бюджет</span></div><div class="preview-value">{safe_budget}</div></div>
                </div>
                <div class="trust-note">Язык: {escape(language_value)} · Длительность: {escape(duration_value)}<br>Доступность проверяется по календарю в датасете. На мобильном экране превью остаётся видимым после выбора.</div>''',
            )

    request = Request(
        city=city, date=selected_date, event_format=event_format, category=category,
        budget_kzt=int(budget), duration_hours=int(duration) if duration else None,
        language=None if language == "Не важно" else language,
    )
    signature = (
        request.city, request.date, request.event_format, request.category,
        request.budget_kzt, request.duration_hours, request.language, bool(use_ai),
    )
    if submitted:
        from matcher import match
        try:
            with st.spinner("Проверяем условия и занятость на выбранную дату…"):
                result = match(profiles, request)
                explanation_bundle = None
                if result.status == "matched":
                    from explanations import build_explanations
                    try:
                        explanation_bundle = build_explanations(result, request, use_ai=use_ai)
                    except Exception:
                        explanation_bundle = build_explanations(result, request, use_ai=False)
                st.session_state["saved_match"] = {
                    "signature": signature, "request": request, "result": result,
                    "use_ai": use_ai, "explanations": explanation_bundle,
                }
        except ValueError as exc:
            st.session_state.pop("saved_match", None)
            st.error(f"Проверьте параметры запроса: {exc}")
        except Exception:
            st.session_state.pop("saved_match", None)
            st.error("Не удалось выполнить подбор. Попробуйте ещё раз.")

    saved_match = st.session_state.get("saved_match")
    if saved_match and saved_match["signature"] == signature:
        st.markdown('<div id="results"></div>', unsafe_allow_html=True)
        st.html('<h2 class="eyebrow">Результат подбора</h2>')
        show_result(
            saved_match["result"], saved_match["request"], profiles,
            saved_match["use_ai"], saved_match["explanations"],
        )
    elif saved_match:
        st.info("Условия изменились. Нажмите «Подобрать подрядчиков», чтобы обновить результат.")
    else:
        st.caption("Начальная цена может отличаться от итоговой сметы. Календарь известен только с 23.09 по 31.12.2026.")

    st.html(
        f'''<h2 id="how-it-works" class="eyebrow section-heading">Как выбираем</h2>
        <div class="feature-row">
          <div class="feature-card"><div class="feature-number">01 · УСЛОВИЯ</div><div class="feature-title">Фильтруем по запросу</div><div class="feature-copy">Город, дата, формат, категория, бюджет и дополнительные условия.</div></div>
          <div class="feature-card"><div class="feature-number">02 · ПРОВЕРКА</div><div class="feature-title">Учитываем занятость</div><div class="feature-copy">Исключаем профили, занятые в выбранный день или не прошедшие ограничения.</div></div>
          <div class="feature-card"><div class="feature-number">03 · ОБЪЯСНЕНИЕ</div><div class="feature-title">Показываем причины</div><div class="feature-copy">До трёх вариантов с объяснениями на основе данных каталога.</div></div>
        </div>
        <h2 id="data" class="eyebrow section-heading" style="margin-top:40px">О данных</h2>
        <div class="catalog-strip">
          <span class="catalog-pill">{len(profiles)} профилей в каталоге</span>
          <span class="catalog-pill">{len(cities)} направления</span>
          <span class="catalog-pill">{len(categories)} категорий</span>
          <span class="catalog-pill">Даты: 23.09–31.12.2026</span>
        </div>
        <div class="trust-note">Каталог анонимизирован. Цена показана как начальная; свободная дата в данных не подтверждает бронирование. Часть профилей синтетическая или содержит заполненные поля — такие записи отмечаются в результатах.</div>''',
    )


if __name__ == "__main__":
    main()
