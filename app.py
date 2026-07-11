"""
╔════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║                        YEV STEAM DEPOSIT CALCULATOR                        ║
║                                                                            ║
║ Copyright (c) 2026 Bohdan Yevtushenko (Mr4Cemper)                          ║
║ License: AGPL 3.0 — see the LICENSE file for the full text.                ║
║                                                                            ║
║ ────────────────────────────────────────────────────────────────────────── ║
║                                                                            ║
║ DISCLAIMER: This application is an independent educational and analytical  ║
║ tool. It is NOT affiliated with, endorsed, sponsored, or specifically      ║
║ approved by Valve Corporation. Counter-Strike, CS2, Steam, and their       ║
║ respective logos are trademarks and/or registered trademarks of Valve      ║
║ Corporation.                                                               ║
║                                                                            ║
╚════════════════════════════════════════════════════════════════════════════╝

Yev Steam Deposit Calculator
============================

Версия: единственный источник истины — константа APP_VERSION ниже
(в докстринге номер намеренно не дублируется, чтобы он не устаревал).

Streamlit-приложение для оценки экономики торговли скинами CS2. Оно сравнивает
стоимость покупки/продажи предмета на стороннем сайте за реальные деньги с
операциями по тому же предмету на Торговой площадке Steam.

Режимы работы:
    Режим 1 — прибыль от покупки скина на стороннем сайте и его последующей
        продажи на Торговой площадке Steam.
    Режим 2 — где купить выгоднее: напрямую на сайте за реальные деньги или
        на Steam за заранее пополненный баланс.
    Режим 3 — вывод средств (cashout): покупка на Steam за баланс, продажа на
        сайте и вывод выручки на карту/крипту.
    Режим 4 — где продать выгоднее: на стороннем сайте (с пополнением Steam в
        плюс) или напрямую на Торговой площадке Steam.
    Режим 5 — оценка коллекции: ранг каждой редкости по соотношению цен соседних
        редкостей. Простой режим — одна цена на редкость (+ бонус «красивое/
        ликвидное»). Продвинутый режим — резка флоата, наценка за чистоту внутри
        скина и экономика контрактов 10→1 на реальных записях (два режима подбора
        филлеров: по самым дешёвым либо по лучшему соотношению цена/качество),
        опциональный учёт цен Steam (ТП) и готовые шаблоны коллекций.

Структура модуля:
    * Расчётные функции (calculate_*, get_valid_steam_price, движок контрактов)
      не зависят от Streamlit и не имеют побочных эффектов; их можно тестировать
      и переиспользовать отдельно от интерфейса.
    * Локализация (en / ru / ua) реализована через словарь переводов и функцию
      доступа _(); английский текст служит ключом перевода. Код языка украинского
      именно 'ua' (а не ISO 'uk') — так его не путают с United Kingdom.
    * Функции calculate_mode_1..calculate_mode_5 строят интерфейс соответствующих
      режимов, main() настраивает страницу и является точкой входа.
    * Новостная лента (NEWS + render_news) — статичные сообщения с датами прямо в
      коде: их видит каждый, кто запустил эту версию. Запросов в сеть нет.

Запуск:
    streamlit run app.py
(В репозитории рабочий файл называется app.py — так его запускает Streamlit Cloud
и так принято во всех проектах; локальные рабочие копии нумеруются как
SteamCalcu<версия>.py и переименовываются в app.py при коммите.)
"""

import html
from datetime import date

import streamlit as st

# Версия приложения — ЕДИНСТВЕННЫЙ источник истины (показывается в интерфейсе).
APP_VERSION = "2.16"

# ===========================================================================
# КОНФИГУРАЦИЯ И КОНСТАНТЫ
# ===========================================================================

# Предустановленные профили комиссий популярных торговых площадок.
# Ключи словаря используются как стабильные идентификаторы и не переводятся;
# исключение — "Manual input", который локализуется через format_func.
# Поля профиля:
#   percent_fee   — процент комиссии пополнения;
#   fixed_fee_usd — фиксированная комиссия; задаётся всегда в долларах США и
#                   при необходимости конвертируется в валюту расчёта;
#   enabled       — учитывать ли комиссию по умолчанию при выборе профиля.
DEPOSIT_FEE_TEMPLATES = {
    "Manual input":      {"percent_fee": 0.00, "fixed_fee_usd": 0.00, "enabled": False},
    "CSFloat (Crypto)":  {"percent_fee": 1.00, "fixed_fee_usd": 0.00, "enabled": True},
    "CSFloat (Card)":    {"percent_fee": 2.80, "fixed_fee_usd": 0.30, "enabled": True},
    "SkinSwap (Card)":   {"percent_fee": 3.52, "fixed_fee_usd": 0.32, "enabled": True},
    "SkinSwap (Crypto)": {"percent_fee": 1.01, "fixed_fee_usd": 0.01, "enabled": True},
}

# Комиссия Steam при продаже по умолчанию: 10% CS2 + 5% Steam = 15%.
DEFAULT_STEAM_FEE_PERCENT = 15.0

# Для целочисленных валют комиссия Steam рассчитывается фиксированным
# разбиением 10% (CS2) + 5% (Steam); общее поле "комиссия %" в интерфейсе
# на такие валюты не влияет.
STEAM_CS2_FEE_PERCENT = 10.0
STEAM_STEAM_FEE_PERCENT = 5.0

# Валюты для отображения результатов (символы).
CURRENCIES = ["$", "€", "₴", "₽", "£"]
DEFAULT_CURRENCY = "$"

# Грубые стартовые курсы "сколько валюты за 1 USD" (РЕДАКТИРУЕМЫЕ значения по
# умолчанию для поля конвертации фиксы; курсы меняются — это лишь подсказка).
USD_RATE_DEFAULTS = {"€": 0.92, "₴": 41.0, "₽": 90.0, "£": 0.79}

# Валюты, у которых на Торговой площадке Steam НЕТ дробной части: цены и
# комиссии выражаются только в целых единицах. Указаны и символы, и буквенные
# коды (гривна, иена, вона, чилийское песо, индонезийская рупия).
INTEGER_CURRENCIES = ["₴", "¥", "₩", "CLP", "IDR"]

# Текстовые псевдонимы тех же валют для распознавания по коду в свободном вводе
# (продвинутый режим). Множество нормализовано к нижнему регистру.
_INTEGER_CURRENCY_ALIASES = {
    "₴", "uah", "грн", "uah.", "¥", "jpy", "yen", "иена",
    "₩", "krw", "won", "вона", "clp", "idr", "rp",
}

# Коды/символы, означающие доллар США.
USD_CODES = {"usd", "$", "usd.", "us$"}

# Языки: отображаемое имя -> код.
LANG_OPTIONS = {"English": "en", "Русский": "ru", "Українська": "ua"}
DEFAULT_LANG_NAME = "English"   # язык интерфейса по умолчанию


# ===========================================================================
# ЛОКАЛИЗАЦИЯ (i18n)
# ===========================================================================
# Английские строки используются как ключи (паттерн gettext). Для en функция
# возвращает сам ключ; исключение — спец-ключи (например, MODE1_FORMULAS),
# для которых английский текст хранится в TRANSLATIONS["en"].

_CURRENT_LANG = "en"


def set_language(code):
    """Задаёт активный язык интерфейса ('en' | 'ru' | 'ua')."""
    global _CURRENT_LANG
    _CURRENT_LANG = code if code in ("en", "ru", "ua") else "en"


def _(key):
    """Возвращает перевод строки key для активного языка.

    Сначала ищем ключ в словаре активного языка (для en там лежат только
    спец-ключи вроде формул). Если не нашли — возвращаем сам ключ, который
    для английского и является готовой строкой (безопасный fallback).
    """
    return TRANSLATIONS.get(_CURRENT_LANG, {}).get(key, key)


# Словарь переводов. Плейсхолдеры в фигурных скобках ({a}, {amount}, ...)
# СОХРАНЯЮТСЯ во всех языках — подстановка значений делается через .format().
TRANSLATIONS = {
    # Для английского храним только строки, чьё значение отличается от ключа.
    "en": {
        "MODE1_FORMULAS":
            "- **Real spent** = (site price × qty) × (1 + fee% / 100) + fixed USD fee (converted)\n"
            "- **Steam received** = seller subtotal × qty. The subtotal is derived from the buyer "
            "price using Steam's exact fee model: each fee is floored (truncated) with a 1-unit "
            "minimum, computed in the currency's smallest unit — whole units for integer currencies, "
            "cents otherwise.\n"
            "- **Net profit** = Steam received − Real spent\n"
            "- **Profit %** = Net profit / Real spent × 100\n\n"
            "In advanced mode each side is first computed in its own currency, then converted to the "
            "spent (base) currency via your rates.",
        "MODE2_FORMULAS":
            "- **Real price (site)** = (site price × qty) × (1 + fee% / 100) + fixed USD fee (converted)\n"
            "- **Real price (Steam)** = (Steam price × qty) / (1 + top-up profit% / 100)\n"
            "- **Savings** = |site real price − Steam real price| (in the base currency when advanced).",
        "MODE3_FORMULAS":
            "- **Steam balance spent** = Steam purchase price × qty\n"
            "- **Real Steam cost** = Steam balance spent / (1 + top-up profit% / 100)  *(only when top-up profit is set)*\n"
            "- **Gross site revenue** = site sell price × qty\n"
            "- **After sales fee** = gross revenue × (1 − sales fee% / 100)\n"
            "- **Real money received** = after sales fee × (1 − withdrawal fee% / 100) − fixed USD fee (converted)\n"
            "- **Cashout ratio** = real money received / Real Steam cost × 100  *(or / Steam balance spent when no top-up profit)*\n"
            "- **Net profit / loss** = real money received − Real Steam cost\n\n"
            "In advanced mode the site side and the Steam side are each computed in their own currency, "
            "then converted to the base (card) currency via your rates.",
        "Real Steam cost (with top-up)": "Real Steam cost (with top-up)",
        "Effective cashout ratio": "Effective cashout ratio",
        "Top-up profit factored in. Ratio > 100% means you profit even after cashing out.":
            "Top-up profit factored in. Ratio > 100% means you profit even after cashing out.",
        # --- Режим 4 ---
        "Where to sell more profitably?": "Where to sell more profitably?",
        "We compare selling a skin on a third-party site (then topping up Steam at a profit) "
        "versus selling it directly on the Steam Market.":
            "We compare selling a skin on a third-party site (then topping up Steam at a profit) "
            "versus selling it directly on the Steam Market.",
        "Sell on third-party site → top up Steam": "Sell on third-party site → top up Steam",
        "Sell on Steam Market directly": "Sell on Steam Market directly",
        "Steam sell price (buyer pays)": "Steam sell price (buyer pays)",
        "Steam Market fee (%)": "Steam Market fee (%)",
        "Steam balance via site (with top-up)": "Steam balance via site (with top-up)",
        "Steam balance via Steam Market": "Steam balance via Steam Market",
        "Real money from site: {amount}": "Real money from site: {amount}",
        "Selling via site and topping up Steam is more profitable. "
        "Extra Steam balance: {amount}.":
            "Selling via site and topping up Steam is more profitable. "
            "Extra Steam balance: {amount}.",
        "Selling on Steam Market is more profitable. "
        "Extra Steam balance: {amount}.":
            "Selling on Steam Market is more profitable. "
            "Extra Steam balance: {amount}.",
        "Both options yield the same Steam balance.":
            "Both options yield the same Steam balance.",
        "MODE4_FORMULAS":
            "**Side A — Sell on site, top up Steam:**\n"
            "- Gross revenue = site sell price × qty\n"
            "- After sales fee = gross × (1 − sales fee% / 100)\n"
            "- Real money = after sales fee × (1 − withdrawal fee% / 100) − fixed fee\n"
            "- **Steam balance (A)** = real money × (1 + top-up profit% / 100)\n\n"
            "**Side B — Sell on Steam Market directly:**\n"
            "- **Steam balance (B)** = seller's cut from Steam Market × qty\n"
            "  *(Steam takes ~15% for CS2: 10% publisher + 5% platform; "
            "  for integer currencies the exact Valve rounding model is used)*\n\n"
            "**Comparison:** whichever side gives more Steam balance is recommended.",
        # --- Режим 5 ---
        "Best rarity to buy (collection)": "Best rarity to buy (collection)",
        "We rank which rarity in a collection is the best buy, based on the price ratio "
        "between adjacent rarities (10 lower-rarity items trade up into 1 higher-rarity item).":
            "We rank which rarity in a collection is the best buy, based on the price ratio "
            "between adjacent rarities (10 lower-rarity items trade up into 1 higher-rarity item).",
        "Prices in one currency; use a single float tier (preferably the lowest). Leave a rarity at 0 to exclude it from the collection.":
            "Prices in one currency; use a single float tier (preferably the lowest). Leave a rarity at 0 to exclude it from the collection.",
        "Rarity prices": "Rarity prices",
        "Price (0 = not in collection)": "Price (0 = not in collection)",
        "Rarity": "Rarity",
        "Price": "Price",
        "Ratio": "Ratio",
        "Rank": "Rank",
        "Comment": "Comment",
        "Enter at least two rarity prices to compare.": "Enter at least two rarity prices to compare.",
        "Best buy: {rarity} (rank {rank})": "Best buy: {rarity} (rank {rank})",
        "Ranking (higher = better buy)": "Ranking (higher = better buy)",
        "{n}× this rarity = one rarity above": "{n}× this rarity = one rarity above",
        "this rarity = {n}× the rarity below": "this rarity = {n}× the rarity below",
        "rarity_consumer": "Consumer Grade (grey)",
        "rarity_industrial": "Industrial Grade (light blue)",
        "rarity_milspec": "Mil-Spec (blue)",
        "rarity_restricted": "Restricted (purple)",
        "rarity_classified": "Classified (pink)",
        "rarity_covert": "Covert (red)",
        "rk_over": "overpriced — bad buy",
        "rk_over_slight": "slightly overpriced",
        "rk_normal": "normal average ratio",
        "rk_good": "a bit better than average",
        "rk_under": "underpriced — good buy",
        "rk_under_susp": "underpriced, suspicious — check liquidity",
        "rk_exp": "expensive vs lower tier",
        "rk_exp_slight": "a bit expensive",
        "rk_cheap": "cheap vs lower tier — good buy",
        "rk_cheap_susp": "very cheap, suspicious — check liquidity",
        "rk_over_strong": "strongly overpriced — bad buy",
        "rk_below": "below average",
        "Site": "Site",
        "Steam Market": "Steam Market",
        "The price shown to buyers on the Steam Market.": "The price shown to buyers on the Steam Market.",
        "How the ranking works": "How the ranking works",
        "rk_exp_strong": "very expensive vs the lower tier",
        "cleanliness": "cleanliness",
        "floor (cheapest)": "floor (cheapest)",
        "cleanliness premium": "cleanliness premium",
        "unit": "unit",
        "pricier and not cleaner — not worth it": "pricier and not cleaner — not worth it",
        "score": "score",
        "cleanliness premium: n/a (add a 2nd float to compare)": "cleanliness premium: n/a (add a 2nd float to compare)",
        "cleanliness premium: n/a (the cheapest is already the cleanest)": "cleanliness premium: n/a (the cheapest is already the cleanest)",
        "cheapest cleanliness {b}/{u} ({skin}) · avg {a}/{u}": "cheapest cleanliness {b}/{u} ({skin}) · avg {a}/{u}",
        "cleanliness premium: n/a (skins need ≥2 floats)": "cleanliness premium: n/a (skins need ≥2 floats)",
        "Score 0–100 is experimental (relative within a skin, 50/50 clean/cheap).": "Score 0–100 is experimental (relative within a skin, 50/50 clean/cheap).",
        'Advanced float analysis (float & cut)':
            'Advanced float analysis (float & cut)',
        'Advanced mode: for each rarity add skins with their float cap and one or more quality records. The ranking then also accounts for float economics — the contract value of cleanliness. (The single-currency note still applies.)':
            'Advanced mode: for each rarity add skins with their float cap and one or more quality records. The ranking then also accounts for float economics — the contract value of cleanliness. (The single-currency note still applies.)',
        'Default float = midpoint of (wear ∩ cap), instead of worst-in-wear':
            'Default float = midpoint of (wear ∩ cap), instead of worst-in-wear',
        "m5a_midpoint_label":
            "Use the wear's MIDPOINT float instead of its worst (dirtiest) — for records without an exact float",
        "m5a_midpoint_help":
            "Affects only quality records where you did NOT enter an exact float. OFF (default): the record's "
            "float is taken as the WORST (dirtiest) end of (wear ∩ cap) — the conservative, cheapest-to-obtain "
            "assumption. ON: the float is the MIDPOINT of (wear ∩ cap) — an average assumption. Records with an "
            "exact float entered are unaffected either way.",
        "m5_tp_help":
            "When on, you can enter a Steam-market price next to each item. Each price is converted to your "
            "real currency (via the rates and the Steam top-up bonus), and for every item the CHEAPER of the "
            "external-market price and the Steam price is used in the ranking. Off: only external prices, one currency.",
        'Skins in this rarity':
            'Skins in this rarity',
        'Skin':
            'Skin',
        'Skin name (optional)':
            'Skin name (optional)',
        'Float cap min':
            'Float cap min',
        'Float cap max':
            'Float cap max',
        'Invalid cap: need 0 ≤ min < max ≤ 1.':
            'Invalid cap: need 0 ≤ min < max ≤ 1.',
        'Quality records':
            'Quality records',
        'Quality (wear)':
            'Quality (wear)',
        'Exact float':
            'Exact float',
        'Float value':
            'Float value',
        'contract float':
            'contract float',
        'float-value':
            'float-value',
        'Auto (cheapest by price)':
            'Auto (cheapest by price)',
        'Record':
            'Record',
        'Record used in the rarity aggregate':
            'Record used in the rarity aggregate',
        'Add at least two rarities, each with a valid priced skin, to compare.':
            'Add at least two rarities, each with a valid priced skin, to compare.',
        'Ranking with float economics (higher = better buy)':
            'Ranking with float economics (higher = better buy)',
        'Float bonus':
            'Float bonus',
        'Trade-up & float details':
            'Trade-up & float details',
        'craft up':
            'craft up',
        "News":
            "News",
        "Show all news":
            "Show all news",
        "m5a_cands_title":
            "Filler candidates: what to buy ({n})",
        "m5a_cands_hint_cheapest":
            "Sorted by price — cheapest first. The contract uses the top row (✅). Each row is "
            "a contract of 10 copies of that exact record. Return = output ÷ input: 100% = you "
            "get your money back (break-even), 105% = +5% profit, below 100% = a loss.",
        "m5a_cands_hint_best":
            "Sorted by return — best price/quality first. The contract uses the top row (✅). "
            "Each row is a contract of 10 copies of that exact skin+quality. Return = output ÷ "
            "input: 100% = break-even, 105% = +5% profit, below 100% = a loss.",
        "m5a_col_skin":
            "Skin",
        "m5a_col_wear":
            "Quality",
        "m5a_col_float":
            "Float",
        "m5a_col_cost":
            "Input ×10",
        "m5a_col_eout":
            "Avg output",
        "m5a_tpl_caption":
            "Loading fills every field for this collection (fully editable). It OVERWRITES "
            "the current advanced-mode input. Prices are approximate USD starting values — "
            "update them to the current market before relying on the result.",
        "m5a_tpl_loaded_msg":
            "Loaded template: {name} · update the prices to the current market.",
        "m5a_craft_line":
            "(filler: {skin} · {wear} · float {f}, w={w} · {price} ×{n} = {cost} → "
            "avg output {eout} · return {roi}% (profit {profit}%))",
        "m5a_col_roi":
            "Return (ROI)",
        "m5a_cmode_label":
            "Contract calculation (fillers)",
        "m5a_cmode_cheapest":
            "By cheapest fillers — the contract uses the cheapest record at its real float",
        "m5a_cmode_best":
            "By best price/quality — every record is tried, the best-ROI filler is used",
        "m5a_cmode_help":
            "Both modes run the contract on your ACTUAL records (10 copies of one filler). "
            "The expected output is the average over ALL skins of the next rarity, each "
            "priced at the float the contract yields from the filler's real float "
            "(1 skin = 1 outcome). 'Cheapest' answers: what does the classic cheapest-filler "
            "craft give? 'Best price/quality' additionally tries every entered record as the "
            "filler and picks the one with the best return — the best craft-purchase option. "
            "Return is output ÷ input (100% = break-even, 105% = +5% profit).",
        'clean pays off — worth buying low-float fillers':
            'clean pays off — worth buying low-float fillers',
        "don't overpay for clean — a dirty filler gives the same output":
            "don't overpay for clean — a dirty filler gives the same output",
        'trade-up into the next rarity is unprofitable':
            'trade-up into the next rarity is unprofitable',
        'MODE5_ADV_NOTE':
            "Advanced mode: a rarity's price for ranking is its CHEAPEST record (the filler "
            "price). Float value is the cleanliness premium WITHIN a skin: the floor is the "
            "skin's cheapest record, and for a record cleaner than the floor the premium = "
            "(price − floor price)/(cleanliness − floor cleanliness) = $ per extra unit of "
            "cleanliness (needs ≥2 floats; lower = cheaper cleanliness = less overpay). One "
            "record → n/a. The float bonus comes from the contract ROI into the next rarity "
            "(10→1) computed on your ACTUAL records per the selected mode: 'cheapest' uses "
            "the cheapest record at its real float, 'best price/quality' tries every record "
            "and uses the best-return filler; the output is averaged over ALL next-rarity skins "
            "priced at the produced float. Return is shown as output ÷ input (100% = "
            "break-even, 105% = +5% profit); the rank bonus only applies above 100%. "
            "Contract float weight w = (float − "
            "cap_min)/(cap_max − cap_min). In advanced mode there is no 'beautiful/liquid' "
            "checkbox — its role is played by the float bonus from contract ROI (the checkbox "
            "described above applies to the simple mode).",
        "Enter the price you consider fair for each rarity. Tick the box if you "
        "find that rarity's skins beautiful or especially liquid.":
            "Enter the price you consider fair for each rarity. Tick the box if you "
            "find that rarity's skins beautiful or especially liquid.",
        "Beautiful / liquid?": "Beautiful / liquid?",
        "rk_top_note": "(Top rarity: it can't be crafted up and its supply only grows, "
                       "so its rank carries a penalty.)",
        "MODE5_FORMULAS":
            "For each rarity except the highest, ratio = **price(rarity above) / price(this rarity)** "
            "— how many of this rarity, by price, equal one item of the rarity above. A trade-up turns "
            "10 of one rarity into 1 of the next, so a **bigger ratio** means this rarity is cheap "
            "relative to what it becomes → a better buy:\n\n"
            "- ≤ 2 → **F** (strongly overpriced)\n"
            "- 2–3.5 → **E** (overpriced)\n"
            "- 3.5–4.5 → **D** (below average)\n"
            "- 4.5–5.5 → **C** (average)\n"
            "- 5.5–6.5 → **B**\n"
            "- 6.5–8 → **A** (this tier underpriced)\n"
            "- 8–10 → **A+**\n"
            "- > 10 → **A++** (unusual — 10 of these craft 1 above; check the higher tier's liquidity)\n\n"
            "The **highest** rarity is scored in reverse (nothing is above it): its ratio vs the tier "
            "below is flipped, then a penalty is applied (−2 if it lands on A or above, −1 for B/C/D/E) "
            "because the top rarity can't be crafted upward and its supply only grows.\n\n"
            "**Beauty / liquidity:** ticking a rarity adds +0.5 to its ratio and to the tier one below, "
            "+0.25 to the tier two below (nothing three below); it never affects a rarity above the "
            "ticked one. For the top rarity the bonus instead improves its reversed score.\n\n"
            "**Rarity-position adjustments (rank only):** independent of beauty, the rank also shifts by "
            "position among the rarities you actually entered — these touch ONLY the rank, never the displayed "
            "ratio or any raw number/comparison. The lowest tier gets +0.25; the top tier an extra −0.5 (on top "
            "of its reverse penalty); the 2nd-from-top −0.5, 3rd-from-top −0.35, 4th-from-top −0.25. In a small "
            "collection one tier can be both 'lowest' and 'k-th from top' — the shifts simply add up.\n\n"
            "Empty/0 prices are skipped, so collections missing some rarities are handled. "
            "This is a heuristic on the prices you enter, not financial advice.",
    },
    "ru": {
        # --- сайдбар / общее ---
        "Settings": "Настройки",
        "Language": "Язык",
        "Display currency": "Валюта отображения",
        "Advanced currency mode (cross-rates)": "Продвинутый режим валют (кросс-курсы)",
        "When enabled, all modes let you set separate currencies for your card, the site, and Steam.":
            "Если включено, во всех режимах можно задать отдельные валюты для карты, сайта и Steam.",
        "CS2 Skin Investing Toolkit": "Инструментарий инвестора CS2",
        "© 2026 Yev Capital. Not affiliated with Valve Corp. Steam and CS2 are trademarks of Valve Corporation.":
            "© 2026 Yev Capital. Не связано с Valve Corp. Steam и CS2 являются торговыми марками Valve Corporation.",
        "This is an analytical tool, not financial advice. All investments carry risks.":
            "Это аналитический инструмент, а не финансовая рекомендация. Все инвестиции сопряжены с рисками.",
        "All prices are entered manually. This is a calculator, not financial advice.":
            "Все цены вводятся вручную. Это калькулятор, а не финансовая рекомендация.",
        "Steam top-up profit, skin purchase and CS2 collection analyzer":
            "Калькулятор выгоды пополнения Steam, покупок скинов и анализа коллекций CS2",
        # --- вкладки ---
        "Balance top-up (profit)": "Пополнение баланса (профит)",
        "Where to buy cheaper?": "Где купить выгоднее?",
        # --- кнопка / общие поля ---
        "Calculate": "Рассчитать",
        "Press Calculate to see the results.": "Нажмите «Рассчитать», чтобы увидеть результат.",
        "Quantity": "Количество предметов",
        # --- блок комиссии ---
        "Fee template": "Шаблон комиссии сайта",
        "Presets for popular sites. Manual input is also available.":
            "Шаблоны популярных сайтов. Также доступен ручной ввод.",
        "Account for top-up fee": "Учитывать комиссию пополнения",
        "Top-up fee (%)": "Комиссия пополнения (%)",
        "Fixed fee (USD)": "Фиксированная комиссия (USD)",
        "Manual input": "Ручной ввод",
        "USD fixed fee converted via your existing cross-rate.":
            "Фиксированная комиссия USD конвертирована по вашему кросс-курсу.",
        # --- Режим 1 ---
        "Steam balance top-up calculator": "Калькулятор пополнения баланса Steam",
        "We calculate the final profit from buying a skin on a third-party site and selling it on the Steam Market.":
            "Считаем итоговый плюс при покупке скина на стороннем сайте и его продаже на Торговой площадке Steam.",
        "Purchase (third-party site)": "Покупка (сторонний сайт)",
        "Skin price on third-party site": "Стоимость скина на стороннем сайте",
        "Sale (Steam Market)": "Продажа (ТП Steam)",
        "Steam sale price": "Цена продажи на ТП Steam",
        "Steam sale fee (%)": "Комиссия Steam при продаже (%)",
        "Default 15% = 10% CS2 fee + 5% Steam fee.":
            "По умолчанию 15% = 10% комиссия CS2 + 5% комиссия Steam.",
        "Currency without cents (integers only)": "Валюта без копеек (целые числа)",
        "Forces integer Steam pricing. Auto-enabled for ₴ / UAH.":
            "Включает целочисленные цены Steam. Для ₴ / UAH включается автоматически.",
        "Cross-currency settings": "Настройки кросс-курсов",
        "Spent currency (e.g. UAH)": "Валюта затрат (например, UAH)",
        "Site currency (e.g. USD)": "Валюта сайта (например, USD)",
        "Steam currency (e.g. EUR)": "Валюта Steam (например, EUR)",
        "Rate: how many {a} in 1 {b}": "Курс: сколько {a} в 1 {b}",
        "Both rates are in the spent currency per 1 unit (a common base).":
            "Оба курса заданы в валюте затрат за 1 единицу (приведение к единому знаменателю).",
        "Site cost": "Затраты на сайте",
        "Steam proceeds": "Получено в Steam",
        "Steam cost": "Стоимость в Steam",
        "Results": "Результаты",
        "Real spent": "Реальные затраты",
        "Steam received": "Получено на Steam",
        "Net profit": "Чистый плюс",
        "Buyer pays {buyer} · you receive {seller} (per item)":
            "Покупатель платит {buyer} · вы получаете {seller} (за 1 шт.)",
        "Steam fees are floored to whole units with a 1-unit minimum, following Steam's exact fee model.":
            "Комиссии Steam округляются вниз до целых единиц с минимумом в одну единицу — по точной модели Steam.",
        "Price {x} is impossible in Steam for integer currencies. Rounded to the nearest possible: {y}.":
            "Цена {x} невозможна в Steam для целочисленных валют. Округлено до ближайшей возможной: {y}.",
        "Steam has no fractions for this currency; price rounded to {y}.":
            "В этой валюте у Steam нет дробной части; цена округлена до {y}.",
        "Enter a skin price to see the calculation.": "Введите стоимость скина, чтобы увидеть расчёт.",
        "Top-up in profit: +{amount} ({percent}).": "Пополнение в плюс: +{amount} ({percent}).",
        "Top-up at a loss: {amount} ({percent}).": "Пополнение в минус: {amount} ({percent}).",
        "Break-even result.": "Нулевой результат — выходите в ноль.",
        "Calculation formulas": "Формулы расчёта",
        "MODE1_FORMULAS":
            "- **Реальные затраты** = (цена на сайте × кол-во) × (1 + %комиссии / 100) + фикса USD (конвертированная)\n"
            "- **Получено на Steam** = промежуточный итог продавца × кол-во. Итог выводится из цены "
            "покупателя по точной модели Steam: каждая комиссия округляется вниз (отбрасывание дробной "
            "части) с минимумом в одну единицу и считается в наименьшей единице валюты — целые единицы "
            "для целочисленных валют, копейки/центы для остальных.\n"
            "- **Чистый плюс** = Получено на Steam − Реальные затраты\n"
            "- **Процент плюса** = Чистый плюс / Реальные затраты × 100\n\n"
            "В продвинутом режиме каждая сторона сначала считается в своей валюте, и только потом "
            "приводится к валюте затрат (базовой) по вашим курсам.",
        # --- Режим 2 ---
        "We compare buying a skin directly on a third-party site with real money versus buying it on the Steam Market with a balance topped up 'in profit'.":
            "Сравниваем покупку скина напрямую на стороннем сайте за реальные деньги "
            "против покупки на ТП Steam за баланс, пополненный «в плюс».",
        "Buy on Steam Market": "Покупка на ТП Steam",
        "Current Steam Market price": "Текущая стоимость скина на ТП Steam",
        "Steam top-up profit (%)": "Процент плюса пополнения Steam (%)",
        "How profitably you topped up Steam earlier. Example: spent 10 real, got 15 on balance → 50% profit.":
            "Насколько выгодно вы ранее пополнили Steam. Например: потратили 10 реальных, "
            "получили 15 на баланс → плюс 50%.",
        "Buy on third-party site": "Покупка на стороннем сайте",
        "Results comparison": "Результаты сравнения",
        "Real price (third-party site)": "Реальная цена (сторонний сайт)",
        "Real price (Steam, with profit)": "Реальная цена (Steam, с учётом плюса)",
        "Enter data to see the comparison.": "Введите данные, чтобы увидеть сравнение.",
        "Cheaper to buy on Steam. Savings: {amount}.": "Выгоднее купить в Steam. Экономия: {amount}.",
        "Cheaper to buy on the third-party site. Savings: {amount}.":
            "Выгоднее купить на стороннем сайте. Экономия: {amount}.",
        "Both options cost the same in real money.": "Оба варианта равнозначны по реальной стоимости.",
        "MODE2_FORMULAS":
            "- **Реальная цена (сайт)** = (цена на сайте × кол-во) × (1 + %комиссии / 100) + фикса USD (конвертированная)\n"
            "- **Реальная цена (Steam)** = (цена на ТП × кол-во) / (1 + %плюса / 100)\n"
            "- **Экономия** = модуль разницы между двумя реальными ценами (в базовой валюте при кросс-курсах).",
        # --- Режим 3 ---
        "Withdrawal (Cashout)": "Вывод средств (Cashout)",
        "Steam balance cashout calculator": "Калькулятор вывода баланса Steam",
        "We calculate how much real money you receive by buying a skin on the Steam Market, "
        "selling it on a third-party site, and withdrawing the proceeds.":
            "Считаем, сколько реальных денег вы получите, купив скин на Торговой площадке Steam, "
            "продав его на стороннем сайте и выведя выручку.",
        "Purchase (Steam Market)": "Покупка (ТП Steam)",
        "Steam purchase price": "Цена покупки в Steam",
        "Withdrawal (third-party site)": "Вывод (сторонний сайт)",
        "Site sell price": "Цена продажи на сайте",
        "Sales fee (%)": "Комиссия сайта за продажу (%)",
        "Withdrawal fee (%)": "Комиссия за вывод (%)",
        "Withdrawal fixed fee (USD)": "Фиксированная комиссия за вывод (USD)",
        "Total Steam spent": "Потрачено Steam баланса",
        "Real money received": "Получено реальных денег",
        "Cashout ratio": "Коэффициент вывода",
        "Net profit / loss": "Чистая прибыль / убыток",
        "Gross site revenue": "Грязная выручка на сайте",
        "After sales fee": "После комиссии за продажу",
        "Enter prices to see the cashout calculation.":
            "Введите цены, чтобы увидеть расчёт вывода.",
        "The higher the cashout ratio, the more of your Steam balance reaches your card.":
            "Чем выше коэффициент вывода, тем большая часть баланса Steam доходит до карты.",
        "MODE3_FORMULAS":
            "- **Потрачено баланса Steam** = цена покупки в Steam × кол-во\n"
            "- **Реальные затраты Steam** = потрачено баланса Steam / (1 + % плюса пополнения / 100)  *(только если задан % плюса)*\n"
            "- **Грязная выручка на сайте** = цена продажи на сайте × кол-во\n"
            "- **После комиссии за продажу** = грязная выручка × (1 − %комиссии продажи / 100)\n"
            "- **Получено реальных денег** = после комиссии за продажу × (1 − %комиссии вывода / 100) − фикса USD (конвертированная)\n"
            "- **Коэффициент вывода** = получено реальных денег / Реальные затраты Steam × 100  *(или / баланс Steam, если плюс не задан)*\n"
            "- **Чистая прибыль / убыток** = получено реальных денег − Реальные затраты Steam\n\n"
            "В продвинутом режиме сторона сайта и сторона Steam считаются каждая в своей валюте, "
            "а затем приводятся к базовой валюте (карты) по вашим курсам.",
        "Real Steam cost (with top-up)": "Реальные затраты Steam (с плюсом)",
        "Effective cashout ratio": "Эффективный коэффициент вывода",
        "Top-up profit factored in. Ratio > 100% means you profit even after cashing out.":
            "Учтён плюс пополнения. Коэффициент > 100% означает, что вы в плюсе даже после вывода.",
        # --- Режим 4 ---
        "Where to sell more profitably?": "Где продать выгоднее?",
        "We compare selling a skin on a third-party site (then topping up Steam at a profit) "
        "versus selling it directly on the Steam Market.":
            "Сравниваем продажу скина на стороннем сайте (с последующим пополнением Steam в плюс) "
            "и продажу напрямую через Steam Market.",
        "Sell on third-party site → top up Steam": "Продать на стороннем сайте → пополнить Steam",
        "Sell on Steam Market directly": "Продать на Steam Market напрямую",
        "Steam sell price (buyer pays)": "Цена на Steam Market (платит покупатель)",
        "Steam Market fee (%)": "Комиссия Steam Market (%)",
        "Steam balance via site (with top-up)": "Баланс Steam через сайт (с пополнением)",
        "Steam balance via Steam Market": "Баланс Steam через Steam Market",
        "Real money from site: {amount}": "Реальных денег с сайта: {amount}",
        "Selling via site and topping up Steam is more profitable. "
        "Extra Steam balance: {amount}.":
            "Выгоднее продать через сайт и пополнить Steam. "
            "Дополнительный баланс: {amount}.",
        "Selling on Steam Market is more profitable. "
        "Extra Steam balance: {amount}.":
            "Выгоднее продать на Steam Market напрямую. "
            "Дополнительный баланс: {amount}.",
        "Both options yield the same Steam balance.":
            "Оба варианта дают одинаковый Steam-баланс.",
        "MODE4_FORMULAS":
            "**Вариант А — Продать на сайте, пополнить Steam:**\n"
            "- Грязная выручка = цена продажи на сайте × кол-во\n"
            "- После комиссии продажи = грязная выручка × (1 − %комиссии продажи / 100)\n"
            "- Реальные деньги = после комиссии × (1 − %комиссии вывода / 100) − фикса\n"
            "- **Баланс Steam (А)** = реальные деньги × (1 + %плюса пополнения / 100)\n\n"
            "**Вариант Б — Продать на Steam Market напрямую:**\n"
            "- **Баланс Steam (Б)** = выручка продавца с учётом комиссии Steam × кол-во\n"
            "  *(Steam удерживает ~15% для CS2: 10% издательская + 5% площадка; "
            "  для целочисленных валют применяется точная модель округления Valve)*\n\n"
            "**Сравнение:** рекомендуется вариант, дающий больший Steam-баланс.",
        # --- Режим 5 ---
        "Best rarity to buy (collection)": "Лучшее качество для покупки (коллекция)",
        "We rank which rarity in a collection is the best buy, based on the price ratio "
        "between adjacent rarities (10 lower-rarity items trade up into 1 higher-rarity item).":
            "Оцениваем, какое качество в коллекции выгоднее покупать, по соотношению цен соседних "
            "качеств (10 предметов нижнего качества через контракт обмена дают 1 предмет выше).",
        "Prices in one currency; use a single float tier (preferably the lowest). Leave a rarity at 0 to exclude it from the collection.":
            "Цены в одной валюте; используй одно качество флоата (желательно самое низкое). Оставь 0, чтобы исключить редкость из коллекции.",
        "Rarity prices": "Цены по качествам",
        "Price (0 = not in collection)": "Цена (0 = нет в коллекции)",
        "Rarity": "Качество",
        "Price": "Цена",
        "Ratio": "Соотношение",
        "Rank": "Ранг",
        "Comment": "Комментарий",
        "Enter at least two rarity prices to compare.":
            "Введи цены минимум для двух качеств для сравнения.",
        "Best buy: {rarity} (rank {rank})": "Выгоднее всего покупать: {rarity} (ранг {rank})",
        "Ranking (higher = better buy)": "Рейтинг (выше — выгоднее покупать)",
        "{n}× this rarity = one rarity above": "{n}× этого качества = одно качество выше",
        "this rarity = {n}× the rarity below": "это качество = {n}× качества ниже",
        "rarity_consumer": "Ширпотреб (серое)",
        "rarity_industrial": "Промышленное (голубое)",
        "rarity_milspec": "Армейское (синее)",
        "rarity_restricted": "Запрещённое (фиолетовое)",
        "rarity_classified": "Засекреченное (розовое)",
        "rarity_covert": "Тайное (красное)",
        "rk_over": "переоценено — невыгодно покупать",
        "rk_over_slight": "слегка переоценено",
        "rk_normal": "нормальное среднее соотношение",
        "rk_good": "чуть лучше среднего",
        "rk_under": "недооценено — выгодно покупать",
        "rk_under_susp": "недооценено, подозрительно — проверь ликвидность",
        "rk_exp": "дорогое относительно качества ниже",
        "rk_exp_slight": "дороговато",
        "rk_cheap": "дёшево относительно качества ниже — выгодно",
        "rk_cheap_susp": "очень дёшево, подозрительно — проверь ликвидность",
        "rk_over_strong": "сильно переоценено — невыгодно покупать",
        "rk_below": "ниже среднего",
        "Site": "Сайт",
        "Steam Market": "Steam Market",
        "The price shown to buyers on the Steam Market.": "Цена, которую видят покупатели на Торговой площадке Steam.",
        "How the ranking works": "Как считается ранг",
        "rk_exp_strong": "очень дорогое относительно качества ниже",
        "cleanliness": "чистота",
        "floor (cheapest)": "пол (самый дешёвый)",
        "cleanliness premium": "наценка за чистоту",
        "unit": "ед.",
        "pricier and not cleaner — not worth it": "дороже и не чище — невыгодно",
        "score": "балл",
        "cleanliness premium: n/a (add a 2nd float to compare)": "наценка за чистоту: н/д (добавьте 2-й флоат для сравнения)",
        "cleanliness premium: n/a (the cheapest is already the cleanest)": "наценка за чистоту: н/д (самый дешёвый уже самый чистый)",
        "cheapest cleanliness {b}/{u} ({skin}) · avg {a}/{u}": "дешевле всего чистота {b}/{u} ({skin}) · в среднем {a}/{u}",
        "cleanliness premium: n/a (skins need ≥2 floats)": "наценка за чистоту: н/д (нужно ≥2 флоата у скинов)",
        "Score 0–100 is experimental (relative within a skin, 50/50 clean/cheap).": "Балл 0–100 — экспериментальный (относительный внутри скина, 50/50 чистота/цена).",
        'Advanced float analysis (float & cut)':
            'Продвинутый анализ флоата (флоат и резка)',
        'Advanced mode: for each rarity add skins with their float cap and one or more quality records. The ranking then also accounts for float economics — the contract value of cleanliness. (The single-currency note still applies.)':
            'Продвинутый режим: для каждой редкости добавь скины с их резкой флоата и одной или несколькими записями качеств. Тогда ранжирование учитывает и экономику флоата — контрактную ценность чистоты. (Условие одной валюты по-прежнему действует.)',
        'Default float = midpoint of (wear ∩ cap), instead of worst-in-wear':
            'Дефолтный флоат = середина (качество ∩ резка), вместо «худшего в качестве»',
        "m5a_midpoint_label":
            "Брать СЕРЕДИНУ флоата качества вместо «худшего» (для записей без точного флоата)",
        "m5a_midpoint_help":
            "Влияет только на записи качества, где НЕ задан точный флоат. ВЫКЛ (по умолчанию): флоат берётся "
            "как «худший» (самый грязный) край пересечения (качество ∩ резка) — консервативная оценка, такой "
            "предмет проще всего получить. ВКЛ: флоат берётся как СЕРЕДИНА пересечения (качество ∩ резка) — "
            "усреднённая оценка. На записи с введённым точным флоатом галочка не влияет.",
        "m5_tp_help":
            "Когда включено, рядом с каждым предметом можно ввести цену из Steam. Каждая цена приводится к "
            "твоей реальной валюте (через курсы и бонус пополнения Steam), и для каждого предмета в ранжировании "
            "берётся ДЕШЕВЛЕ из цены внешнего маркета и цены Steam. Выкл: только внешние цены, одна валюта.",
        "Account for Steam (TP) prices":
            "Учёт цен Steam (ТП)",
        "Steam (TP) pricing settings":
            "Настройки цен Steam (ТП)",
        "External sites currency":
            "Валюта внешних площадок",
        "Steam currency":
            "Валюта Steam",
        "Real currency (you pay in)":
            "Реальная валюта (которой платишь)",
        "Steam top-up bonus %":
            "Бонус пополнения Steam, %",
        "Prices are converted to your real currency; for each item the CHEAPER of market vs Steam is used.":
            "Цены приводятся к реальной валюте; для каждого предмета берётся ДЕШЕВЛЕ из маркета и Steam.",
        "Steam price":
            "Цена Steam",
        "price from Steam":
            "цена из Steam",
        "price from market":
            "цена с внешнего маркета",
        'Skins in this rarity':
            'Скинов в этой редкости',
        'Skin':
            'Скин',
        'Skin name (optional)':
            'Название скина (необязательно)',
        'Float cap min':
            'Резка флоата, min',
        'Float cap max':
            'Резка флоата, max',
        'Invalid cap: need 0 ≤ min < max ≤ 1.':
            'Некорректная резка: нужно 0 ≤ min < max ≤ 1.',
        'Quality records':
            'Записей качества',
        'Quality (wear)':
            'Качество (износ)',
        'Exact float':
            'Точный флоат',
        'Float value':
            'Значение флоата',
        'contract float':
            'контрактный флоат',
        'float-value':
            'стоимость за флоат',
        'Auto (cheapest by price)':
            'Авто (самая дешёвая по цене)',
        'Record':
            'Запись',
        'Record used in the rarity aggregate':
            'Запись для агрегата редкости',
        'Add at least two rarities, each with a valid priced skin, to compare.':
            'Добавь минимум две редкости с валидным скином с ценой для сравнения.',
        'Ranking with float economics (higher = better buy)':
            'Ранжирование с учётом экономики флоата (выше = выгоднее)',
        'Float bonus':
            'Флоат-бонус',
        'Trade-up & float details':
            'Контракты и детали флоата',
        'craft up':
            'крафт вверх',
        "News":
            "Новости",
        "Show all news":
            "Показать все новости",
        "m5a_cands_title":
            "Кандидаты-филлеры: что покупать ({n})",
        "m5a_cands_hint_cheapest":
            "Отсортировано по цене — сначала самые дешёвые. Контракт берёт верхнюю строку (✅). "
            "Каждая строка = контракт из 10 копий именно этой записи. Возврат = выход ÷ вход: "
            "100% = вложенное вернулось (в ноль), 105% = +5% прибыли, ниже 100% = убыток.",
        "m5a_cands_hint_best":
            "Отсортировано по возврату — сначала лучшее соотношение цена/качество. Контракт берёт "
            "верхнюю строку (✅). Каждая строка = контракт из 10 копий этого скина в этом качестве. "
            "Возврат = выход ÷ вход: 100% = в ноль, 105% = +5% прибыли, ниже 100% = убыток.",
        "m5a_col_skin":
            "Скин",
        "m5a_col_wear":
            "Качество",
        "m5a_col_float":
            "Флоат",
        "m5a_col_cost":
            "Вход ×10",
        "m5a_col_eout":
            "Ср. выход",
        "Collection template":
            "Шаблон коллекции",
        "Load template":
            "Загрузить шаблон",
        "m5a_tpl_caption":
            "Загрузка заполняет все поля этой коллекции (всё редактируемо). Она ПЕРЕЗАПИСЫВАЕТ "
            "текущий ввод продвинутого режима. Цены — ориентировочные стартовые "
            "значения (USD); перед использованием обнови их под текущий рынок.",
        "m5a_tpl_loaded_msg":
            "Загружен шаблон: {name} · обнови цены под текущий рынок.",
        "m5a_craft_line":
            "(филлер: {skin} · {wear} · флоат {f}, w={w} · {price} ×{n} = {cost} → "
            "ср. выход {eout} · возврат {roi}% (прибыль {profit}%))",
        "m5a_col_roi":
            "Возврат (ROI)",
        "m5a_cmode_label":
            "Расчёт контрактов (филлеры)",
        "m5a_cmode_cheapest":
            "По самым дешёвым — контракт из самой дешёвой записи с её реальным флоатом",
        "m5a_cmode_best":
            "По лучшему соотношению цена/качество — перебираются все записи, берётся филлер с лучшим ROI",
        "m5a_cmode_help":
            "Оба режима считают контракт на твоих РЕАЛЬНЫХ записях (10 копий одного филлера). "
            "Ожидаемый выход — среднее по ВСЕМ скинам следующей редкости, каждый оценён на "
            "флоате, который даст контракт от реального флоата филлера (1 скин = 1 исход). "
            "«По самым дешёвым» отвечает: что даёт классический крафт из самых дешёвых филлеров? "
            "«По лучшему соотношению» дополнительно пробует каждую введённую запись как филлер "
            "и берёт запись с лучшим возвратом — лучший вариант для крафта-закупки. Возврат = "
            "выход ÷ вход (100% = в ноль, 105% = +5% прибыли).",
        'clean pays off — worth buying low-float fillers':
            'чистота окупается — есть смысл брать низкофлоатные филлеры',
        "don't overpay for clean — a dirty filler gives the same output":
            'не переплачивай за чистоту — грязный филлер даёт тот же выход',
        'trade-up into the next rarity is unprofitable':
            'контракт в следующую редкость невыгоден',
        'MODE5_ADV_NOTE':
            'Продвинутый режим: цена редкости для ранга — её САМАЯ ДЕШЁВАЯ запись (цена филлеров). Флоат-ценность — это наценка за чистоту ВНУТРИ скина: пол = самая дешёвая запись скина, и для записи чище пола наценка = (цена − цена пола)/(чистота − чистота пола) = $ за доп. единицу чистоты (нужно ≥2 флоата; ниже = дешевле чистота = меньше переплата). Одна запись → н/д. Флоат-бонус берётся из ROI контракта в следующую редкость (10→1), посчитанного на РЕАЛЬНЫХ записях по выбранному режиму: «по самым дешёвым» — самая дешёвая запись с её реальным флоатом, «по лучшему соотношению цена/качество» — перебираются все записи и берётся филлер с лучшим возвратом; выход усредняется по ВСЕМ скинам следующей редкости на полученном флоате. Возврат показывается как выход ÷ вход (100% = в ноль, 105% = +5% прибыли); ранговый бонус даётся только выше 100%. Контрактный вес флоата w = (флоат − cap_min)/(cap_max − cap_min). В продвинутом режиме галочки «красивое/ликвидное» нет — её роль играет флоат-бонус из контрактного ROI (описание галочки выше относится к простому режиму).',
        "Enter the price you consider fair for each rarity. Tick the box if you "
        "find that rarity's skins beautiful or especially liquid.":
            "Укажи цену, которую считаешь справедливой для каждого качества. Отметь галочку, "
            "если скины этого качества красивые или особенно ликвидные.",
        "Beautiful / liquid?": "Красивое / ликвидное?",
        "rk_top_note": "(Высшее качество: его нельзя скрафтить выше, а предложение только "
                       "растёт, поэтому к рангу применён штраф.)",
        "MODE5_FORMULAS":
            "Для каждого качества, кроме высшего, соотношение = **цена качества выше / цена этого "
            "качества** — сколько штук этого качества по цене равны одному предмету качества выше. "
            "Контракт превращает 10 предметов одного качества в 1 предмет следующего, поэтому "
            "**большее соотношение** означает, что качество дёшево относительно того, чем становится "
            "→ выгоднее покупать:\n\n"
            "- ≤ 2 → **F** (сильно переоценено)\n"
            "- 2–3.5 → **E** (переоценено)\n"
            "- 3.5–4.5 → **D** (ниже среднего)\n"
            "- 4.5–5.5 → **C** (среднее)\n"
            "- 5.5–6.5 → **B**\n"
            "- 6.5–8 → **A** (это качество недооценено)\n"
            "- 8–10 → **A+**\n"
            "- > 10 → **A++** (необычно — 10 этих крафтят 1 выше; проверь ликвидность верхнего)\n\n"
            "**Высшее** качество считается наоборот (выше него ничего нет): его соотношение к качеству "
            "ниже реверсируется, затем применяется штраф (−2, если попадает на A и выше, −1 для B/C/D/E), "
            "потому что высшее качество нельзя скрафтить дальше, а его предложение только растёт.\n\n"
            "**Красота / ликвидность:** галочка добавляет +0.5 к соотношению этого качества и качества "
            "на 1 ниже, +0.25 к качеству на 2 ниже (на 3 ниже — ничего); на качество выше отмеченного "
            "бонус не влияет. Для высшего качества бонус, наоборот, улучшает его реверс-оценку.\n\n"
            "**Позиционные корректировки (только ранг):** независимо от красоты, ранг дополнительно "
            "сдвигается по позиции среди ВВЕДЁННЫХ редкостей — это влияет ТОЛЬКО на ранг, не на показанный "
            "ratio и не на сырые цифры/сравнения. Самой нижней +0.25; высшей — дополнительный −0.5 (сверх её "
            "реверс-штрафа); предпоследней −0.5, пред-предпоследней −0.35, пред-пред-предпоследней −0.25. В "
            "маленькой коллекции одна редкость может быть и «самой нижней», и «k-й сверху» — сдвиги тогда "
            "просто складываются.\n\n"
            "Пустые цены/0 пропускаются, поэтому коллекции без некоторых качеств учитываются. "
            "Это эвристика по введённым ценам, а не финансовая рекомендация.",
    },
    "ua": {
        # --- сайдбар / загальне ---
        "Settings": "Налаштування",
        "Language": "Мова",
        "Display currency": "Валюта відображення",
        "Advanced currency mode (cross-rates)": "Розширений режим валют (крос-курси)",
        "When enabled, all modes let you set separate currencies for your card, the site, and Steam.":
            "Якщо увімкнено, в усіх режимах можна задати окремі валюти для картки, сайту та Steam.",
        "CS2 Skin Investing Toolkit": "Інструментарій інвестора CS2",
        "© 2026 Yev Capital. Not affiliated with Valve Corp. Steam and CS2 are trademarks of Valve Corporation.":
            "© 2026 Yev Capital. Не пов'язано з Valve Corp. Steam та CS2 є торговими марками Valve Corporation.",
        "This is an analytical tool, not financial advice. All investments carry risks.":
            "Це аналітичний інструмент, а не фінансова порада. Усі інвестиції пов'язані з ризиками.",
        "All prices are entered manually. This is a calculator, not financial advice.":
            "Усі ціни вводяться вручну. Це калькулятор, а не фінансова порада.",
        "Steam top-up profit, skin purchase and CS2 collection analyzer":
            "Калькулятор вигоди поповнення Steam, покупок скінів та аналізу колекцій CS2",
        # --- вкладки ---
        "Balance top-up (profit)": "Поповнення балансу (профіт)",
        "Where to buy cheaper?": "Де купити вигідніше?",
        # --- кнопка / загальні поля ---
        "Calculate": "Розрахувати",
        "Press Calculate to see the results.": "Натисніть «Розрахувати», щоб побачити результат.",
        "Quantity": "Кількість предметів",
        # --- блок комісії ---
        "Fee template": "Шаблон комісії сайту",
        "Presets for popular sites. Manual input is also available.":
            "Шаблони популярних сайтів. Також доступне ручне введення.",
        "Account for top-up fee": "Враховувати комісію поповнення",
        "Top-up fee (%)": "Комісія поповнення (%)",
        "Fixed fee (USD)": "Фіксована комісія (USD)",
        "Manual input": "Ручне введення",
        "USD fixed fee converted via your existing cross-rate.":
            "Фіксована комісія USD конвертована за вашим крос-курсом.",
        # --- Режим 1 ---
        "Steam balance top-up calculator": "Калькулятор поповнення балансу Steam",
        "We calculate the final profit from buying a skin on a third-party site and selling it on the Steam Market.":
            "Рахуємо підсумковий плюс при купівлі скіна на сторонньому сайті та його продажу на Торговому майданчику Steam.",
        "Purchase (third-party site)": "Купівля (сторонній сайт)",
        "Skin price on third-party site": "Вартість скіна на сторонньому сайті",
        "Sale (Steam Market)": "Продаж (ТМ Steam)",
        "Steam sale price": "Ціна продажу на ТМ Steam",
        "Steam sale fee (%)": "Комісія Steam при продажу (%)",
        "Default 15% = 10% CS2 fee + 5% Steam fee.":
            "За замовчуванням 15% = 10% комісія CS2 + 5% комісія Steam.",
        "Currency without cents (integers only)": "Валюта без копійок (цілі числа)",
        "Forces integer Steam pricing. Auto-enabled for ₴ / UAH.":
            "Вмикає цілочисельні ціни Steam. Для ₴ / UAH вмикається автоматично.",
        "Cross-currency settings": "Налаштування крос-курсів",
        "Spent currency (e.g. UAH)": "Валюта витрат (наприклад, UAH)",
        "Site currency (e.g. USD)": "Валюта сайту (наприклад, USD)",
        "Steam currency (e.g. EUR)": "Валюта Steam (наприклад, EUR)",
        "Rate: how many {a} in 1 {b}": "Курс: скільки {a} в 1 {b}",
        "Both rates are in the spent currency per 1 unit (a common base).":
            "Обидва курси задані у валюті витрат за 1 одиницю (приведення до спільного знаменника).",
        "Site cost": "Витрати на сайті",
        "Steam proceeds": "Отримано в Steam",
        "Steam cost": "Вартість у Steam",
        "Results": "Результати",
        "Real spent": "Реальні витрати",
        "Steam received": "Отримано на Steam",
        "Net profit": "Чистий плюс",
        "Buyer pays {buyer} · you receive {seller} (per item)":
            "Покупець платить {buyer} · ви отримуєте {seller} (за 1 шт.)",
        "Steam fees are floored to whole units with a 1-unit minimum, following Steam's exact fee model.":
            "Комісії Steam округлюються вниз до цілих одиниць з мінімумом в одну одиницю — за точною моделлю Steam.",
        "Price {x} is impossible in Steam for integer currencies. Rounded to the nearest possible: {y}.":
            "Ціна {x} неможлива в Steam для цілочисельних валют. Заокруглено до найближчої можливої: {y}.",
        "Steam has no fractions for this currency; price rounded to {y}.":
            "У цій валюті Steam не має дробової частини; ціну заокруглено до {y}.",
        "Enter a skin price to see the calculation.": "Введіть вартість скіна, щоб побачити розрахунок.",
        "Top-up in profit: +{amount} ({percent}).": "Поповнення в плюс: +{amount} ({percent}).",
        "Top-up at a loss: {amount} ({percent}).": "Поповнення в мінус: {amount} ({percent}).",
        "Break-even result.": "Нульовий результат — виходите в нуль.",
        "Calculation formulas": "Формули розрахунку",
        "MODE1_FORMULAS":
            "- **Реальні витрати** = (ціна на сайті × к-сть) × (1 + %комісії / 100) + фікса USD (конвертована)\n"
            "- **Отримано на Steam** = проміжний підсумок продавця × к-сть. Підсумок виводиться з ціни "
            "покупця за точною моделлю Steam: кожна комісія округлюється вниз (відкидання дробової "
            "частини) з мінімумом в одну одиницю і рахується в найменшій одиниці валюти — цілі одиниці "
            "для цілочисельних валют, копійки/центи для інших.\n"
            "- **Чистий плюс** = Отримано на Steam − Реальні витрати\n"
            "- **Відсоток плюса** = Чистий плюс / Реальні витрати × 100\n\n"
            "У розширеному режимі кожна сторона спочатку рахується у своїй валюті, і лише потім "
            "приводиться до валюти витрат (базової) за вашими курсами.",
        # --- Режим 2 ---
        "We compare buying a skin directly on a third-party site with real money versus buying it on the Steam Market with a balance topped up 'in profit'.":
            "Порівнюємо купівлю скіна напряму на сторонньому сайті за реальні гроші "
            "проти купівлі на ТМ Steam за баланс, поповнений «у плюс».",
        "Buy on Steam Market": "Купівля на ТМ Steam",
        "Current Steam Market price": "Поточна вартість скіна на ТМ Steam",
        "Steam top-up profit (%)": "Відсоток плюса поповнення Steam (%)",
        "How profitably you topped up Steam earlier. Example: spent 10 real, got 15 on balance → 50% profit.":
            "Наскільки вигідно ви раніше поповнили Steam. Наприклад: витратили 10 реальних, "
            "отримали 15 на баланс → плюс 50%.",
        "Buy on third-party site": "Купівля на сторонньому сайті",
        "Results comparison": "Результати порівняння",
        "Real price (third-party site)": "Реальна ціна (сторонній сайт)",
        "Real price (Steam, with profit)": "Реальна ціна (Steam, з урахуванням плюса)",
        "Enter data to see the comparison.": "Введіть дані, щоб побачити порівняння.",
        "Cheaper to buy on Steam. Savings: {amount}.": "Вигідніше купити в Steam. Економія: {amount}.",
        "Cheaper to buy on the third-party site. Savings: {amount}.":
            "Вигідніше купити на сторонньому сайті. Економія: {amount}.",
        "Both options cost the same in real money.": "Обидва варіанти рівнозначні за реальною вартістю.",
        "MODE2_FORMULAS":
            "- **Реальна ціна (сайт)** = (ціна на сайті × к-сть) × (1 + %комісії / 100) + фікса USD (конвертована)\n"
            "- **Реальна ціна (Steam)** = (ціна на ТМ × к-сть) / (1 + %плюса / 100)\n"
            "- **Економія** = модуль різниці між двома реальними цінами (у базовій валюті за крос-курсів).",
        # --- Режим 3 ---
        "Withdrawal (Cashout)": "Виведення коштів (Cashout)",
        "Steam balance cashout calculator": "Калькулятор виведення балансу Steam",
        "We calculate how much real money you receive by buying a skin on the Steam Market, "
        "selling it on a third-party site, and withdrawing the proceeds.":
            "Рахуємо, скільки реальних грошей ви отримаєте, купивши скін на Торговому майданчику Steam, "
            "продавши його на сторонньому сайті та вивівши виручку.",
        "Purchase (Steam Market)": "Купівля (ТМ Steam)",
        "Steam purchase price": "Ціна купівлі в Steam",
        "Withdrawal (third-party site)": "Виведення (сторонній сайт)",
        "Site sell price": "Ціна продажу на сайті",
        "Sales fee (%)": "Комісія сайту за продаж (%)",
        "Withdrawal fee (%)": "Комісія за виведення (%)",
        "Withdrawal fixed fee (USD)": "Фіксована комісія за виведення (USD)",
        "Total Steam spent": "Витрачено Steam балансу",
        "Real money received": "Отримано реальних грошей",
        "Cashout ratio": "Коефіцієнт виведення",
        "Net profit / loss": "Чистий прибуток / збиток",
        "Gross site revenue": "Брудна виручка на сайті",
        "After sales fee": "Після комісії за продаж",
        "Enter prices to see the cashout calculation.":
            "Введіть ціни, щоб побачити розрахунок виведення.",
        "The higher the cashout ratio, the more of your Steam balance reaches your card.":
            "Що вищий коефіцієнт виведення, то більша частина балансу Steam доходить до картки.",
        "MODE3_FORMULAS":
            "- **Витрачено балансу Steam** = ціна купівлі в Steam × к-сть\n"
            "- **Реальні витрати Steam** = витрачено балансу Steam / (1 + % плюса поповнення / 100)  *(лише якщо задано % плюса)*\n"
            "- **Брудна виручка на сайті** = ціна продажу на сайті × к-сть\n"
            "- **Після комісії за продаж** = брудна виручка × (1 − %комісії продажу / 100)\n"
            "- **Отримано реальних грошей** = після комісії за продаж × (1 − %комісії виведення / 100) − фікса USD (конвертована)\n"
            "- **Коефіцієнт виведення** = отримано реальних грошей / Реальні витрати Steam × 100  *(або / баланс Steam, якщо плюс не задано)*\n"
            "- **Чистий прибуток / збиток** = отримано реальних грошей − Реальні витрати Steam\n\n"
            "У розширеному режимі сторона сайту та сторона Steam рахуються кожна у своїй валюті, "
            "а потім приводяться до базової валюти (картки) за вашими курсами.",
        "Real Steam cost (with top-up)": "Реальні витрати Steam (з плюсом)",
        "Effective cashout ratio": "Ефективний коефіцієнт виведення",
        "Top-up profit factored in. Ratio > 100% means you profit even after cashing out.":
            "Враховано плюс поповнення. Коефіцієнт > 100% означає, що ви в плюсі навіть після виведення.",
        # --- Режим 4 ---
        "Where to sell more profitably?": "Де продати вигідніше?",
        "We compare selling a skin on a third-party site (then topping up Steam at a profit) "
        "versus selling it directly on the Steam Market.":
            "Порівнюємо продаж скіна на сторонньому сайті (з подальшим поповненням Steam у плюс) "
            "та продаж напряму через Steam Market.",
        "Sell on third-party site → top up Steam": "Продати на сторонньому сайті → поповнити Steam",
        "Sell on Steam Market directly": "Продати на Steam Market напряму",
        "Steam sell price (buyer pays)": "Ціна на Steam Market (платить покупець)",
        "Steam Market fee (%)": "Комісія Steam Market (%)",
        "Steam balance via site (with top-up)": "Баланс Steam через сайт (з поповненням)",
        "Steam balance via Steam Market": "Баланс Steam через Steam Market",
        "Real money from site: {amount}": "Реальних грошей з сайту: {amount}",
        "Selling via site and topping up Steam is more profitable. "
        "Extra Steam balance: {amount}.":
            "Вигідніше продати через сайт і поповнити Steam. "
            "Додатковий баланс: {amount}.",
        "Selling on Steam Market is more profitable. "
        "Extra Steam balance: {amount}.":
            "Вигідніше продати на Steam Market напряму. "
            "Додатковий баланс: {amount}.",
        "Both options yield the same Steam balance.":
            "Обидва варіанти дають однаковий Steam-баланс.",
        "MODE4_FORMULAS":
            "**Варіант А — Продати на сайті, поповнити Steam:**\n"
            "- Брудна виручка = ціна продажу на сайті × к-сть\n"
            "- Після комісії продажу = брудна виручка × (1 − %комісії продажу / 100)\n"
            "- Реальні гроші = після комісії × (1 − %комісії виведення / 100) − фікса\n"
            "- **Баланс Steam (А)** = реальні гроші × (1 + %плюса поповнення / 100)\n\n"
            "**Варіант Б — Продати на Steam Market напряму:**\n"
            "- **Баланс Steam (Б)** = виручка продавця з урахуванням комісії Steam × к-сть\n"
            "  *(Steam утримує ~15% для CS2: 10% видавнича + 5% майданчик; "
            "  для цілочисельних валют застосовується точна модель округлення Valve)*\n\n"
            "**Порівняння:** рекомендується варіант, що дає більший Steam-баланс.",
        # --- Режим 5 ---
        "Best rarity to buy (collection)": "Найкраща якість для купівлі (колекція)",
        "We rank which rarity in a collection is the best buy, based on the price ratio "
        "between adjacent rarities (10 lower-rarity items trade up into 1 higher-rarity item).":
            "Оцінюємо, яку якість у колекції вигідніше купувати, за співвідношенням цін сусідніх "
            "якостей (10 предметів нижчої якості через контракт обміну дають 1 предмет вище).",
        "Prices in one currency; use a single float tier (preferably the lowest). Leave a rarity at 0 to exclude it from the collection.":
            "Ціни в одній валюті; використовуй одну якість флоата (бажано найнижчу). Залиш 0, щоб виключити рідкість з колекції.",
        "Rarity prices": "Ціни за якостями",
        "Price (0 = not in collection)": "Ціна (0 = немає в колекції)",
        "Rarity": "Якість",
        "Price": "Ціна",
        "Ratio": "Співвідношення",
        "Rank": "Ранг",
        "Comment": "Коментар",
        "Enter at least two rarity prices to compare.":
            "Введи ціни мінімум для двох якостей для порівняння.",
        "Best buy: {rarity} (rank {rank})": "Найвигідніше купувати: {rarity} (ранг {rank})",
        "Ranking (higher = better buy)": "Рейтинг (вище — вигідніше купувати)",
        "{n}× this rarity = one rarity above": "{n}× цієї якості = одна якість вище",
        "this rarity = {n}× the rarity below": "ця якість = {n}× якості нижче",
        "rarity_consumer": "Ширвжиток (сіре)",
        "rarity_industrial": "Промислове (блакитне)",
        "rarity_milspec": "Армійське (синє)",
        "rarity_restricted": "Заборонене (фіолетове)",
        "rarity_classified": "Засекречене (рожеве)",
        "rarity_covert": "Таємне (червоне)",
        "rk_over": "переоцінено — невигідно купувати",
        "rk_over_slight": "трохи переоцінено",
        "rk_normal": "нормальне середнє співвідношення",
        "rk_good": "трохи краще за середнє",
        "rk_under": "недооцінено — вигідно купувати",
        "rk_under_susp": "недооцінено, підозріло — перевір ліквідність",
        "rk_exp": "дороге відносно якості нижче",
        "rk_exp_slight": "дорогувато",
        "rk_cheap": "дешево відносно якості нижче — вигідно",
        "rk_cheap_susp": "дуже дешево, підозріло — перевір ліквідність",
        "rk_over_strong": "сильно переоцінено — невигідно купувати",
        "rk_below": "нижче середнього",
        "Site": "Сайт",
        "Steam Market": "Steam Market",
        "The price shown to buyers on the Steam Market.": "Ціна, яку бачать покупці на Торговій площадці Steam.",
        "How the ranking works": "Як рахується ранг",
        "rk_exp_strong": "дуже дороге відносно якості нижче",
        "cleanliness": "чистота",
        "floor (cheapest)": "підлога (найдешевший)",
        "cleanliness premium": "націнка за чистоту",
        "unit": "од.",
        "pricier and not cleaner — not worth it": "дорожче і не чистіше — невигідно",
        "score": "бал",
        "cleanliness premium: n/a (add a 2nd float to compare)": "націнка за чистоту: н/д (додайте 2-й флоат для порівняння)",
        "cleanliness premium: n/a (the cheapest is already the cleanest)": "націнка за чистоту: н/д (найдешевший уже найчистіший)",
        "cheapest cleanliness {b}/{u} ({skin}) · avg {a}/{u}": "найдешевше чистота {b}/{u} ({skin}) · у середньому {a}/{u}",
        "cleanliness premium: n/a (skins need ≥2 floats)": "націнка за чистоту: н/д (потрібно ≥2 флоата у скінів)",
        "Score 0–100 is experimental (relative within a skin, 50/50 clean/cheap).": "Бал 0–100 — експериментальний (відносний усередині скіна, 50/50 чистота/ціна).",
        'Advanced float analysis (float & cut)':
            'Просунутий аналіз флоата (флоат і різання)',
        'Advanced mode: for each rarity add skins with their float cap and one or more quality records. The ranking then also accounts for float economics — the contract value of cleanliness. (The single-currency note still applies.)':
            'Просунутий режим: для кожної рідкості додай скіни з їх різанням флоата та одним чи кількома записами якостей. Тоді ранжування враховує й економіку флоата — контрактну цінність чистоти. (Умова однієї валюти досі діє.)',
        'Default float = midpoint of (wear ∩ cap), instead of worst-in-wear':
            'Дефолтний флоат = середина (якість ∩ різання), замість «найгіршого в якості»',
        "m5a_midpoint_label":
            "Брати СЕРЕДИНУ флоата якості замість «найгіршого» (для записів без точного флоата)",
        "m5a_midpoint_help":
            "Впливає лише на записи якості, де НЕ задано точний флоат. ВИМК (за замовчуванням): флоат береться "
            "як «найгірший» (найбрудніший) край перетину (якість ∩ різання) — консервативна оцінка, такий "
            "предмет найпростіше отримати. УВІМК: флоат береться як СЕРЕДИНА перетину (якість ∩ різання) — "
            "усереднена оцінка. На записи з уведеним точним флоатом галочка не впливає.",
        "m5_tp_help":
            "Коли увімкнено, поряд із кожним предметом можна ввести ціну зі Steam. Кожна ціна приводиться до "
            "твоєї реальної валюти (через курси та бонус поповнення Steam), і для кожного предмета в ранжуванні "
            "береться ДЕШЕВША з ціни зовнішнього маркету та ціни Steam. Вимк: лише зовнішні ціни, одна валюта.",
        "Account for Steam (TP) prices":
            "Облік цін Steam (ТП)",
        "Steam (TP) pricing settings":
            "Налаштування цін Steam (ТП)",
        "External sites currency":
            "Валюта зовнішніх майданчиків",
        "Steam currency":
            "Валюта Steam",
        "Real currency (you pay in)":
            "Реальна валюта (якою платиш)",
        "Steam top-up bonus %":
            "Бонус поповнення Steam, %",
        "Prices are converted to your real currency; for each item the CHEAPER of market vs Steam is used.":
            "Ціни приводяться до реальної валюти; для кожного предмета береться ДЕШЕВША з маркету та Steam.",
        "Steam price":
            "Ціна Steam",
        "price from Steam":
            "ціна зі Steam",
        "price from market":
            "ціна із зовнішнього маркету",
        'Skins in this rarity':
            'Скінів у цій рідкості',
        'Skin':
            'Скін',
        'Skin name (optional)':
            "Назва скіна (необов'язково)",
        'Float cap min':
            'Різання флоата, min',
        'Float cap max':
            'Різання флоата, max',
        'Invalid cap: need 0 ≤ min < max ≤ 1.':
            'Некоректне різання: потрібно 0 ≤ min < max ≤ 1.',
        'Quality records':
            'Записів якості',
        'Quality (wear)':
            'Якість (знос)',
        'Exact float':
            'Точний флоат',
        'Float value':
            'Значення флоата',
        'contract float':
            'контрактний флоат',
        'float-value':
            'вартість за флоат',
        'Auto (cheapest by price)':
            'Авто (найдешевша за ціною)',
        'Record':
            'Запис',
        'Record used in the rarity aggregate':
            'Запис для агрегату рідкості',
        'Add at least two rarities, each with a valid priced skin, to compare.':
            'Додай щонайменше дві рідкості з валідним скіном із ціною для порівняння.',
        'Ranking with float economics (higher = better buy)':
            'Ранжування з урахуванням економіки флоата (вище = вигідніше)',
        'Float bonus':
            'Флоат-бонус',
        'Trade-up & float details':
            'Контракти та деталі флоата',
        'craft up':
            'крафт вгору',
        "News":
            "Новини",
        "Show all news":
            "Показати всі новини",
        "m5a_cands_title":
            "Кандидати-філери: що купувати ({n})",
        "m5a_cands_hint_cheapest":
            "Відсортовано за ціною — спочатку найдешевші. Контракт бере верхній рядок (✅). "
            "Кожен рядок = контракт із 10 копій саме цього запису. Повернення = вихід ÷ вхід: "
            "100% = вкладене повернулося (у нуль), 105% = +5% прибутку, нижче 100% = збиток.",
        "m5a_cands_hint_best":
            "Відсортовано за поверненням — спочатку найкраще співвідношення ціна/якість. Контракт бере "
            "верхній рядок (✅). Кожен рядок = контракт із 10 копій цього скіна в цій якості. "
            "Повернення = вихід ÷ вхід: 100% = у нуль, 105% = +5% прибутку, нижче 100% = збиток.",
        "m5a_col_skin":
            "Скін",
        "m5a_col_wear":
            "Якість",
        "m5a_col_float":
            "Флоат",
        "m5a_col_cost":
            "Вхід ×10",
        "m5a_col_eout":
            "Сер. вихід",
        "Collection template":
            "Шаблон колекції",
        "Load template":
            "Завантажити шаблон",
        "m5a_tpl_caption":
            "Завантаження заповнює всі поля цієї колекції (усе редаговане). Воно ПЕРЕЗАПИСУЄ "
            "поточний ввід просунутого режиму. Ціни — орієнтовні стартові "
            "значення (USD); перед використанням онови їх під поточний ринок.",
        "m5a_tpl_loaded_msg":
            "Завантажено шаблон: {name} · онови ціни під поточний ринок.",
        "m5a_craft_line":
            "(філер: {skin} · {wear} · флоат {f}, w={w} · {price} ×{n} = {cost} → "
            "сер. вихід {eout} · повернення {roi}% (прибуток {profit}%))",
        "m5a_col_roi":
            "Повернення (ROI)",
        "m5a_cmode_label":
            "Розрахунок контрактів (філери)",
        "m5a_cmode_cheapest":
            "За найдешевшими — контракт із найдешевшого запису з його реальним флоатом",
        "m5a_cmode_best":
            "За найкращим співвідношенням ціна/якість — перебираються всі записи, береться філер із найкращим ROI",
        "m5a_cmode_help":
            "Обидва режими рахують контракт на твоїх РЕАЛЬНИХ записах (10 копій одного філера). "
            "Очікуваний вихід — середнє за ВСІМА скінами наступної рідкості, кожен оцінений на "
            "флоаті, який дасть контракт від реального флоата філера (1 скін = 1 результат). "
            "«За найдешевшими» відповідає: що дає класичний крафт із найдешевших філерів? "
            "«За найкращим співвідношенням» додатково пробує кожен уведений запис як філер "
            "і бере запис із найкращим поверненням — найкращий варіант для крафта-закупівлі. Повернення = "
            "вихід ÷ вхід (100% = у нуль, 105% = +5% прибутку).",
        'clean pays off — worth buying low-float fillers':
            'чистота окупається — є сенс брати низькофлоатні філери',
        "don't overpay for clean — a dirty filler gives the same output":
            'не переплачуй за чистоту — брудний філер дає той самий вихід',
        'trade-up into the next rarity is unprofitable':
            'контракт у наступну рідкість невигідний',
        'MODE5_ADV_NOTE':
            'Просунутий режим: ціна рідкості для рангу — її НАЙДЕШЕВШИЙ запис (ціна філерів). Флоат-цінність — це націнка за чистоту ВСЕРЕДИНІ скіна: підлога = найдешевший запис скіна, і для запису чистішого підлоги націнка = (ціна − ціна підлоги)/(чистота − чистота підлоги) = $ за дод. одиницю чистоти (потрібно ≥2 флоата; нижче = дешевша чистота = менша переплата). Один запис → н/д. Флоат-бонус береться з ROI контракту в наступну рідкість (10→1), порахованого на РЕАЛЬНИХ записах за обраним режимом: «за найдешевшими» — найдешевший запис із його реальним флоатом, «за найкращим співвідношенням ціна/якість» — перебираються всі записи й береться філер із найкращим поверненням; вихід усереднюється за ВСІМА скінами наступної рідкості на отриманому флоаті. Повернення показується як вихід ÷ вхід (100% = у нуль, 105% = +5% прибутку); ранговий бонус дається лише вище 100%. Контрактна вага флоата w = (флоат − cap_min)/(cap_max − cap_min). У просунутому режимі галочки «красиве/ліквідне» немає — її роль виконує флоат-бонус із контрактного ROI (опис галочки вище стосується простого режиму).',
        "Enter the price you consider fair for each rarity. Tick the box if you "
        "find that rarity's skins beautiful or especially liquid.":
            "Вкажи ціну, яку вважаєш справедливою для кожної якості. Постав галочку, "
            "якщо скіни цієї якості гарні або особливо ліквідні.",
        "Beautiful / liquid?": "Гарне / ліквідне?",
        "rk_top_note": "(Найвища якість: її не можна скрафтити вище, а пропозиція лише "
                       "зростає, тому до рангу застосовано штраф.)",
        "MODE5_FORMULAS":
            "Для кожної якості, крім найвищої, співвідношення = **ціна якості вище / ціна цієї "
            "якості** — скільки штук цієї якості за ціною дорівнюють одному предмету якості вище. "
            "Контракт перетворює 10 предметів однієї якості на 1 предмет наступної, тому "
            "**більше співвідношення** означає, що якість дешева відносно того, чим стає "
            "→ вигідніше купувати:\n\n"
            "- ≤ 2 → **F** (сильно переоцінено)\n"
            "- 2–3.5 → **E** (переоцінено)\n"
            "- 3.5–4.5 → **D** (нижче середнього)\n"
            "- 4.5–5.5 → **C** (середнє)\n"
            "- 5.5–6.5 → **B**\n"
            "- 6.5–8 → **A** (ця якість недооцінена)\n"
            "- 8–10 → **A+**\n"
            "- > 10 → **A++** (незвично — 10 цих крафтять 1 вище; перевір ліквідність верхньої)\n\n"
            "**Найвища** якість рахується навпаки (вище неї нічого немає): її співвідношення до якості "
            "нижче реверсується, потім застосовується штраф (−2, якщо потрапляє на A і вище, −1 для "
            "B/C/D/E), бо найвищу якість не можна скрафтити далі, а її пропозиція лише зростає.\n\n"
            "**Краса / ліквідність:** галочка додає +0.5 до співвідношення цієї якості та якості на 1 "
            "нижче, +0.25 до якості на 2 нижче (на 3 нижче — нічого); на якість вище позначеної бонус "
            "не впливає. Для найвищої якості бонус навпаки покращує її реверс-оцінку.\n\n"
            "**Позиційні корективи (лише ранг):** незалежно від краси, ранг додатково зсувається за позицією "
            "серед ВВЕДЕНИХ рідкостей — це впливає ЛИШЕ на ранг, не на показаний ratio і не на сирі цифри/"
            "порівняння. Найнижчій +0.25; найвищій — додатковий −0.5 (понад її реверс-штраф); передостанній "
            "−0.5, перед-передостанній −0.35, перед-перед-передостанній −0.25. У маленькій колекції одна "
            "рідкість може бути і «найнижчою», і «k-ю згори» — зсуви тоді просто додаються.\n\n"
            "Порожні ціни/0 пропускаються, тож колекції без деяких якостей враховуються. "
            "Це евристика за введеними цінами, а не фінансова порада.",
    },
}


# ===========================================================================
# МАТЕМАТИЧЕСКИЕ ФУНКЦИИ (чистые, без Streamlit — удобно тестировать)
# ===========================================================================

def calculate_real_spent(site_price, fee_percent=0.0, fixed_fee=0.0, quantity=1):
    """Реальные затраты на покупку предмета(ов) на стороннем сайте.

    Процентная комиссия начисляется на всю сумму заказа (цена × количество),
    фиксированная комиссия добавляется один раз за транзакцию:

        real_spent = (site_price * quantity) * (1 + fee_percent / 100) + fixed_fee

    Аргумент fixed_fee должен быть уже приведён к валюте расчёта; конвертация
    фиксированной комиссии из долларов выполняется в resolve_fixed_fee_in_target.

    Все числовые аргументы приводятся к неотрицательным значениям.
    """
    site_price = max(0.0, float(site_price))
    fee_percent = max(0.0, float(fee_percent))
    fixed_fee = max(0.0, float(fixed_fee))
    quantity = max(1, int(quantity))
    base = site_price * quantity
    return base * (1.0 + fee_percent / 100.0) + fixed_fee


def _split_total_fee(total_fee_percent):
    """Делит общий процент комиссии Steam на издательскую и площадочную части.

    Steam удерживает две отдельные комиссии: издательскую (для CS2 — 10%) и
    комиссию площадки (5%), в сумме 15%. Поле «комиссия %» в интерфейсе задаёт
    суммарный процент; здесь он распределяется пропорционально структуре 10:5,
    поэтому значение по умолчанию 15% даёт ровно (10.0, 5.0).

    Возвращает (cs2_fee_pct, steam_fee_pct).
    """
    total = max(0.0, float(total_fee_percent))
    default_total = STEAM_CS2_FEE_PERCENT + STEAM_STEAM_FEE_PERCENT
    if default_total <= 0:
        return 0.0, 0.0
    cs2 = total * (STEAM_CS2_FEE_PERCENT / default_total)
    steam = total * (STEAM_STEAM_FEE_PERCENT / default_total)
    return cs2, steam


def round_half_up_int(value):
    """Округление цены к ближайшему целому по правилу «half up» (0.5 -> 1).

    Встроенная функция round() в Python использует банковское округление
    (round half to even): round(14.5) == 14, round(15.5) == 16. Решатель цен
    в calculate_exact_steam_revenue нормализует ввод как int(price + 0.5), то
    есть «арифметическим» округлением вверх на .5. Эта функция повторяет ту же
    логику, чтобы интерфейс и расчёт не расходились на 1 единицу для
    целочисленных валют (например, при вводе 14.5 или 16.5 с шагом 0.5).

    Цены всегда неотрицательны (min_value=0.0 во всех полях), поэтому простого
    int(value + 0.5) достаточно; на всякий случай вход приводится к >= 0.
    """
    return int(max(0.0, float(value)) + 0.5)


def calculate_exact_steam_revenue(steam_buyer_price, is_integer_currency,
                                  cs2_fee_pct=STEAM_CS2_FEE_PERCENT,
                                  steam_fee_pct=STEAM_STEAM_FEE_PERCENT):
    """Точная модель комиссий Steam: из цены покупателя — выручка продавца.

    Для ЦЕЛОЧИСЛЕННЫХ валют (грн, ¥, ₩, CLP, IDR) модель выверена по реальным
    данным Торговой площадки Steam. Комиссия считается от суммы продавца, каждая
    часть округляется к БЛИЖАЙШЕМУ (round half up) с минимумом в одну единицу:

        fee_cs    = max(1, round(seller * cs2_fee_pct   / 100))
        fee_steam = max(1, round(seller * steam_fee_pct / 100))
        buyer     = seller + fee_cs + fee_steam

    Сумма продавца для запрошенной цены — это МАКСИМАЛЬНЫЙ seller, при котором
    итоговая цена покупателя не превышает запрошенную. Если ровно такая цена
    недостижима (на площадке лот можно выставить только за достижимую цену),
    берётся ближайшая меньшая достижимая.

    Единица расчёта зависит от валюты:
        * целочисленные валюты — целые единицы;
        * остальные (USD, EUR, RUB, …) — центы/копейки (цена × 100, затем / 100).

    Возвращает кортеж в ИСХОДНОМ масштабе валюты:
        (seller_revenue, valid_buyer_price, fee_cs, fee_steam).
    Для цены меньше одной минимальной единицы — нули.

    Деление на ноль исключено: масштаб равен 1 или 100, а делитель оценки
    защищён проверкой суммарного процента.
    """
    price = max(0.0, float(steam_buyer_price))
    scale = 1 if is_integer_currency else 100
    # Перевод цены покупателя в целые единицы/центы (округление ввода к ближайшему).
    desired = int(price * scale + 0.5)
    if desired < 1:
        return 0.0, 0.0, 0.0, 0.0

    cs2_fee_pct = max(0.0, float(cs2_fee_pct))
    steam_fee_pct = max(0.0, float(steam_fee_pct))

    def _fees(seller_units):
        """Комиссии Steam в наименьшей единице валюты.

        Для ЦЕЛОЧИСЛЕННЫХ валют (грн, ¥, ₩ и т.п.) комиссия считается от суммы
        продавца с округлением каждой части к БЛИЖАЙШЕМУ (round half up) и
        минимумом в одну единицу — это поведение Valve, выверенное по реальным
        данным Торговой площадки. Для дробных валют (центы) сохраняется прежний
        расчёт через отбрасывание дробной части (floor) в наименьшей единице.

        Исключение: если соответствующий процент комиссии равен ровно 0, то и
        удержание равно 0 (минимум в 1 единицу применяется только к реальной,
        ненулевой комиссии — у Steam она всегда 10% + 5%).
        """
        if is_integer_currency:
            fc = 0 if cs2_fee_pct == 0 else max(1, int(seller_units * cs2_fee_pct / 100.0 + 0.5))
            fs = 0 if steam_fee_pct == 0 else max(1, int(seller_units * steam_fee_pct / 100.0 + 0.5))
        else:
            fc = 0 if cs2_fee_pct == 0 else max(1, int(seller_units * cs2_fee_pct / 100.0 + 1e-9))
            fs = 0 if steam_fee_pct == 0 else max(1, int(seller_units * steam_fee_pct / 100.0 + 1e-9))
        return fc, fs

    total_pct = cs2_fee_pct + steam_fee_pct
    growth = 1.0 + total_pct / 100.0
    estimate = int(desired / growth + 0.5) if growth > 0 else desired

    # Сумма продавца = МАКСИМАЛЬНЫЙ seller, при котором цена покупателя
    # (seller + комиссии) не превышает запрошенную цену. Если ровно эта цена
    # недостижима, берётся ближайшая меньшая достижимая (как на самой площадке:
    # выставить лот можно только за достижимую цену).
    best_seller = 0
    best_buyer = 0
    best_fc = 0
    best_fs = 0
    for seller in range(max(1, estimate - 8), estimate + 9):
        fc, fs = _fees(seller)
        buyer = seller + fc + fs
        if buyer <= desired and seller > best_seller:
            best_seller, best_buyer, best_fc, best_fs = seller, buyer, fc, fs

    if best_seller == 0:
        return 0.0, 0.0, 0.0, 0.0
    return (best_seller / scale, best_buyer / scale,
            best_fc / scale, best_fs / scale)


def calculate_steam_received(steam_price, steam_fee_percent=DEFAULT_STEAM_FEE_PERCENT):
    """Выручка продавца на Steam для валют с дробной частью (в центах/копейках).

    Тонкая обёртка над calculate_exact_steam_revenue: суммарный процент делится
    на издательскую и площадочную части, расчёт ведётся в центах по точной
    модели Steam (floor с минимумом в одну единицу), что устойчивее простого
    деления цены на коэффициент.

    Пример: при комиссии 15% цена покупателя 3299.00 даёт продавцу 2868.70
    (удержано 286.87 + 143.43).
    """
    cs2_fee_pct, steam_fee_pct = _split_total_fee(steam_fee_percent)
    seller_revenue, _, _, _ = calculate_exact_steam_revenue(
        steam_price, is_integer_currency=False,
        cs2_fee_pct=cs2_fee_pct, steam_fee_pct=steam_fee_pct)
    return seller_revenue


def calculate_profit(real_spent, steam_received):
    """Чистый плюс и процент плюса.

    Возвращает (profit_amount, profit_percent).
        profit_amount  = steam_received - real_spent
        profit_percent = (profit_amount / real_spent) * 100

    Защита от деления на ноль: при нулевых затратах процент = 0.
    """
    profit_amount = float(steam_received) - float(real_spent)
    profit_percent = (profit_amount / real_spent) * 100.0 if real_spent > 0 else 0.0
    return profit_amount, profit_percent


def calculate_profit_cross_currency(site_real_cost, steam_received,
                                    rate_site_to_spent, rate_steam_to_spent):
    """Профит в продвинутом мультивалютном режиме.

    Приводит затраты на сайте и выручку Steam к ВАЛЮТЕ ЗАТРАТ (базовая валюта),
    после чего считает плюс уже в ней.

        rate_site_to_spent  — сколько единиц валюты затрат в 1 единице валюты сайта;
        rate_steam_to_spent — сколько единиц валюты затрат в 1 единице валюты Steam.

    Возвращает (real_spent_base, steam_received_base, profit_amount, profit_percent).
    """
    real_spent_base = max(0.0, float(site_real_cost)) * max(0.0, float(rate_site_to_spent))
    steam_received_base = max(0.0, float(steam_received)) * max(0.0, float(rate_steam_to_spent))
    profit_amount, profit_percent = calculate_profit(real_spent_base, steam_received_base)
    return real_spent_base, steam_received_base, profit_amount, profit_percent


def calculate_steam_real_cost(steam_price, deposit_profit_percent):
    """Реальная стоимость покупки скина(ов) в Steam с учётом плюса пополнения (Режим 2).

        real_cost = steam_price / (1 + deposit_profit_percent / 100)

    Пример: при плюсе 50% скин за 15 на ТП стоит 15 / 1.5 = 10 реальных денег.
    (steam_price может быть уже умножена на количество — функция этого не знает.)

    Защита: при делителе <= 0 (плюс -100% и ниже — полная потеря пополнения)
    возвращается 0.0 как безопасный fallback. В интерфейсе такой ввод недостижим
    (минимум -99.9%), поэтому ветка служит лишь страховкой от деления на ноль.
    """
    steam_price = max(0.0, float(steam_price))
    divisor = 1.0 + (float(deposit_profit_percent) / 100.0)
    # Защита от деления на ноль: при -100% (и ниже) делитель <= 0 — возвращаем 0.
    if divisor <= 0:
        return 0.0
    return steam_price / divisor


def effective_real_value(site_price, steam_price, rate_site_to_real,
                         rate_steam_to_real, topup_pct):
    """Эффективная стоимость предмета в РЕАЛЬНОЙ валюте — дешевле из внешней и Steam.

    Внешняя -> реальная: site_price × rate_site_to_real (внешние цены уже в реальных
    деньгах; курс лишь приводит к реальной валюте). Steam -> реальная:
    (steam_price / (1 + topup%/100)) × rate_steam_to_real (учёт бонуса пополнения).
    Если задана только одна цена — берётся она; если обе — берётся ДЕШЕВЛЕ.
    Возвращает (value, source): source ∈ {'market', 'steam', None}; None — ни одной
    цены (предмет исключается). Курсы/деления защищены от мусора.
    """
    site_real = None
    if site_price and float(site_price) > 0:
        site_real = float(site_price) * max(0.0, float(rate_site_to_real))
    steam_real = None
    if steam_price and float(steam_price) > 0:
        sr = calculate_steam_real_cost(steam_price, topup_pct) * max(0.0, float(rate_steam_to_real))
        # Отбрасываем вырожденный ноль (бонус <= -100%% — срабатывает защита деления
        # в calculate_steam_real_cost — или курс 0): такой Steam-вариант недоступен,
        # поэтому берём внешнюю цену, а не «бесплатный» Steam.
        if sr > 0:
            steam_real = sr
    if site_real is None and steam_real is None:
        return 0.0, None
    if steam_real is None:
        return site_real, "market"
    if site_real is None:
        return steam_real, "steam"
    return (steam_real, "steam") if steam_real < site_real else (site_real, "market")


def compare_purchase_options(site_real_cost, steam_real_cost):
    """Сравнение реальных затрат двух вариантов покупки (Режим 2).

    Возвращает словарь: recommendation ('steam'|'site'|'equal'),
    savings (абсолютная экономия) и обе исходные цены.
    """
    difference = abs(float(site_real_cost) - float(steam_real_cost))
    if steam_real_cost < site_real_cost:
        recommendation = "steam"
    elif site_real_cost < steam_real_cost:
        recommendation = "site"
    else:
        recommendation = "equal"
    return {
        "recommendation": recommendation,
        "savings": difference,
        "site_real_cost": site_real_cost,
        "steam_real_cost": steam_real_cost,
    }


def calculate_cashout(steam_price, site_sell_price, quantity=1,
                      sales_fee_percent=0.0, withdrawal_fee_percent=0.0,
                      withdrawal_fixed_fee=0.0):
    """Вывод баланса Steam в реальные деньги через продажу предмета на сайте.

    Модель денежного потока (все суммы — в одной валюте; конвертация, если
    нужна, выполняется вызывающим кодом до и после этой функции):

        steam_spent       = steam_price * quantity            # списано с баланса Steam
        gross_revenue     = site_sell_price * quantity        # цена выставления на сайте
        after_sales_fee   = gross_revenue * (1 - sales_fee% / 100)
        real_received     = after_sales_fee * (1 - withdrawal_fee% / 100)
                            - withdrawal_fixed_fee             # минус фикса за вывод

    Аргумент withdrawal_fixed_fee должен быть уже приведён к валюте сайта.
    Отрицательная чистая выручка обнуляется (вывести меньше нуля нельзя).

    Возвращает словарь:
        steam_spent    — затраты баланса Steam;
        gross_revenue  — выручка до комиссий;
        after_sales    — выручка после комиссии за продажу;
        real_received  — сумма к получению на карту/крипту;
        net_profit     — real_received - steam_spent (обычно отрицательна);
        ratio_percent  — (real_received / steam_spent) * 100; 0 при нулевых затратах.

    Все числовые аргументы приводятся к неотрицательным значениям; проценты
    выше 100 дают нулевую (а не отрицательную) выручку на соответствующем шаге.
    """
    steam_price = max(0.0, float(steam_price))
    site_sell_price = max(0.0, float(site_sell_price))
    quantity = max(1, int(quantity))
    sales_fee_percent = max(0.0, float(sales_fee_percent))
    withdrawal_fee_percent = max(0.0, float(withdrawal_fee_percent))
    withdrawal_fixed_fee = max(0.0, float(withdrawal_fixed_fee))

    steam_spent = steam_price * quantity
    gross_revenue = site_sell_price * quantity
    after_sales = gross_revenue * max(0.0, 1.0 - sales_fee_percent / 100.0)
    real_received = after_sales * max(0.0, 1.0 - withdrawal_fee_percent / 100.0)
    real_received = max(0.0, real_received - withdrawal_fixed_fee)

    net_profit = real_received - steam_spent
    ratio_percent = (real_received / steam_spent) * 100.0 if steam_spent > 0 else 0.0

    return {
        "steam_spent": steam_spent,
        "gross_revenue": gross_revenue,
        "after_sales": after_sales,
        "real_received": real_received,
        "net_profit": net_profit,
        "ratio_percent": ratio_percent,
    }


def get_valid_steam_price(desired_buyer_price,
                          cs2_fee_pct=STEAM_CS2_FEE_PERCENT,
                          steam_fee_pct=STEAM_STEAM_FEE_PERCENT):
    """Ближайшая достижимая цена покупателя для целочисленных валют (например, ₴).

    Тонкая обёртка над calculate_exact_steam_revenue для валют без дробной части.
    Возвращает кортеж (buyer_pays, seller_receive) целыми числами; для цены
    меньше одной единицы — (0, 0).
    """
    seller, buyer, _, _ = calculate_exact_steam_revenue(
        desired_buyer_price, is_integer_currency=True,
        cs2_fee_pct=cs2_fee_pct, steam_fee_pct=steam_fee_pct)
    return int(round(buyer)), int(round(seller))


def _esc(text):
    """Экранирование пользовательского текста перед вставкой в HTML.

    Имена скинов вводит пользователь, а таблицы/детали рендерятся с
    unsafe_allow_html=True — без экранирования имя вида '<img src=x onerror=...>'
    исполнилось бы как разметка/скрипт. Здесь всё превращается в безопасный текст.
    """
    return html.escape(str(text), quote=True)


def is_integer_currency(code):
    """True, если валюта считается без дробной части (см. INTEGER_CURRENCIES)."""
    if code is None:
        return False
    token = str(code).strip().lower()
    known = {c.lower() for c in INTEGER_CURRENCIES} | _INTEGER_CURRENCY_ALIASES
    return token in known


def is_usd_currency(code):
    """True, если валюта (символ/код) означает доллар США."""
    if code is None:
        return False
    return str(code).strip().lower() in USD_CODES


# ===========================================================================
# ВСПОМОГАТЕЛЬНЫЕ UI-ФУНКЦИИ
# ===========================================================================

def format_currency(value, currency=DEFAULT_CURRENCY, decimals=2):
    """Форматирует число как денежную сумму: '1 234.56 $' или '16 ₴' (decimals=0)."""
    return f"{value:,.{decimals}f} {currency}"


def default_usd_rate(currency_symbol):
    """Грубое значение по умолчанию «сколько валюты за 1 USD» (редактируемое)."""
    return USD_RATE_DEFAULTS.get(currency_symbol, 1.0)


def render_fee_template_controls(key_prefix):
    """Селектор профиля комиссии и флажок учёта комиссии.

    Размещается вне st.form, чтобы смена профиля немедленно перерисовывала
    зависимые поля ввода. Возвращает (template_name, enable_fees).
    """
    template_name = st.selectbox(
        _("Fee template"),
        options=list(DEPOSIT_FEE_TEMPLATES.keys()),
        format_func=_,  # переводит только "Manual input"; бренды — как есть
        key=f"{key_prefix}_template",
        help=_("Presets for popular sites. Manual input is also available."),
    )
    template = DEPOSIT_FEE_TEMPLATES[template_name]
    enable_fees = st.checkbox(
        _("Account for top-up fee"),
        value=template.get("enabled", False),
        key=f"{key_prefix}_enable",
    )
    return template_name, enable_fees


def render_fee_value_inputs(key_prefix, template_name, enable_fees):
    """Поля процентной и фиксированной (USD) комиссии внутри st.form.

    Ключи виджетов содержат имя профиля (template_name): при смене профиля
    Streamlit создаёт новые виджеты и сразу подставляет их значения по
    умолчанию. Без этого приёма значения обновлялись бы только при повторном
    взаимодействии с виджетом.

    Возвращает (fee_percent, fixed_fee_usd).
    """
    template = DEPOSIT_FEE_TEMPLATES[template_name]
    if not enable_fees:
        return 0.0, 0.0

    col_a, col_b = st.columns(2)
    with col_a:
        fee_percent = st.number_input(
            _("Top-up fee (%)"),
            min_value=0.0, value=float(template.get("percent_fee", 0.0)), step=0.5,
            key=f"{key_prefix}_percent_{template_name}",   # ключ зависит от профиля
        )
    with col_b:
        fixed_fee_usd = st.number_input(
            _("Fixed fee (USD)"),
            min_value=0.0, value=float(template.get("fixed_fee_usd", 0.0)), step=0.05,
            key=f"{key_prefix}_fixed_{template_name}",      # ключ зависит от профиля
        )
    return fee_percent, fixed_fee_usd


def resolve_fixed_fee_in_target(key_prefix, fixed_fee_usd, enable_fees, advanced,
                                display_ccy, spent_ccy, site_ccy, rate_site_to_spent):
    """Приводит фиксированную комиссию из долларов к валюте расчёта затрат.

    Рендерится внутри st.form. Целевая валюта зависит от режима:
        * обычный режим      — валюта отображения (display_ccy);
        * мультивалютный     — валюта сайта (site_ccy), которая позже
                               домножается на кросс-курс.

    Определение курса доллара:
        * если целевая валюта уже доллар — конвертация не требуется;
        * в мультивалютном режиме при затратах в долларах курс доллара к валюте
          сайта вычисляется из введённого кросс-курса (1 / rate_site_to_spent);
        * иначе отображается отдельное поле для ввода курса.

    Возвращает фиксированную комиссию в целевой валюте (неотрицательное число).
    """
    if not enable_fees or fixed_fee_usd <= 0:
        return 0.0

    # --- Простой режим: цель = валюта отображения ---
    if not advanced:
        if is_usd_currency(display_ccy):
            return float(fixed_fee_usd)
        usd_to_disp = st.number_input(
            _("Rate: how many {a} in 1 {b}").format(a=display_ccy, b="USD"),
            min_value=0.0, value=default_usd_rate(display_ccy), step=0.5,
            key=f"{key_prefix}_usd_rate",
        )
        return float(fixed_fee_usd) * usd_to_disp

    # --- Продвинутый режим: цель = валюта сайта ---
    if is_usd_currency(site_ccy):
        return float(fixed_fee_usd)

    # Если затраты в USD — выводим курс USD→сайт из существующего кросс-курса.
    if is_usd_currency(spent_ccy) and rate_site_to_spent and rate_site_to_spent > 0:
        usd_to_site = 1.0 / float(rate_site_to_spent)  # 1 USD = 1 / (курс сайта к валюте затрат)
        st.caption(_("USD fixed fee converted via your existing cross-rate."))
        return float(fixed_fee_usd) * usd_to_site

    # Иначе — явное поле курса USD → валюта сайта.
    usd_to_site = st.number_input(
        _("Rate: how many {a} in 1 {b}").format(a=site_ccy or "?", b="USD"),
        min_value=0.0, value=1.0, step=0.5,
        key=f"{key_prefix}_usd_rate",
    )
    return float(fixed_fee_usd) * usd_to_site


def render_cross_currency_selectors(key_prefix):
    """Рендерит ВНЕ формы три текстовых поля валют (карта/сайт/Steam).

    Вне формы — чтобы зависимое поле «курс USD → валюта сайта» появлялось
    динамически. Возвращает (spent_ccy, site_ccy, steam_ccy).
    """
    st.markdown("#### 🌍 " + _("Cross-currency settings"))
    c1, c2, c3 = st.columns(3)
    with c1:
        spent_ccy = st.text_input(_("Spent currency (e.g. UAH)"), value="UAH", key=f"{key_prefix}_spent_ccy")
    with c2:
        site_ccy = st.text_input(_("Site currency (e.g. USD)"), value="USD", key=f"{key_prefix}_site_ccy")
    with c3:
        steam_ccy = st.text_input(_("Steam currency (e.g. EUR)"), value="EUR", key=f"{key_prefix}_steam_ccy")
    return spent_ccy, site_ccy, steam_ccy


def render_cross_currency_rates(key_prefix, spent_ccy, site_ccy, steam_ccy):
    """Рендерит ВНУТРИ формы два курса к валюте затрат.

    Возвращает (rate_site_to_spent, rate_steam_to_spent).
    """
    spent_lbl = spent_ccy or "?"
    r1, r2 = st.columns(2)
    with r1:
        rate_site_to_spent = st.number_input(
            _("Rate: how many {a} in 1 {b}").format(a=spent_lbl, b=site_ccy or "?"),
            min_value=0.0, value=41.0, step=0.5, key=f"{key_prefix}_rate_site",
        )
    with r2:
        rate_steam_to_spent = st.number_input(
            _("Rate: how many {a} in 1 {b}").format(a=spent_lbl, b=steam_ccy or "?"),
            min_value=0.0, value=45.0, step=0.5, key=f"{key_prefix}_rate_steam",
        )
    st.caption(_("Both rates are in the spent currency per 1 unit (a common base)."))
    return rate_site_to_spent, rate_steam_to_spent


def render_m5_tp_block():
    """Блок «учёт цен Steam (ТП)» для Режима 5. Рендерится ВНЕ формы, чтобы тумблер
    сразу показывал/прятал поля. Возвращает dict: enabled, real_ccy, site_ccy,
    steam_ccy, rate_site, rate_steam, topup. Курсы — «реальной валюты за 1 единицу»,
    как в режимах 1–4. По умолчанию курсы 1.0 и бонус 0%% — тогда суммы не меняются."""
    enabled = st.checkbox(_("Account for Steam (TP) prices"), value=False,
                          key="m5_tp_enable", help=_("m5_tp_help"))
    tp = {"enabled": bool(enabled), "real_ccy": "USD", "site_ccy": "USD",
          "steam_ccy": "USD", "rate_site": 1.0, "rate_steam": 1.0, "topup": 0.0}
    if not enabled:
        return tp
    st.markdown("#### 💱 " + _("Steam (TP) pricing settings"))
    c1, c2, c3 = st.columns(3)
    tp["site_ccy"] = c1.text_input(_("External sites currency"), value="USD", key="m5_tp_site_ccy")
    tp["steam_ccy"] = c2.text_input(_("Steam currency"), value="USD", key="m5_tp_steam_ccy")
    tp["real_ccy"] = c3.text_input(_("Real currency (you pay in)"), value="USD", key="m5_tp_real_ccy")
    real_lbl = tp["real_ccy"] or "?"
    r1, r2 = st.columns(2)
    tp["rate_steam"] = r1.number_input(
        _("Rate: how many {a} in 1 {b}").format(a=real_lbl, b=tp["steam_ccy"] or "?"),
        min_value=0.0, value=1.0, step=0.5, key="m5_tp_rate_steam")
    tp["rate_site"] = r2.number_input(
        _("Rate: how many {a} in 1 {b}").format(a=real_lbl, b=tp["site_ccy"] or "?"),
        min_value=0.0, value=1.0, step=0.5, key="m5_tp_rate_site")
    tp["topup"] = st.number_input(_("Steam top-up bonus %"), min_value=-99.9, value=0.0,
                                  step=1.0, key="m5_tp_topup")
    st.caption(_("Prices are converted to your real currency; for each item the CHEAPER of market vs Steam is used."))
    return tp



# ===========================================================================
# НОВОСТНАЯ ЛЕНТА (статичные сообщения — редактируются прямо здесь)
# ===========================================================================
# Сообщения едут вместе с кодом: их увидит каждый, кто запустил ЭТУ версию —
# и на Streamlit Cloud, и локально. Никаких запросов в сеть: новости не могут
# ничего сломать, подтянуть чужой текст или утечь наружу.
#
# Как добавить новость — допиши элемент в начало списка NEWS:
#     {
#         "date": "2026-07-15",          # дата (YYYY-MM-DD) — сохраняется здесь же
#         "kind": "update",              # info | update | fix | warning (иконка)
#         "text": "Короткий текст",      # одна строка на всех языках...
#     },
#     {
#         "date": "2026-07-20",
#         "kind": "info",
#         "text": {                      # ...или отдельный текст на каждый язык
#             "ru": "Текст по-русски",   # (можно заполнить не все — подставится
#             "en": "Text in English",   #  любой доступный)
#             "ua": "Текст українською",
#         },
#     },
# Порядок в списке не важен: лента сама сортируется по дате (новые сверху).
# В тексте работает markdown: **жирный**, *курсив*, [ссылка](https://...).
# HTML намеренно НЕ выполняется (st.markdown без unsafe_allow_html) — безопасно.
NEWS_KIND_ICONS = {"info": "📣", "update": "🆕", "fix": "🔧", "warning": "⚠️"}
NEWS_FRESH_DAYS = 14   # сколько дней новость считается свежей (лента раскрыта сразу)
NEWS_MAX_SHOWN = 5     # сколько последних новостей показывать без «показать все»

NEWS = [
    {
        "date": "2026-07-11",
        "kind": "update",
        "text": {
            "ru": "**Режим 5 (коллекции):** контракты 10→1 считаются на реальных записях — "
                  "два режима подбора филлеров (по самым дешёвым / по лучшему соотношению "
                  "цена-качество), таблица «что покупать» по каждой редкости, учёт цен Steam (ТП) "
                  "и шаблоны коллекций (Cobblestone, Overpass 2024). Доходность теперь "
                  "показывается как полный возврат: 100% = в ноль, 105% = +5% прибыли.",
            "en": "**Mode 5 (collections):** 10→1 trade-ups are computed on your actual records — "
                  "two filler strategies (cheapest / best price-quality), a \"what to buy\" table per "
                  "rarity, optional Steam (TP) prices and ready-made collection templates "
                  "(Cobblestone, Overpass 2024). Returns are now shown in full: 100% = break-even, "
                  "105% = +5% profit.",
            "ua": "**Режим 5 (колекції):** контракти 10→1 рахуються на реальних записах — "
                  "два режими добору філерів (за найдешевшими / за найкращим співвідношенням "
                  "ціна-якість), таблиця «що купувати» для кожної рідкості, облік цін Steam (ТП) "
                  "і шаблони колекцій (Cobblestone, Overpass 2024). Дохідність тепер показується "
                  "як повне повернення: 100% = у нуль, 105% = +5% прибутку.",
        },
    },
]


def _news_date(item):
    """Дата новости как объект date (для сортировки).

    Кривой/пустой формат не роняет приложение: такая новость просто уходит вниз
    ленты (date.min). Новости не должны ломать калькулятор ни при каких данных.
    """
    try:
        return date.fromisoformat(str(item.get("date", "")))
    except (ValueError, TypeError, AttributeError):
        return date.min


def _news_text(item):
    """Текст новости на активном языке.

    text может быть строкой (одна на всех) или словарём {'ru': ..., 'en': ..., 'ua': ...}.
    Если перевода на активный язык нет — подставляется любой доступный (en → ru → ua),
    чтобы сообщение не пропало.
    """
    txt = item.get("text")
    if isinstance(txt, dict):
        for code in (_CURRENT_LANG, "en", "ru", "ua"):
            val = txt.get(code)
            if val:
                return str(val)
        return next((str(v) for v in txt.values() if v), "")
    return str(txt or "")


def news_sorted(items=None):
    """Новости от свежих к старым (устойчиво к некорректным датам)."""
    return sorted(items if items is not None else NEWS,
                  key=_news_date, reverse=True)


def render_news():
    """Лента новостей под заголовком. Пустой список NEWS — блок не рисуется вовсе."""
    items = [it for it in news_sorted() if _news_text(it)]
    if not items:
        return
    newest = _news_date(items[0])
    fresh = (newest != date.min
             and 0 <= (date.today() - newest).days <= NEWS_FRESH_DAYS)
    title = "📣 " + _("News")
    if newest != date.min:
        title += f" · {newest.isoformat()}"
    with st.expander(title, expanded=fresh):
        shown = items[:NEWS_MAX_SHOWN]
        rest = items[NEWS_MAX_SHOWN:]
        if rest and st.checkbox(_("Show all news"), value=False, key="news_show_all"):
            shown = items
        for it in shown:
            icon = NEWS_KIND_ICONS.get(it.get("kind", "info"), "📣")
            day = it.get("date", "")
            # Без unsafe_allow_html: markdown работает, HTML не выполняется.
            st.markdown(f"{icon} **{day}** — {_news_text(it)}")


# ===========================================================================
# РЕЖИМ 1: КАЛЬКУЛЯТОР ПОПОЛНЕНИЯ БАЛАНСА STEAM
# ===========================================================================

def calculate_mode_1(currency, advanced):
    """Интерфейс Режима 1. currency — валюта отображения; advanced — режим кросс-курсов."""
    st.subheader("💰 " + _("Steam balance top-up calculator"))
    st.write(_("We calculate the final profit from buying a skin on a third-party site "
               "and selling it on the Steam Market."))

    # --- ВНЕ формы: шаблон комиссии + (опц.) валюты кросс-курсов ---
    template_name, enable_fees = render_fee_template_controls("m1")
    spent_ccy = site_ccy = steam_ccy = None
    if advanced:
        spent_ccy, site_ccy, steam_ccy = render_cross_currency_selectors("m1")

    # Значения по умолчанию (на случай отключённых блоков).
    fee_percent, fixed_fee_usd = 0.0, 0.0
    rate_site_to_spent, rate_steam_to_spent = 1.0, 1.0

    # Поля и кнопка внутри st.form: пересчёт выполняется только по нажатию.
    with st.form("m1_form"):
        col_buy, col_sell = st.columns(2)

        with col_buy:
            st.markdown("#### 🛒 " + _("Purchase (third-party site)"))
            site_price = st.number_input(
                _("Skin price on third-party site"),
                min_value=0.0, value=10.0, step=0.5, key="m1_site_price",
            )
            quantity = st.number_input(
                _("Quantity"), min_value=1, value=1, step=1, key="m1_qty",
            )
            fee_percent, fixed_fee_usd = render_fee_value_inputs("m1", template_name, enable_fees)

        with col_sell:
            st.markdown("#### 🏪 " + _("Sale (Steam Market)"))
            steam_price = st.number_input(
                _("Steam sale price"),
                min_value=0.0, value=15.0, step=0.5, key="m1_steam_price",
            )
            steam_fee = st.number_input(
                _("Steam sale fee (%)"),
                min_value=0.0, max_value=100.0, value=DEFAULT_STEAM_FEE_PERCENT, step=0.5,
                key="m1_steam_fee", help=_("Default 15% = 10% CS2 fee + 5% Steam fee."),
            )
            manual_integer = st.checkbox(
                _("Currency without cents (integers only)"),
                value=False, key="m1_manual_integer",
                help=_("Forces integer Steam pricing. Auto-enabled for ₴ / UAH."),
            )

        if advanced:
            st.divider()
            rate_site_to_spent, rate_steam_to_spent = render_cross_currency_rates(
                "m1", spent_ccy, site_ccy, steam_ccy)

        # Конвертация фиксы из USD в целевую валюту (поле появляется при нужде).
        fixed_in_target = resolve_fixed_fee_in_target(
            "m1", fixed_fee_usd, enable_fees, advanced,
            currency, spent_ccy, site_ccy, rate_site_to_spent)

        submitted = st.form_submit_button("🧮 " + _("Calculate"),
                                          type="primary", use_container_width=True)

    # --- Результаты (только после нажатия) ---
    if not submitted:
        st.info(_("Press Calculate to see the results."))
        return

    # Валютный контекст вывода.
    if advanced:
        steam_side_ccy = steam_ccy
        output_ccy = spent_ccy or DEFAULT_CURRENCY
    else:
        steam_side_ccy = currency
        output_ccy = currency

    quantity = max(1, int(quantity))

    # Без цены скина считать нечего: даже при включённой фиксе площадки (которая
    # сама по себе делает затраты > 0) показывать «профит» бессмысленно.
    if site_price <= 0:
        st.info(_("Enter a skin price to see the calculation."))
        return

    # Целочисленный режим — по валюте стороны Steam или ручной галочке.
    integer_mode = manual_integer or is_integer_currency(steam_side_ccy)

    # Выручка продавца за 1 предмет считается по точной модели Steam (floor с
    # минимумом в одну единицу) в наименьшей единице валюты. Суммарный процент
    # комиссии распределяется на издательскую и площадочную части (10:5).
    cs2_fee_pct, steam_fee_pct = _split_total_fee(steam_fee)
    seller_unit, valid_buyer, fee_cs_unit, fee_steam_unit = calculate_exact_steam_revenue(
        steam_price, integer_mode, cs2_fee_pct, steam_fee_pct)
    steam_received_unit = float(seller_unit)
    integer_detail = (valid_buyer, seller_unit)

    # Предупреждение нужно только для целочисленных валют: там не каждая цена
    # листинга достижима и могла быть приведена к ближайшей возможной. Сравниваем
    # с тем же нормализованным вводом, что использует решатель (round half up),
    # иначе на .5-значениях (шаг 0.5) предупреждение срабатывало бы ложно.
    integer_warning = None
    if integer_mode:
        requested_unit = round_half_up_int(steam_price)
        achievable_unit = int(round(valid_buyer))
        if achievable_unit != requested_unit:
            integer_warning = (requested_unit, achievable_unit)

    # Выручка за всё количество (в валюте стороны Steam); конвертация — далее.
    steam_received_total = steam_received_unit * quantity

    # Реальные затраты на сайте (фикса уже в целевой валюте) за всё количество.
    site_real_cost = calculate_real_spent(site_price, fee_percent, fixed_in_target, quantity)

    # Приведение к базовой валюте и итоговый плюс.
    if advanced:
        real_spent_base, steam_received_base, profit_amount, profit_percent = \
            calculate_profit_cross_currency(
                site_real_cost, steam_received_total, rate_site_to_spent, rate_steam_to_spent)
    else:
        real_spent_base = site_real_cost
        steam_received_base = steam_received_total
        profit_amount, profit_percent = calculate_profit(real_spent_base, steam_received_base)

    # --- Вывод результатов ---
    st.divider()
    st.markdown("### 📊 " + _("Results"))

    if integer_warning is not None:
        x, y = integer_warning
        st.warning(_("Price {x} is impossible in Steam for integer currencies. "
                     "Rounded to the nearest possible: {y}.").format(x=x, y=y))

    # В целочисленном одновалютном режиме «Получено» — целое (без копеек).
    received_decimals = 0 if (integer_mode and not advanced) else 2

    m_spent, m_received, m_profit = st.columns(3)
    m_spent.metric(_("Real spent"), format_currency(real_spent_base, output_ccy))
    m_received.metric(_("Steam received"),
                      format_currency(steam_received_base, output_ccy, received_decimals))
    m_profit.metric(
        _("Net profit"),
        format_currency(profit_amount, output_ccy),
        delta=f"{profit_percent:+.2f}%",   # зелёный для плюса, красный для минуса
    )

    # Пояснение для целочисленного режима (за 1 предмет).
    if integer_mode and integer_detail is not None:
        buyer_pays, seller_receive = integer_detail
        st.caption(_("Buyer pays {buyer} · you receive {seller} (per item)").format(
            buyer=format_currency(buyer_pays, steam_side_ccy, 0),
            seller=format_currency(seller_receive, steam_side_ccy, 0)))
        st.caption(_("Steam fees are floored to whole units with a 1-unit minimum, "
                     "following Steam's exact fee model."))

    # В продвинутом режиме — промежуточные суммы в исходных валютах.
    if advanced:
        st.caption(
            f"{_('Site cost')}: {format_currency(site_real_cost, site_ccy or '?')} · "
            f"{_('Steam proceeds')}: "
            f"{format_currency(steam_received_total, steam_side_ccy or '?', received_decimals)}")

    # --- Итоговый вердикт ---
    if real_spent_base <= 0:
        st.info(_("Enter a skin price to see the calculation."))
    elif profit_amount > 0:
        st.success(_("Top-up in profit: +{amount} ({percent}).").format(
            amount=format_currency(profit_amount, output_ccy), percent=f"{profit_percent:+.2f}%"))
    elif profit_amount < 0:
        st.error(_("Top-up at a loss: {amount} ({percent}).").format(
            amount=format_currency(profit_amount, output_ccy), percent=f"{profit_percent:+.2f}%"))
    else:
        st.warning(_("Break-even result."))

    with st.expander("ℹ️ " + _("Calculation formulas")):
        st.markdown(_("MODE1_FORMULAS"))


# ===========================================================================
# РЕЖИМ 2: АНАЛИЗАТОР ВЫГОДНОЙ ПОКУПКИ ("ГДЕ КУПИТЬ ВЫГОДНЕЕ?")
# ===========================================================================

def calculate_mode_2(currency, advanced):
    """Интерфейс Режима 2. currency — валюта отображения; advanced — режим кросс-курсов."""
    st.subheader("🔍 " + _("Where to buy cheaper?"))
    st.write(_("We compare buying a skin directly on a third-party site with real money "
               "versus buying it on the Steam Market with a balance topped up 'in profit'."))

    # --- ВНЕ формы: шаблон комиссии + (опц.) валюты кросс-курсов ---
    template_name, enable_fees = render_fee_template_controls("m2")
    spent_ccy = site_ccy = steam_ccy = None
    if advanced:
        spent_ccy, site_ccy, steam_ccy = render_cross_currency_selectors("m2")

    fee_percent, fixed_fee_usd = 0.0, 0.0
    rate_site_to_spent, rate_steam_to_spent = 1.0, 1.0

    # --- Форма ---
    with st.form("m2_form"):
        col_steam, col_site = st.columns(2)

        with col_steam:
            st.markdown("#### 🏪 " + _("Buy on Steam Market"))
            steam_price = st.number_input(
                _("Current Steam Market price"),
                min_value=0.0, value=15.0, step=0.5, key="m2_steam_price",
            )
            deposit_profit = st.number_input(
                _("Steam top-up profit (%)"),
                min_value=-99.9, value=50.0, step=1.0, key="m2_deposit_profit",
                help=_("How profitably you topped up Steam earlier. "
                       "Example: spent 10 real, got 15 on balance → 50% profit."),
            )

        with col_site:
            st.markdown("#### 🛒 " + _("Buy on third-party site"))
            site_price = st.number_input(
                _("Skin price on third-party site"),
                min_value=0.0, value=12.0, step=0.5, key="m2_site_price",
            )
            quantity = st.number_input(
                _("Quantity"), min_value=1, value=1, step=1, key="m2_qty",
            )
            fee_percent, fixed_fee_usd = render_fee_value_inputs("m2", template_name, enable_fees)

        if advanced:
            st.divider()
            rate_site_to_spent, rate_steam_to_spent = render_cross_currency_rates(
                "m2", spent_ccy, site_ccy, steam_ccy)

        fixed_in_target = resolve_fixed_fee_in_target(
            "m2", fixed_fee_usd, enable_fees, advanced,
            currency, spent_ccy, site_ccy, rate_site_to_spent)

        submitted = st.form_submit_button("🧮 " + _("Calculate"),
                                          type="primary", use_container_width=True)

    # --- Результаты ---
    if not submitted:
        st.info(_("Press Calculate to see the results."))
        return

    if advanced:
        steam_side_ccy = steam_ccy
        output_ccy = spent_ccy or DEFAULT_CURRENCY
    else:
        steam_side_ccy = currency
        output_ccy = currency

    quantity = max(1, int(quantity))

    # Затраты на сайте (фикса уже в целевой валюте) за всё количество.
    site_real_cost = calculate_real_spent(site_price, fee_percent, fixed_in_target, quantity)

    # Сторона Steam: цена сначала приводится к целому для целочисленных валют
    # (округление «half up», как в решателе), затем применяется выгода пополнения
    # и только потом — конвертация.
    integer_mode = is_integer_currency(steam_side_ccy)
    if integer_mode:
        unit_price = float(round_half_up_int(steam_price))
        if unit_price != float(steam_price):
            st.warning(_("Steam has no fractions for this currency; price rounded to {y}.").format(
                y=int(unit_price)))
    else:
        unit_price = float(steam_price)
    steam_nominal = unit_price * quantity
    steam_real_cost = calculate_steam_real_cost(steam_nominal, deposit_profit)

    # Приведение к базовой валюте.
    if advanced:
        site_real_base = max(0.0, site_real_cost) * max(0.0, rate_site_to_spent)
        steam_real_base = max(0.0, steam_real_cost) * max(0.0, rate_steam_to_spent)
    else:
        site_real_base = site_real_cost
        steam_real_base = steam_real_cost

    result = compare_purchase_options(site_real_base, steam_real_base)

    # --- Вывод ---
    st.divider()
    st.markdown("### 📊 " + _("Results comparison"))

    m_site, m_steam = st.columns(2)
    m_site.metric(_("Real price (third-party site)"), format_currency(site_real_base, output_ccy))
    m_steam.metric(_("Real price (Steam, with profit)"), format_currency(steam_real_base, output_ccy))

    if advanced:
        st.caption(
            f"{_('Site cost')}: {format_currency(site_real_cost, site_ccy or '?')} · "
            f"{_('Steam cost')}: {format_currency(steam_real_cost, steam_side_ccy or '?')}")

    if site_real_base <= 0 and steam_real_base <= 0:
        st.info(_("Enter data to see the comparison."))
    elif result["recommendation"] == "steam":
        st.success(_("Cheaper to buy on Steam. Savings: {amount}.").format(
            amount=format_currency(result["savings"], output_ccy)))
    elif result["recommendation"] == "site":
        st.success(_("Cheaper to buy on the third-party site. Savings: {amount}.").format(
            amount=format_currency(result["savings"], output_ccy)))
    else:
        st.warning(_("Both options cost the same in real money."))

    with st.expander("ℹ️ " + _("Calculation formulas")):
        st.markdown(_("MODE2_FORMULAS"))


def calculate_sell_via_site_topup(site_sell_price, quantity, sales_fee_percent,
                                   withdrawal_fee_percent, withdrawal_fixed_fee,
                                   deposit_profit_percent):
    """Steam-баланс от продажи на сайте с последующим пополнением Steam (Режим 4).

    Шаги:
        gross_revenue = site_sell_price × quantity
        after_sales   = gross × (1 − sales_fee_percent / 100)
        real_money    = after_sales × (1 − withdrawal_fee_percent / 100) − withdrawal_fixed_fee
        steam_balance = real_money × (1 + deposit_profit_percent / 100)

    При deposit_profit_percent = 0 steam_balance равен real_money (пополнение 1:1).

    Возвращает dict с ключами:
        «gross»         — выручка до комиссий,
        «real_money»    — реальные деньги после всех удержаний,
        «steam_balance» — итоговый Steam-баланс.
    """
    site_sell_price = max(0.0, float(site_sell_price))
    quantity = max(1, int(quantity))
    sales_fee_percent = min(100.0, max(0.0, float(sales_fee_percent)))
    withdrawal_fee_percent = min(100.0, max(0.0, float(withdrawal_fee_percent)))
    withdrawal_fixed_fee = max(0.0, float(withdrawal_fixed_fee))

    gross = site_sell_price * quantity
    after_sales = gross * (1.0 - sales_fee_percent / 100.0)
    after_withdrawal = after_sales * (1.0 - withdrawal_fee_percent / 100.0)
    real_money = max(0.0, after_withdrawal - withdrawal_fixed_fee)

    divisor = 1.0 + float(deposit_profit_percent) / 100.0
    steam_balance = real_money * max(0.0, divisor)
    return {"gross": gross, "real_money": real_money, "steam_balance": steam_balance}


def calculate_steam_market_sell(steam_sell_price, quantity, total_steam_fee_percent,
                                 currency_code):
    """Steam-баланс от продажи на Steam Market напрямую (Режим 4).

    Для целочисленных валют (UAH, JPY, …) применяется точная модель Valve:
    round(10%)+round(5%) от суммы продавца с минимумом в одну единицу. Цена
    buyer-side нормализуется округлением «half up» и прогоняется через
    calculate_exact_steam_revenue, который и приводит её к ближайшей достижимой,
    как реальная торговая площадка.

    Для дробных валют используется расчёт в центах (floor-модель).

    Возвращает dict:
        «seller_per_unit» — продавцу за единицу;
        «steam_balance»   — итого за всё количество;
        «requested_unit»  — запрошенная цена (целое) — только для целочисленных валют;
        «valid_buyer»     — ближайшая достижимая цена покупателя (целое) — то же.
    Для дробных валют requested_unit / valid_buyer равны None.
    """
    steam_sell_price = max(0.0, float(steam_sell_price))
    quantity = max(1, int(quantity))
    total_steam_fee_percent = max(0.0, float(total_steam_fee_percent))
    cs2_fee_pct, steam_fee_pct = _split_total_fee(total_steam_fee_percent)
    int_ccy = is_integer_currency(currency_code)
    requested_unit = valid_buyer = None
    if int_ccy:
        requested_unit = round_half_up_int(steam_sell_price)
        seller_per_unit, buyer_unit, _, _ = calculate_exact_steam_revenue(
            float(requested_unit), is_integer_currency=True,
            cs2_fee_pct=cs2_fee_pct, steam_fee_pct=steam_fee_pct)
        valid_buyer = int(round(buyer_unit))
    else:
        seller_per_unit = calculate_steam_received(steam_sell_price, total_steam_fee_percent)
    return {"seller_per_unit": seller_per_unit, "steam_balance": seller_per_unit * quantity,
            "requested_unit": requested_unit, "valid_buyer": valid_buyer}


# ===========================================================================
# РЕЖИМ 5: ЛУЧШЕЕ КАЧЕСТВО ДЛЯ ПОКУПКИ В КОЛЛЕКЦИИ
# ===========================================================================

# Качества CS2 по возрастанию редкости: (ключ перевода, цвет редкости).
RARITY_DEFS = [
    ("rarity_consumer",   "#b0c3d9"),
    ("rarity_industrial", "#5e98d9"),
    ("rarity_milspec",    "#4b69ff"),
    ("rarity_restricted", "#8847ff"),
    ("rarity_classified", "#d32ce6"),
    ("rarity_covert",     "#eb4b4b"),
]

# Цвета рангов для подсветки (выше ранг — выгоднее покупка).
RANK_COLORS = {
    "A++": "#1a9850", "A+": "#52b04f", "A": "#86cb66",
    "B": "#b8b8b8", "C": "#9e9e9e",
    "D": "#f08a4b", "E": "#e2563b", "F": "#c0392b",
}


_RARITY_RANKS = ["F", "E", "D", "C", "B", "A", "A+", "A++"]  # индекс 0..7


def _ratio_rank_index(ratio):
    """Индекс ранга 0..6 (E..A++) по соотношению цен соседних качеств.

    ratio — «во сколько раз дороже». Чем больше предметов нижнего качества по цене
    эквивалентны одному верхнему, тем дешевле нижнее относительно того, во что оно
    превращается контрактом 10->1, тем выгоднее покупка. Пороги заданы пользователем:
        ≤2 → F, 2–3.5 → E, 3.5–4.5 → D, 4.5–5.5 → C (среднее), 5.5–6.5 → B,
        6.5–8 → A, 8–10 → A+, >10 → A++.
    Возвращает None при отсутствующем соотношении.
    """
    if ratio is None:
        return None
    if ratio <= 2.0:
        return 0   # F  — сильно переоценено
    if ratio <= 3.5:
        return 1   # E  — переоценено
    if ratio <= 4.5:
        return 2   # D  — ниже среднего
    if ratio <= 5.5:
        return 3   # C  — среднее
    if ratio <= 6.5:
        return 4   # B
    if ratio <= 8.0:
        return 5   # A
    if ratio <= 10.0:
        return 6   # A+
    return 7       # A++


# Категория комментария по (ранг, роль). role: "lower" — обычное качество,
# сравниваемое с тем, что выше; "highest" — высшее качество (наоборот).
_RANK_COMMENT_KEYS = {
    "lower":   {"F": "rk_over_strong", "E": "rk_over", "D": "rk_below", "C": "rk_normal",
                "B": "rk_good", "A": "rk_under", "A+": "rk_under", "A++": "rk_under_susp"},
    "highest": {"F": "rk_exp_strong", "E": "rk_exp", "D": "rk_exp_slight", "C": "rk_normal",
                "B": "rk_good", "A": "rk_cheap", "A+": "rk_cheap", "A++": "rk_cheap_susp"},
}

# Порядок рангов для выбора лучшего (по убыванию привлекательности покупки).
_RANK_ORDER = {"A++": 7, "A+": 6, "A": 5, "B": 4, "C": 3, "D": 2, "E": 1, "F": 0}


def _positional_rank_adjustments(n):
    """Корректировки РАНГА по позиции редкости среди присутствующих (снизу вверх).

    Влияют ТОЛЬКО на ранг (как бонус красоты), не на сырые цифры/ratio/сравнения.
    Знак: «+» = ранг лучше (реверс-логика высшего качества учитывает знак сама).
        самый нижний грейд (idx 0): +0.25 (бонус, ВСЕГДА; штрафы его НЕ касаются)
        лестница штрафов СВЕРХУ — только для грейдов ВЫШЕ самого нижнего (idx > 0):
            самая высокая (idx n-1):           -0.50 (доп. штраф сверх реверс-штрафа)
            предпоследняя (idx n-2):           -0.50
            пред-предпоследняя (idx n-3):      -0.35
            пред-пред-предпоследняя (idx n-4): -0.25
    В маленькой коллекции штрафы НЕ «сползают» на нижние грейды: при n=2 штраф только
    на самом верхнем, при n=3 — на двух верхних и т.д. (нижний грейд защищён). Если
    позиция штрафа совпала бы с самым нижним грейдом (idx 0) — штраф не ставится.
    Возвращает список длины n.
    """
    adj = [0.0] * n
    if n <= 0:
        return adj
    adj[0] += 0.25  # самый нижний грейд — только бонус, штрафы его не трогают
    # Лестница штрафов сверху; ставится лишь грейдам ВЫШЕ самого нижнего (idx > 0),
    # поэтому в маленькой коллекции штрафы не доходят до низа.
    for idx, penalty in ((n - 1, -0.50), (n - 2, -0.50), (n - 3, -0.35), (n - 4, -0.25)):
        if idx > 0:
            adj[idx] += penalty
    return adj


def analyze_collection_rarities(tiers):
    """Считает соотношения и инвестиционные ранги для заполненных качеств коллекции.

    tiers — список словарей {«key», «name», «color», «price», «beautiful»} ТОЛЬКО
    для качеств с ценой > 0, по возрастанию редкости (нижнее первым). Возвращает
    тот же список, обогащённый ключами «ratio» (базовое соотношение), «rank»,
    «rank_index», «role» («lower»/«highest»).

    Логика рангов:
        * НЕ высшее качество: ratio = цена_выше / цена_этого (сколько штук этого по
          цене = одно качество выше). Ранг — по _ratio_rank_index. Чем больше ratio,
          тем выгоднее покупка нижнего качества.
        * ВЫСШЕЕ качество (выше ничего нет): ранг считается «наоборот». ratio =
          цена_высшего / цена_ниже; индекс реверсируется (7 - idx, т.е. F<->A++).
          Затем штраф за инфляцию саплая (высшее нельзя скрафтить дальше, его
          предложение только растёт): для итогового A и выше −2, для B/C/D/E −1.
          Штраф берётся от БАЗОВОГО ratio (структурно), чтобы бонус красоты его
          не гасил.
        * Бонус «красоты/ликвидности»: если у качества включён флаг, к его ratio и к
          ratio качества на 1 ниже прибавляется +0.5, на 2 ниже +0.25 (на 3 ниже —
          ничего). На качество ВЫШЕ отмеченного бонус не влияет. Для высшего
          качества бонус, наоборот, СНИЖАЕТ его ratio (улучшает реверс-ранг).
    """
    n = len(tiers)

    # Накопление бонуса красоты по списку активных качеств (снизу вверх).
    bonus = [0.0] * n
    for i, t in enumerate(tiers):
        if t.get("beautiful"):
            bonus[i] += 0.5
            if i - 1 >= 0:
                bonus[i - 1] += 0.5
            if i - 2 >= 0:
                bonus[i - 2] += 0.25

    # Позиционные корректировки РАНГА (только ранг, не сырые цифры/ratio): штрафы
    # верхним редкостям и бонус самой нижней. Знак как у красоты: «+» = ранг лучше.
    pos = _positional_rank_adjustments(n)
    for i in range(n):
        bonus[i] += pos[i]

    results = []
    for i, t in enumerate(tiers):
        price = t["price"]
        if i < n - 1:  # не высшее: сравниваем с тем, что выше
            higher = tiers[i + 1]["price"]
            base_ratio = (higher / price) if price > 0 else None
            role = "lower"
            idx = _ratio_rank_index(base_ratio + bonus[i]) if base_ratio is not None else None
        else:  # высшее: наоборот + штраф за инфляцию саплая
            role = "highest"
            lower = tiers[i - 1]["price"] if n >= 2 else 0.0
            if n >= 2 and lower > 0:
                base_ratio = price / lower
                reversed_base = 7 - _ratio_rank_index(base_ratio)
                penalty = 2 if reversed_base >= 5 else 1   # A и выше -> -2, иначе -1
                eff = max(0.0, base_ratio - bonus[i])      # красота снижает M -> лучше
                reversed_eff = 7 - _ratio_rank_index(eff)
                idx = max(0, min(7, reversed_eff - penalty))
            else:
                base_ratio = None
                idx = None
        rank = _RARITY_RANKS[idx] if idx is not None else None
        results.append({**t, "ratio": base_ratio, "rank": rank, "rank_index": idx, "role": role})
    return results


# ===========================================================================
# РЕЖИМ 5 — ПРОДВИНУТЫЙ АНАЛИЗ ФЛОАТА (чистые функции, основа для продв. режима)
# ===========================================================================
# Структура данных (готова к расширению, см. план):
#   record = {"wear": str|None, "exact_float": float|None, "price": float}
#   skin   = {"name": str, "cap_lo": float, "cap_hi": float,
#             "records": [record, ...], "agg_choice": int|None}
#   rarity = {"key": ..., "skins": [skin, ...]}   # до 10 скинов на редкость
# Резка скина [cap_lo, cap_hi] может быть нестандартной (например, перчатки 0.06–0.80).
# Все расчёты ведутся в полном float64 (12+ значащих цифр сохраняются).

# Степени износа CS2 и их границы по флоату. Сами флоаты 0.0 и 1.0 не существуют,
# но как ГРАНИЦЫ полос и резки 0.0/1.0 допустимы (стандартный скин 0.00–1.00).
WEAR_ORDER = ["FN", "MW", "FT", "WW", "BS"]
WEAR_BANDS = {
    "FN": (0.00, 0.07), "MW": (0.07, 0.15), "FT": (0.15, 0.38),
    "WW": (0.38, 0.45), "BS": (0.45, 1.00),
}
FLOAT_EPS = 0.001  # отступ от верхней границы для дефолтного «худшего в качестве» флоата


def wear_of_float(f):
    """Степень износа (FN/MW/FT/WW/BS) по значению флоата."""
    for name in WEAR_ORDER:
        lo, hi = WEAR_BANDS[name]
        if lo <= f < hi:
            return name
    return "BS" if f >= 0.45 else None


def wears_intersecting_cap(cap_lo, cap_hi):
    """Список степеней износа, чьи диапазоны пересекаются с резкой скина."""
    out = []
    for name in WEAR_ORDER:
        wl, wh = WEAR_BANDS[name]
        if max(wl, cap_lo) < min(wh, cap_hi):
            out.append(name)
    return out


def default_float_for_wear(wear, cap_lo, cap_hi, midpoint=False, eps=FLOAT_EPS):
    """Дефолтный флоат записи, у которой указано только качество (без точного флоата).

    По умолчанию — ВЕРХ пересечения (качество ∩ резка) − ε («худший в качестве»):
    для стандартной резки 0–1 это FN 0.069, MW 0.149, FT 0.379, WW 0.449, BS 0.999.
    midpoint=True — СЕРЕДИНА пересечения (например, для FT с резкой 0.25–0.75 это
    середина [0.25; 0.38)). Возвращает None, если качество не пересекает резку.
    """
    wl, wh = WEAR_BANDS[wear]
    lo, hi = max(wl, cap_lo), min(wh, cap_hi)
    if lo >= hi:
        return None
    if midpoint:
        return (lo + hi) / 2.0
    cand = hi - eps
    return cand if cand > lo else (lo + hi) / 2.0


def contract_weight(f, cap_lo, cap_hi):
    """Вес флоата в контракте: w = (f − cap_lo) / (cap_hi − cap_lo), зажат в [0, 1].

    Скин с нестандартной резкой «растягивается» к стандартному 0–1: например,
    Savannah Halftone FT 0.265 (резка 0.25–0.75) даёт w = 0.03 (в контракте — почти FN).
    Возвращает None при некорректной резке (span ≤ 0).
    """
    span = cap_hi - cap_lo
    if span <= 0:
        return None
    return min(1.0, max(0.0, (f - cap_lo) / span))


def validate_cap(cap_lo, cap_hi):
    """Проверка резки. Границы 0.0 и 1.0 допустимы (стандартный скин 0.00–1.00);
    несуществование флоатов 0.0/1.0 — ограничение на ФЛОАТ, а не на резку."""
    if cap_lo is None or cap_hi is None or not (0.0 <= cap_lo < cap_hi <= 1.0):
        return ["cap: требуется 0 <= min < max <= 1"]
    return []


def record_effective_float(rec, cap_lo, cap_hi, midpoint=False):
    """Эффективный флоат записи: точный, если указан; иначе дефолт по качеству."""
    if rec.get("exact_float") is not None:
        return rec["exact_float"]
    if rec.get("wear"):
        return default_float_for_wear(rec["wear"], cap_lo, cap_hi, midpoint)
    return None


def validate_record(rec, cap_lo, cap_hi):
    """Валидация записи в контексте резки скина. Возвращает список ошибок (пуст — ок).

    Правила: цена > 0; указано качество ИЛИ точный флоат; если резка валидна —
    точный флоат внутри резки И внутри указанного качества; выбранное качество
    пересекается с резкой (FN невозможен при резке от 0.25).
    """
    errs = []
    cap_errs = validate_cap(cap_lo, cap_hi)
    errs += cap_errs
    price = rec.get("price")
    if price is None or price <= 0:
        errs.append("цена должна быть > 0")
    wear = rec.get("wear")
    ef = rec.get("exact_float")
    if wear is None and ef is None:
        errs.append("укажите качество или точный флоат")
    if not cap_errs:
        if wear is not None and wear not in wears_intersecting_cap(cap_lo, cap_hi):
            errs.append(f"качество {wear} не пересекается с резкой [{cap_lo}; {cap_hi})")
        if ef is not None:
            if not (cap_lo <= ef < cap_hi):
                errs.append(f"точный флоат {ef:.12f} вне резки [{cap_lo}; {cap_hi})")
            elif wear is not None:
                wl, wh = WEAR_BANDS[wear]
                if not (wl <= ef < wh):
                    errs.append(f"точный флоат {ef:.12f} вне качества {wear}")
    return errs


def record_metrics(rec, cap_lo, cap_hi, midpoint=False):
    """Показатели валидной записи: eff_float, w (вес в контракте), q (чистота = 1−w),
    price. None — если запись невалидна. Метрики «ценности» (наценка за чистоту, балл)
    считаются на уровне скина/редкости, где есть с чем сравнивать."""
    if validate_record(rec, cap_lo, cap_hi):
        return None
    f = record_effective_float(rec, cap_lo, cap_hi, midpoint)
    if f is None:
        return None
    w = contract_weight(f, cap_lo, cap_hi)
    return {"eff_float": f, "w": w,
            "q": (1.0 - w) if w is not None else None,
            "price": float(rec["price"])}


def skin_records_metrics(skin, midpoint=False):
    """Метрики по каждой валидной записи скина (основа для сравнения качеств внутри скина)."""
    cap_lo, cap_hi = skin["cap_lo"], skin["cap_hi"]
    out = []
    for i, rec in enumerate(skin.get("records", [])):
        m = record_metrics(rec, cap_lo, cap_hi, midpoint)
        if m is not None:
            out.append({"index": i, **m})
    return out


def skin_premium_table(skin, midpoint=False):
    """Наценка за чистоту ВНУТРИ скина — основная метрика флоат-ценности.

    Пол — самая дешёвая валидная запись скина (при равной цене — самая чистая); это
    цена без наценки за флоат. Для каждой записи ЧИЩЕ пола:
        наценка = (Цена − Цена_пола) / (Чистота − Чистота_пола)   [$ за ед. чистоты]
    Здесь нет деления на (1−w), поэтому метрика не взрывается ни при грязи (w→1), ни
    при любой цене. Ниже наценка — дешевле чистота — меньше переплата за флоат.

    Статусы записей: 'floor' (пол), 'premium' (чище пола, есть наценка),
    'dominated' (дороже пола и не чище — невыгодно). best_premium — минимальная наценка
    среди 'premium'-записей (лучшая сделка по чистоте у этого скина); None, если записей
    меньше двух ИЛИ ни одна не чище пола (например, самый дешёвый уже самый чистый).
    Возвращает dict или None (нет валидных записей).
    """
    ms = skin_records_metrics(skin, midpoint)
    if not ms:
        return None
    floor = min(ms, key=lambda m: (m["price"], m["w"]))  # дешевле, при равенстве — чище
    p0, q0 = floor["price"], floor["q"]
    recs, best, best_i, cleaner_exists = [], None, None, False
    for m in ms:
        if m["index"] == floor["index"]:
            recs.append({**m, "premium": None, "status": "floor"})
            continue
        dq = m["q"] - q0
        if dq > 1e-9:  # чище пола
            cleaner_exists = True
            prem = (m["price"] - p0) / dq
            recs.append({**m, "premium": prem, "status": "premium"})
            if best is None or prem < best:
                best, best_i = prem, m["index"]
        else:  # не чище, но не дешевле пола — доминируется
            recs.append({**m, "premium": None, "status": "dominated"})
    return {"floor_index": floor["index"], "records": recs, "best_premium": best,
            "best_index": best_i, "n_records": len(ms), "cleaner_exists": cleaner_exists}


def rarity_float_summary(skins, midpoint=False):
    """Сводка наценки за чистоту по редкости (на основе наценок внутри скинов).

    best_premium — минимальная наценка за чистоту среди всех скинов (самая дешёвая
    чистота в редкости); avg_premium — среднее лучших наценок по скинам, у которых
    наценку вообще можно посчитать (≥2 флоата и есть запись чище пола). n_with_premium —
    сколько скинов попало в расчёт. Скины с одной записью в наценку не входят.
    """
    per_skin_best, overall_best, overall_best_skin = [], None, None
    for skin in skins:
        t = skin_premium_table(skin, midpoint)
        if t and t["best_premium"] is not None:
            per_skin_best.append(t["best_premium"])
            if overall_best is None or t["best_premium"] < overall_best:
                overall_best, overall_best_skin = t["best_premium"], skin.get("name", "?")
    if not per_skin_best:
        return {"best_premium": None, "avg_premium": None,
                "best_skin": None, "n_with_premium": 0}
    return {"best_premium": overall_best,
            "avg_premium": sum(per_skin_best) / len(per_skin_best),
            "best_skin": overall_best_skin, "n_with_premium": len(per_skin_best)}


def float_value_scores(records_metrics, alpha=0.5):
    """ЭКСПЕРИМЕНТАЛЬНЫЙ балл флоат-ценности 0–100 внутри набора записей.

    Сводит чистоту q (уже [0,1]) и дешевизну (нормированную min-max цену в наборе) в
    один балл: score = 100·(α·q + (1−α)·дешевизна). Выше = чище и дешевле. Балл
    ОТНОСИТЕЛЕН набору (меняется при добавлении записей) и зависит от веса α (по
    умолчанию 0.5/0.5) — поэтому он вспомогательный, а основная метрика — наценка за
    чистоту. Возвращает тот же список со старыми полями плюс 'score'.
    """
    if not records_metrics:
        return []
    prices = [m["price"] for m in records_metrics]
    p_min, p_max = min(prices), max(prices)
    out = []
    for m in records_metrics:
        cheap = 1.0 - ((m["price"] - p_min) / (p_max - p_min)) if p_max > p_min else 1.0
        out.append({**m, "score": 100.0 * (alpha * m["q"] + (1.0 - alpha) * cheap)})
    return out


# ===========================================================================
# РЕЖИМ 5 — ДВИЖОК КОНТРАКТОВ 10→1 (чистые функции)
# ===========================================================================
# Модель контракта обмена: 10 входов редкости R -> 1 выход редкости R+1.
# Выходной флоат: f_out = a_out + W̄·(b_out − a_out), где W̄ — контрактный вес входов.
# Исход — равновероятный по всем выходным скинам коллекции (1 скин = 1 исход).

def contract_output_float(W_bar, out_cap_lo, out_cap_hi):
    """Выходной флоат контракта: f_out = a + W̄·(b − a). W̄ — средняя чистота входов."""
    return out_cap_lo + W_bar * (out_cap_hi - out_cap_lo)


def _price_by_wear_map(skin, midpoint=False):
    """{качество: минимальная цена среди валидных записей скина в этом качестве}."""
    m = {}
    for rec in skin.get("records", []):
        rm = record_metrics(rec, skin["cap_lo"], skin["cap_hi"], midpoint)
        if rm is None:
            continue
        wear = wear_of_float(rm["eff_float"])
        if wear is None:
            continue
        if wear not in m or rec["price"] < m[wear]:
            m[wear] = rec["price"]
    return m


def skin_price_at_wear(skin, target_wear, midpoint=False):
    """Цена скина в заданном качестве; если записи нет — ближайшее качество по индексу
    (оценка). None, если у скина нет валидных записей."""
    m = _price_by_wear_map(skin, midpoint)
    if not m:
        return None
    if target_wear in m:
        return m[target_wear]
    ti = WEAR_ORDER.index(target_wear)
    nearest = min(m.keys(), key=lambda w: abs(WEAR_ORDER.index(w) - ti))
    return m[nearest]


def expected_output_value(W_bar, output_skins, midpoint=False):
    """E(выход, W̄) — средняя цена по выходным скинам на полученном флоате
    (равновероятный исход контракта одной коллекции). None, если данных нет."""
    vals = []
    for s in output_skins:
        f = contract_output_float(W_bar, s["cap_lo"], s["cap_hi"])
        wear = wear_of_float(f)
        if wear is None:
            continue
        p = skin_price_at_wear(s, wear, midpoint)
        if p is not None:
            vals.append(p)
    if not vals:
        return None
    return sum(vals) / len(vals)


def _filler_candidate_records(filler_skins, midpoint=False):
    """Все валидные записи редкости как кандидаты-филлеры контракта.

    Возвращает список {skin, wear, f, w, price}: подпись скина (имя или #номер),
    качество (введённое или по флоату), эффективный флоат, контрактный вес w и цена.
    """
    cands = []
    for si, sk in enumerate(filler_skins):
        label = (sk.get("name") or "").strip() or f"#{si + 1}"
        for rec in sk.get("records", []):
            rm = record_metrics(rec, sk["cap_lo"], sk["cap_hi"], midpoint)
            if rm is None or rm["w"] is None:
                continue
            cands.append({
                "skin": label,
                "wear": rec.get("wear") or wear_of_float(rm["eff_float"]) or "?",
                "f": rm["eff_float"], "w": rm["w"], "price": rm["price"],
            })
    return cands


def tradeup_candidates(filler_skins, output_skins, midpoint=False, n=10):
    """ВСЕ кандидаты-филлеры редкости с полной экономикой контракта R→R+1.

    Для каждой валидной записи: контракт из n её копий (W̄ = её контрактный вес w),
    затраты = n × цена, ожидаемый выход = среднее по ВСЕМ скинам следующей редкости,
    оценённым на флоате, который даст контракт (1 скин = 1 равновероятный исход).

    ДВА показателя доходности (чтобы не путать их между собой):
        net_roi  — чистая доходность = (выход − затраты)/затраты. 0 = в ноль,
                   +0.05 = +5% прибыли, отрицательное = убыток. На ней построены
                   пороги (флоат-бонус, вердикт «убыточен») — так удобнее считать.
        roi_full — ПОЛНЫЙ возврат = выход/затраты = 1 + net_roi. 1.0 (100%) = вернул
                   вложенное, 1.05 (105%) = +5% прибыли. Это то, что показывается
                   в интерфейсе, чтобы «105%» нельзя было прочитать как «+105%».

    Единый источник математики: этим пользуются и выбор филлера (best_tradeup_roi),
    и таблица «что покупать» в интерфейсе — расчёты не могут разойтись.
    Возвращает список {skin, wear, f, w, price, cost, E_out, net_roi, roi_full}.
    """
    if not output_skins:
        return []
    out = []
    for c in _filler_candidate_records(filler_skins, midpoint):
        e = expected_output_value(c["w"], output_skins, midpoint)
        cost = n * c["price"]
        if e is None or cost <= 0:
            continue
        out.append({**c, "cost": cost, "E_out": e,
                    "net_roi": (e - cost) / cost, "roi_full": e / cost})
    return out


def sort_tradeup_candidates(cands, contract_mode="cheapest"):
    """Порядок кандидатов под выбранный режим (первый = тот, который берёт контракт).

        'cheapest' — сначала самые дешёвые (при равной цене — более чистый);
        'best'     — сначала лучшая доходность (при равной — дешевле, затем чище).
    Порядок по net_roi и по roi_full одинаков (roi_full = 1 + net_roi), сортируем по net_roi.
    """
    if contract_mode == "best":
        return sorted(cands, key=lambda c: (-c["net_roi"], c["cost"], c["w"]))
    return sorted(cands, key=lambda c: (c["price"], c["w"]))


def best_tradeup_roi(filler_skins, output_skins, midpoint=False, n=10,
                     contract_mode="cheapest"):
    """Контракт R→R+1 на РЕАЛЬНЫХ записях (без интерполяции гипотетических филлеров).

    Кандидат-филлер = валидная запись редкости; контракт = n её копий (см.
    tradeup_candidates). contract_mode:
        'cheapest' — филлер = САМАЯ ДЕШЁВАЯ запись (её реальный флоат; при равной
                     цене берётся более чистая);
        'best'     — перебираются ВСЕ записи, берётся филлер с ЛУЧШЕЙ доходностью
                     (лучшее соотношение цена/качество для крафта-закупки).

    Возвращает {net_roi, roi_full, W_star, E_out, cost, filler, n_candidates,
    overpay_clean} или None (нет валидных данных). net_roi — чистая доходность
    (0 = в ноль), roi_full — полный возврат (1.0 = 100% = в ноль); см. tradeup_candidates.
    filler = {skin, wear, f, price}. overpay_clean:
        'worth'        — (только 'best') лучший филлер дороже самого дешёвого, но его
                         доходность выше: доплата за чистоту окупается,
        'avoid'        — (только 'best') самый дешёвый филлер и есть лучший:
                         за чистоту не переплачивать,
        'unprofitable' — net_roi ≤ 0, т.е. возврат ≤ 100% (крафт убыточен),
        None           — режим 'cheapest' при net_roi > 0 (чистоту не сравнивали).
    """
    cands = tradeup_candidates(filler_skins, output_skins, midpoint, n)
    if not cands:
        return None
    cheapest = sort_tradeup_candidates(cands, "cheapest")[0]
    chosen = sort_tradeup_candidates(cands, contract_mode)[0]
    best = {
        "net_roi": chosen["net_roi"], "roi_full": chosen["roi_full"],
        "W_star": chosen["w"], "E_out": chosen["E_out"],
        "cost": chosen["cost"], "n_candidates": len(cands),
        "filler": {"skin": chosen["skin"], "wear": chosen["wear"],
                   "f": chosen["f"], "price": chosen["price"]},
    }
    if best["net_roi"] <= 0:
        best["overpay_clean"] = "unprofitable"
    elif contract_mode != "best":
        best["overpay_clean"] = None  # в режиме «по самым дешёвым» чистоту не сравниваем
    elif chosen["net_roi"] > cheapest["net_roi"] + 1e-12:
        best["overpay_clean"] = "worth"
    else:
        best["overpay_clean"] = "avoid"
    return best


def tradeup_float_bonus(net_roi, cap=2.0, max_bonus=1.0):
    """Флоат-бонус к ratio из ЧИСТОЙ доходности контракта (ограниченный, как бонус красоты).

    Считается на net_roi (0 = в ноль), а не на полном возврате: net_roi ≤ 0 → бонуса нет
    (убыточный контракт ранг не поднимает); net_roi ≥ cap → max_bonus; между — линейно.
    cap = 2.0 ⇒ чистая доходность +200% (полный возврат 300%) даёт максимальный бонус.
    """
    if net_roi is None or net_roi <= 0:
        return 0.0
    return min(max_bonus, (net_roi / cap) * max_bonus)


def rarity_representative_price(skins, midpoint=False):
    """Цена редкости для ценового ранга = САМАЯ ДЕШЁВАЯ валидная запись в редкости.

    Это цена, по которой реально набираешь филлеры для контракта; в контракт идут
    самые дешёвые скины, а не «средний» скин, поэтому берётся минимум по всем
    валидным записям всех скинов редкости (не среднее). None, если валидных нет."""
    prices = []
    for skin in skins:
        for rec in skin.get("records", []):
            if record_metrics(rec, skin["cap_lo"], skin["cap_hi"], midpoint) is not None:
                prices.append(rec["price"])
    if not prices:
        return None
    return min(prices)


def analyze_collection_advanced(rarity_data, midpoint=False, contract_mode="cheapest"):
    """Ранжирование редкостей в продвинутом режиме (с учётом резки и флоат-экономики).

    rarity_data — список (key, skins) ПО ВОЗРАСТАНИЮ редкости. Учитываются редкости,
    у которых есть хотя бы один валидный скин с ценой. Логика ценового ранга та же,
    что в простом режиме (ratio соседних цен, реверс+штраф для высшего), НО к ratio
    нижних редкостей добавляется флоат-бонус из чистой доходности контракта R→R+1 на
    РЕАЛЬНЫХ записях (best_tradeup_roi; contract_mode: 'cheapest' — самая дешёвая запись,
    'best' — запись с лучшей доходностью). Высшее качество флоат-бонус не получает
    (из него не крафтят).

    Возвращает список dict: key, price, ratio, rank, rank_index, role, float_bonus,
    net_roi (чистая доходность, 0 = в ноль), roi_full (полный возврат, 1.0 = 100% = в ноль),
    W_star, E_out, cost, filler, n_candidates, candidates (все филлеры в порядке выбранного
    режима: первый = взятый контрактом), verdict (overpay_clean),
    float_summary (наценка за чистоту по редкости).
    """
    active = []
    for key, skins in rarity_data:
        price = rarity_representative_price(skins, midpoint)
        if price is not None and price > 0:
            active.append({"key": key, "skins": skins, "price": price})
    if len(active) < 2:
        return []
    n = len(active)
    # Позиционные корректировки РАНГА (только ранг, не сырые цифры): как в простом
    # режиме. «+» = ранг лучше; штрафы верхним редкостям, бонус самой нижней.
    pos = _positional_rank_adjustments(n)
    results = []
    for i, rar in enumerate(active):
        price = rar["price"]
        float_bonus, verdict, roi_info, cands = 0.0, None, None, []
        if i < n - 1:  # не высшее: возможен контракт R -> R+1
            roi_info = best_tradeup_roi(rar["skins"], active[i + 1]["skins"], midpoint,
                                        contract_mode=contract_mode)
            # Полный список кандидатов-филлеров в порядке выбранного режима:
            # первый = тот, который берёт контракт (та же математика и те же тай-брейки).
            cands = sort_tradeup_candidates(
                tradeup_candidates(rar["skins"], active[i + 1]["skins"], midpoint),
                contract_mode)
            if roi_info:
                float_bonus = tradeup_float_bonus(roi_info["net_roi"])
                verdict = roi_info["overpay_clean"]
            base_ratio = active[i + 1]["price"] / price
            idx = _ratio_rank_index(base_ratio + float_bonus + pos[i])
            role = "lower"
        else:  # высшее: реверс + штраф (+ позиционный штраф), без флоат-бонуса
            base_ratio = price / active[i - 1]["price"]
            reversed_base = 7 - _ratio_rank_index(base_ratio)
            penalty = 2 if reversed_base >= 5 else 1
            eff = max(0.0, base_ratio - pos[i])   # pos[i] (<0 у высшего) -> eff больше -> хуже
            reversed_eff = 7 - _ratio_rank_index(eff)
            idx = max(0, min(7, reversed_eff - penalty))
            role = "highest"
        results.append({
            "key": rar["key"], "price": price, "ratio": base_ratio,
            "rank": _RARITY_RANKS[idx], "rank_index": idx, "role": role,
            "float_bonus": float_bonus,
            "net_roi": (roi_info["net_roi"] if roi_info else None),
            "roi_full": (roi_info["roi_full"] if roi_info else None),
            "W_star": (roi_info["W_star"] if roi_info else None),
            "E_out": (roi_info["E_out"] if roi_info else None),
            "cost": (roi_info["cost"] if roi_info else None),
            "filler": (roi_info["filler"] if roi_info else None),
            "n_candidates": (roi_info["n_candidates"] if roi_info else None),
            "candidates": cands,
            "verdict": verdict,
            "float_summary": rarity_float_summary(rar["skins"], midpoint),
        })
    return results


# ===========================================================================
# ШАБЛОНЫ СУЩЕСТВУЮЩИХ КОЛЛЕКЦИЙ (для продвинутого режима)
# Данные статичны (зашиты в коде): названия/резки/качества/флоаты неизменны,
# со временем меняются только цены — пользователь правит их сам после загрузки.
# Внешнего ввода (файлы/сеть) нет — источник уязвимостей отсутствует.
# ФЛОАТЫ: если указан exact_float — берётся он (точнее для контрактов); None —
# качество без точного флоата (калькулятор возьмёт «худший в качестве»/середину).
# ===========================================================================
COLLECTION_TEMPLATES = {
    "cobblestone": {
        "name": "Cobblestone",
        "data": {
            "rarity_covert": [
                {"name": "AWP | Dragon Lore", "cap_lo": 0.00, "cap_hi": 0.70, "records": [
                    {"wear": "FN", "exact_float": 0.0646, "price": 11172.0},
                    {"wear": "MW", "exact_float": 0.0941, "price": 8300.0},
                    {"wear": "FT", "exact_float": 0.288, "price": 6140.0},
                    {"wear": "WW", "exact_float": 0.446, "price": 5318.0},
                    {"wear": "BS", "exact_float": 0.518, "price": 4549.0},
                ]},
            ],
            "rarity_classified": [
                {"name": "M4A1-S | Knight", "cap_lo": 0.00, "cap_hi": 0.10, "records": [
                    {"wear": "FN", "exact_float": 0.034, "price": 2360.0},
                    {"wear": "MW", "exact_float": None, "price": 2185.0},
                ]},
            ],
            "rarity_restricted": [
                {"name": "Desert Eagle | Hand Cannon", "cap_lo": 0.01, "cap_hi": 0.70, "records": [
                    {"wear": "FN", "exact_float": 0.056, "price": 441.0},
                    {"wear": "MW", "exact_float": 0.084, "price": 301.0},
                    {"wear": "FT", "exact_float": 0.304, "price": 276.0},
                    {"wear": "WW", "exact_float": 0.407, "price": 350.0},
                    {"wear": "BS", "exact_float": 0.627, "price": 302.0},
                ]},
                {"name": "CZ75-Auto | Chalice", "cap_lo": 0.00, "cap_hi": 0.10, "records": [
                    {"wear": "FN", "exact_float": 0.041, "price": 280.0},
                    {"wear": "MW", "exact_float": 0.071, "price": 279.0},
                ]},
            ],
            "rarity_milspec": [
                {"name": "P2000 | Chainmail", "cap_lo": 0.00, "cap_hi": 0.22, "records": [
                    {"wear": "FN", "exact_float": 0.0267, "price": 45.73},
                    {"wear": "MW", "exact_float": 0.086, "price": 40.0},
                    {"wear": "FT", "exact_float": 0.163, "price": 38.0},
                ]},
                {"name": "MP9 | Dark Age", "cap_lo": 0.00, "cap_hi": 0.22, "records": [
                    {"wear": "FN", "exact_float": 0.0275, "price": 55.4},
                    {"wear": "MW", "exact_float": 0.089, "price": 42.5},
                    {"wear": "FT", "exact_float": 0.206, "price": 40.25},
                ]},
            ],
            "rarity_industrial": [
                {"name": "USP-S | Royal Blue", "cap_lo": 0.06, "cap_hi": 0.80, "records": [
                    {"wear": "FN", "exact_float": 0.0697, "price": 117.0},
                    {"wear": "MW", "exact_float": 0.130, "price": 37.0},
                    {"wear": "FT", "exact_float": 0.378, "price": 8.87},
                    {"wear": "WW", "exact_float": 0.386, "price": 9.8},
                    {"wear": "BS", "exact_float": 0.521, "price": 8.73},
                ]},
                {"name": "MAG-7 | Silver", "cap_lo": 0.00, "cap_hi": 0.08, "records": [
                    {"wear": "FN", "exact_float": 0.0255, "price": 16.0},
                    {"wear": "MW", "exact_float": 0.074, "price": 20.0},
                ]},
                {"name": "Nova | Green Apple", "cap_lo": 0.00, "cap_hi": 0.30, "records": [
                    {"wear": "FN", "exact_float": 0.056, "price": 9.13},
                    {"wear": "MW", "exact_float": 0.129, "price": 8.0},
                    {"wear": "FT", "exact_float": 0.243, "price": 8.0},
                ]},
                {"name": "Sawed-Off | Rust Coat", "cap_lo": 0.00, "cap_hi": 0.80, "records": [
                    {"wear": "FN", "exact_float": 0.018, "price": 17.5},
                    {"wear": "MW", "exact_float": 0.134, "price": 7.5},
                    {"wear": "FT", "exact_float": 0.257, "price": 6.2},
                    {"wear": "WW", "exact_float": 0.4, "price": 12.0},
                    {"wear": "BS", "exact_float": 0.491, "price": 6.16},
                ]},
            ],
            "rarity_consumer": [
                {"name": "MAC-10 | Indigo", "cap_lo": 0.06, "cap_hi": 0.80, "records": [
                    {"wear": "FN", "exact_float": 0.065, "price": 28.4},
                    {"wear": "MW", "exact_float": 0.128, "price": 4.18},
                    {"wear": "FT", "exact_float": 0.353, "price": 1.91},
                    {"wear": "WW", "exact_float": 0.4, "price": 1.6},
                    {"wear": "BS", "exact_float": 0.76, "price": 1.5},
                ]},
                {"name": "P90 | Storm", "cap_lo": 0.06, "cap_hi": 0.80, "records": [
                    {"wear": "FN", "exact_float": 0.0652, "price": 23.42},
                    {"wear": "MW", "exact_float": 0.140, "price": 3.2},
                    {"wear": "FT", "exact_float": 0.274, "price": 1.84},
                    {"wear": "WW", "exact_float": 0.4, "price": 1.84},
                    {"wear": "BS", "exact_float": None, "price": 1.6},
                ]},
                {"name": "UMP-45 | Indigo", "cap_lo": 0.06, "cap_hi": 0.80, "records": [
                    {"wear": "FN", "exact_float": 0.065, "price": 17.0},
                    {"wear": "MW", "exact_float": 0.124, "price": 3.12},
                    {"wear": "FT", "exact_float": 0.23, "price": 2.0},
                    {"wear": "WW", "exact_float": None, "price": 1.8},
                    {"wear": "BS", "exact_float": None, "price": 2.0},
                ]},
                {"name": "Dual Berettas | Briar", "cap_lo": 0.00, "cap_hi": 0.22, "records": [
                    {"wear": "FN", "exact_float": 0.0446, "price": 3.7},
                    {"wear": "MW", "exact_float": 0.0879, "price": 2.2},
                    {"wear": "FT", "exact_float": 0.188, "price": 2.22},
                ]},
                {"name": "SCAR-20 | Storm", "cap_lo": 0.06, "cap_hi": 0.80, "records": [
                    {"wear": "FN", "exact_float": 0.069, "price": 14.37},
                    {"wear": "MW", "exact_float": 0.124, "price": 3.3},
                    {"wear": "FT", "exact_float": 0.339, "price": 1.8},
                    {"wear": "WW", "exact_float": 0.38, "price": 2.0},
                    {"wear": "BS", "exact_float": 0.57, "price": 1.7},
                ]},
            ],
        },
    },
    "overpass_2024": {
        "name": "Overpass 2024",
        "data": {
            "rarity_covert": [
                {"name": "AK-47 | B the Monster", "cap_lo": 0.00, "cap_hi": 0.80, "records": [
                    {"wear": "FN", "exact_float": 0.0698, "price": 518.65},
                    {"wear": "MW", "exact_float": 0.14533, "price": 210.36},
                    {"wear": "FT", "exact_float": 0.3516491, "price": 120.80},
                    {"wear": "WW", "exact_float": 0.44308909, "price": 104.72},
                    {"wear": "BS", "exact_float": 0.65594, "price": 67.81},
                ]},
            ],
            "rarity_classified": [
                {"name": "AWP | Crakow!", "cap_lo": 0.00, "cap_hi": 1.00, "records": [
                    {"wear": "FN", "exact_float": 0.069545, "price": 115.91},
                    {"wear": "MW", "exact_float": 0.14996999, "price": 52.32},
                    {"wear": "FT", "exact_float": 0.3508890, "price": 25.50},
                    {"wear": "WW", "exact_float": 0.44785550, "price": 19.81},
                    {"wear": "BS", "exact_float": 0.55, "price": 18.26},
                ]},
                {"name": "Zeus x27 | Dragon Snore", "cap_lo": 0.00, "cap_hi": 0.80, "records": [
                    {"wear": "FN", "exact_float": 0.06850666, "price": 56.97},
                    {"wear": "MW", "exact_float": 0.14625713, "price": 30.12},
                    {"wear": "FT", "exact_float": 0.293806, "price": 14.60},
                    {"wear": "WW", "exact_float": 0.441100, "price": 11.96},
                    {"wear": "BS", "exact_float": 0.684881, "price": 11.06},
                ]},
            ],
            "rarity_restricted": [
                {"name": "AUG | Eye of Zapems", "cap_lo": 0.00, "cap_hi": 0.85, "records": [
                    {"wear": "FN", "exact_float": 0.06980744, "price": 12.58},
                    {"wear": "MW", "exact_float": 0.12237, "price": 4.69},
                    {"wear": "FT", "exact_float": 0.366743, "price": 2.03},
                    {"wear": "WW", "exact_float": 0.395760, "price": 2.10},
                    {"wear": "BS", "exact_float": 0.542031, "price": 1.55},
                ]},
                {"name": "Dual Berettas | Sweet Little Angels", "cap_lo": 0.00, "cap_hi": 0.82, "records": [
                    {"wear": "FN", "exact_float": 0.067218, "price": 11.44},
                    {"wear": "MW", "exact_float": 0.118830, "price": 4.81},
                    {"wear": "FT", "exact_float": 0.3241175, "price": 2.14},
                    {"wear": "WW", "exact_float": 0.436387, "price": 1.92},
                    {"wear": "BS", "exact_float": 0.757794797421, "price": 1.68},
                ]},
                {"name": "XM1014 | Monster Melt", "cap_lo": 0.00, "cap_hi": 0.75, "records": [
                    {"wear": "FN", "exact_float": 0.065963, "price": 9.26},
                    {"wear": "MW", "exact_float": 0.132945328, "price": 4.30},
                    {"wear": "FT", "exact_float": 0.3212739, "price": 2.07},
                    {"wear": "WW", "exact_float": 0.411673, "price": 2.03},
                    {"wear": "BS", "exact_float": 0.57791, "price": 1.50},
                ]},
            ],
            "rarity_milspec": [
                {"name": "MAC-10 | Pipsqueak", "cap_lo": 0.00, "cap_hi": 0.90, "records": [
                    {"wear": "FN", "exact_float": 0.058604, "price": 3.69},
                    {"wear": "MW", "exact_float": 0.1132334, "price": 0.80},
                    {"wear": "FT", "exact_float": 0.3575538, "price": 0.25},
                    {"wear": "WW", "exact_float": 0.426355, "price": 0.23},
                    {"wear": "BS", "exact_float": 0.840270638, "price": 0.18},
                ]},
                {"name": "Galil AR | Metallic Squeezer", "cap_lo": 0.00, "cap_hi": 0.60, "records": [
                    {"wear": "FN", "exact_float": 0.05666, "price": 1.23},
                    {"wear": "MW", "exact_float": 0.141441, "price": 0.37},
                    {"wear": "FT", "exact_float": 0.23418, "price": 0.19},
                    {"wear": "WW", "exact_float": 0.444086, "price": 0.17},
                    {"wear": "BS", "exact_float": 0.557496, "price": 0.17},
                ]},
                {"name": "Nova | Wurst H\u00f6lle", "cap_lo": 0.00, "cap_hi": 1.00, "records": [
                    {"wear": "FN", "exact_float": 0.0675576, "price": 2.36},
                    {"wear": "MW", "exact_float": 0.13355, "price": 0.51},
                    {"wear": "FT", "exact_float": 0.36028, "price": 0.24},
                    {"wear": "WW", "exact_float": 0.386, "price": 0.21},
                    {"wear": "BS", "exact_float": 0.8996814, "price": 0.18},
                ]},
                {"name": "Glock-18 | Teal Graf", "cap_lo": 0.00, "cap_hi": 0.50, "records": [
                    {"wear": "FN", "exact_float": 0.06567, "price": 1.05},
                    {"wear": "MW", "exact_float": 0.147716611, "price": 0.31},
                    {"wear": "FT", "exact_float": 0.258276, "price": 0.20},
                    {"wear": "WW", "exact_float": 0.3951617, "price": 0.18},
                    {"wear": "BS", "exact_float": 0.450902223, "price": 0.17},
                ]},
            ],
            "rarity_industrial": [
                {"name": "Desert Eagle | Tilted", "cap_lo": 0.00, "cap_hi": 0.75, "records": [
                    {"wear": "FN", "exact_float": None, "price": 1.63},
                    {"wear": "MW", "exact_float": None, "price": 0.35},
                    {"wear": "FT", "exact_float": None, "price": 0.09},
                    {"wear": "WW", "exact_float": None, "price": 0.11},
                    {"wear": "BS", "exact_float": None, "price": 0.07},
                ]},
                {"name": "M4A1-S | Wash me plz", "cap_lo": 0.00, "cap_hi": 0.58, "records": [
                    {"wear": "FN", "exact_float": None, "price": 0.41},
                    {"wear": "MW", "exact_float": None, "price": 0.08},
                    {"wear": "FT", "exact_float": None, "price": 0.04},
                    {"wear": "WW", "exact_float": None, "price": 0.06},
                    {"wear": "BS", "exact_float": None, "price": 0.05},
                ]},
                {"name": "MP5-SD | Neon Squeezer", "cap_lo": 0.20, "cap_hi": 0.90, "records": [
                    {"wear": "FT", "exact_float": None, "price": 0.08},
                    {"wear": "WW", "exact_float": None, "price": 0.03},
                    {"wear": "BS", "exact_float": None, "price": 0.02},
                ]},
                {"name": "Negev | Wall Bang", "cap_lo": 0.00, "cap_hi": 1.00, "records": [
                    {"wear": "FN", "exact_float": None, "price": 1.12},
                    {"wear": "MW", "exact_float": None, "price": 0.09},
                    {"wear": "FT", "exact_float": None, "price": 0.02},
                    {"wear": "WW", "exact_float": None, "price": 0.02},
                    {"wear": "BS", "exact_float": None, "price": 0.01},
                ]},
                {"name": "Five-SeveN | Midnight Paintover", "cap_lo": 0.00, "cap_hi": 0.50, "records": [
                    {"wear": "FN", "exact_float": None, "price": 0.17},
                    {"wear": "MW", "exact_float": None, "price": 0.03},
                    {"wear": "FT", "exact_float": None, "price": 0.02},
                    {"wear": "WW", "exact_float": None, "price": 0.02},
                    {"wear": "BS", "exact_float": None, "price": 0.03},
                ]},
                {"name": "P90 | Wash me", "cap_lo": 0.00, "cap_hi": 0.50, "records": [
                    {"wear": "FN", "exact_float": None, "price": 0.17},
                    {"wear": "MW", "exact_float": None, "price": 0.04},
                    {"wear": "FT", "exact_float": None, "price": 0.02},
                    {"wear": "WW", "exact_float": None, "price": 0.02},
                    {"wear": "BS", "exact_float": None, "price": 0.01},
                ]},
            ],
        },
    },
}


def _m5a_load_template():
    """Колбэк кнопки «Загрузить шаблон»: заполняет session_state полей продвинутого
    режима значениями выбранной коллекции. Выполняется ДО перерисовки (on_click),
    поэтому виджеты подхватывают значения на следующем ране. Полностью перезаписывает
    прежний ввод: сначала чистит поля скинов, затем ставит данные шаблона. Данные
    статичны (в коде), внешнего ввода нет — безопасно."""
    tpl = COLLECTION_TEMPLATES.get(st.session_state.get("m5a_tpl_select"))
    if not tpl:
        return
    skin_prefixes = ("m5a_n_", "m5a_name_", "m5a_caplo_", "m5a_caphi_", "m5a_nrec_",
                     "m5a_wear_", "m5a_exon_", "m5a_exact_", "m5a_price_", "m5a_steam_")
    for k in list(st.session_state.keys()):
        if k.startswith(skin_prefixes):
            del st.session_state[k]
    for rkey, skins in tpl["data"].items():
        st.session_state[f"m5a_n_{rkey}"] = len(skins)
        for si, sk in enumerate(skins):
            st.session_state[f"m5a_name_{rkey}_{si}"] = sk["name"]
            st.session_state[f"m5a_caplo_{rkey}_{si}"] = float(sk["cap_lo"])
            st.session_state[f"m5a_caphi_{rkey}_{si}"] = float(sk["cap_hi"])
            recs = sk["records"]
            st.session_state[f"m5a_nrec_{rkey}_{si}"] = len(recs)
            for ri, rec in enumerate(recs):
                st.session_state[f"m5a_wear_{rkey}_{si}_{ri}"] = rec["wear"]
                has_f = rec.get("exact_float") is not None
                st.session_state[f"m5a_exon_{rkey}_{si}_{ri}"] = has_f
                if has_f:
                    st.session_state[f"m5a_exact_{rkey}_{si}_{ri}"] = float(rec["exact_float"])
                st.session_state[f"m5a_price_{rkey}_{si}_{ri}"] = float(rec["price"])
    st.session_state["m5a_tpl_just_loaded"] = tpl["name"]


def _mode_5_advanced(currency, tp):
    """Продвинутый режим: ввод скинов с резкой и записями качеств, метрики флоата,
    агрегаты редкости, контрактный ROI и ранги с флоат-бонусом. Защита от поломок:
    вся валидация на месте, пустые/невалидные записи не ломают расчёт."""
    st.caption(_(
        "Advanced mode: for each rarity add skins with their float cap and one or more "
        "quality records. The ranking then also accounts for float economics — the "
        "contract value of cleanliness. (The single-currency note still applies.)"
    ))
    midpoint = st.checkbox(
        _("m5a_midpoint_label"),
        value=False, key="m5a_midpoint", help=_("m5a_midpoint_help"))
    # Режим расчёта контрактов: «по самым дешёвым» / «по лучшему соотношению».
    contract_mode = st.radio(
        _("m5a_cmode_label"), options=["cheapest", "best"],
        format_func=lambda v: _("m5a_cmode_cheapest") if v == "cheapest" else _("m5a_cmode_best"),
        key="m5a_contract_mode", help=_("m5a_cmode_help"))
    # При учёте ТП цен суммы показываются в реальной валюте, иначе — в валюте отображения.
    disp_ccy = tp["real_ccy"] if tp["enabled"] else currency
    price_decimals = 0 if is_integer_currency(disp_ccy) else 2

    # Шаблоны готовых коллекций: одна кнопка заполняет все поля (правишь только цены).
    # Загрузка ПЕРЕЗАПИСЫВАЕТ текущий ввод продвинутого режима.
    if COLLECTION_TEMPLATES:
        tc1, tc2 = st.columns([3, 1])
        with tc1:
            st.selectbox(
                _("Collection template"), options=list(COLLECTION_TEMPLATES.keys()),
                format_func=lambda t: COLLECTION_TEMPLATES[t]["name"], key="m5a_tpl_select")
        with tc2:
            st.markdown("<div style='height:1.75rem;'></div>", unsafe_allow_html=True)
            st.button(_("Load template"), on_click=_m5a_load_template,
                      key="m5a_tpl_load", use_container_width=True)
        _just = st.session_state.pop("m5a_tpl_just_loaded", None)
        if _just:
            st.success(_("m5a_tpl_loaded_msg").format(name=_just))
        st.caption(_("m5a_tpl_caption"))

    rarity_data = []
    for key, color in RARITY_DEFS:
        with st.expander("● " + _(key), expanded=False):
            nskins = int(st.number_input(
                _("Skins in this rarity"), min_value=0, max_value=10, value=0, step=1,
                key=f"m5a_n_{key}"))
            skins = []
            for si in range(nskins):
                st.markdown(f"**{_('Skin')} {si + 1}**")
                name = st.text_input(_("Skin name (optional)"), value="",
                                     key=f"m5a_name_{key}_{si}")
                cc1, cc2 = st.columns(2)
                cap_lo = cc1.number_input(
                    _("Float cap min"), min_value=0.0, max_value=1.0, value=0.0,
                    step=0.01, format="%.12f", key=f"m5a_caplo_{key}_{si}")
                cap_hi = cc2.number_input(
                    _("Float cap max"), min_value=0.0, max_value=1.0, value=1.0,
                    step=0.01, format="%.12f", key=f"m5a_caphi_{key}_{si}")
                cap_errs = validate_cap(cap_lo, cap_hi)
                if cap_errs:
                    st.warning("⚠️ " + _("Invalid cap: need 0 ≤ min < max ≤ 1."))
                inter = wears_intersecting_cap(cap_lo, cap_hi) if not cap_errs else list(WEAR_ORDER)
                if not inter:
                    inter = list(WEAR_ORDER)
                nrec = int(st.number_input(
                    _("Quality records"), min_value=1, max_value=5, value=1, step=1,
                    key=f"m5a_nrec_{key}_{si}"))
                records = []
                for ri in range(nrec):
                    if tp["enabled"]:
                        rc1, rc2, rc3, rc4 = st.columns([2, 2, 2, 2])
                    else:
                        rc1, rc2, rc3 = st.columns([2, 2, 2])
                        rc4 = None
                    wear = rc1.selectbox(_("Quality (wear)"), options=inter,
                                         key=f"m5a_wear_{key}_{si}_{ri}")
                    use_exact = rc2.checkbox(_("Exact float"), value=False,
                                             key=f"m5a_exon_{key}_{si}_{ri}")
                    exact = None
                    if use_exact:
                        mid = min(0.999999, max(0.000001, (cap_lo + cap_hi) / 2.0))
                        exact = rc2.number_input(
                            _("Float value"), min_value=0.0, max_value=1.0, value=float(mid),
                            step=0.000001, format="%.12f", key=f"m5a_exact_{key}_{si}_{ri}")
                    price = rc3.number_input(_("Price"), min_value=0.0, value=0.0, step=0.1,
                                             key=f"m5a_price_{key}_{si}_{ri}")
                    # При учёте ТП цен цена записи = ДЕШЕВЛЕ из внешней и стим-цены (в
                    # реальной валюте). На этой реальной цене считается ВСЯ флоат-
                    # математика (наценка за чистоту, контракты, репрезент. цена).
                    src = None
                    if rc4 is not None:
                        steam_price = rc4.number_input(
                            _("Steam price"), min_value=0.0, value=0.0, step=0.1,
                            key=f"m5a_steam_{key}_{si}_{ri}")
                        eff_value, src = effective_real_value(
                            price, steam_price, tp["rate_site"], tp["rate_steam"], tp["topup"])
                    else:
                        eff_value = float(price)
                    rec = {"wear": wear, "exact_float": exact, "price": float(eff_value),
                           "_price_source": src}
                    records.append(rec)
                    if rec["price"] > 0:
                        errs = validate_record(rec, cap_lo, cap_hi)
                        if errs:
                            st.caption("⚠️ " + "; ".join(errs))
                        else:
                            m = record_metrics(rec, cap_lo, cap_hi, midpoint)
                            if m:
                                note = ""
                                if tp["enabled"]:
                                    money_eff = format_currency(rec["price"], disp_ccy, price_decimals)
                                    srctxt = (_("price from Steam") if src == "steam"
                                              else _("price from market") if src == "market" else "")
                                    note = f" · {money_eff}" + (f" ({srctxt})" if srctxt else "")
                                st.caption(
                                    f"{_('cleanliness')} = {m['q'] * 100:.1f}% · w = {m['w']:.12f}" + note)
                skin_obj = {"name": name or f"{_(key)} #{si + 1}",
                            "cap_lo": cap_lo, "cap_hi": cap_hi,
                            "records": records, "agg_choice": None}
                pt = skin_premium_table(skin_obj, midpoint)
                if pt:
                    scored = {r["index"]: r["score"]
                              for r in float_value_scores(pt["records"], alpha=0.5)}
                    lines = []
                    for r in pt["records"]:
                        wn = wear_of_float(r["eff_float"]) or "?"
                        qp = f"{r['q'] * 100:.1f}%"
                        sc = scored.get(r["index"])
                        sct = f" · {_('score')} {sc:.0f}" if sc is not None else ""
                        money = format_currency(r["price"], disp_ccy, price_decimals)
                        if r["status"] == "floor":
                            lines.append(f"{wn} ({r['eff_float']:.3f}): {_('cleanliness')} {qp} · "
                                         f"{money} — {_('floor (cheapest)')}{sct}")
                        elif r["status"] == "premium":
                            prem = format_currency(r["premium"], disp_ccy, price_decimals)
                            lines.append(f"{wn} ({r['eff_float']:.3f}): {_('cleanliness')} {qp} · "
                                         f"{_('cleanliness premium')} {prem}/{_('unit')}{sct}")
                        else:
                            lines.append(f"{wn} ({r['eff_float']:.3f}): {_('cleanliness')} {qp} — "
                                         f"{_('pricier and not cleaner — not worth it')}{sct}")
                    st.markdown("<div style='font-size:0.85em;opacity:0.85;'>" +
                                "<br>".join(lines) + "</div>", unsafe_allow_html=True)
                    if pt["n_records"] < 2:
                        st.caption("ℹ️ " + _("cleanliness premium: n/a (add a 2nd float to compare)"))
                    elif not pt["cleaner_exists"]:
                        st.caption("ℹ️ " + _("cleanliness premium: n/a (the cheapest is already the cleanest)"))
                skins.append(skin_obj)
            if skins:
                rarity_data.append((key, skins))

    results = analyze_collection_advanced(rarity_data, midpoint, contract_mode)
    st.divider()
    if not results:
        st.info(_("Add at least two rarities, each with a valid priced skin, to compare."))
        return

    name_by_key = {k: _(k) for k, _c in RARITY_DEFS}
    color_by_key = {k: c for k, c in RARITY_DEFS}

    st.markdown("### 📊 " + _("Ranking with float economics (higher = better buy)"))
    verdict_label = {
        "worth": _("clean pays off — worth buying low-float fillers"),
        "avoid": _("don't overpay for clean — a dirty filler gives the same output"),
        "unprofitable": _("trade-up into the next rarity is unprofitable"),
    }
    header = (
        "<tr>"
        f"<th style='text-align:left;padding:6px 10px;'>{_('Rarity')}</th>"
        f"<th style='text-align:right;padding:6px 10px;'>{_('Price')}</th>"
        f"<th style='text-align:center;padding:6px 10px;'>{_('Ratio')}</th>"
        f"<th style='text-align:center;padding:6px 10px;'>{_('Float bonus')}</th>"
        f"<th style='text-align:center;padding:6px 10px;'>{_('Rank')}</th>"
        "</tr>"
    )
    rows = []
    for r in results:
        rank = r["rank"]
        rcolor = RANK_COLORS.get(rank, "#9e9e9e")
        ratio_str = "—" if r["ratio"] is None else (
            _("this rarity = {n}× the rarity below").format(n=f"{r['ratio']:.2f}")
            if r["role"] == "highest" else
            _("{n}× this rarity = one rarity above").format(n=f"{r['ratio']:.2f}"))
        bonus_str = f"+{r['float_bonus']:.2f}" if r["float_bonus"] else "—"
        badge = (f"<span style='background:{rcolor};color:white;padding:2px 8px;"
                 f"border-radius:6px;font-weight:700;'>{rank or '—'}</span>")
        name_cell = (f"<span style='color:{color_by_key.get(r['key'], '#888')};"
                     f"font-weight:600;'>● </span>{name_by_key.get(r['key'], r['key'])}")
        rows.append(
            "<tr style='border-top:1px solid #3a3a3a;'>"
            f"<td style='text-align:left;padding:6px 10px;'>{name_cell}</td>"
            f"<td style='text-align:right;padding:6px 10px;'>{format_currency(r['price'], disp_ccy, price_decimals)}</td>"
            f"<td style='text-align:center;padding:6px 10px;font-size:0.85em;'>{ratio_str}</td>"
            f"<td style='text-align:center;padding:6px 10px;'>{bonus_str}</td>"
            f"<td style='text-align:center;padding:6px 10px;'>{badge}</td>"
            "</tr>")
    st.markdown("<table style='width:100%;border-collapse:collapse;'><thead>" + header +
                "</thead><tbody>" + "".join(rows) + "</tbody></table>", unsafe_allow_html=True)

    ranked = [r for r in results if r["rank"] is not None]
    if ranked:
        best = max(ranked, key=lambda r: _RANK_ORDER.get(r["rank"], -1))
        st.success(_("Best buy: {rarity} (rank {rank})").format(
            rarity=name_by_key.get(best["key"], best["key"]), rank=best["rank"]))

    # Контрактные вердикты и наценка за чистоту по редкости
    st.markdown("#### 🔧 " + _("Trade-up & float details"))
    for r in results:
        nm = name_by_key.get(r["key"], r["key"])
        fs = r["float_summary"]
        parts = []
        if r["net_roi"] is not None and r.get("filler") is not None:
            fl = r["filler"]
            line = _("m5a_craft_line").format(
                skin=_esc(fl["skin"]), wear=_esc(fl["wear"]), f=f"{fl['f']:.4f}",
                w=f"{r['W_star']:.2f}",
                price=format_currency(fl["price"], disp_ccy, price_decimals),
                n=10, cost=format_currency(r["cost"], disp_ccy, price_decimals),
                eout=format_currency(r["E_out"], disp_ccy, price_decimals),
                roi=f"{r['roi_full']*100:.0f}", profit=f"{r['net_roi']*100:+.0f}")
            verdict_txt = verdict_label.get(r["verdict"])
            parts.append(f"**{_('craft up')}:** "
                         + (verdict_txt + " " + line if verdict_txt else line))
        if fs and fs["best_premium"] is not None:
            best_p = format_currency(fs["best_premium"], disp_ccy, price_decimals)
            avg_p = format_currency(fs["avg_premium"], disp_ccy, price_decimals)
            parts.append(_("cheapest cleanliness {b}/{u} ({skin}) · avg {a}/{u}").format(
                b=best_p, a=avg_p, u=_("unit"), skin=_esc(fs["best_skin"])))
        else:
            parts.append(_("cleanliness premium: n/a (skins need ≥2 floats)"))
        st.markdown(f"<span style='color:{color_by_key.get(r['key'],'#888')};'>● </span>"
                    f"**{_esc(nm)}** — " + " · ".join(parts), unsafe_allow_html=True)

        # Что конкретно покупать: все кандидаты-филлеры этой редкости в порядке
        # выбранного режима (первый ✅ — тот, который берёт контракт).
        cands = r.get("candidates") or []
        if cands:
            with st.expander("🛒 " + _("m5a_cands_title").format(n=len(cands)), expanded=False):
                st.caption(_("m5a_cands_hint_best") if contract_mode == "best"
                           else _("m5a_cands_hint_cheapest"))
                chead = (
                    "<tr>"
                    f"<th style='text-align:left;padding:6px 10px;'>{_('m5a_col_skin')}</th>"
                    f"<th style='text-align:center;padding:6px 10px;'>{_('m5a_col_wear')}</th>"
                    f"<th style='text-align:center;padding:6px 10px;'>{_('m5a_col_float')}</th>"
                    "<th style='text-align:center;padding:6px 10px;'>w</th>"
                    f"<th style='text-align:right;padding:6px 10px;'>{_('Price')}</th>"
                    f"<th style='text-align:right;padding:6px 10px;'>{_('m5a_col_cost')}</th>"
                    f"<th style='text-align:right;padding:6px 10px;'>{_('m5a_col_eout')}</th>"
                    f"<th style='text-align:center;padding:6px 10px;'>{_('m5a_col_roi')}</th>"
                    "</tr>")
                crows = []
                for ci, c in enumerate(cands):
                    top = (ci == 0)
                    roi_color = "#4caf50" if c["net_roi"] > 0 else "#e05555"
                    style = "font-weight:600;background:rgba(76,175,80,0.08);" if top else ""
                    mark = "✅ " if top else ""
                    crows.append(
                        f"<tr style='border-top:1px solid #3a3a3a;{style}'>"
                        f"<td style='text-align:left;padding:6px 10px;'>{mark}{_esc(c['skin'])}</td>"
                        f"<td style='text-align:center;padding:6px 10px;'>{_esc(c['wear'])}</td>"
                        f"<td style='text-align:center;padding:6px 10px;font-size:0.85em;'>{c['f']:.4f}</td>"
                        f"<td style='text-align:center;padding:6px 10px;font-size:0.85em;'>{c['w']:.2f}</td>"
                        f"<td style='text-align:right;padding:6px 10px;'>{format_currency(c['price'], disp_ccy, price_decimals)}</td>"
                        f"<td style='text-align:right;padding:6px 10px;'>{format_currency(c['cost'], disp_ccy, price_decimals)}</td>"
                        f"<td style='text-align:right;padding:6px 10px;'>{format_currency(c['E_out'], disp_ccy, price_decimals)}</td>"
                        f"<td style='text-align:center;padding:6px 10px;color:{roi_color};'>{c['roi_full'] * 100:.0f}%</td>"
                        "</tr>")
                st.markdown("<table style='width:100%;border-collapse:collapse;'><thead>" + chead +
                            "</thead><tbody>" + "".join(crows) + "</tbody></table>",
                            unsafe_allow_html=True)
    st.caption("ℹ️ " + _("Score 0–100 is experimental (relative within a skin, 50/50 clean/cheap)."))

    with st.expander("ℹ️ " + _("How the ranking works")):
        st.markdown(_("MODE5_FORMULAS"))
        st.markdown(_("MODE5_ADV_NOTE"))


def calculate_mode_5(currency):
    """Интерфейс Режима 5. Только одна валюта (кросс-курсы не применяются).

    Пользователь вводит цены качеств коллекции; приложение ранжирует, какое
    качество выгоднее покупать, по соотношению цен соседних качеств.
    """
    st.subheader("🎚️ " + _("Best rarity to buy (collection)"))
    advanced = st.checkbox(_("Advanced float analysis (float & cut)"),
                           value=False, key="m5_advanced")
    # Учёт цен Steam (ТП): общий блок для ОБОИХ режимов (простого и продвинутого).
    # Рендерится ВНЕ формы — тумблер сразу показывает/прячет поля валют и курсов.
    tp = render_m5_tp_block()
    if advanced:
        _mode_5_advanced(currency, tp)
        return

    st.write(_(
        "We rank which rarity in a collection is the best buy, based on the price ratio "
        "between adjacent rarities (10 lower-rarity items trade up into 1 higher-rarity item)."
    ))
    st.info(_(
        "Prices in one currency; use a single float tier (preferably the lowest). Leave a rarity at 0 to exclude it from the collection."
    ))

    with st.form("m5_form"):
        st.markdown("#### 🎨 " + _("Rarity prices"))
        st.caption(_(
            "Enter the price you consider fair for each rarity. Tick the box if you "
            "find that rarity's skins beautiful or especially liquid."
        ))
        if tp["enabled"]:
            h_name, h_price, h_steam, h_beauty = st.columns([2, 1, 1, 1])
            h_steam.markdown("**" + _("Steam price") + "**")
        else:
            h_name, h_price, h_beauty = st.columns([2, 1, 1])
        h_name.markdown("**" + _("Rarity") + "**")
        h_price.markdown("**" + _("Price") + "**")
        h_beauty.markdown("**" + _("Beautiful / liquid?") + "**")
        prices = {}
        steam_prices = {}
        beauty = {}
        for key, color in RARITY_DEFS:
            label = (f"<span style='color:{color};font-weight:600;'>● </span>" + _(key))
            if tp["enabled"]:
                c_name, c_price, c_steam, c_beauty = st.columns([2, 1, 1, 1])
            else:
                c_name, c_price, c_beauty = st.columns([2, 1, 1])
                c_steam = None
            with c_name:
                st.markdown("<div style='padding-top:0.5rem;'>" + label + "</div>",
                            unsafe_allow_html=True)
            with c_price:
                prices[key] = st.number_input(
                    _(key), min_value=0.0, value=0.0, step=0.1,
                    key=f"m5_price_{key}", label_visibility="collapsed",
                )
            if c_steam is not None:
                with c_steam:
                    steam_prices[key] = st.number_input(
                        _("Steam price"), min_value=0.0, value=0.0, step=0.1,
                        key=f"m5_steam_{key}", label_visibility="collapsed",
                    )
            with c_beauty:
                beauty[key] = st.checkbox(
                    _("Beautiful / liquid?"), value=False,
                    key=f"m5_beauty_{key}", label_visibility="collapsed",
                )
        submitted = st.form_submit_button("🧮 " + _("Calculate"),
                                          type="primary", use_container_width=True)

    if not submitted:
        st.info(_("Press Calculate to see the results."))
        return

    # Берём только заполненные качества, сохраняя порядок редкости. При учёте ТП цен
    # стоимость предмета = ДЕШЕВЛЕ из внешней и стим-цены (в реальной валюте), плюс
    # помечаем источник цены.
    tiers = []
    for key, color in RARITY_DEFS:
        if tp["enabled"]:
            value, source = effective_real_value(
                prices.get(key, 0.0), steam_prices.get(key, 0.0),
                tp["rate_site"], tp["rate_steam"], tp["topup"])
        else:
            value, source = float(prices.get(key, 0.0)), None
        if value and value > 0:
            tiers.append({"key": key, "name": _(key), "color": color,
                          "price": float(value), "beautiful": bool(beauty.get(key)),
                          "source": source})

    if len(tiers) < 2:
        st.warning(_("Enter at least two rarity prices to compare."))
        return

    results = analyze_collection_rarities(tiers)

    # --- Таблица результатов ---
    st.divider()
    st.markdown("### 📊 " + _("Ranking (higher = better buy)"))

    header = (
        "<tr>"
        f"<th style='text-align:left;padding:6px 10px;'>{_('Rarity')}</th>"
        f"<th style='text-align:right;padding:6px 10px;'>{_('Price')}</th>"
        f"<th style='text-align:center;padding:6px 10px;'>{_('Ratio')}</th>"
        f"<th style='text-align:center;padding:6px 10px;'>{_('Rank')}</th>"
        f"<th style='text-align:left;padding:6px 10px;'>{_('Comment')}</th>"
        "</tr>"
    )
    rows = []
    # Для целочисленных валют (₴, ¥, ₩, …) цены выводим без дробной части —
    # как их показывает сам Steam (паритет с Режимами 1/2). При учёте ТП цен суммы
    # показываются в реальной валюте.
    disp_ccy = tp["real_ccy"] if tp["enabled"] else currency
    price_decimals = 0 if is_integer_currency(disp_ccy) else 2
    for r in results:
        rank = r["rank"]
        rank_color = RANK_COLORS.get(rank, "#9e9e9e")
        # Ранг и для нижних качеств, и для высшего после реверса означает одно:
        # высокий ранг — выгодная покупка. Но формулировка комментария зависит от
        # роли: у нижних качеств соотношение показывается как «N× этого = одно
        # выше», у высшего — как «это = N× качества ниже». Поэтому берём шкалу по
        # роли (для высшего — отдельную «highest»), а сверху добавляем пометку про
        # штраф за инфляцию саплая.
        scale = _RANK_COMMENT_KEYS["highest"] if r["role"] == "highest" else _RANK_COMMENT_KEYS["lower"]
        comment_key = scale.get(rank, "rk_normal") if rank else "rk_normal"
        comment = _(comment_key) if rank else "—"
        if rank and r["role"] == "highest":
            comment += " " + _("rk_top_note")
        if r["beautiful"]:
            comment += " ✨"
        if r.get("source") == "steam":
            comment += " · " + _("price from Steam")
        elif r.get("source") == "market":
            comment += " · " + _("price from market")
        if r["ratio"] is None:
            ratio_str = "—"
        elif r["role"] == "highest":
            ratio_str = _("this rarity = {n}× the rarity below").format(n=f"{r['ratio']:.2f}")
        else:
            ratio_str = _("{n}× this rarity = one rarity above").format(n=f"{r['ratio']:.2f}")
        rank_badge = (
            f"<span style='background:{rank_color};color:white;padding:2px 8px;"
            f"border-radius:6px;font-weight:700;'>{rank or '—'}</span>"
        )
        name_cell = f"<span style='color:{r['color']};font-weight:600;'>● </span>{r['name']}"
        rows.append(
            "<tr style='border-top:1px solid #3a3a3a;'>"
            f"<td style='text-align:left;padding:6px 10px;'>{name_cell}</td>"
            f"<td style='text-align:right;padding:6px 10px;'>{format_currency(r['price'], disp_ccy, price_decimals)}</td>"
            f"<td style='text-align:center;padding:6px 10px;font-size:0.85em;'>{ratio_str}</td>"
            f"<td style='text-align:center;padding:6px 10px;'>{rank_badge}</td>"
            f"<td style='text-align:left;padding:6px 10px;font-size:0.9em;'>{comment}</td>"
            "</tr>"
        )
    table_html = (
        "<table style='width:100%;border-collapse:collapse;'>"
        + "<thead>" + header + "</thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    st.markdown(table_html, unsafe_allow_html=True)

    # --- Лучшая покупка: наивысший ранг среди качеств с оценкой ---
    ranked = [r for r in results if r["rank"] is not None]
    if ranked:
        best = max(ranked, key=lambda r: _RANK_ORDER.get(r["rank"], -1))
        st.success(_("Best buy: {rarity} (rank {rank})").format(
            rarity=best["name"], rank=best["rank"]))

    with st.expander("ℹ️ " + _("Calculation formulas")):
        st.markdown(_("MODE5_FORMULAS"))


# ===========================================================================
# РЕЖИМ 4: АНАЛИЗАТОР ВЫГОДНОЙ ПРОДАЖИ ("ГДЕ ПРОДАТЬ ВЫГОДНЕЕ?")
# ===========================================================================

def calculate_mode_4(currency, advanced):
    """Интерфейс Режима 4. currency — валюта отображения; advanced — режим кросс-курсов.

    Сравниваем два пути продажи скина:
        Вариант А — продать на стороннем сайте, вывести деньги и пополнить Steam в плюс;
        Вариант Б — продать напрямую на Steam Market (Steam удерживает ~15%).

    Оба варианта дают Steam-баланс как итог, поэтому сравнение прямое.
    """
    st.subheader("📊 " + _("Where to sell more profitably?"))
    st.write(_(
        "We compare selling a skin on a third-party site (then topping up Steam at a profit) "
        "versus selling it directly on the Steam Market."
    ))

    # --- Вне формы: (опц.) валюты кросс-курсов ---
    spent_ccy = site_ccy = steam_ccy = None
    if advanced:
        spent_ccy, site_ccy, steam_ccy = render_cross_currency_selectors("m4")

    rate_site_to_spent, rate_steam_to_spent = 1.0, 1.0

    # --- Форма ---
    with st.form("m4_form"):
        col_site, col_steam = st.columns(2)

        with col_site:
            st.markdown("#### 🛒 " + _("Sell on third-party site → top up Steam"))
            site_sell_price = st.number_input(
                _("Site sell price"),
                min_value=0.0, value=12.0, step=0.5, key="m4_site_sell",
            )
            quantity = st.number_input(
                _("Quantity"), min_value=1, value=1, step=1, key="m4_qty",
            )
            sales_fee = st.number_input(
                _("Sales fee (%)"),
                min_value=0.0, max_value=100.0, value=2.0, step=0.5, key="m4_sales_fee",
            )
            withdrawal_fee = st.number_input(
                _("Withdrawal fee (%)"),
                min_value=0.0, max_value=100.0, value=0.0, step=0.5, key="m4_wd_fee",
            )
            withdrawal_fixed_usd = st.number_input(
                _("Withdrawal fixed fee (USD)"),
                min_value=0.0, value=0.0, step=0.5, key="m4_wd_fixed",
            )
            deposit_profit = st.number_input(
                _("Steam top-up profit (%)"),
                min_value=-99.9, value=50.0, step=1.0, key="m4_deposit_profit",
                help=_(
                    "How profitably you topped up Steam earlier. "
                    "Example: spent 10 real, got 15 on balance → 50% profit."
                ),
            )

        with col_steam:
            st.markdown("#### 🏪 " + _("Sell on Steam Market directly"))
            steam_sell_price = st.number_input(
                _("Steam sell price (buyer pays)"),
                min_value=0.0, value=15.0, step=0.5, key="m4_steam_sell",
                help=_("The price shown to buyers on the Steam Market."),
            )
            steam_fee_total = st.number_input(
                _("Steam Market fee (%)"),
                min_value=0.0, max_value=100.0, value=15.0, step=0.5, key="m4_steam_fee",
                help=_("Default 15% = 10% CS2 fee + 5% Steam fee."),
            )

        if advanced:
            st.divider()
            rate_site_to_spent, rate_steam_to_spent = render_cross_currency_rates(
                "m4", spent_ccy, site_ccy, steam_ccy)

        # Фиксированная комиссия вывода задаётся в USD → приводится к валюте сайта.
        fixed_in_site = resolve_fixed_fee_in_target(
            "m4", withdrawal_fixed_usd, True, advanced,
            currency, spent_ccy, site_ccy, rate_site_to_spent)

        submitted = st.form_submit_button("🧮 " + _("Calculate"),
                                          type="primary", use_container_width=True)

    if not submitted:
        st.info(_("Press Calculate to see the results."))
        return

    # --- Контекст валют ---
    if advanced:
        output_ccy = spent_ccy or DEFAULT_CURRENCY
        steam_side_ccy = steam_ccy or DEFAULT_CURRENCY
    else:
        output_ccy = currency
        steam_side_ccy = currency

    quantity = max(1, int(quantity))

    # --- Вариант А: продать на сайте → реальные деньги → пополнить Steam ---
    result_site = calculate_sell_via_site_topup(
        site_sell_price=site_sell_price,
        quantity=quantity,
        sales_fee_percent=sales_fee,
        withdrawal_fee_percent=withdrawal_fee,
        withdrawal_fixed_fee=fixed_in_site,
        deposit_profit_percent=deposit_profit,
    )

    # --- Вариант Б: продать напрямую на Steam Market ---
    result_steam = calculate_steam_market_sell(
        steam_sell_price=steam_sell_price,
        quantity=quantity,
        total_steam_fee_percent=steam_fee_total,
        currency_code=steam_side_ccy,
    )

    # --- Приведение к базовой валюте для сравнения ---
    if advanced:
        # Вариант А: реальные деньги (сайт) → базовая валюта → мысленное пополнение Steam
        # Вариант Б: Steam-баланс → базовая валюта (по курсу Steam→base)
        real_money_base_a = result_site["real_money"] * max(0.0, rate_site_to_spent)
        divisor_a = max(0.0, 1.0 + deposit_profit / 100.0)
        steam_balance_a = real_money_base_a * divisor_a
        steam_balance_b = result_steam["steam_balance"] * max(0.0, rate_steam_to_spent)
    else:
        steam_balance_a = result_site["steam_balance"]
        steam_balance_b = result_steam["steam_balance"]

    # --- Вывод результатов ---
    st.divider()
    st.markdown("### 📊 " + _("Results comparison"))

    # Для целочисленных валют введённая цена продажи на Steam (сторона покупателя)
    # могла быть приведена к ближайшей достижимой — сообщаем об этом, как в Режиме 1.
    if (result_steam["valid_buyer"] is not None
            and result_steam["valid_buyer"] != result_steam["requested_unit"]):
        st.warning(_("Price {x} is impossible in Steam for integer currencies. "
                     "Rounded to the nearest possible: {y}.").format(
            x=result_steam["requested_unit"], y=result_steam["valid_buyer"]))

    m_site, m_steam = st.columns(2)
    m_site.metric(
        _("Steam balance via site (with top-up)"),
        format_currency(steam_balance_a, output_ccy),
    )
    # Подсказка: сколько реальных денег пришло с сайта (до пополнения)
    real_money_display = (result_site["real_money"] * max(0.0, rate_site_to_spent)
                          if advanced else result_site["real_money"])
    m_site.caption(
        _("Real money from site: {amount}").format(
            amount=format_currency(real_money_display, output_ccy))
    )

    m_steam.metric(
        _("Steam balance via Steam Market"),
        format_currency(steam_balance_b, output_ccy),
    )
    # В продвинутом режиме — показываем исходные значения до конвертации.
    if advanced:
        st.caption(
            f"{_('Site')} → real: {format_currency(result_site['real_money'], site_ccy or '?')} · "
            f"{_('Steam Market')} → balance: "
            f"{format_currency(result_steam['steam_balance'], steam_side_ccy or '?')}"
        )

    # --- Вердикт ---
    diff = steam_balance_a - steam_balance_b
    both_zero = steam_balance_a <= 0 and steam_balance_b <= 0

    if both_zero:
        st.info(_("Enter data to see the comparison."))
    elif diff > 1e-4:
        st.success(_(
            "Selling via site and topping up Steam is more profitable. "
            "Extra Steam balance: {amount}."
        ).format(amount=format_currency(diff, output_ccy)))
    elif diff < -1e-4:
        st.success(_(
            "Selling on Steam Market is more profitable. "
            "Extra Steam balance: {amount}."
        ).format(amount=format_currency(-diff, output_ccy)))
    else:
        st.warning(_("Both options yield the same Steam balance."))

    with st.expander("ℹ️ " + _("Calculation formulas")):
        st.markdown(_("MODE4_FORMULAS"))


def calculate_mode_3(currency, advanced):
    """Интерфейс Режима 3. currency — валюта отображения; advanced — режим кросс-курсов.

    Сценарий: покупка предмета на Торговой площадке Steam за баланс, продажа на
    стороннем сайте и вывод выручки на карту/крипту. Цель — оценить, какая доля
    баланса Steam доходит до реальных денег (коэффициент вывода).
    """
    st.subheader("💳 " + _("Steam balance cashout calculator"))
    st.write(_("We calculate how much real money you receive by buying a skin on the Steam Market, "
               "selling it on a third-party site, and withdrawing the proceeds."))

    # Управляющие элементы вне формы: валюты кросс-курсов (если включён режим).
    spent_ccy = site_ccy = steam_ccy = None
    if advanced:
        spent_ccy, site_ccy, steam_ccy = render_cross_currency_selectors("m3")

    # Значения по умолчанию на случай отключённых блоков.
    rate_site_to_spent, rate_steam_to_spent = 1.0, 1.0

    # Поля и кнопка внутри st.form: пересчёт выполняется только по нажатию.
    with st.form("m3_form"):
        col_buy, col_sell = st.columns(2)

        with col_buy:
            st.markdown("#### 🏪 " + _("Purchase (Steam Market)"))
            steam_price = st.number_input(
                _("Steam purchase price"),
                min_value=0.0, value=15.0, step=0.5, key="m3_steam_price",
            )
            quantity = st.number_input(
                _("Quantity"), min_value=1, value=1, step=1, key="m3_qty",
            )
            deposit_profit = st.number_input(
                _("Steam top-up profit (%)"),
                min_value=-99.9, value=0.0, step=1.0, key="m3_deposit_profit",
                help=_(
                    "How profitably you topped up Steam earlier. "
                    "Example: spent 10 real, got 15 on balance → 50% profit."
                ),
            )

        with col_sell:
            st.markdown("#### 💳 " + _("Withdrawal (third-party site)"))
            site_sell_price = st.number_input(
                _("Site sell price"),
                min_value=0.0, value=12.0, step=0.5, key="m3_site_sell",
            )
            sales_fee = st.number_input(
                _("Sales fee (%)"),
                min_value=0.0, max_value=100.0, value=2.0, step=0.5, key="m3_sales_fee",
            )
            withdrawal_fee = st.number_input(
                _("Withdrawal fee (%)"),
                min_value=0.0, max_value=100.0, value=0.0, step=0.5, key="m3_wd_fee",
            )
            withdrawal_fixed_usd = st.number_input(
                _("Withdrawal fixed fee (USD)"),
                min_value=0.0, value=0.0, step=0.5, key="m3_wd_fixed",
            )

        if advanced:
            st.divider()
            rate_site_to_spent, rate_steam_to_spent = render_cross_currency_rates(
                "m3", spent_ccy, site_ccy, steam_ccy)

        # Фиксированная комиссия вывода задаётся в USD и приводится к валюте сайта
        # (комиссии сайта удерживаются именно в валюте сайта). enable_fees=True,
        # так как поле фиксы в этом режиме присутствует всегда.
        fixed_in_site = resolve_fixed_fee_in_target(
            "m3", withdrawal_fixed_usd, True, advanced,
            currency, spent_ccy, site_ccy, rate_site_to_spent)

        submitted = st.form_submit_button("🧮 " + _("Calculate"),
                                          type="primary", use_container_width=True)

    # Результаты выводятся только после нажатия кнопки.
    if not submitted:
        st.info(_("Press Calculate to see the results."))
        return

    # Валютный контекст вывода: база (карта) — итоговая валюта результата.
    # steam_side_ccy — валюта стороны Steam (в ней удерживается цена покупки на
    # Торговой площадке), как в Режимах 1/2.
    if advanced:
        steam_side_ccy = steam_ccy
        output_ccy = spent_ccy or DEFAULT_CURRENCY
    else:
        steam_side_ccy = currency
        output_ccy = currency

    quantity = max(1, int(quantity))

    # Сторона Steam: для целочисленных валют (₴, ¥, ₩ и т. п.) Steam не принимает
    # дробную цену, поэтому цену покупки приводим к целому («half up», как в
    # решателе) и предупреждаем пользователя об изменении — паритет с Режимами 1/2.
    if is_integer_currency(steam_side_ccy):
        rounded_steam_price = float(round_half_up_int(steam_price))
        if rounded_steam_price != float(steam_price):
            st.warning(_("Steam has no fractions for this currency; price rounded to {y}.").format(
                y=int(rounded_steam_price)))
        steam_price = rounded_steam_price

    # Расчёт в валюте сайта: затраты Steam и стороны сайта изначально в разных
    # валютах, поэтому Steam-затраты считаем отдельно, а денежный поток сайта —
    # через чистую функцию, приведя обе стороны к базовой валюте далее.
    cashout = calculate_cashout(
        steam_price=steam_price, site_sell_price=site_sell_price, quantity=quantity,
        sales_fee_percent=sales_fee, withdrawal_fee_percent=withdrawal_fee,
        withdrawal_fixed_fee=fixed_in_site,
    )

    # Приведение к базовой валюте (карты).
    if advanced:
        steam_spent_base = max(0.0, cashout["steam_spent"]) * max(0.0, rate_steam_to_spent)
        real_received_base = max(0.0, cashout["real_received"]) * max(0.0, rate_site_to_spent)
    else:
        steam_spent_base = cashout["steam_spent"]
        real_received_base = cashout["real_received"]

    net_profit_base = real_received_base - steam_spent_base
    ratio_base = (real_received_base / steam_spent_base * 100.0) if steam_spent_base > 0 else 0.0

    # --- Вывод результатов ---
    st.divider()
    st.markdown("### 📊 " + _("Results"))

    # Если задан % плюса пополнения — пересчитываем реальные затраты на Steam-баланс.
    # deposit_profit=0 => real_steam_cost = steam_spent (поведение идентично прежнему).
    use_deposit = deposit_profit != 0.0
    if use_deposit:
        real_steam_cost_base = calculate_steam_real_cost(steam_spent_base, deposit_profit)
        display_ratio = (real_received_base / real_steam_cost_base * 100.0
                         ) if real_steam_cost_base > 0 else 0.0
        net_result_base = real_received_base - real_steam_cost_base
    else:
        real_steam_cost_base = steam_spent_base
        display_ratio = ratio_base
        net_result_base = net_profit_base

    base_ratio_delta = display_ratio - 100.0

    m_spent, m_received, m_ratio = st.columns(3)

    if use_deposit:
        # Колонка «потрачено»: баланс Steam сверху, реальная стоимость — подписью.
        m_spent.metric(
            _("Total Steam spent"),
            format_currency(steam_spent_base, output_ccy),
        )
        m_spent.caption(
            f"↳ {_('Real Steam cost (with top-up)')}: "
            f"**{format_currency(real_steam_cost_base, output_ccy)}**"
        )
        m_ratio.metric(
            _("Effective cashout ratio"),
            f"{display_ratio:.1f}%",
            help=_("Top-up profit factored in. Ratio > 100% means you profit even after cashing out."),
        )
    else:
        m_spent.metric(_("Total Steam spent"), format_currency(steam_spent_base, output_ccy))
        m_ratio.metric(_("Cashout ratio"), f"{display_ratio:.1f}%")

    m_received.metric(_("Real money received"), format_currency(real_received_base, output_ccy))

    # Чистый результат: знак процента используется как delta (красный для минуса).
    st.metric(
        _("Net profit / loss"),
        format_currency(net_result_base, output_ccy),
        delta=f"{base_ratio_delta:+.1f}%",
    )

    # В продвинутом режиме — промежуточные суммы в исходных валютах.
    if advanced:
        st.caption(
            f"{_('Gross site revenue')}: "
            f"{format_currency(cashout['gross_revenue'], site_ccy or '?')} · "
            f"{_('After sales fee')}: "
            f"{format_currency(cashout['after_sales'], site_ccy or '?')}")

    # Итоговый вердикт по чистому результату.
    if steam_spent_base <= 0:
        st.info(_("Enter prices to see the cashout calculation."))
    elif net_result_base > 0:
        st.success(_("Top-up in profit: +{amount} ({percent}).").format(
            amount=format_currency(net_result_base, output_ccy),
            percent=f"{base_ratio_delta:+.1f}%"))
    elif net_result_base < 0:
        st.error(_("Top-up at a loss: {amount} ({percent}).").format(
            amount=format_currency(net_result_base, output_ccy),
            percent=f"{base_ratio_delta:+.1f}%"))
    else:
        st.warning(_("Break-even result."))

    st.caption(_("The higher the cashout ratio, the more of your Steam balance reaches your card."))

    with st.expander("ℹ️ " + _("Calculation formulas")):
        st.markdown(_("MODE3_FORMULAS"))


# ===========================================================================
# ТОЧКА ВХОДА
# ===========================================================================

def main():
    """Точка входа: настройка страницы, выбор языка, сайдбар и вкладки режимов."""
    st.set_page_config(
        page_title="Yev Steam Deposit Calculator",
        page_icon="🎯",
        layout="wide",
    )

    # Язык восстанавливается до отрисовки, чтобы подписи были корректны при
    # повторных запусках скрипта (значение хранится в session_state).
    st.session_state.setdefault("lang_name", DEFAULT_LANG_NAME)
    set_language(LANG_OPTIONS[st.session_state["lang_name"]])

    # --- Сайдбар: общие настройки ---
    with st.sidebar:
        st.title("⚙️ " + _("Settings"))

        # Выбор языка. После выбора синхронизируем активный язык.
        lang_name = st.selectbox(_("Language"), list(LANG_OPTIONS.keys()), key="lang_name")
        set_language(LANG_OPTIONS[lang_name])

        currency = st.selectbox(
            _("Display currency"), CURRENCIES, index=CURRENCIES.index(DEFAULT_CURRENCY),
        )

        # Переключатель продвинутого валютного режима (по умолчанию выключен).
        advanced = st.checkbox(
            _("Advanced currency mode (cross-rates)"), value=False,
            help=_("When enabled, all modes let you set separate currencies "
                   "for your card, the site, and Steam."),
        )

        st.divider()
        st.caption(_("CS2 Skin Investing Toolkit"))
        st.caption(_("All prices are entered manually. This is a calculator, not financial advice."))
        st.caption("⚠️ " + _("This is an analytical tool, not financial advice. All investments carry risks."))

    # --- Заголовок ---
    st.title("🎯 Yev Steam Deposit Calculator")
    st.caption(_("Steam top-up profit, skin purchase and CS2 collection analyzer")
               + f" · v{APP_VERSION}")

    # --- Новости (статичные сообщения из кода) ---
    render_news()

    # --- Вкладки режимов ---
    tab_mode_1, tab_mode_2, tab_mode_3, tab_mode_4, tab_mode_5 = st.tabs([
        "💰 " + _("Balance top-up (profit)"),
        "🔍 " + _("Where to buy cheaper?"),
        "💳 " + _("Withdrawal (Cashout)"),
        "📈 " + _("Where to sell more profitably?"),
        "🎚️ " + _("Best rarity to buy (collection)"),
    ])
    with tab_mode_1:
        calculate_mode_1(currency, advanced)
    with tab_mode_2:
        calculate_mode_2(currency, advanced)
    with tab_mode_3:
        calculate_mode_3(currency, advanced)
    with tab_mode_4:
        calculate_mode_4(currency, advanced)
    with tab_mode_5:
        calculate_mode_5(currency)

    # --- Юридический футер (i18n): копирайт и товарные знаки Valve ---
    st.divider()
    st.markdown(
        "<div style='text-align: center; color: gray; font-size: 0.8em;'>"
        + _("© 2026 Yev Capital. Not affiliated with Valve Corp. "
            "Steam and CS2 are trademarks of Valve Corporation.")
        + "</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()