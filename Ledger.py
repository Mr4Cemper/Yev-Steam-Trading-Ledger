# -*- coding: utf-8 -*-
"""
Yev Steam Trading Ledger — журнал сделок со скинами CS2
=======================================================

Локальное приложение на Streamlit для личного учёта сделок: покупка предмета за
баланс Steam (пополненный «в плюс») и продажа на стороннем сайте с выводом в
реальные деньги. Приложение хранит каждую сделку, считает реальную прибыль и
показывает сводку.

Модель «партий» (lots):
    Одна покупка N одинаковых предметов может продаваться по частям — в разные
    дни и по разным ценам, а часть может остаться непроданной. Единица учёта —
    «партия»: количество предметов из одной покупки с общей ценой закупки.
    Покупка создаёт открытую партию (остаток на руках). Продажа списывает часть
    остатка и создаёт закрытую партию со своей ценой и датой.

Хранение данных:
    Партии хранятся в локальном файле SQLite (deals.db) рядом со скриптом.
    Данные переживают перезапуск приложения и компьютера. Идентификаторы партий
    (id) СТАБИЛЬНЫ: правки из таблицы применяются точечными UPDATE/INSERT/DELETE
    по id, а не полной перезаписью, поэтому номера в списках не «убегают».
    Запись в базу выполняется атомарно (в одной транзакции).

Две валюты и фиксация курса (важно для корректной долларовой прибыли):
    * баланс Steam — в гривнах (₴); продажа на сайте — в долларах ($);
    * у партии ДВА независимых курса:
        buy_uah_per_usd  — курс на момент ПОКУПКИ (фиксирует долларовую
                           себестоимость баланса);
        sell_uah_per_usd — курс на момент ПРОДАЖИ (переводит выручку в ₴).
    Долларовая себестоимость считается по курсу покупки, а не по курсу продажи,
    поэтому девальвация гривны не создаёт «фантомную» долларовую прибыль.

Расчёт по одной партии:
    Реальная стоимость (₴) = (цена покупки в Steam × кол-во) ÷ (1 + плюс% / 100)
    Себестоимость ($)      = реальная стоимость (₴) ÷ курс ПОКУПКИ
    Выручка ($)            = (цена продажи × кол-во) × (1 − комиссия% / 100)
    Выручка (₴)            = выручка ($) × курс ПРОДАЖИ
    Прибыль (₴)            = выручка (₴) − реальная стоимость (₴)
    Прибыль ($)            = выручка ($) − себестоимость ($)
    ROI %                  = прибыль (₴) ÷ реальная стоимость (₴) × 100

Запуск:
    streamlit run ledger.py
"""

import io
import math
import os
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st


# ===========================================================================
# КОНСТАНТЫ
# ===========================================================================

DB_PATH = Path(__file__).resolve().parent / "deals.db"

# Папка для резервных копий и параметры ротации.
BACKUP_DIR = DB_PATH.parent / "backups"
BACKUP_KEEP_DAILY = 8            # сколько последних ежедневных (авто) копий хранить
BACKUP_KEEP_MANUAL = 4           # сколько последних ручных копий хранить
BACKUP_PREFIX = "deals.db."      # имя копии: deals.db.YYYY-MM-DD.bak
BACKUP_SUFFIX = ".bak"

DEFAULT_DEPOSIT_PROFIT = 48.0   # чистый плюс пополнения Steam по умолчанию, %
DEFAULT_SALES_FEE = 2.0         # комиссия сайта за продажу, %
DEFAULT_RATE = 45.05            # сколько ₴ в 1 $

# Поля ввода, видимые в журнале (два отдельных курса вместо одного).
INPUT_COLUMNS = [
    "item_name", "buy_date", "steam_buy_price", "quantity", "deposit_profit_pct",
    "buy_uah_per_usd", "sold", "sell_date", "site_sell_price", "sales_fee_pct",
    "sell_uah_per_usd",
]
COMPUTED_COLUMNS = [
    "real_cost_uah", "profit_uah", "profit_usd", "roi_pct", "holding_days", "status",
]

# Поля, описывающие покупку (общие для всех партий из одной покупки).
PURCHASE_FIELDS = ["item_name", "buy_date", "steam_buy_price", "deposit_profit_pct",
                   "buy_uah_per_usd", "lot_group"]

# Полный список колонок данных в таблице БД (без id).
DB_FIELDS = [
    "item_name", "buy_date", "steam_buy_price", "quantity", "deposit_profit_pct",
    "buy_uah_per_usd", "sold", "sell_date", "site_sell_price", "sales_fee_pct",
    "sell_uah_per_usd", "lot_group", "sold_at",
]

# Формат отметки времени ЗАПИСИ продажи (sold_at). Это НЕ дата продажи (её вводит
# пользователь), а момент, когда продажа внесена в журнал: только он позволяет
# ответить «что продал последним» и «на чём остановился», если за один день
# записано несколько продаж.
SOLD_AT_FMT = "%Y-%m-%d %H:%M:%S"

# --- ЕДИНЫЙ ФОРМАТ ОБМЕНА (экспорт CSV == импорт CSV) --------------------------
# Сырые поля — источник истины: только они читаются при импорте.
CSV_RAW_COLUMNS = [
    ("id",                 "id"),
    ("item_name",          "Предмет"),
    ("buy_date",           "Дата покупки"),
    ("steam_buy_price",    "Цена покупки ₴/шт"),
    ("quantity",           "Количество"),
    ("deposit_profit_pct", "Чистый плюс пополнения, %"),
    ("buy_uah_per_usd",    "Курс покупки ₴/$"),
    ("sold",               "Продано"),
    ("sell_date",          "Дата продажи"),
    ("site_sell_price",    "Цена продажи $/шт"),
    ("sales_fee_pct",      "Комиссия продажи %"),
    ("sell_uah_per_usd",   "Курс продажи ₴/$"),
    ("lot_group",          "Группа партии"),
    ("sold_at",            "Записано (продажа)"),
]
# Расчётные колонки — только для чтения глазами. При импорте ИГНОРИРУЮТСЯ и
# пересчитываются из сырых полей: править их в файле бессмысленно.
CSV_CALC_COLUMNS = [
    ("real_cost_uah", "Себестоимость ₴ (расчёт)"),
    ("profit_uah",    "Прибыль ₴ (расчёт)"),
    ("profit_usd",    "Прибыль $ (расчёт)"),
    ("roi_pct",       "ROI % (расчёт)"),
    ("holding_days",  "Дней в холде (расчёт)"),
    ("status",        "Статус (расчёт)"),
]
# Заголовок -> поле. Сопоставление идёт по НОРМАЛИЗОВАННОМУ виду (только буквы и
# цифры, нижний регистр), поэтому переживает: лишние пробелы и запятые, регистр,
# и — главное — пересохранение файла в cp1251, где символ ₴ не существует и Excel
# заменяет его на «?». Без этого колонка «Цена покупки ₴/шт» после Excel-а
# перестала бы распознаваться.
def _norm_header(text):
    """Заголовок -> только буквы/цифры в нижнем регистре (устойчиво к мусору)."""
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


IMPORT_HEADER_MAP = {}
for _f, _ru in CSV_RAW_COLUMNS:
    IMPORT_HEADER_MAP[_norm_header(_ru)] = _f      # русский заголовок экспорта
    IMPORT_HEADER_MAP[_norm_header(_f)] = _f       # сырое имя поля (файл «руками»)
IMPORT_HEADER_MAP.update({
    # Короткие/старые варианты заголовков — на случай файла, собранного вручную.
    "uahperusd": "uah_per_usd",                    # устаревшая одно-курсовая схема
    "название": "item_name",
    "ценапокупки": "steam_buy_price",
    "ценапродажи": "site_sell_price",
    "курспокупки": "buy_uah_per_usd",
    "курспродажи": "sell_uah_per_usd",
    "комиссия": "sales_fee_pct",
    "выгода": "deposit_profit_pct",
    "чистыйплюс": "deposit_profit_pct",
    "чистыйплюспополнения": "deposit_profit_pct",
    # Старый заголовок из файлов, выгруженных до переименования колонки, — чтобы
    # ранее сохранённые CSV по-прежнему импортировались без правки вручную.
    "выгодапополнения": "deposit_profit_pct",
    "группа": "lot_group",
    "записано": "sold_at",
})

MAX_IMPORT_BYTES = 5 * 1024 * 1024   # 5 МБ — защита от «файла-бомбы»
MAX_IMPORT_ROWS = 20000              # разумный предел строк


# ===========================================================================
# БЕЗОПАСНОЕ ПРИВЕДЕНИЕ ТИПОВ (защита от NaN/None/пустых значений)
# ===========================================================================

def _num(value, default):
    """NaN / None / пустая строка -> default; иначе исходное значение.

    st.data_editor для новых/пустых строк отдаёт NaN, а NaN «истинный»
    (bool(nan) == True), поэтому конструкции `value or default` его не отсекают,
    а int(nan) и вовсе падает. Эта функция централизует защиту.
    """
    try:
        if value is None or pd.isna(value):
            return default
    except (TypeError, ValueError):
        return default
    if isinstance(value, str) and value.strip() == "":
        return default
    return value


def _to_float(value, default=0.0):
    """Безопасно приводит к float (NaN/мусор -> default).

    Понимает десятичную ЗАПЯТУЮ и разделители разрядов: «45,05» -> 45.05,
    «1 234,56» -> 1234.56, «1,234.56» -> 1234.56. Это не косметика: Excel в
    украинской/русской локали сохраняет CSV именно так, и без этой обработки
    введённое число молча превращалось бы в 0.

    NaN и бесконечности (например, текст «NaN» в файле) тоже уходят в default —
    в базе им не место: они ломают любые последующие суммы и сравнения.
    """
    raw = _num(value, default)
    if isinstance(raw, str):
        txt = raw.strip().replace("\u00a0", "").replace(" ", "")
        if "," in txt and "." in txt:
            # Десятичный разделитель — тот, что стоит ПОСЛЕДНИМ; второй разделяет разряды.
            # «1,234.56» -> 1234.56 (США); «1.234,56» -> 1234.56 (Европа).
            if txt.rfind(",") > txt.rfind("."):
                txt = txt.replace(".", "").replace(",", ".")
            else:
                txt = txt.replace(",", "")
        elif "," in txt:
            # Одиночная запятая — ДЕСЯТИЧНЫЙ разделитель («100,50» -> 100.5, «1,2» -> 1.2):
            # так пишет Excel в украинской/русской локали. Единственное исключение —
            # НЕСКОЛЬКО групп ровно по 3 цифры («1,234,567»): это разряды. Одну группу
            # из 3 цифр как разряды не трактуем — иначе цена «1,200» (= 1.2) стала бы 1200.
            groups = txt.lstrip("-").split(",")
            is_thousns = (len(groups) >= 3 and groups[0].isdigit() and 1 <= len(groups[0]) <= 3
                          and all(len(g) == 3 and g.isdigit() for g in groups[1:]))
            txt = txt.replace(",", "") if is_thousns else txt.replace(",", ".")
        raw = txt
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if math.isfinite(val) else default


def _to_int(value, default=1):
    """Безопасно приводит к int (NaN/мусор -> default).

    Числа разбирает через _to_float, поэтому понимает те же форматы: десятичную
    запятую («3,0» -> 3) и разделители разрядов. Без этого количество из Excel-CSV
    украинской локали молча превратилось бы в значение по умолчанию.
    """
    raw = _num(value, None)
    if raw is None:
        return default
    val = _to_float(raw, float("nan"))
    if not math.isfinite(val):
        return default
    return int(val)


def _to_bool(value):
    """Безопасно приводит к bool (NaN/None -> False).

    Строки трактуются по смыслу: '0', 'false', 'no', 'нет', '' и подобные дают
    False (в обычном Python bool('0') был бы True). Это защищает от случая, когда
    в базу попала строка вместо целого 0/1 (например, после ручной правки).
    """
    val = _num(value, False)
    if isinstance(val, str):
        return val.strip().lower() not in ("0", "false", "no", "нет", "none", "")
    return bool(val)


def _is_blank(value):
    """True, если значение пустое (None/NaN/пустая строка)."""
    try:
        if value is None or pd.isna(value):
            return True
    except (TypeError, ValueError):
        return False
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


# ===========================================================================
# МАТЕМАТИКА (чистые функции, без Streamlit)
# ===========================================================================

def compute_deal(steam_buy_price, quantity, deposit_profit_pct,
                 buy_uah_per_usd, sell_uah_per_usd=None,
                 sold=False, site_sell_price=0.0, sales_fee_pct=0.0):
    """Экономика одной партии в ₴ и $ с РАЗДЕЛЬНЫМИ курсами покупки и продажи.

    Долларовая себестоимость берётся по курсу ПОКУПКИ (buy_uah_per_usd), а
    выручка в ₴ — по курсу ПРОДАЖИ (sell_uah_per_usd). Это исключает «фантомную»
    долларовую прибыль при изменении курса между покупкой и продажей.

    Если sell_uah_per_usd не задан, для перевода выручки используется курс
    покупки (обратная совместимость с записями, где один курс).

    ROI возвращается None при нулевой себестоимости (доходность не определена).

    Защита от деления на ноль: нулевой/отрицательный множитель плюса -> стоимость
    0; нулевой курс обнуляет соответствующий долларовый эквивалент, но не роняет
    расчёт.
    """
    steam_buy_price = max(0.0, _to_float(steam_buy_price))
    quantity = max(1, _to_int(quantity))
    deposit_profit_pct = _to_float(deposit_profit_pct)
    buy_rate = max(0.0, _to_float(buy_uah_per_usd))
    sell_rate = buy_rate if sell_uah_per_usd is None else max(0.0, _to_float(sell_uah_per_usd))
    site_sell_price = max(0.0, _to_float(site_sell_price))
    sales_fee_pct = min(100.0, max(0.0, _to_float(sales_fee_pct)))

    growth = 1.0 + deposit_profit_pct / 100.0
    real_cost_uah = (steam_buy_price * quantity) / growth if growth > 0 else 0.0
    # Долларовая себестоимость — ПО КУРСУ ПОКУПКИ (фиксируется в момент покупки).
    real_cost_usd = real_cost_uah / buy_rate if buy_rate > 0 else 0.0

    result = {
        "real_cost_uah": real_cost_uah, "real_cost_usd": real_cost_usd,
        "revenue_usd": 0.0, "revenue_uah": 0.0,
        "profit_uah": 0.0, "profit_usd": 0.0, "roi_pct": None,
    }
    if sold:
        gross_usd = site_sell_price * quantity
        revenue_usd = gross_usd * (1.0 - sales_fee_pct / 100.0)
        # Выручка в ₴ — ПО КУРСУ ПРОДАЖИ.
        revenue_uah = revenue_usd * sell_rate
        profit_usd = revenue_usd - real_cost_usd
        profit_uah = revenue_uah - real_cost_uah
        roi_pct = (profit_uah / real_cost_uah * 100.0) if real_cost_uah > 0 else None
        result.update(revenue_usd=revenue_usd, revenue_uah=revenue_uah,
                      profit_usd=profit_usd, profit_uah=profit_uah, roi_pct=roi_pct)
    return result


def holding_days(buy_iso, sell_iso):
    """Срок удержания в днях: от покупки до продажи (или до сегодня).

    Возвращает None, если:
        * не задана дата покупки;
        * дата продажи указана, но не распознаётся (кривой ввод) — чтобы мусор не
          маскировался под «сегодня» и не давал обманчивое число дней;
        * дата продажи раньше даты покупки (противоречие).
    Если дата продажи просто пустая (открытая позиция), срок считается до сегодня.
    Противоречивые/кривые даты дополнительно отмечает validate_deals.
    """
    buy = _parse_iso(buy_iso)
    if buy is None:
        return None
    # Пустая дата продажи -> открытая позиция (до сегодня). Непустая, но
    # нераспознанная -> None (а не молчаливое «сегодня»).
    if _is_blank(sell_iso):
        end = date.today()
    else:
        end = _parse_iso(sell_iso)
        if end is None:
            return None
    days = (end - buy).days
    return days if days >= 0 else None


# ===========================================================================
# ДАТЫ (ISO-строки в базе <-> date в интерфейсе)
# ===========================================================================

def _parse_iso(value):
    """ISO-строку / date / Timestamp -> date или None."""
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        if isinstance(value, pd.Timestamp):
            return value.date()
    except Exception:
        pass
    # Кроме ISO принимаем локальные форматы: Excel в украинской/русской локали
    # сохраняет даты как 14.07.2026, и без этого при импорте они молча становились бы
    # пустыми (терялась история покупок и расчёт дней удержания).
    #
    # Берём только ДАТНУЮ часть: отсекаем время по первому пробелу или «T». Жёсткий
    # срез [:10] тут не годится — у даты без ведущих нулей («5.9.2026 15:30») он
    # отрезал бы «5.9.2026 1» и парсинг падал бы.
    text = str(value).strip()
    date_part = text.replace("T", " ").split(" ", 1)[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d",
                "%d.%m.%y", "%d/%m/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_part, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _to_iso(value):
    """date / Timestamp / строку -> ISO-строка 'YYYY-MM-DD' или ''."""
    d = _parse_iso(value)
    return d.isoformat() if d else ""


_CSV_RISKY_PREFIX = ("=", "+", "-", "@")


def _csv_needs_quote(text):
    """Нужно ли защищать ячейку апострофом при выгрузке."""
    core = text.lstrip("'")
    return (bool(core) and core[:1] in _CSV_RISKY_PREFIX
            and text[:1] in _CSV_RISKY_PREFIX + ("'",))


def _csv_safe_text(value):
    """Обезвреживает текст перед записью в CSV (защита от инъекции формул).

    Excel/LibreOffice исполняют ячейку, начинающуюся с =, +, - или @, как ФОРМУЛУ.
    Название вводит человек, поэтому такие ячейки предваряются апострофом: файл
    остаётся текстом и ничего не выполняет.

    Защищается и имя, которое САМО начинается с апострофа перед опасным символом
    ("'=x" -> "''=x"): иначе импорт снял бы его апостроф и молча испортил название.
    """
    text = str(value if value is not None else "")
    return "'" + text if _csv_needs_quote(text) else text


def _csv_unquote(value):
    """Снимает защитный апостроф, добавленный при экспорте (обмен полностью обратим)."""
    text = str(value if value is not None else "")
    return text[1:] if text[:1] == "'" and _csv_needs_quote(text[1:]) else text


def now_stamp():
    """Текущий момент как строка для sold_at (локальное время машины)."""
    return datetime.now().strftime(SOLD_AT_FMT)


# ===========================================================================
# БАЗА ДАННЫХ (SQLite): стабильные id, точечные правки, авто-миграция
# ===========================================================================

def get_conn():
    import sqlite3
    return sqlite3.connect(DB_PATH)


def _table_columns(conn):
    return [r[1] for r in conn.execute("PRAGMA table_info(deals)").fetchall()]


def init_db():
    """Создаёт таблицу, если её нет, и при необходимости мигрирует схему.

    Миграция: если в существующей базе есть только старый столбец uah_per_usd,
    добавляются buy_uah_per_usd и sell_uah_per_usd, и старое значение курса
    копируется в оба — исторические записи остаются корректными.
    """
    conn = get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deals (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                item_name           TEXT    NOT NULL DEFAULT '',
                buy_date            TEXT    NOT NULL DEFAULT '',
                steam_buy_price     REAL    NOT NULL DEFAULT 0,
                quantity            INTEGER NOT NULL DEFAULT 1,
                deposit_profit_pct  REAL    NOT NULL DEFAULT 0,
                buy_uah_per_usd     REAL    NOT NULL DEFAULT 0,
                sold                INTEGER NOT NULL DEFAULT 0,
                sell_date           TEXT    NOT NULL DEFAULT '',
                site_sell_price     REAL    NOT NULL DEFAULT 0,
                sales_fee_pct       REAL    NOT NULL DEFAULT 0,
                sell_uah_per_usd    REAL    NOT NULL DEFAULT 0,
                lot_group           TEXT    NOT NULL DEFAULT '',
                sold_at             TEXT    NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()

        cols = _table_columns(conn)
        # Миграция со старой схемы (один курс uah_per_usd).
        if "buy_uah_per_usd" not in cols:
            conn.execute("ALTER TABLE deals ADD COLUMN buy_uah_per_usd REAL NOT NULL DEFAULT 0")
        if "sell_uah_per_usd" not in cols:
            conn.execute("ALTER TABLE deals ADD COLUMN sell_uah_per_usd REAL NOT NULL DEFAULT 0")
        if "sold_at" not in cols:
            # Момент ЗАПИСИ продажи. Старым закрытым партиям остаётся пусто: когда
            # их вносили — неизвестно, и выдумывать это нельзя.
            conn.execute("ALTER TABLE deals ADD COLUMN sold_at TEXT NOT NULL DEFAULT ''")
            conn.commit()
        if "lot_group" not in cols:
            conn.execute("ALTER TABLE deals ADD COLUMN lot_group TEXT NOT NULL DEFAULT ''")
            conn.commit()
            # Старым записям назначаем группу = собственный id (каждая отдельно).
            conn.execute("UPDATE deals SET lot_group = 'id:' || id WHERE lot_group = ''")
        conn.commit()
        cols = _table_columns(conn)
        if "uah_per_usd" in cols:
            # Переносим старый единый курс в оба новых поля там, где они ещё пустые.
            conn.execute(
                "UPDATE deals SET buy_uah_per_usd = uah_per_usd "
                "WHERE (buy_uah_per_usd IS NULL OR buy_uah_per_usd = 0) AND uah_per_usd > 0"
            )
            conn.execute(
                "UPDATE deals SET sell_uah_per_usd = uah_per_usd "
                "WHERE (sell_uah_per_usd IS NULL OR sell_uah_per_usd = 0) AND uah_per_usd > 0"
            )
            conn.commit()
    finally:
        conn.close()


# ===========================================================================
# РЕЗЕРВНЫЕ КОПИИ (консистентный снимок через sqlite backup API + ротация)
# ===========================================================================

def _daily_backup_path(day):
    """Путь к АВТОМАТИЧЕСКОМУ ежедневному бэкапу за дату: deals.db.YYYY-MM-DD.bak."""
    return BACKUP_DIR / f"{BACKUP_PREFIX}{day.isoformat()}{BACKUP_SUFFIX}"


def _manual_backup_path(moment):
    """Путь к РУЧНОМУ бэкапу с точностью до секунды.

    Содержит время (deals.db.YYYY-MM-DD_HH-MM-SS.bak), поэтому ручной бэкап
    никогда не перезаписывает утренний ежедневный снимок и другие ручные копии.
    """
    ts = moment.strftime("%Y-%m-%d_%H-%M-%S")
    return BACKUP_DIR / f"{BACKUP_PREFIX}{ts}{BACKUP_SUFFIX}"


def _parse_backup_name(path):
    """Разбирает имя файла бэкапа в (core, числовой_суффикс_коллизии).

    Имя: deals.db.YYYY-MM-DD[.bak] (ежедневный) или
    deals.db.YYYY-MM-DD_HH-MM-SS[_N][.bak] (ручной, N — суффикс коллизии).
        core    — 'YYYY-MM-DD' (ежедневный) либо 'YYYY-MM-DD_HH-MM-SS' (ручной);
        суффикс — ДОПОЛНИТЕЛЬНЫЙ _<число> ПОСЛЕ времени (или 0).
    Единый разбор имени, чтобы сортировка и классификация типа не разошлись.
    """
    stem = path.name[len(BACKUP_PREFIX):]
    if stem.endswith(BACKUP_SUFFIX):
        stem = stem[:-len(BACKUP_SUFFIX)]
    core, suffix_num = stem, 0
    if "_" in stem:
        head, _, tail = stem.rpartition("_")
        # Суффикс коллизии — это _<число> уже ПОСЛЕ core; core это 'YYYY-MM-DD'
        # либо 'YYYY-MM-DD_HH-MM-SS' (0 или 1 символ '_').
        if tail.isdigit() and head and head.count("_") in (0, 1):
            core, suffix_num = head, int(tail)
    return core, suffix_num


def _backup_is_manual(path):
    """True для РУЧНОГО бэкапа (в имени есть время), False для ежедневного (только дата)."""
    core, _ = _parse_backup_name(path)
    return "_" in core


def _backup_sort_key(path):
    """Ключ хронологической сортировки бэкапа по РАЗОБРАННОМУ имени.

    Возвращает (метка_времени, числовой_суффикс): порядок совпадает с реальной
    хронологией независимо от ширины суффикса (_2 vs _10 vs _100) — суффикс
    сравнивается как ЧИСЛО. Ежедневный снимок (без времени) считается «началом
    дня» (00:00:00) с суффиксом 0.
    """
    core, suffix_num = _parse_backup_name(path)
    ts = core if "_" in core else core + "_00-00-00"
    return (ts, suffix_num)


def list_backups():
    """Список существующих файлов бэкапов, новейшие первыми.

    Сортировка — по разобранному ключу (метка времени + числовой суффикс), а не
    по строке имени, поэтому хронология сохраняется при любом числе коллизий в
    одну секунду (_2, _10, _100 сравниваются как числа).
    """
    if not BACKUP_DIR.exists():
        return []
    files = [p for p in BACKUP_DIR.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}") if p.is_file()]
    return sorted(files, key=_backup_sort_key, reverse=True)


def prune_backups(keep_daily=BACKUP_KEEP_DAILY, keep_manual=BACKUP_KEEP_MANUAL):
    """Прореживает копии РАЗДЕЛЬНО по типам.

    Оставляет последние keep_daily ЕЖЕДНЕВНЫХ (авто) снимков и последние
    keep_manual РУЧНЫХ копий, считая каждый тип в своей квоте. Поэтому всплеск
    ручных копий (например, серия удалений за день) больше НЕ вытесняет
    ежедневную историю, и наоборот. Сегодняшний ежедневный снимок защищён всегда.

    Возвращает число удалённых файлов. Ошибки удаления отдельных файлов
    игнорируются (бэкап не должен мешать работе приложения).
    """
    all_backups = list_backups()                       # новейшие первыми
    daily = [p for p in all_backups if not _backup_is_manual(p)]
    manual = [p for p in all_backups if _backup_is_manual(p)]
    # Последние keep_daily ежедневных + последние keep_manual ручных (каждый тип — своя квота).
    keep_set = set(daily[:keep_daily]) | set(manual[:keep_manual])
    # Сегодняшний ежедневный снимок защищаем всегда (даже если квота ежедневных = 0).
    today_daily = _daily_backup_path(date.today())
    if today_daily.exists():
        keep_set.add(today_daily)

    removed = 0
    for p in all_backups:
        if p in keep_set:
            continue
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _copy_db_to(target_path):
    """Делает консистентный снимок базы в target_path через sqlite backup API.

    Пишет во временный файл рядом и атомарно переименовывает — незавершённый
    бэкап не оставит «битый» .bak. Возвращает True при успехе.
    """
    import sqlite3
    tmp = target_path.with_suffix(BACKUP_SUFFIX + ".tmp")
    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)            # консистентный снимок даже при открытой базе
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    if tmp.stat().st_size <= 0:
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, target_path)       # атомарная замена
    return True


def make_backup(force=False, manual=False):
    """Создаёт консистентный снимок базы в BACKUP_DIR.

    Два режима:
        * АВТОМАТИЧЕСКИЙ (manual=False) — не чаще одного в сутки. Имя содержит
          только дату; если копия за сегодня уже есть и force=False, ничего не
          делается. Утренний снимок «как было на начало дня».
        * РУЧНОЙ (manual=True) — отдельный файл с датой-временем (до секунды).
          Никогда не перезаписывает утренний ежедневный снимок и не теряет его,
          даже если базу только что испортили и нажали кнопку.

    После успешной копии прореживает старые: отдельно до BACKUP_KEEP_DAILY ежедневных
    и BACKUP_KEEP_MANUAL ручных копий.

    Возвращает (status, path):
        status: 'created' | 'exists' | 'error'; path: Path или None.
    Любая ошибка перехватывается и возвращается как 'error' — приложение
    продолжает работать без свежего бэкапа.
    """
    target = None
    try:
        if not DB_PATH.exists():
            return "error", None
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        if manual:
            target = _manual_backup_path(datetime.now())
            # Защита от совпадения по секунде: если файл с таким именем уже есть
            # (две копии в одну и ту же секунду), добавляем числовой суффикс,
            # чтобы вторая копия не затёрла первую.
            if target.exists():
                stem = target.name[:-len(BACKUP_SUFFIX)]
                n = 2
                while True:
                    # Нулевое заполнение (_02, _03 … _10, _11), чтобы лексикографи-
                    # ческая сортировка совпадала с хронологией даже при >9 копий
                    # в одну и ту же секунду.
                    cand = BACKUP_DIR / f"{stem}_{n:02d}{BACKUP_SUFFIX}"
                    if not cand.exists():
                        target = cand
                        break
                    n += 1
        else:
            target = _daily_backup_path(date.today())
            if target.exists() and not force:
                return "exists", target

        if not _copy_db_to(target):
            return "error", None
        prune_backups()
        return "created", target
    except Exception:
        # Подчищаем временный файл, если он остался.
        try:
            if target is not None:
                tmp_path = target.with_suffix(BACKUP_SUFFIX + ".tmp")
                if tmp_path.exists():
                    tmp_path.unlink()
        except OSError:
            pass
        return "error", None


def _normalize_deal(d):
    """Приводит словарь партии к полному набору полей с корректными типами.

    Безопасное приведение (NaN/None/пустые -> значения по умолчанию).

    Про курсы:
        * курс покупки восстанавливается из старого единого ключа uah_per_usd
          (обратная совместимость с миграцией одно-курсовой схемы);
        * курс покупки НЕ выдумывается из курса продажи: если реальный курс
          покупки неизвестен, он остаётся 0 (пустым), а долларовая себестоимость
          считается неопределённой. Это честнее, чем подменять историческую
          себестоимость курсом на момент продажи и тихо её искажать;
        * курс продажи для ОТКРЫТОЙ партии НЕ выдумывается и остаётся 0 (пустым):
          так его нельзя по ошибке принять за реальный курс будущей продажи. Курс
          продажи подставляется (из курса покупки) только если партия ПРОДАНА, а
          сам курс продажи не задан — чтобы у закрытой сделки расчёт был полным.
    """
    sold_flag = _to_bool(d.get("sold"))
    buy_rate = max(0.0, _to_float(d.get("buy_uah_per_usd"), 0.0))
    sell_rate = max(0.0, _to_float(d.get("sell_uah_per_usd"), 0.0))
    # Обратная совместимость: старый единый ключ uah_per_usd (миграция схемы).
    legacy = max(0.0, _to_float(d.get("uah_per_usd"), 0.0))
    if buy_rate == 0.0 and legacy > 0:
        buy_rate = legacy
    # Запасной курс продажи берётся из курса ПОКУПКИ (ниже), а не из устаревшего
    # единого поля: если в записи есть и явный курс покупки, и legacy-поле, верить
    # надо явному.
    # Курс продажи заполняем из курса покупки ТОЛЬКО для проданных партий.
    # Обратное (курс покупки из курса продажи) НЕ делаем — см. docstring.
    if sold_flag and sell_rate == 0.0 and buy_rate > 0:
        sell_rate = buy_rate
    # Для открытых партий все поля продажи очищаются: курс, дата, цена, комиссия.
    # Иначе при снятии галочки «Продано» в журнале зависали бы «призрачные»
    # данные продажи, и проверка постоянно ругалась бы на несоответствие.
    if not sold_flag:
        sell_rate = 0.0
        sell_date_val = ""
        site_sell_val = 0.0
        sales_fee_val = 0.0
        sold_at_val = ""          # партия снова открыта -> отметка записи не нужна
    else:
        sell_date_val = _to_iso(d.get("sell_date", ""))
        site_sell_val = max(0.0, _to_float(d.get("site_sell_price"), 0.0))
        sales_fee_val = max(0.0, _to_float(d.get("sales_fee_pct"), 0.0))
        # sold_at ПЕРЕНОСИТСЯ как есть и здесь НЕ выдумывается: функция остаётся
        # чистой (без обращения к часам). Отметку ставят места, где продажа реально
        # записывается (форма продажи, покупка «уже продано», правка журнала);
        # при импорте берётся значение из файла.
        sold_at_val = str(_num(d.get("sold_at"), "") or "").strip()
    return {
        "item_name": str(_num(d.get("item_name"), "") or "").strip(),
        "buy_date": _to_iso(d.get("buy_date", "")),
        "steam_buy_price": max(0.0, _to_float(d.get("steam_buy_price"), 0.0)),
        # Количество НЕ принуждаем к 1: пустое/мусор -> 0 (через _to_int с дефолтом
        # 0), чтобы битая запись (0 или отрицательное) сохранялась как есть и
        # оставалась видимой ошибкой, а не подменялась молча на 1 при сохранении.
        # validate_deals предупреждает о quantity<1, а сводка исключает такие
        # строки из итогов. Защита от деления/умножения на мусор — в compute_deal.
        "quantity": _to_int(d.get("quantity"), 0),
        "deposit_profit_pct": _to_float(d.get("deposit_profit_pct"), 0.0),
        "buy_uah_per_usd": buy_rate,
        "sold": 1 if sold_flag else 0,
        "sell_date": sell_date_val,
        "site_sell_price": site_sell_val,
        "sales_fee_pct": sales_fee_val,
        "sell_uah_per_usd": sell_rate,
        "lot_group": str(_num(d.get("lot_group"), "") or "").strip(),
        "sold_at": sold_at_val,
    }


_INSERT_SQL = """
    INSERT INTO deals (item_name, buy_date, steam_buy_price, quantity,
                       deposit_profit_pct, buy_uah_per_usd, sold, sell_date,
                       site_sell_price, sales_fee_pct, sell_uah_per_usd, lot_group,
                       sold_at)
    VALUES (:item_name, :buy_date, :steam_buy_price, :quantity,
            :deposit_profit_pct, :buy_uah_per_usd, :sold, :sell_date,
            :site_sell_price, :sales_fee_pct, :sell_uah_per_usd, :lot_group,
            :sold_at)
"""

_INSERT_SQL_WITH_ID = """
    INSERT INTO deals (id, item_name, buy_date, steam_buy_price, quantity,
                       deposit_profit_pct, buy_uah_per_usd, sold, sell_date,
                       site_sell_price, sales_fee_pct, sell_uah_per_usd, lot_group,
                       sold_at)
    VALUES (:id, :item_name, :buy_date, :steam_buy_price, :quantity,
            :deposit_profit_pct, :buy_uah_per_usd, :sold, :sell_date,
            :site_sell_price, :sales_fee_pct, :sell_uah_per_usd, :lot_group,
            :sold_at)
"""

_UPDATE_SQL = """
    UPDATE deals SET
        item_name=:item_name, buy_date=:buy_date, steam_buy_price=:steam_buy_price,
        quantity=:quantity, deposit_profit_pct=:deposit_profit_pct,
        buy_uah_per_usd=:buy_uah_per_usd, sold=:sold, sell_date=:sell_date,
        site_sell_price=:site_sell_price, sales_fee_pct=:sales_fee_pct,
        sell_uah_per_usd=:sell_uah_per_usd, lot_group=:lot_group,
        sold_at=:sold_at
    WHERE id=:id
"""


def insert_deals_atomic(deals):
    """Вставляет несколько связанных партий в ОДНОЙ транзакции.

    Используется, когда покупка сразу порождает несколько партий (закрытую часть
    и остаток): либо записываются все, либо ни одной — промежуточного состояния
    не возникает даже при сбое. Партиям без явной группы назначается общая группа
    по id ПЕРВОЙ вставленной партии, чтобы части одной покупки сводились в сверке.
    Возвращает список id вставленных строк.
    """
    conn = get_conn()
    try:
        ids = []
        first_group = None
        for d in deals:
            payload = _normalize_deal(d)
            cur = conn.execute(_INSERT_SQL, payload)
            rid = cur.lastrowid
            ids.append(rid)
            if first_group is None:
                first_group = payload.get("lot_group") or f"id:{rid}"
            if not payload.get("lot_group"):
                conn.execute("UPDATE deals SET lot_group = ? WHERE id = ?", (first_group, rid))
        conn.commit()
        return ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_deal(deal):
    """Добавляет одну партию (атомарно). Возвращает id новой строки."""
    conn = get_conn()
    try:
        payload = _normalize_deal(deal)
        cur = conn.execute(_INSERT_SQL, payload)
        new_id = cur.lastrowid
        # Если группа не задана явно, делаем её равной собственному id партии.
        if not payload.get("lot_group"):
            conn.execute("UPDATE deals SET lot_group = ? WHERE id = ?", (f"id:{new_id}", new_id))
        conn.commit()
        return new_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_deals():
    """Все партии списком словарей, отсортированные по дате покупки, затем id."""
    import sqlite3
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM deals").fetchall()
    finally:
        conn.close()
    deals = [dict(r) for r in rows]
    deals.sort(key=lambda d: (_parse_iso(d.get("buy_date")) or date.max, d.get("id", 0)))
    return deals


def apply_changes(updates, inserts, delete_ids):
    """Применяет точечные изменения в ОДНОЙ транзакции (стабильные id).

        updates    — список словарей с обязательным ключом 'id' (UPDATE по id);
        inserts    — список словарей без id (INSERT, id назначит база);
        delete_ids — список id для удаления.

    Используется для сохранения правок из таблицы: существующие строки
    обновляются по id (id НЕ меняются), удалённые — удаляются, новые —
    добавляются. Это не трогает id остальных строк и не «раздувает» счётчик.
    """
    conn = get_conn()
    try:
        if delete_ids:
            conn.executemany("DELETE FROM deals WHERE id=?", [(i,) for i in delete_ids])
        for u in updates:
            payload = _normalize_deal(u)
            payload["id"] = int(u["id"])
            conn.execute(_UPDATE_SQL, payload)
        for x in inserts:
            payload = _normalize_deal(x)
            cur = conn.execute(_INSERT_SQL, payload)
            if not payload.get("lot_group"):
                conn.execute("UPDATE deals SET lot_group = ? WHERE id = ?",
                             (f"id:{cur.lastrowid}", cur.lastrowid))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replace_lot_with_sale(deals, lot_id, sell_qty, sell_date_iso,
                          site_sell_price, sales_fee_pct, sell_rate, sold_at=None):
    """Оформляет продажу части открытой партии через ТОЧЕЧНЫЕ правки по id.

    Возвращает (updates, inserts, delete_ids) для apply_changes:
        * исходная открытая партия превращается (UPDATE по её id) в закрытую на
          проданное количество (курс продажи фиксируется отдельно);
        * если остался хвост — добавляется (INSERT) новая открытая партия;
        * если продаётся весь остаток — хвоста нет.
    Курс покупки наследуется от исходной партии. Сумма «продано + остаток» равна
    исходному количеству, баланс предметов сохраняется.
    """
    lot = next((d for d in deals if d.get("id") == lot_id and not _to_bool(d.get("sold"))), None)
    if lot is None:
        return [], [], []
    open_qty = max(1, _to_int(lot.get("quantity"), 1))
    q = max(1, min(_to_int(sell_qty, 1), open_qty))
    remaining = open_qty - q
    pf = {k: lot.get(k) for k in PURCHASE_FIELDS}
    # Гарантируем общую группу для проданной части и остатка (для корректной сверки).
    group = str(lot.get("lot_group") or "").strip() or f"id:{lot_id}"
    pf["lot_group"] = group

    sold_lot = _normalize_deal({
        **pf, "quantity": q, "sold": 1, "sell_date": sell_date_iso,
        "site_sell_price": site_sell_price, "sales_fee_pct": sales_fee_pct,
        "sell_uah_per_usd": sell_rate,
        # Момент записи продажи: по нему строится история «что продал последним».
        "sold_at": sold_at or now_stamp(),
    })
    sold_lot["id"] = lot_id  # переиспользуем существующий id (UPDATE)

    inserts = []
    if remaining > 0:
        inserts.append(_normalize_deal({
            **pf, "quantity": remaining, "sold": 0, "sell_date": "",
            "site_sell_price": 0.0, "sales_fee_pct": 0.0, "sell_uah_per_usd": 0.0,
        }))
    return [sold_lot], inserts, []


# ===========================================================================
# СОПОСТАВЛЕНИЕ ПРАВОК РЕДАКТОРА С id (для точечного сохранения)
# ===========================================================================

def _stamp_if_new_sale(row, stamp, was_sold=False):
    """Ставит отметку времени записи продажи (sold_at), если партия СТАЛА проданной.

    Отметка проставляется только когда партия помечена «Продано», отметки ещё нет И
    прежде она НЕ была проданной (was_sold=False — новая строка или только что снятая
    галочка «Продано» → поставленная обратно). Для НОВОЙ строки was_sold=False.

    Почему важно was_sold: у архивных записей (внесённых до появления колонки sold_at)
    это поле пустое. Без проверки правка любого поля такой записи — даже опечатки в
    названии — проставила бы текущее время, и старая продажа «всплыла» бы в истории
    как только что совершённая. Прежнее sold_at при этом никогда не перезаписывается.
    """
    if (_to_bool(row.get("sold")) and not was_sold
            and not str(row.get("sold_at") or "").strip()):
        row["sold_at"] = stamp


def _has_sale_data(row):
    """Есть ли в строке хоть какие-то данные продажи (дата/цена/комиссия/курс)."""
    return (not _is_blank(row.get("sell_date"))
            or _to_float(row.get("site_sell_price"), 0.0) > 0
            or _to_float(row.get("sales_fee_pct"), 0.0) > 0
            or _to_float(row.get("sell_uah_per_usd"), 0.0) > 0)


def sale_data_without_flag(updates, inserts, originals_by_id):
    """Строки, где заполнены данные продажи, но не отмечено «Продано».

    Такие строки — ловушка: _normalize_deal (по замыслу) очищает поля продажи у
    открытых партий, поэтому при сохранении введённые цена/дата/комиссия/курс были
    бы СТЁРТЫ без следа. Возвращаем их названия, чтобы предупредить ДО записи.

    Снятие галочки у ранее проданной партии — осознанное «открыть обратно», такие
    строки не считаются ошибкой.
    """
    bad = []
    for row in updates:
        original = originals_by_id.get(row.get("id")) or {}
        if _to_bool(original.get("sold")):
            continue                       # партия была продана -> это открытие назад
        if not _to_bool(row.get("sold")) and _has_sale_data(row):
            bad.append(str(row.get("item_name") or "").strip() or f"#{row.get('id')}")
    for row in inserts:
        if not _to_bool(row.get("sold")) and _has_sale_data(row):
            bad.append(str(row.get("item_name") or "").strip() or "(новая строка)")
    return bad


def reopened_sold_lots(updates, originals_by_id):
    """Партии, у которых в редакторе СНЯЛИ галочку «Продано».

    Это необратимо: _normalize_deal очистит цену, дату, комиссию и курс продажи, а
    также отметку записи (sold_at). Пользователь должен подтвердить это осознанно,
    поэтому такие строки возвращаются для предупреждения ДО сохранения.
    """
    reopened = []
    for row in updates:
        original = originals_by_id.get(row.get("id")) or {}
        if _to_bool(original.get("sold")) and not _to_bool(row.get("sold")):
            reopened.append(str(original.get("item_name") or "").strip()
                            or f"#{row.get('id')}")
    return reopened


def diff_editor_state(original_deals, editor_state, stamp=None):
    """Преобразует состояние st.data_editor в (updates, inserts, delete_ids).

    original_deals — список партий В ТОМ ЖЕ порядке, в каком строки поданы в
    редактор (позиция строки = индекс в этом списке). editor_state — словарь
    из st.session_state[key] с ключами 'edited_rows', 'added_rows',
    'deleted_rows'. Возвращает изменения, привязанные к стабильным id.

    Пустые/частично пустые добавленные строки отсекаются (нужны название или
    положительная цена покупки).

    sold_at: строки, которые в редакторе ТОЛЬКО ЧТО стали проданными (галочка
    «Продано») и не имеют отметки записи, получают её. У уже проданных строк
    отметка сохраняется — она приходит из исходной записи БД и не затирается
    при правке других полей.
    """
    stamp = stamp or now_stamp()
    edited = editor_state.get("edited_rows", {}) or {}
    added = editor_state.get("added_rows", []) or []
    deleted = editor_state.get("deleted_rows", []) or []

    # Удаления. Позиция меньше длины исходного списка -> удаление существующей партии
    # по id. Позиция ЗА его пределами -> это удалённая ТОЛЬКО ЧТО добавленная строка:
    # Streamlit оставляет её в added_rows и одновременно кладёт позицию в deleted_rows,
    # поэтому без явного отсева такая строка снова вставлялась бы в базу.
    delete_ids = []
    dropped_added = set()
    for pos in deleted:
        pos = int(pos)
        if 0 <= pos < len(original_deals):
            delete_ids.append(int(original_deals[pos]["id"]))
        else:
            dropped_added.add(pos - len(original_deals))

    # Изменения существующих строк -> применяем поверх исходных значений.
    # Если позиция выходит за пределы исходного списка (некоторые версии
    # Streamlit так представляют ПРАВКИ только что добавленных строк), трактуем
    # такую строку как новую вставку, а не отбрасываем её.
    updates = []
    edited_beyond = {}      # позиция за пределами исходных строк -> правки
    cleared_delete_ids = []
    for pos, changes in edited.items():
        pos = int(pos)
        if 0 <= pos < len(original_deals):
            base = dict(original_deals[pos])
            base.update(changes)  # перезаписываем только изменённые поля
            base["id"] = int(original_deals[pos]["id"])
            # Если существующую строку полностью очистили (не осталось ни названия,
            # ни цены, ни данных продажи, ни курса, ни отметки «Продано»), это
            # намерение УДАЛИТЬ её, а не сохранить пустой «призрак» с количеством 1.
            if not _meaningful_row(base):
                cleared_delete_ids.append(int(original_deals[pos]["id"]))
            else:
                # was_sold — прежнее состояние партии из БД: штампуем только переход
                # «не продано -> продано», а не правку уже проданной (в т.ч. архивной).
                _stamp_if_new_sale(base, stamp,
                                   was_sold=_to_bool(original_deals[pos].get("sold")))
                updates.append(base)
        else:
            # Правка строки за пределами исходных данных относится к ТОЛЬКО ЧТО
            # добавленной строке (так это представляют некоторые версии Streamlit).
            edited_beyond[pos] = dict(changes)

    # Новые строки -> INSERT. Правки с позициями за пределами исходного списка
    # НАКЛАДЫВАЮТСЯ на соответствующую добавленную строку, а не вставляются отдельно:
    # иначе одна и та же новая строка сохранилась бы дважды (и в added, и как правка).
    kept = [i for i in range(len(added)) if i not in dropped_added]
    added_rows = [dict(added[i]) for i in kept]
    added_index_map = {orig: new for new, orig in enumerate(kept)}
    orphan_edits = []
    for pos, changes in sorted(edited_beyond.items()):
        idx = pos - len(original_deals)
        if idx in dropped_added:
            continue                                  # строку удалили — правка не нужна
        target = added_index_map.get(idx)
        if target is not None:
            added_rows[target].update(changes)
        else:
            orphan_edits.append(changes)

    inserts = [row for row in added_rows if _meaningful_row(row)]
    inserts.extend(row for row in orphan_edits if _meaningful_row(row))
    for row in inserts:
        # У НОВОЙ строки пустое количество означает «одна штука», а не «ноль»: ноль
        # сделал бы запись битой и выкинул её из всех расчётов. Очистку количества у
        # СУЩЕСТВУЮЩЕЙ строки не трогаем — это осознанная правка.
        if _is_blank(row.get("quantity")):
            row["quantity"] = 1
        _stamp_if_new_sale(row, stamp)   # новая строка: прежнего «Продано» не было

    # Очищенные существующие строки удаляем (без дублей с явными удалениями).
    for did in cleared_delete_ids:
        if did not in delete_ids:
            delete_ids.append(did)

    return updates, inserts, delete_ids


def _meaningful_row(row):
    """True, если в строке есть осмысленная сделка.

    Строка считается заполненной, если задано НАЗВАНИЕ, либо положительная цена
    покупки, либо есть данные продажи (дата/цена), либо проставлен любой курс.
    Ни количество, ни одна лишь галочка «Продано» сами по себе строку осмысленной
    не делают: это характеристики реальной сделки, а не свидетельство её наличия.
    Поэтому пустая строка, где случайно тронули только количество или только
    чекбокс «Продано», не превращается в фантомную сделку. «Бесплатные» дропы
    (цена 0) при наличии названия по-прежнему сохраняются.
    """
    name = str(_num(row.get("item_name"), "") or "").strip()
    price = _to_float(row.get("steam_buy_price"), 0.0)
    has_sale = (not _is_blank(row.get("sell_date"))) or _to_float(row.get("site_sell_price"), 0.0) > 0
    has_rate = _to_float(row.get("buy_uah_per_usd"), 0.0) > 0 or _to_float(row.get("sell_uah_per_usd"), 0.0) > 0
    return bool(name) or price > 0 or has_sale or has_rate


# ===========================================================================
# ПРОВЕРКА ДАННЫХ И СВЕРКА ПО ПОКУПКАМ
# ===========================================================================

def validate_deals(deals):
    """Список предупреждений о внутренне противоречивых записях.

    Правила:
        * партия отмечена проданной, но НЕ заполнено хотя бы одно из полей
          (дата продажи ИЛИ цена продажи) — строгая проверка, ловит и частично
          заполненные продажи;
        * заполнены данные продажи, но партия не отмечена «Продано»;
        * дата продажи раньше даты покупки;
        * дата покупки или продажи в будущем (вероятная опечатка);
        * партия с нулевым/пустым курсом покупки (долларовые суммы будут неверны);
        * проданная партия без курса продажи И без курса покупки (нет даже
          запасного курса для пересчёта выручки). Если курс покупки есть, он
          служит запасным курсом продажи, и предупреждение не выдаётся.
    """
    today = date.today()
    warnings = []
    for d in deals:
        name = (str(d.get("item_name") or "").strip()) or "без названия"
        sold = _to_bool(d.get("sold"))
        sell_date = _to_iso(d.get("sell_date"))
        sell_price = _to_float(d.get("site_sell_price"))
        has_any_sale = (sell_date != "") or sell_price > 0

        if sold and (sell_date == "" or sell_price <= 0):
            missing = []
            if sell_date == "":
                missing.append("дата продажи")
            if sell_price <= 0:
                missing.append("цена продажи")
            warnings.append(f"«{name}»: отмечено как продано, но не заполнено: {', и '.join(missing)}.")
        if (not sold) and has_any_sale:
            warnings.append(f"«{name}»: заполнены данные продажи, но партия не отмечена как «Продано».")

        bd = _parse_iso(d.get("buy_date"))
        sd = _parse_iso(d.get("sell_date"))
        if bd and sd and sd < bd:
            warnings.append(f"«{name}»: дата продажи ({sd.isoformat()}) раньше даты покупки ({bd.isoformat()}).")
        # Даты из будущего — вероятная опечатка.
        if bd and bd > today:
            warnings.append(f"«{name}»: дата покупки ({bd.isoformat()}) в будущем — проверь дату.")
        if sd and sd > today:
            warnings.append(f"«{name}»: дата продажи ({sd.isoformat()}) в будущем — проверь дату.")
        # Нет даты покупки у ПРОДАННОЙ партии: себестоимость считается, но срок
        # удержания (holding_days) посчитать нельзя — для завершённой сделки это важно.
        if sold and _to_iso(d.get("buy_date")) == "":
            warnings.append(f"«{name}»: продано, но не указана дата покупки — срок удержания не посчитать.")

        buy_rate = _to_float(d.get("buy_uah_per_usd"))
        sell_rate = _to_float(d.get("sell_uah_per_usd"))
        if buy_rate <= 0:
            warnings.append(f"«{name}»: не задан курс покупки (₴/$) — долларовые суммы будут некорректны.")
        # Про курс продажи предупреждаем, только если нет и курса покупки: при
        # наличии курса покупки он используется как запасной, и выручка считается
        # корректно — ругаться не на что (иначе это ложный шум на старых записях).
        if sold and sell_rate <= 0 and buy_rate <= 0:
            warnings.append(f"«{name}»: продано, но не задан ни курс продажи, ни курс покупки (₴/$) — "
                            "выручку в ₴ не на что пересчитать.")

        # Проверки уже сохранённых «мусорных» значений (например, после ручных
        # правок или из старой базы): количество, отрицательные суммы, проценты.
        if _to_int(d.get("quantity"), 0) < 1:
            warnings.append(f"«{name}»: количество меньше 1 — строка не участвует в расчётах.")
        if _to_float(d.get("steam_buy_price")) < 0:
            warnings.append(f"«{name}»: отрицательная цена покупки.")
        if _to_float(d.get("site_sell_price")) < 0:
            warnings.append(f"«{name}»: отрицательная цена продажи.")
        # Комиссия вне диапазона. Для >100% сообщение подробнее (в расчёте она
        # урезается до 100%), для <0 — короткое. Второй проверки ниже нет — во
        # избежание дублирующего предупреждения.
        fee = _to_float(d.get("sales_fee_pct"))
        if fee > 100.0:
            warnings.append(f"«{name}»: комиссия {fee:g}% больше 100% — в расчёте используется "
                            "100% (выручка обнуляется). Похоже на опечатку.")
        elif fee < 0:
            warnings.append(f"«{name}»: отрицательная комиссия продажи ({fee:g}%).")
        # Чистый плюс -100% и ниже делает множитель (1 + плюс/100) нулевым или
        # отрицательным, и реальная стоимость обнуляется — отчёты исказятся.
        dep = _to_float(d.get("deposit_profit_pct"))
        if dep <= -100.0:
            warnings.append(f"«{name}»: чистый плюс {dep:g}% (≤ −100%) обнуляет себестоимость — "
                            "проверь значение, иначе прибыль и ROI будут неверны.")
    def _label(deal):
        name = str(_num(deal.get("item_name"), "") or "").strip() or "(без названия)"
        return f"#{deal.get('id')} {name}" if deal.get("id") else name

    # (Проверка количества < 1 выполняется выше, в общем цикле, — здесь не дублируем.)

    # Открытая партия без даты покупки: дата нужна и для холда, и для сверки.
    for d in deals:
        if not _to_bool(d.get("sold")) and _to_iso(d.get("buy_date")) == "":
            warnings.append(f"{_label(d)}: открытая партия без даты покупки.")

    # Расхождения внутри одной покупки: сверка показывает данные ПЕРВОЙ партии группы,
    # поэтому опечатка в одной из частей молча маскируется.
    groups = {}
    for d in deals:
        gid = str(_num(d.get("lot_group"), "") or "").strip()
        if gid:
            groups.setdefault(gid, []).append(d)
    for gid, members in groups.items():
        if len(members) < 2:
            continue
        for field, title in (("item_name", "названию"), ("buy_date", "дате покупки"),
                             ("steam_buy_price", "цене покупки"),
                             ("deposit_profit_pct", "чистому плюсу пополнения"),
                             ("buy_uah_per_usd", "курсу покупки")):
            if field in ("item_name", "buy_date"):
                values = {str(_num(x.get(field), "") or "").strip() for x in members}
            else:
                values = {round(_to_float(x.get(field)), 2) for x in members}
            if len(values) > 1:
                warnings.append(
                    f"Партии одной покупки (группа {gid}) расходятся по {title}: "
                    f"{sorted(str(v) for v in values)}. Сверка покажет только первое значение.")

    return warnings


def reconcile_purchases(deals):
    """Группирует партии по ИСХОДНОЙ покупке и считает: продано / на руках / всего.

    Партии, отколовшиеся от одной покупки через частичную продажу, имеют общий
    идентификатор группы lot_group (если он есть в записи). Группировка идёт
    именно по нему, поэтому:
        * части одной покупки (одна открытая + несколько закрытых) сводятся
          в одну строку;
        * две НЕЗАВИСИМЫЕ покупки с одинаковыми параметрами НЕ сливаются —
          у них разные группы.
    Для записей без lot_group ключом группы служит собственный id партии, то есть
    по умолчанию они не схлопываются друг с другом.

    В строке показываются параметры покупки (берутся из любой партии группы;
    у частей одной покупки они одинаковы).
    """
    groups = {}
    for idx, d in enumerate(deals):
        gid = d.get("lot_group")
        if gid in (None, "", 0):
            did = d.get("id")
            # Нет группы: если есть id — ключ по нему; если id тоже нет (новая
            # строка из редактора), даём УНИКАЛЬНЫЙ ключ по позиции, чтобы разные
            # новые строки не схлопывались в одну (id:None).
            gid = f"id:{did}" if did not in (None, "", 0) else f"row:{idx}"
        g = groups.setdefault(str(gid), {
            "sold": 0, "open": 0,
            "name": (str(d.get("item_name") or "").strip()),
            "buy_date": _to_iso(d.get("buy_date")),
            "price": round(_to_float(d.get("steam_buy_price")), 2),
            "dep": round(_to_float(d.get("deposit_profit_pct")), 2),
            "brate": round(_to_float(d.get("buy_uah_per_usd")), 2),
        })
        qty = max(0, _to_int(d.get("quantity"), 0))
        if _to_bool(d.get("sold")):
            g["sold"] += qty
        else:
            g["open"] += qty

    rows = []
    for v in groups.values():
        rows.append({
            "Предмет": v["name"] or "—",
            "Куплено": v["buy_date"] or "—",
            "Цена закупки, ₴": v["price"],
            "Чистый плюс пополнения, %": v["dep"],
            "Курс покупки": v["brate"],
            "Продано": v["sold"],
            "На руках": v["open"],
            "Всего": v["sold"] + v["open"],
        })
    rows.sort(key=lambda r: (r["Предмет"], r["Куплено"]))
    return rows


# ===========================================================================
# ПОДГОТОВКА ДАННЫХ ДЛЯ ОТОБРАЖЕНИЯ
# ===========================================================================

def build_dataframe(deals):
    """DataFrame: поля ввода (два курса) + рассчитанные колонки.

    Сохраняет скрытый столбец _id (id партии) для точечного сохранения; он не
    показывается в редакторе (отключён в column_order), но позволяет привязать
    правки к стабильным id.
    """
    records = []
    for d in deals:
        sold = _to_bool(d.get("sold"))
        # Для проданной партии без заданного курса продажи используем курс покупки
        # (как описано в compute_deal); для открытой курс продажи не нужен (None).
        buy_rate = _to_float(d.get("buy_uah_per_usd"))
        sell_rate_raw = _to_float(d.get("sell_uah_per_usd"))
        if sold:
            sell_rate_arg = sell_rate_raw if sell_rate_raw > 0 else (buy_rate if buy_rate > 0 else None)
        else:
            sell_rate_arg = None
        calc = compute_deal(
            steam_buy_price=d.get("steam_buy_price", 0.0),
            quantity=d.get("quantity", 1),
            deposit_profit_pct=d.get("deposit_profit_pct", 0.0),
            buy_uah_per_usd=d.get("buy_uah_per_usd", 0.0),
            sell_uah_per_usd=sell_rate_arg,
            sold=sold,
            site_sell_price=d.get("site_sell_price", 0.0),
            sales_fee_pct=d.get("sales_fee_pct", 0.0),
        )
        roi = calc["roi_pct"]
        # Продажа считается ПОЛНОЙ, только если есть и цена, и дата продажи. Иначе
        # (галочка «Продано» без цены/даты) прибыль в журнале НЕ показывается —
        # чтобы число на экране не приняли за итог, который сводка всё равно не
        # учитывает. Такая строка получает отдельный статус «Закрыта (неполная)».
        sell_price_val = _to_float(d.get("site_sell_price"), 0.0)
        sell_date_val = _parse_iso(d.get("sell_date"))
        complete_sale = sold and sell_price_val > 0 and sell_date_val is not None
        if sold and not complete_sale:
            status = "Закрыта (неполная)"
        elif sold:
            status = "Закрыта"
        else:
            status = "Открыта"
        # Долларовую прибыль показываем только при заданном курсе покупки: без
        # него долларовая себестоимость = 0, и profit_usd был бы завышен (вся
        # выручка). Гривневую прибыль показываем всегда (она от курса не зависит).
        usd_defined = complete_sale and _to_float(d.get("buy_uah_per_usd")) > 0
        # ₴-прибыль имеет смысл только если известен ХОТЬ КАКОЙ-ТО курс: без курса
        # выручка в ₴ = 0, и журнал показывал бы «убыток на всю себестоимость» (ROI
        # -100%) там, где на самом деле просто нет данных.
        uah_defined = complete_sale and (_to_float(d.get("sell_uah_per_usd")) > 0
                                         or _to_float(d.get("buy_uah_per_usd")) > 0)
        # Некорректное количество (<1) исключаем из ВСЕХ расчётных колонок: иначе журнал
        # показал бы себестоимость/прибыль, посчитанную по форсированному количеству 1
        # (compute_deal), тогда как сводка такую строку исключает. Так journal и сводка
        # согласованы — для битого количества расчёт пуст (—).
        qty_ok = _to_int(d.get("quantity"), 0) >= 1
        records.append({
            "_id": d.get("id"),
            "_lot_group": d.get("lot_group", ""),
            "item_name": d.get("item_name", ""),
            "buy_date": _parse_iso(d.get("buy_date")),
            "steam_buy_price": _to_float(d.get("steam_buy_price")),
            "quantity": _to_int(d.get("quantity"), 0),
            "deposit_profit_pct": _to_float(d.get("deposit_profit_pct")),
            "buy_uah_per_usd": _to_float(d.get("buy_uah_per_usd")),
            "sold": sold,
            "sell_date": sell_date_val,
            "site_sell_price": sell_price_val,
            "sales_fee_pct": _to_float(d.get("sales_fee_pct")),
            "sell_uah_per_usd": _to_float(d.get("sell_uah_per_usd")),
            "real_cost_uah": round(calc["real_cost_uah"], 2) if qty_ok else None,
            "profit_uah": round(calc["profit_uah"], 2) if (uah_defined and qty_ok) else None,
            "profit_usd": round(calc["profit_usd"], 2) if (usd_defined and qty_ok) else None,
            "roi_pct": round(roi, 1) if (uah_defined and roi is not None and qty_ok) else None,
            # Скрытые НЕокруглённые значения — только для точных сумм в сводке
            # (в таблице не показываются). Суммирование округлённых копеек на
            # больших объёмах накопило бы погрешность; здесь её нет.
            "_raw_real_cost_uah": calc["real_cost_uah"] if qty_ok else None,
            "_raw_profit_uah": calc["profit_uah"] if (uah_defined and qty_ok) else None,
            "_raw_profit_usd": calc["profit_usd"] if (usd_defined and qty_ok) else None,
            # Для проданной партии без даты продажи срок удержания неизвестен: считать
            # «до сегодня» нельзя (предмет уже продан), поэтому — пусто, а не растущее число.
            "holding_days": (None if (sold and sell_date_val is None)
                             else holding_days(d.get("buy_date"), d.get("sell_date"))),
            "status": status,
        })

    _raw_cols = ["_raw_real_cost_uah", "_raw_profit_uah", "_raw_profit_usd"]
    columns = ["_id", "_lot_group"] + INPUT_COLUMNS + COMPUTED_COLUMNS + _raw_cols
    if not records:
        df = pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    else:
        df = pd.DataFrame(records, columns=columns)
    df["buy_date"] = pd.to_datetime(df["buy_date"], errors="coerce")
    df["sell_date"] = pd.to_datetime(df["sell_date"], errors="coerce")
    return df


def column_config():
    """Подписи, типы и форматы колонок для st.data_editor.

    У всех денежных полей и курсов формат %.2f и шаг 0.01 — сотые (например курс
    44.44) вводятся и сохраняются без округления до десятых.
    """
    return {
        "item_name": st.column_config.TextColumn("Предмет", width="medium"),
        "buy_date": st.column_config.DateColumn("Дата покупки", format="YYYY-MM-DD"),
        "steam_buy_price": st.column_config.NumberColumn("Покупка в Steam, ₴", min_value=0.0, step=0.01, format="%.2f"),
        "quantity": st.column_config.NumberColumn("Кол-во", min_value=1, step=1, format="%d"),
        "deposit_profit_pct": st.column_config.NumberColumn(
            "Чистый плюс пополнения, %", min_value=-99.9, step=0.01, format="%.2f",
            help="Насколько выгоднее пополнить баланс Steam, чем платить напрямую. "
                 "Потратил 10, получил 15 на баланс → 50%. Уменьшает себестоимость предмета."),
        "buy_uah_per_usd": st.column_config.NumberColumn("Курс покупки ₴/$", min_value=0.0, step=0.01, format="%.2f"),
        "sold": st.column_config.CheckboxColumn("Продано"),
        "sell_date": st.column_config.DateColumn("Дата продажи", format="YYYY-MM-DD"),
        "site_sell_price": st.column_config.NumberColumn("Продажа на сайте, $", min_value=0.0, step=0.01, format="%.2f"),
        "sales_fee_pct": st.column_config.NumberColumn("Комиссия сайта, %", min_value=0.0, max_value=100.0, step=0.01, format="%.2f"),
        "sell_uah_per_usd": st.column_config.NumberColumn("Курс продажи ₴/$", min_value=0.0, step=0.01, format="%.2f"),
        "real_cost_uah": st.column_config.NumberColumn("Реальная стоимость, ₴", format="%.2f"),
        "profit_uah": st.column_config.NumberColumn("Прибыль, ₴", format="%.2f"),
        "profit_usd": st.column_config.NumberColumn("Прибыль, $", format="%.2f"),
        "roi_pct": st.column_config.NumberColumn(
            "ROI ₴, %", format="%.1f",
            help="Доходность в гривне: прибыль ₴ ÷ реальная стоимость ₴. При девальвации "
                 "растёт, даже если долларовая доходность нулевая."),
        "holding_days": st.column_config.NumberColumn("Дней", format="%d"),
        "status": st.column_config.TextColumn("Статус"),
    }


def open_lots(deals):
    """Открытые партии (остатки на руках)."""
    return [d for d in deals if not _to_bool(d.get("sold")) and _to_int(d.get("quantity"), 0) > 0]


def lot_label(d):
    """Человекочитаемая подпись партии для выпадающего списка."""
    bdate = _to_iso(d.get("buy_date")) or "—"
    return (f"#{d.get('id')} · {d.get('item_name') or 'без названия'} · "
            f"{_to_int(d.get('quantity'), 0)} шт · куплено {bdate} по "
            f"{_to_float(d.get('steam_buy_price')):.2f} ₴/шт")


def lot_delete_label(d):
    """Подпись партии для удаления: базовая подпись + статус (продано/остаток)."""
    base = lot_label(d)
    if _to_bool(d.get("sold")):
        sdate = _to_iso(d.get("sell_date")) or "—"
        return base + f" · ПРОДАНО {sdate} по {_to_float(d.get('site_sell_price')):.2f} $/шт"
    return base + " · остаток на руках"


def _delta_str(roi):
    """Строка для delta метрики: '+12.3%' или None, если ROI не определён."""
    return f"{roi:+.1f}%" if roi is not None else None


# ===========================================================================
# UI: ПОКУПКА (создание партии; опционально — часть/всё уже продано)
# ===========================================================================

# ===========================================================================
# ОБМЕН ДАННЫМИ: ЭКСПОРТ И ИМПОРТ (ОДИН ФОРМАТ, ОДНИ ПРАВИЛА)
# ===========================================================================

def build_export_df(deals):
    """DataFrame единого формата обмена: сырые поля + расчётные (справочные).

    Сырые поля идут из БД как есть (включая id, группу партии и отметку записи
    продажи) — именно они читаются при импорте. Расчётные колонки берутся из того
    же build_dataframe, что и журнал, и при импорте игнорируются.
    """
    calc = build_dataframe(deals).set_index("_id")
    records = []
    for d in deals:
        row = {}
        for field, header in CSV_RAW_COLUMNS:
            val = d.get(field, "")
            if field in ("item_name", "lot_group", "sold_at"):
                val = _csv_safe_text(val)
            row[header] = val
        rid = d.get("id")
        for field, header in CSV_CALC_COLUMNS:
            try:
                row[header] = calc.at[rid, field] if rid in calc.index else None
            except KeyError:
                row[header] = None
        records.append(row)
    columns = [h for _f, h in CSV_RAW_COLUMNS] + [h for _f, h in CSV_CALC_COLUMNS]
    if not records:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})
    return pd.DataFrame(records, columns=columns)


def parse_import_csv(data):
    """Разбирает CSV импорта в нормализованные партии + отчёт для предпросмотра.

    БЕЗОПАСНОСТЬ: файл — это ДАННЫЕ, а не код. Ничего не выполняется и не
    вычисляется из файла: значения только парсятся pandas и приводятся теми же
    функциями (_to_float/_to_int/_to_bool/_normalize_deal), что и ручной ввод.
    Ограничены размер файла и число строк. Неизвестные и расчётные колонки
    игнорируются. Проверка — та же validate_deals, что и для журнала.

    Возвращает (rows, report). report: total / ready / skipped / warnings /
    ignored_columns либо error (тогда rows пуст).
    """
    if not data:
        return [], {"error": "Файл пустой."}
    if len(data) > MAX_IMPORT_BYTES:
        return [], {"error": f"Файл больше {MAX_IMPORT_BYTES // (1024 * 1024)} МБ — импорт отклонён."}
    # Excel в украинской/русской локали сохраняет CSV в cp1251 и с разделителем «;».
    # Жёсткая привязка к utf-8 и запятой ломала бы импорт такого файла целиком,
    # поэтому пробуем обе кодировки, а разделитель определяем автоматически
    # (sep=None + engine="python" — распознаёт «,», «;», табуляцию).
    df, last_error = None, None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False,
                             encoding=encoding, sep=None, engine="python")
            break
        except Exception as exc:
            last_error = exc
    if df is None:
        return [], {"error": f"Не удалось прочитать CSV: {last_error}"}
    if len(df) > MAX_IMPORT_ROWS:
        return [], {"error": f"Слишком много строк ({len(df)}), предел — {MAX_IMPORT_ROWS}."}

    mapping = {}
    for col in df.columns:
        field = IMPORT_HEADER_MAP.get(_norm_header(col))
        if field:
            mapping[col] = field
    if not ({"item_name", "buy_date", "steam_buy_price"} & set(mapping.values())):
        return [], {"error": "В файле нет колонок журнала (нужны «Предмет», «Дата покупки» "
                             "или «Цена покупки»). Похоже, это не файл экспорта."}

    # Если колонки количества в файле нет вовсе — это одна штука на строку, а не ноль:
    # с нулём каждая запись оказалась бы «битой» и выпала из всех расчётов.
    has_qty_column = "quantity" in set(mapping.values())
    extra_warnings = []
    if not has_qty_column:
        extra_warnings.append("В файле нет колонки количества — принято по 1 шт. на строку.")

    rows, skipped, unflagged = [], 0, []
    for _idx, r in df.iterrows():
        raw = {}
        for col, field in mapping.items():
            val = r[col]
            if field in ("item_name", "lot_group", "sold_at"):
                val = _csv_unquote(val)
            raw[field] = val
        if not has_qty_column:
            raw["quantity"] = 1
        if not _meaningful_row(raw):
            skipped += 1
            continue
        # Проверяем ДО нормализации: у неотмеченной как проданная строки поля продажи
        # будут очищены, и пользователь должен знать об этом заранее.
        if not _to_bool(raw.get("sold")) and _has_sale_data(raw):
            unflagged.append(str(raw.get("item_name") or "").strip() or "(без названия)")
        row = _normalize_deal(raw)
        rid = _to_int(raw.get("id"), 0)
        if rid > 0:
            row["id"] = rid
        rows.append(row)

    if unflagged:
        extra_warnings.append(
            f"У {len(unflagged)} строк заполнены данные продажи, но не стоит «Продано» — "
            f"эти поля при импорте будут очищены: {', '.join(unflagged[:10])}"
            + ("…" if len(unflagged) > 10 else ""))

    report = {
        "total": int(len(df)),
        "ready": len(rows),
        "skipped": skipped,
        "warnings": extra_warnings + validate_deals(rows),
        "ignored_columns": [str(c) for c in df.columns if c not in mapping],
    }
    return rows, report


def import_deals(rows, mode="append"):
    """Записывает импортированные партии в ОДНОЙ транзакции (всё или ничего).

        mode='replace' — ПОЛНАЯ ПЕРЕЗАПИСЬ: таблица очищается, затем пишутся строки
            файла. id из файла сохраняются, только если они корректны и уникальны у
            ВСЕХ строк; иначе id назначает база (так не возникнет коллизий).
        mode='append'  — ДОБАВЛЕНИЕ: id из файла игнорируются (их назначит база), а
            метки групп получают префикс импорта. Без префикса группа из файла
            (например 'id:5') могла бы совпасть с существующей, и сверка покупок
            склеила бы чужие партии в одну.

    Дубликаты не отсеиваются — строки записываются как есть. При любой ошибке
    выполняется откат: база остаётся в прежнем состоянии.
    """
    conn = get_conn()
    try:
        if mode == "replace":
            ids = [_to_int(r.get("id"), 0) for r in rows]
            keep_ids = bool(rows) and all(i > 0 for i in ids) and len(set(ids)) == len(ids)
            conn.execute("DELETE FROM deals")
            for r in rows:
                payload = _normalize_deal(r)
                if keep_ids:
                    payload["id"] = _to_int(r.get("id"), 0)
                    conn.execute(_INSERT_SQL_WITH_ID, payload)
                    rid = payload["id"]
                else:
                    rid = conn.execute(_INSERT_SQL, payload).lastrowid
                if not payload.get("lot_group"):
                    conn.execute("UPDATE deals SET lot_group = ? WHERE id = ?", (f"id:{rid}", rid))
        else:
            base = conn.execute("SELECT COALESCE(MAX(id), 0) FROM deals").fetchone()[0]
            tag = f"imp{_to_int(base, 0) + 1}"
            for r in rows:
                payload = _normalize_deal(r)
                group = payload.get("lot_group")
                if group:
                    payload["lot_group"] = f"{tag}:{group}"
                rid = conn.execute(_INSERT_SQL, payload).lastrowid
                if not payload.get("lot_group"):
                    conn.execute("UPDATE deals SET lot_group = ? WHERE id = ?", (f"id:{rid}", rid))
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ===========================================================================
# ИСТОРИЯ ПРОДАЖ
# ===========================================================================

def sold_lots(deals):
    """Закрытые партии (отмечены как «Продано»)."""
    return [d for d in deals if _to_bool(d.get("sold"))]


def sale_record_sort_key(d):
    """Порядок ЗАПИСИ продажи: сначала отметка sold_at, затем дата продажи, затем id.

    Записи без sold_at (внесённые до появления поля) идут ниже всех записей с
    отметкой: они были сделаны раньше, а их порядок внутри дня достоверно
    неизвестен — выдумывать его нельзя.
    """
    stamp = str(d.get("sold_at") or "").strip()
    return (1 if stamp else 0, stamp,
            (_parse_iso(d.get("sell_date")) or date.min).isoformat(),
            int(d.get("id") or 0))


def sale_date_sort_key(d):
    """Порядок по ДАТЕ продажи (бизнес-хронология), затем по моменту записи и id."""
    return ((_parse_iso(d.get("sell_date")) or date.min).isoformat(),
            str(d.get("sold_at") or "").strip(),
            int(d.get("id") or 0))


def sale_row(d):
    """Строка истории по закрытой партии.

    Деньги считаются тем же compute_deal, что и журнал/сводка, и по тем же
    правилам: прибыль и ROI показываются только для ПОЛНОЙ продажи (есть цена и
    дата), долларовая прибыль — только при известном курсе покупки, а битое
    количество (<1) обнуляет расчётные колонки. Иначе история противоречила бы
    сводке.
    """
    qty = _to_int(d.get("quantity"), 0)
    buy_rate = _to_float(d.get("buy_uah_per_usd"))
    sell_rate_raw = _to_float(d.get("sell_uah_per_usd"))
    sell_rate_arg = sell_rate_raw if sell_rate_raw > 0 else (buy_rate if buy_rate > 0 else None)
    calc = compute_deal(
        steam_buy_price=d.get("steam_buy_price", 0.0), quantity=qty,
        deposit_profit_pct=d.get("deposit_profit_pct", 0.0),
        buy_uah_per_usd=buy_rate, sell_uah_per_usd=sell_rate_arg, sold=True,
        site_sell_price=d.get("site_sell_price", 0.0),
        sales_fee_pct=d.get("sales_fee_pct", 0.0),
    )
    price = _to_float(d.get("site_sell_price"), 0.0)
    sell_d = _parse_iso(d.get("sell_date"))
    complete = price > 0 and sell_d is not None
    qty_ok = qty >= 1
    usd_defined = complete and buy_rate > 0
    # Без курса (ни продажи, ни покупки) гривневая выручка неизвестна: показывать
    # «убыток на всю себестоимость» нельзя — это ложь, а не результат сделки.
    uah_defined = complete and (sell_rate_raw > 0 or buy_rate > 0)
    roi = calc["roi_pct"]
    return {
        "Предмет": str(d.get("item_name") or "").strip() or "(без названия)",
        "Кол-во": qty,
        "Дата продажи": sell_d.isoformat() if sell_d else "—",
        "Записано": str(d.get("sold_at") or "").strip() or "—",
        "Цена продажи, $/шт": round(price, 2) if price > 0 else None,
        "Выручка, $": round(calc["revenue_usd"], 2) if (complete and qty_ok) else None,
        "Прибыль, $": round(calc["profit_usd"], 2) if (usd_defined and qty_ok) else None,
        "Прибыль, ₴": round(calc["profit_uah"], 2) if (uah_defined and qty_ok) else None,
        "ROI ₴, %": round(roi, 1) if (uah_defined and qty_ok and roi is not None) else None,
        "Курс продажи": round(sell_rate_raw, 2) if sell_rate_raw > 0 else None,
        "Статус": "Закрыта" if complete else "Закрыта (неполная)",
    }


def render_sales_history(deals):
    """История продаж: что продано последним и в каком порядке это записывалось."""
    st.subheader("📜 История продаж")
    sold = sold_lots(deals)
    if not sold:
        st.info("Продаж пока нет. Здесь появится список закрытых партий — от последней записи к первой.")
        return

    by_record = sorted(sold, key=sale_record_sort_key, reverse=True)
    by_date = sorted(sold, key=sale_date_sort_key, reverse=True)
    last = by_record[0]
    last_name = str(last.get("item_name") or "").strip() or "(без названия)"
    last_stamp = str(last.get("sold_at") or "").strip()
    last_sell = _parse_iso(last.get("sell_date"))
    st.success(
        f"**На чём остановился:** последняя запись — **{last_name}** · "
        f"{_to_int(last.get('quantity'), 0)} шт · дата продажи "
        f"{last_sell.isoformat() if last_sell else '—'} · "
        + (f"записано {last_stamp}" if last_stamp else "без отметки времени (старая запись)")
    )
    # Самая свежая ПО ДАТЕ продажи может быть другой партией (продажу могли внести
    # задним числом) — тогда показываем и её, чтобы не запутаться.
    newest = by_date[0]
    if newest.get("id") != last.get("id"):
        n_name = str(newest.get("item_name") or "").strip() or "(без названия)"
        n_date = _parse_iso(newest.get("sell_date"))
        st.caption(f"Самая свежая по дате продажи — другая партия: {n_name} "
                   f"({n_date.isoformat() if n_date else '—'}).")

    c1, c2, c3 = st.columns([2, 2, 1])
    order = c1.radio("Порядок", ["По порядку записи", "По дате продажи"],
                     horizontal=True, key="hist_order")
    names = sorted({str(d.get("item_name") or "").strip() or "(без названия)" for d in sold})
    item = c2.selectbox("Предмет", ["Все предметы"] + names, key="hist_item")
    limit_label = c3.selectbox("Показать", ["10", "25", "50", "все"], key="hist_limit")

    view = by_record if order == "По порядку записи" else by_date
    if item != "Все предметы":
        view = [d for d in view
                if (str(d.get("item_name") or "").strip() or "(без названия)") == item]
    total = len(view)
    if limit_label != "все":
        view = view[:int(limit_label)]

    rows = []
    for i, d in enumerate(view, start=1):
        rows.append({"№": i, **sale_row(d)})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(f"Показано {len(view)} из {total} закрытых партий. "
               "«Записано» — момент внесения продажи в журнал: именно по нему видно, "
               "на чём ты остановился (дату продажи можно поставить задним числом).")

    legacy = sum(1 for d in sold if not str(d.get("sold_at") or "").strip())
    if legacy:
        st.caption(f"⚠️ У {legacy} записей нет отметки времени — они внесены до появления "
                   "этого поля. Их порядок внутри дня достоверно неизвестен, поэтому в "
                   "режиме «по порядку записи» они идут в конце.")


def render_purchase():
    """Запись покупки. Создаёт открытую партию (остаток на руках)."""
    st.subheader("➕ Покупка")
    st.caption("Запиши покупку — создастся остаток на руках. Продажи (в том числе по частям "
               "в разные дни) оформляются ниже в «Продать из остатков».")

    col_buy, col_sell = st.columns(2)

    with col_buy:
        st.markdown("#### 🏪 Покупка в Steam")
        item_name = st.text_input("Название предмета", value="", key="buy_item",
                                  placeholder="напр. AK-47 | Redline (FT)")
        buy_d = st.date_input("Дата покупки", value=date.today(), key="buy_date", format="YYYY-MM-DD")
        steam_price = st.number_input("Цена покупки в Steam, ₴ (за 1 шт.)",
                                      min_value=0.0, value=0.0, step=0.01, format="%.2f", key="buy_price")
        qty_bought = st.number_input("Сколько куплено", min_value=1, value=1, step=1, key="buy_qty")
        deposit_profit = st.number_input("Чистый плюс пополнения, %",
                                         min_value=-99.9, value=DEFAULT_DEPOSIT_PROFIT,
                                         step=0.01, format="%.2f", key="buy_deposit",
                                         help="Насколько выгоднее пополнить баланс Steam, чем платить "
                                              "напрямую. Потратил 10, получил 15 на баланс → 50%. "
                                              "Уменьшает себестоимость предмета.")
        buy_rate = st.number_input("Курс на момент покупки: ₴ в 1 $",
                                   min_value=0.01, value=DEFAULT_RATE, step=0.01, format="%.2f", key="buy_rate",
                                   help="Фиксирует долларовую себестоимость. Не меняется при будущих продажах.")

    with col_sell:
        st.markdown("#### 💳 Уже продано? (необязательно)")
        sold_now = st.checkbox("Часть или всё уже продано", value=False, key="buy_sold_now",
                               help="Включи, если оформляешь уже завершённую сделку. "
                                    "Иначе продажу можно записать позже в «Продать из остатков».")
        # Streamlit падает, если значение в session_state больше нового max_value
        # (уменьшили купленное количество, а «сколько продано» осталось прежним).
        if _to_int(st.session_state.get("buy_qty_sold"), 0) > int(qty_bought):
            st.session_state["buy_qty_sold"] = int(qty_bought)
        qty_sold = st.number_input("Сколько продано", min_value=0, max_value=int(qty_bought),
                                   value=int(qty_bought), step=1, key="buy_qty_sold", disabled=not sold_now)
        sell_d = st.date_input("Дата продажи", value=date.today(), key="buy_sell_date",
                               format="YYYY-MM-DD", disabled=not sold_now)
        site_price = st.number_input("Цена продажи на сайте, $ (за 1 шт.)",
                                     min_value=0.0, value=0.0, step=0.01, format="%.2f",
                                     key="buy_site_price", disabled=not sold_now)
        sales_fee = st.number_input("Комиссия сайта за продажу, %",
                                    min_value=0.0, max_value=100.0, value=DEFAULT_SALES_FEE,
                                    step=0.01, format="%.2f", key="buy_sales_fee", disabled=not sold_now)
        sell_rate = st.number_input("Курс на момент продажи: ₴ в 1 $",
                                    min_value=0.01, value=DEFAULT_RATE, step=0.01, format="%.2f",
                                    key="buy_sell_rate", disabled=not sold_now,
                                    help="Используется только если часть продаётся сразу.")

    # --- Предпросмотр ---
    st.markdown("##### 📊 Предпросмотр")
    cost_all = compute_deal(steam_price, qty_bought, deposit_profit, buy_rate, sold=False)
    c1, c2, c3 = st.columns(3)
    c1.metric("Стоимость всей покупки", f"{cost_all['real_cost_uah']:,.2f} ₴",
              help=f"≈ {cost_all['real_cost_usd']:,.2f} $ по курсу покупки")

    will_sell = min(int(qty_sold), int(qty_bought)) if sold_now else 0
    if sold_now and will_sell > 0:
        sale = compute_deal(steam_price, will_sell, deposit_profit, buy_rate, sell_uah_per_usd=sell_rate,
                            sold=True, site_sell_price=site_price, sales_fee_pct=sales_fee)
        c2.metric(f"Прибыль с продажи ({will_sell} шт)", f"{sale['profit_uah']:,.2f} ₴",
                  delta=_delta_str(sale["roi_pct"]))
        # Без курса покупки долларовая себестоимость = 0, и «прибыль в $» равнялась бы
        # ВСЕЙ выручке. Показываем «н/д» вместо завышенной цифры.
        if buy_rate > 0:
            c3.metric("Прибыль, $", f"{sale['profit_usd']:,.2f} $")
        else:
            c3.metric("Прибыль, $", "н/д",
                      help="Не задан курс покупки — долларовую прибыль посчитать не из чего.")
        remaining = int(qty_bought) - will_sell
        if remaining > 0:
            st.info(f"Будет создано: закрытая партия {will_sell} шт + открытый остаток {remaining} шт.")
        else:
            st.info(f"Будет создана закрытая партия на {will_sell} шт (остатка нет).")
    else:
        c2.metric("Статус", "Открытая позиция")
        st.info(f"Будет создан остаток на руках: {int(qty_bought)} шт. Прибыль появится после продажи.")

    # --- Сохранение ---
    if st.button("💾 Добавить покупку", type="primary", use_container_width=True):
        if not item_name.strip() and steam_price == 0.0:
            st.warning("Укажи хотя бы название предмета или цену покупки.")
            return
        purchase = {
            "item_name": item_name.strip(), "buy_date": _to_iso(buy_d),
            "steam_buy_price": float(steam_price), "deposit_profit_pct": float(deposit_profit),
            "buy_uah_per_usd": float(buy_rate),
        }
        try:
            if sold_now and will_sell > 0:
                # Закрытая часть и остаток одной покупки записываются АТОМАРНО и с
                # общей группой (insert_deals_atomic назначит её по id первой части).
                lots = [{**purchase, "quantity": will_sell, "sold": 1,
                         "sell_date": _to_iso(sell_d), "site_sell_price": float(site_price),
                         "sales_fee_pct": float(sales_fee), "sell_uah_per_usd": float(sell_rate),
                         "sold_at": now_stamp()}]
                remaining = int(qty_bought) - will_sell
                if remaining > 0:
                    lots.append({**purchase, "quantity": remaining, "sold": 0, "sell_date": "",
                                 "site_sell_price": 0.0, "sales_fee_pct": 0.0, "sell_uah_per_usd": 0.0})
                insert_deals_atomic(lots)
            else:
                insert_deal({**purchase, "quantity": int(qty_bought), "sold": 0, "sell_date": "",
                             "site_sell_price": 0.0, "sales_fee_pct": 0.0, "sell_uah_per_usd": 0.0})
        except Exception as e:
            st.error(f"Не удалось сохранить покупку в базу: {e}. Данные не записаны — попробуй ещё раз.")
        else:
            st.success("Покупка добавлена.")
            st.rerun()


# ===========================================================================
# UI: ПРОДАЖА ИЗ ОСТАТКОВ (частичная или полная продажа открытой партии)
# ===========================================================================

def render_sell_from_holdings(deals):
    """Продажа из выбранной открытой партии — полностью или частично."""
    st.subheader("💰 Продать из остатков")

    lots = open_lots(deals)
    if not lots:
        st.info("Открытых позиций нет. Сначала добавь покупку выше.")
        return

    st.caption("Выбери позицию и укажи, сколько продаёшь. Можно продать часть — остаток "
               "сохранится на руках, и его можно будет продать позже по другой цене.")

    lot_map = {int(d["id"]): d for d in lots}
    selected_id = st.selectbox("Позиция (остаток на руках)", list(lot_map.keys()),
                               format_func=lambda i: lot_label(lot_map[i]), key="sell_lot")
    lot = lot_map[selected_id]
    open_qty = _to_int(lot["quantity"], 1)

    # Курс покупки берём строго из записи; если его там нет — честно предупреждаем.
    lot_buy_rate = _to_float(lot.get("buy_uah_per_usd"))
    if lot_buy_rate <= 0:
        st.warning("У этой партии не задан курс покупки — долларовая себестоимость будет неверной. "
                   "Исправь курс покупки в журнале ниже.")

    col_a, col_b = st.columns(2)
    with col_a:
        sell_qty = st.number_input("Сколько продать", min_value=1, max_value=open_qty,
                                   value=open_qty, step=1, key=f"sell_qty_{selected_id}")
        sell_d = st.date_input("Дата продажи", value=date.today(),
                               key=f"sell_date_{selected_id}", format="YYYY-MM-DD")
    with col_b:
        site_price = st.number_input("Цена продажи на сайте, $ (за 1 шт.)", min_value=0.0,
                                     value=0.0, step=0.01, format="%.2f", key=f"sell_price_{selected_id}")
        sales_fee = st.number_input("Комиссия сайта за продажу, %", min_value=0.0, max_value=100.0,
                                    value=DEFAULT_SALES_FEE, step=0.01, format="%.2f", key=f"sell_fee_{selected_id}")
    sell_rate = st.number_input("Курс на момент ЭТОЙ продажи: ₴ в 1 $", min_value=0.01,
                                value=DEFAULT_RATE, step=0.01, format="%.2f", key=f"sell_rate_{selected_id}",
                                help="Курс именно на дату продажи. Себестоимость считается по курсу покупки отдельно.")

    # --- Предпросмотр продажи выбранного количества (два курса) ---
    sale = compute_deal(lot.get("steam_buy_price", 0.0), int(sell_qty),
                        lot.get("deposit_profit_pct", 0.0),
                        buy_uah_per_usd=lot_buy_rate, sell_uah_per_usd=sell_rate,
                        sold=True, site_sell_price=site_price, sales_fee_pct=sales_fee)
    st.markdown("##### 📊 Предпросмотр продажи")
    c1, c2, c3 = st.columns(3)
    c1.metric("Реальная стоимость", f"{sale['real_cost_uah']:,.2f} ₴", help=f"≈ {sale['real_cost_usd']:,.2f} $ по курсу покупки")
    c2.metric("Прибыль", f"{sale['profit_uah']:,.2f} ₴", delta=_delta_str(sale["roi_pct"]))
    # То же самое: без курса покупки $-прибыль была бы равна всей выручке.
    if lot_buy_rate > 0:
        c3.metric("Прибыль, $", f"{sale['profit_usd']:,.2f} $")
    else:
        c3.metric("Прибыль, $", "н/д",
                  help="Не задан курс покупки — долларовую прибыль посчитать не из чего.")
    remaining = open_qty - int(sell_qty)
    if remaining > 0:
        st.caption(f"После продажи на руках останется {remaining} шт (открытая позиция).")
    else:
        st.caption("Продаётся весь остаток — позиция закроется полностью.")

    if st.button("💾 Записать продажу", type="primary", use_container_width=True):
        try:
            updates, inserts, dels = replace_lot_with_sale(
                deals, selected_id, int(sell_qty), _to_iso(sell_d),
                float(site_price), float(sales_fee), float(sell_rate))
            # Пустой результат = партия не найдена или уже продана (её могли изменить
            # в другой вкладке). Тогда писать нечего и «успех» показывать нельзя.
            if updates:
                apply_changes(updates, inserts, dels)
        except Exception as e:
            st.error(f"Не удалось записать продажу: {e}. Данные не изменены — попробуй ещё раз.")
        else:
            if not updates:
                st.warning("Продажа не записана: партия не найдена или уже продана. "
                           "Обнови страницу и попробуй снова.")
            else:
                st.success("Продажа записана.")
                st.rerun()


# ===========================================================================
# UI: ЖУРНАЛ (редактируемая таблица партий) + ПРОВЕРКА ДАННЫХ
# ===========================================================================

def render_data_checks(deals, label_suffix=""):
    """Блок проверки: предупреждения о несоответствиях и сверка по покупкам."""
    warns = validate_deals(deals)
    recon = reconcile_purchases(deals)
    title = "🔎 Проверка данных и сверка по покупкам"
    if label_suffix:
        title += f" ({label_suffix})"
    if warns:
        title += f" — ⚠️ {len(warns)}"
    with st.expander(title, expanded=bool(warns)):
        if warns:
            for w in warns:
                st.warning(w)
        else:
            st.success("Несоответствий не найдено.")
        if recon:
            st.markdown("##### Сверка по покупкам")
            st.dataframe(pd.DataFrame(recon), hide_index=True, use_container_width=True)
            st.caption("«Всего» = продано + на руках по каждой покупке. Если это число не "
                       "совпадает с тем, что ты реально покупал — поправь количества в журнале.")


def _filter_and_sort_deals(deals, query, sort_mode):
    """Возвращает партии для отображения: фильтр по названию + сортировка.

    ВАЖНО: порядок этого списка должен совпадать с порядком строк в редакторе,
    потому что сохранение правок привязывает позицию строки к элементу этого
    же списка (а через него — к стабильному id). Поэтому ровно этот список
    передаётся и в build_dataframe, и в diff_editor_state.

    query     — подстрока названия (регистронезависимо); пусто => без фильтра.
    sort_mode — 'Дата (новые сверху)' | 'Дата (старые сверху)' |
                'Название (А→Я)' | 'Название (Я→А)'.
    """
    q = (query or "").strip().lower()
    if q:
        view = [d for d in deals if q in (str(d.get("item_name") or "").lower())]
    else:
        view = list(deals)

    def _name_key(d):
        return (str(d.get("item_name") or "").strip().lower(), d.get("id", 0))

    def _date_key(d):
        return (_parse_iso(d.get("buy_date")) or date.min, d.get("id", 0))

    def _date_key_asc(d):
        # При «старые сверху» партии БЕЗ даты не должны прыгать наверх: первый элемент
        # ключа держит их внизу (True сортируется после False).
        parsed = _parse_iso(d.get("buy_date"))
        return (parsed is None, parsed or date.min, d.get("id", 0))

    if sort_mode == "Название (А→Я)":
        view.sort(key=_name_key)
    elif sort_mode == "Название (Я→А)":
        view.sort(key=_name_key, reverse=True)
    elif sort_mode == "Дата (старые сверху)":
        view.sort(key=_date_key_asc)
    else:  # 'Дата (новые сверху)' — по умолчанию
        view.sort(key=_date_key, reverse=True)
    return view


def render_ledger(deals):
    """Редактируемая таблица всех партий: правка любых полей и удаление строк.

    Сохранение — точечными UPDATE/INSERT/DELETE по стабильным id (id строк не
    меняются). Предупреждения и сверка показываются и по сохранённым данным, и
    по предварительному состоянию из текущих правок редактора.

    Поиск и сортировка не влияют на сохранность данных: правки привязываются к
    строке по её скрытому id, а не по позиции на экране, поэтому фильтрация и
    смена порядка строк безопасны.
    """
    st.subheader("📒 Журнал партий")

    if not deals:
        st.info("Журнал пуст. Добавь первую покупку выше.")
        return

    # --- Поиск по названию + сортировка ---
    col_search, col_sort = st.columns([2, 1])
    with col_search:
        query = st.text_input("🔍 Поиск по названию предмета", value="",
                              key="ledger_search",
                              placeholder="например: AK-47 или Redline").strip()
    with col_sort:
        sort_mode = st.selectbox(
            "Сортировка",
            ["Дата (новые сверху)", "Дата (старые сверху)", "Название (А→Я)", "Название (Я→А)"],
            index=0, key="ledger_sort")

    view_deals = _filter_and_sort_deals(deals, query, sort_mode)

    if query and not view_deals:
        st.warning(f"По запросу «{query}» ничего не найдено. Очисти поиск, чтобы увидеть все партии.")
        return
    if query:
        st.caption(f"Найдено партий: {len(view_deals)} из {len(deals)}. "
                   "Правки в отфильтрованном виде сохраняются в правильные партии. "
                   "⚠️ Несохранённые правки сбросятся при смене текста поиска или сортировки — "
                   "сначала нажми «Сохранить изменения».")
    else:
        st.caption("Каждая строка — партия (часть покупки). Любое поле, включая количество и оба "
                   "курса, можно править прямо здесь; строки можно удалять (выдели строку → Delete). "
                   "Серые колонки считаются автоматически. После правок нажми «Сохранить изменения». "
                   "Правки ручные и не пересчитывают другие партии — сверяйся с блоком ниже.")

    df = build_dataframe(view_deals)
    # Ключ редактора зависит от вида (число строк + поиск + сортировка) И от содержимого
    # видимых партий: при смене вида ИЛИ после сохранения (данные изменились, но число
    # строк осталось прежним) Streamlit пересоздаёт виджет, а не подмешивает устаревшие
    # правки. Подпись считается по СОХРАНЁННЫМ данным (view_deals), поэтому во время
    # редактирования (до сохранения) ключ стабилен и правки не сбрасываются.
    import hashlib
    data_sig = hashlib.md5(repr([
        (d.get("id"), d.get("item_name"), _to_iso(d.get("buy_date")),
         _to_float(d.get("steam_buy_price")), _to_int(d.get("quantity"), 0),
         _to_float(d.get("deposit_profit_pct")), _to_float(d.get("buy_uah_per_usd")),
         _to_bool(d.get("sold")), _to_iso(d.get("sell_date")),
         _to_float(d.get("site_sell_price")), _to_float(d.get("sales_fee_pct")),
         _to_float(d.get("sell_uah_per_usd")))
        for d in view_deals
    ]).encode("utf-8")).hexdigest()[:10]
    view_sig = f"{len(df)}_{query.lower()}_{sort_mode}_{data_sig}"
    editor_key = f"ledger_editor_{view_sig}"
    column_order = INPUT_COLUMNS + COMPUTED_COLUMNS  # _id и _lot_group скрыты
    edited_df = st.data_editor(
        df, column_config=column_config(), disabled=COMPUTED_COLUMNS,
        num_rows="dynamic", hide_index=True, use_container_width=True,
        key=editor_key, column_order=column_order,
    )

    # Предварительная проверка по ТЕКУЩИМ правкам в редакторе (а не только по БД).
    preview_visible = dataframe_to_preview_deals(edited_df)

    # Если активен поиск/сортировка, на экране лишь срез базы. Чтобы проверка
    # охватывала ВСЕ партии (а не только видимые), накладываем правки с экрана
    # поверх полного списка: видимые строки берём из предпросмотра (по id), а
    # скрытые — из полной базы как есть. Новые строки (без id) добавляем тоже.
    if query:
        visible_by_id = {d["id"]: d for d in preview_visible if "id" in d}
        visible_ids = set(visible_by_id)
        view_ids = {d.get("id") for d in view_deals}
        check_deals = []
        for d in deals:
            did = d.get("id")
            if did in visible_by_id:
                check_deals.append(visible_by_id[did])      # правленая версия
            elif did in view_ids and did not in visible_ids:
                continue  # видимая строка была удалена на экране — пропускаем
            else:
                check_deals.append(d)                        # скрытая строка как есть
        check_deals.extend(d for d in preview_visible if "id" not in d)  # новые строки
    else:
        check_deals = preview_visible

    # Снятие галочки «Продано» СТИРАЕТ данные продажи и отметку записи — необратимо.
    # Поэтому предупреждаем и просим подтверждение ДО нажатия «Сохранить».
    pending_state = st.session_state.get(editor_key, {}) or {}
    pending_reopen = []
    if pending_state:
        _pu, _pi, _pd = diff_editor_state(view_deals, pending_state)
        pending_reopen = reopened_sold_lots(_pu, {d.get("id"): d for d in view_deals})
    if pending_reopen:
        st.warning("Снятие галочки «Продано» удалит данные продажи (цена, дата, комиссия, "
                   "курс и отметку записи) у: " + ", ".join(pending_reopen))
        st.checkbox("Понимаю: данные продажи этих партий будут удалены", key="reopen_confirm")

    col_save, col_info = st.columns([1, 3])
    with col_save:
        if st.button("💾 Сохранить изменения", type="primary", use_container_width=True):
            state = st.session_state.get(editor_key, {})
            # Диффим относительно ОТОБРАЖАЕМОГО среза в том же порядке — привязка
            # позиция->id остаётся верной даже при поиске и сортировке.
            updates, inserts, dels = diff_editor_state(view_deals, state)
            # ЗАЩИТА ОТ МОЛЧАЛИВОЙ ПОТЕРИ ДАННЫХ: заполнены поля продажи, но галочка
            # «Продано» не стоит -> при записи они были бы стёрты. Не сохраняем.
            unflagged = sale_data_without_flag(updates, inserts,
                                               {d.get("id"): d for d in view_deals})
            if pending_reopen and not st.session_state.get("reopen_confirm"):
                st.error("Не сохранено: снятие галочки «Продано» удалит данные продажи. "
                         "Подтверди это галочкой выше или верни «Продано» на место.")
            elif unflagged:
                st.error("Не сохранено: у этих строк заполнены данные продажи, но не отмечено "
                         "«Продано» — иначе они были бы стёрты. Поставь галочку или очисти "
                         "поля продажи: " + ", ".join(unflagged))
            elif not updates and not inserts and not dels:
                st.info("Нет изменений для сохранения.")
            else:
                # Защита от повторного применения одного и того же набора правок
                # (например, при двойном клике до перерисовки).
                import json
                sig = json.dumps({
                    "u": sorted((u.get("id"), tuple(sorted((k, str(v)) for k, v in u.items())))
                                for u in updates),
                    "i": len(inserts), "d": sorted(dels),
                }, default=str, sort_keys=True)
                if st.session_state.get("_last_save_sig") == sig:
                    st.info("Эти изменения уже сохранены.")
                else:
                    try:
                        apply_changes(updates, inserts, dels)
                    except Exception as e:
                        st.error(f"Не удалось сохранить изменения: {e}. Данные не записаны — попробуй ещё раз.")
                    else:
                        st.session_state["_last_save_sig"] = sig
                        st.success(f"Сохранено: изменено {len(updates)}, добавлено {len(inserts)}, удалено {len(dels)}.")
                        st.rerun()
    with col_info:
        st.caption("Изменения в таблице не сохранятся, пока не нажата кнопка.")

    # Проверка охватывает ВСЮ базу (с учётом правок на экране), даже при активном
    # поиске — иначе несоответствия в скрытых строках остались бы незамеченными.
    check_label = "вся база, с учётом правок на экране" if query else "предпросмотр изменений на экране"
    render_data_checks(check_deals, label_suffix=check_label)


def dataframe_to_preview_deals(df):
    """Преобразует ТЕКУЩИЙ датафрейм редактора в список партий для предпросмотра.

    Используется только для предварительной проверки/сверки (НЕ для записи).
    В отличие от записи, здесь поля продажи НЕ обнуляются при снятой галочке
    «Продано»: иначе предпросмотр не смог бы предупредить о введённых данных
    продажи без отметки (и пользователь молча потерял бы их при сохранении).
    Значения только приводятся к корректным типам; смысл строки сохраняется.
    Количество тоже НЕ «чинится» до 1: если в строке стоит 0 или мусор, оно
    остаётся как есть (через _to_int с дефолтом 0 для пустых), чтобы проверка
    данных могла предупредить о некорректном количестве, а не сгладить его молча.
    """
    deals = []
    for _, row in df.iterrows():
        if not _meaningful_row(row):
            continue
        d = {
            "item_name": str(_num(row.get("item_name"), "") or "").strip(),
            "buy_date": _to_iso(row.get("buy_date", "")),
            "steam_buy_price": max(0.0, _to_float(row.get("steam_buy_price"), 0.0)),
            "quantity": _to_int(row.get("quantity"), 0),
            "deposit_profit_pct": _to_float(row.get("deposit_profit_pct"), 0.0),
            "buy_uah_per_usd": max(0.0, _to_float(row.get("buy_uah_per_usd"), 0.0)),
            "sold": 1 if _to_bool(row.get("sold")) else 0,
            "sell_date": _to_iso(row.get("sell_date", "")),
            "site_sell_price": max(0.0, _to_float(row.get("site_sell_price"), 0.0)),
            "sales_fee_pct": max(0.0, _to_float(row.get("sales_fee_pct"), 0.0)),
            "sell_uah_per_usd": max(0.0, _to_float(row.get("sell_uah_per_usd"), 0.0)),
        }
        if "_id" in row and not _is_blank(row.get("_id")):
            d["id"] = _to_int(row.get("_id"), 0)
        if "_lot_group" in row and not _is_blank(row.get("_lot_group")):
            d["lot_group"] = str(row.get("_lot_group")).strip()
        deals.append(d)
    return deals


# ===========================================================================
# UI: СВОДКА
# ===========================================================================

def render_summary(deals):
    """Итоги по всем партиям (в ₴ и $) и помесячная реализованная прибыль."""
    st.subheader("📈 Сводка")

    if not deals:
        st.info("Пока нет данных для сводки.")
        return

    df = build_dataframe(deals)
    # «Реализованными» считаем только ПОЛНЫЕ продажи: отмечены проданными, есть
    # цена И дата продажи И корректное количество (≥1). Требование даты — чтобы
    # общий итог и помесячный график охватывали ОДНИ И ТЕ ЖЕ строки. Битые строки
    # (quantity<1) исключаются из ВСЕХ метрик, чтобы итог не считался по
    # подменённым значениям (в расчёте количество форсится в 1, но в сводных
    # цифрах такие строки участвовать не должны).
    sold_mask = df["sold"] == True  # noqa: E712  (булева колонка)
    has_price = df["site_sell_price"].fillna(0) > 0
    has_date = df["sell_date"].notna()
    valid_qty = df["quantity"].fillna(0) >= 1
    has_buy_rate = df["buy_uah_per_usd"].fillna(0) > 0

    closed = df[sold_mask & has_price & has_date & valid_qty]
    # Долларовые итоги считаем ТОЛЬКО по строкам с заданным курсом покупки: без
    # него долларовая себестоимость = 0, и USD-прибыль/ROI были бы завышены.
    closed_usd = closed[closed["buy_uah_per_usd"].fillna(0) > 0]
    incomplete = df[sold_mask & ~(has_price & has_date)]
    bad_qty = df[sold_mask & has_price & has_date & ~valid_qty]
    open_pos = df[~sold_mask & valid_qty]

    # ₴-итоги — ТОЛЬКО по продажам с известным курсом (profit_uah не пуст). Иначе
    # сделка без курсов внесла бы в сумму «убыток на всю себестоимость» и занизила
    # общий ROI, хотя на деле по ней просто нет данных.
    closed_uah = closed[closed["profit_uah"].notna()]
    # Суммы — по НЕокруглённым (_raw_*) значениям, чтобы не копить погрешность
    # округления копеек на больших объёмах. Округляем только при выводе.
    realized_profit_uah = float(closed_uah["_raw_profit_uah"].sum()) if not closed_uah.empty else 0.0
    realized_profit_usd = float(closed_usd["_raw_profit_usd"].fillna(0).sum()) if not closed_usd.empty else 0.0
    closed_cost_uah = float(closed_uah["_raw_real_cost_uah"].fillna(0).sum()) if not closed_uah.empty else 0.0
    open_cost_uah = float(open_pos["_raw_real_cost_uah"].fillna(0).sum()) if not open_pos.empty else 0.0
    overall_roi = (realized_profit_uah / closed_cost_uah * 100.0) if closed_cost_uah > 0 else None
    # Долларовый ROI — по закрытым продажам с курсом покупки. Он показывает реальную
    # доходность в долларах: гривневый ROI при девальвации завышается (та же сделка в
    # $0 прибыли выглядит как плюс, потому что гривна подешевела). Долларовая
    # себестоимость = выручка − прибыль (в $), по определению compute_deal.
    if closed_usd.empty:
        overall_roi_usd = None
    else:
        cost_usd_series = (closed_usd["_raw_real_cost_uah"].fillna(0)
                           / closed_usd["buy_uah_per_usd"].replace(0, pd.NA))
        closed_cost_usd = float(cost_usd_series.fillna(0).sum())
        overall_roi_usd = (realized_profit_usd / closed_cost_usd * 100.0) if closed_cost_usd > 0 else None
    wins = int((closed_uah["_raw_profit_uah"] > 0).sum()) if not closed_uah.empty else 0
    win_rate = (wins / len(closed_uah) * 100.0) if len(closed_uah) > 0 else 0.0
    qty_open = int(open_pos["quantity"].sum()) if not open_pos.empty else 0
    # Сколько закрытых продаж без курса покупки (их $-прибыль не учтена).
    no_rate_count = int(len(closed) - len(closed_usd))
    # Сколько закрытых продаж вообще без курсов (не учтены и в ₴-итогах).
    no_uah_rate_count = int(len(closed) - len(closed_uah))

    m1, m2, m3 = st.columns(3)
    # ROI не определён при нулевой себестоимости (например, бесплатные дропы): показываем
    # «н/д», а не обманчивые 0%. Если закрытых продаж нет вовсе — дельту не показываем.
    if overall_roi is not None:
        m1.metric("Реализованная прибыль", f"{realized_profit_uah:,.2f} ₴", delta=f"{overall_roi:+.1f}% ROI ₴")
    elif not closed.empty:
        m1.metric("Реализованная прибыль", f"{realized_profit_uah:,.2f} ₴",
                  delta="ROI н/д (себестоимость 0)", delta_color="off")
    else:
        m1.metric("Реализованная прибыль", f"{realized_profit_uah:,.2f} ₴")
    usd_note = f"≈ {realized_profit_usd:,.2f} $"
    if overall_roi_usd is not None:
        usd_note += f" · ROI в $: {overall_roi_usd:+.1f}%"
    if no_rate_count > 0:
        usd_note += f" (без {no_rate_count} партий без курса покупки)"
    m1.caption(usd_note)
    if overall_roi is not None and overall_roi_usd is not None:
        m1.caption("ROI ₴ и ROI $ расходятся на величину изменения курса гривны: "
                   "гривневый растёт при девальвации, долларовый показывает чистую "
                   "доходность в валюте продажи.")
    if no_uah_rate_count > 0:
        m1.caption(f"⚠️ {no_uah_rate_count} закрытых партий вообще без курсов — они не учтены "
                   "и в гривневых итогах (иначе дали бы ложный убыток). Впиши курс в журнале.")
    m2.metric("В остатках на руках", f"{open_cost_uah:,.2f} ₴")
    m2.caption(f"{qty_open} шт в {len(open_pos)} открытых партиях")
    m3.metric("Доля прибыльных", f"{win_rate:.0f}%")
    m3.caption(f"{wins} из {len(closed_uah)} завершённых продаж с известным курсом")

    if not incomplete.empty:
        st.warning(f"Не учтено в итогах: {len(incomplete)} партий отмечены проданными, но без цены "
                   "или даты продажи. Заполни оба поля в журнале, чтобы они попали в реализованную "
                   "прибыль и в помесячный график.")
    if not bad_qty.empty:
        st.warning(f"Исключено из итогов: {len(bad_qty)} партий с некорректным количеством (меньше 1). "
                   "Поправь количество в журнале.")
    if no_rate_count > 0:
        st.warning(f"Долларовая прибыль посчитана без {no_rate_count} проданных партий, у которых не "
                   "задан курс покупки (для них долларовая себестоимость неизвестна). Гривневый итог "
                   "по ним учтён. Заполни курс покупки в журнале для точного $-итога.")

    if not closed.empty:
        monthly = closed.dropna(subset=["sell_date"]).copy()
        if not monthly.empty:
            monthly["Месяц"] = monthly["sell_date"].dt.strftime("%Y-%m")
            by_month = monthly.groupby("Месяц")["_raw_profit_uah"].sum().reset_index()
            by_month = by_month.rename(columns={"_raw_profit_uah": "Прибыль, ₴"})
            st.markdown("##### Прибыль по месяцам (₴)")
            st.bar_chart(by_month, x="Месяц", y="Прибыль, ₴", use_container_width=True)


# ===========================================================================
# UI: ЭКСПОРТ
# ===========================================================================

def _run_import(rows, mode):
    """Безопасный запуск импорта: сначала резервная копия, затем запись одной
    транзакцией. Если копию сделать не удалось — импорт НЕ начинается.

    make_backup возвращает ПАРУ (status, path), а не путь: раньше здесь пара
    принималась за путь, и Path((status, path)) валился с TypeError — импорт падал
    при каждом запуске. Теперь пара распаковывается, а статус проверяется.
    """
    try:
        status, backup_path = make_backup(force=True, manual=True)
    except Exception as exc:
        st.error(f"Не удалось создать резервную копию — импорт отменён, база не тронута: {exc}")
        return
    if status == "error" or backup_path is None:
        st.error("Импорт отменён: не удалось создать резервную копию. Проверь папку backups "
                 "и свободное место — без копии перезаписывать базу нельзя.")
        return
    try:
        written = import_deals(rows, mode=mode)
    except Exception as exc:
        st.error(f"Импорт не выполнен, база осталась прежней (откат транзакции): {exc}")
        return
    st.success(f"Импортировано строк: {written} · резервная копия: {backup_path.name}")
    st.rerun()


def render_import():
    """Импорт журнала из CSV: полная перезапись или добавление.

    Рисуется всегда (в том числе при пустой базе) — это основной путь
    восстановления данных из файла.
    """
    st.subheader("📥 Импорт из CSV")
    st.caption("Принимается файл кнопки «Скачать CSV» (или свой с такими же колонками). "
               "Читаются только сырые поля; расчётные колонки игнорируются и "
               "пересчитываются. Проверки — те же, что и при ручном вводе.")

    uploaded = st.file_uploader("Файл CSV", type=["csv"], key="imp_file",
                               help="Только CSV. Excel-файл не принимается.")
    if uploaded is None:
        return

    rows, report = parse_import_csv(uploaded.getvalue())
    if report.get("error"):
        st.error(report["error"])
        return

    st.info(f"Строк в файле: {report['total']} · к записи: {report['ready']} · "
            f"пропущено пустых: {report['skipped']}")
    if report.get("ignored_columns"):
        st.caption("Игнорируются колонки (расчётные/неизвестные): "
                   + ", ".join(report["ignored_columns"]))
    if not rows:
        st.warning("Нечего импортировать: не найдено ни одной осмысленной строки.")
        return

    with st.expander(f"👁 Предпросмотр (первые 20 из {len(rows)})"):
        st.dataframe(pd.DataFrame(rows[:20]), use_container_width=True, hide_index=True)

    warnings_list = report.get("warnings") or []
    if warnings_list:
        with st.expander(f"⚠️ Предупреждения проверки ({len(warnings_list)})"):
            for w in warnings_list[:50]:
                st.write("• " + str(w))
            if len(warnings_list) > 50:
                st.caption(f"…и ещё {len(warnings_list) - 50}.")
        st.caption("Это те же проверки, что и для журнала. Импортировать можно, "
                   "но такие записи стоит поправить.")

    mode_label = st.radio("Режим импорта",
                          ["Заменить всё (перезапись базы)", "Добавить к текущим"],
                          key="imp_mode")
    replace_mode = mode_label.startswith("Заменить")

    if replace_mode:
        st.error("Перезапись УДАЛИТ все текущие записи и заменит их содержимым файла. "
                 "Резервная копия создаётся автоматически перед записью.")
        confirmed = st.checkbox("Понимаю: текущие записи будут удалены", key="imp_confirm")
        if st.button("♻️ Заменить всё данными из файла", type="primary", key="imp_run_replace",
                     disabled=not confirmed, use_container_width=True):
            _run_import(rows, "replace")
    else:
        st.caption("Строки добавятся КАК ЕСТЬ (дубликаты не отсеиваются). id из файла "
                   "игнорируются, а группы партий переименовываются — чтобы импорт не "
                   "склеился с уже существующими покупками.")
        if st.button("➕ Добавить строки в журнал", key="imp_run_append",
                     use_container_width=True):
            _run_import(rows, "append")


def render_export(deals):
    """Выгрузка журнала: CSV (единый формат обмена — его же читает импорт) и Excel
    (только для просмотра/печати)."""
    if not deals:
        return
    st.subheader("💾 Экспорт")
    df = build_dataframe(deals).drop(
        columns=["_id", "_lot_group", "_raw_real_cost_uah", "_raw_profit_uah", "_raw_profit_usd"])
    export_df = df.copy()
    export_df["buy_date"] = export_df["buy_date"].dt.strftime("%Y-%m-%d")
    export_df["sell_date"] = export_df["sell_date"].dt.strftime("%Y-%m-%d")

    col_csv, col_xlsx = st.columns(2)
    with col_csv:
        # CSV — ЕДИНЫЙ формат обмена: содержит сырые поля (id, группа партии,
        # отметка записи продажи), поэтому этот же файл принимает импорт.
        csv_bytes = build_export_df(deals).to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ Скачать CSV (для импорта)", data=csv_bytes,
                           file_name="steam_ledger.csv", mime="text/csv",
                           use_container_width=True)
    with col_xlsx:
        # openpyxl — необязательная зависимость pandas. Если её нет, кнопка Excel не
        # должна ронять приложение: показываем подсказку, а CSV продолжает работать.
        try:
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                export_df.to_excel(writer, index=False, sheet_name="Сделки")
        except ImportError:
            st.button("⬇️ Скачать Excel", disabled=True, use_container_width=True,
                      help="Нужен пакет openpyxl: pip install openpyxl")
        else:
            st.download_button("⬇️ Скачать Excel", data=buffer.getvalue(),
                               file_name="steam_ledger.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True)
    st.caption("CSV — полный формат обмена (сырые поля + расчётные колонки; расчётные "
               "при импорте игнорируются и пересчитываются). Excel — только для просмотра, "
               "импортировать его нельзя.")
    st.caption(f"База данных хранится локально: {DB_PATH.name} (рядом с приложением).")

    # --- Резервные копии ---
    st.markdown("##### 🛟 Резервные копии")
    backups = list_backups()
    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        if st.button("📦 Сделать бэкап сейчас", use_container_width=True):
            status, path = make_backup(force=True, manual=True)
            if status == "created":
                st.toast(f"Бэкап сохранён: {path.name}", icon="📦")
            else:
                st.toast("Не удалось создать бэкап (проверь место и права на папку).", icon="⚠️")
            st.rerun()
    with col_info:
        if backups:
            newest = backups[0].name
            st.caption(f"Копий: {len(backups)} (хранится до {BACKUP_KEEP_DAILY} ежедневных "
                       f"и {BACKUP_KEEP_MANUAL} ручных). Свежая: {newest}. Папка: {BACKUP_DIR.name}/")
        else:
            st.caption(f"Копий пока нет. Бэкап создаётся автоматически раз в сутки при запуске; "
                       f"папка: {BACKUP_DIR.name}/")
        st.caption("Автоматический бэкап создаётся при ПЕРВОМ запуске приложения в этот день "
                   "(одна копия в сутки) — это снимок состояния на момент того запуска, до правок "
                   "в текущей сессии. Чтобы откатиться, закрой приложение и замени deals.db "
                   "выбранной копией из папки backups.")


# ===========================================================================
# UI: УДАЛЕНИЕ ПАРТИИ (двухшаговое подтверждение + бэкап перед удалением)
# ===========================================================================

def render_delete(deals):
    """Удаление выбранной партии по стабильному id с защитой от случайных кликов.

    Удаляется ровно одна партия (строка). Подтверждение двухшаговое: сначала
    «Удалить выбранную партию», затем явное «Да, удалить» — одиночный случайный
    клик ничего не стирает. Перед удалением создаётся резервная копия базы
    (ежедневный снимок при этом не вытесняется — см. prune_backups). Удаление не
    пересчитывает другие партии; если удаляется часть дробившейся покупки — об
    этом предупреждаем, остальные части группы остаются.
    """
    if not deals:
        return
    st.subheader("🗑 Удаление партии")
    st.caption("Безвозвратно удаляет выбранную партию — например, ошибочную запись. "
               "Удаление с подтверждением; перед ним создаётся резервная копия базы. "
               "Другие партии при этом не пересчитываются.")

    # Все партии (и открытые, и проданные), новые сверху — чтобы удобно найти свежую ошибку.
    ordered = sorted(
        deals,
        key=lambda d: (_parse_iso(d.get("buy_date")) or date.min, d.get("id", 0)),
        reverse=True,
    )
    id_by_label, labels = {}, []
    for d in ordered:
        lbl = lot_delete_label(d)
        id_by_label[lbl] = int(d["id"])
        labels.append(lbl)

    chosen_label = st.selectbox("Партия для удаления", labels, key="delete_select")
    chosen_id = id_by_label.get(chosen_label)

    # Если выбрали ДРУГУЮ партию — сбрасываем ранее «взведённое» подтверждение,
    # чтобы случайно не удалить не ту запись.
    pending = st.session_state.get("pending_delete_id")
    if pending is not None and pending != chosen_id:
        st.session_state.pop("pending_delete_id", None)
        pending = None

    # Шаг 1: кнопка «взводит» подтверждение для выбранной партии (ещё не удаляет).
    if pending != chosen_id:
        if st.button("🗑 Удалить выбранную партию", key="delete_arm"):
            st.session_state["pending_delete_id"] = chosen_id
            st.rerun()
        return

    # Шаг 2: подтверждение взведено именно для chosen_id — показываем предупреждение.
    d = next((x for x in deals if int(x.get("id", -1)) == chosen_id), None)
    if d is None:  # партия исчезла (например, удалена в другом месте) — сбрасываем.
        st.session_state.pop("pending_delete_id", None)
        st.rerun()
        return

    # Предупреждение о группе (дробившаяся покупка): останутся другие её части.
    group = str(d.get("lot_group") or "").strip()
    group_note = ""
    if group:
        siblings = [x for x in deals
                    if str(x.get("lot_group") or "").strip() == group
                    and int(x.get("id", -1)) != chosen_id]
        if siblings:
            group_note = (f"\n\n⚠️ Партия входит в группу с другими ({len(siblings)} шт — "
                          "покупка дробилась на части/продажи). Удаление только этой части "
                          "может нарушить сверку по группе (блок «Сверка покупок»); "
                          "остальные части останутся.")

    st.warning("⚠️ Подтверди удаление — действие необратимо.\n\n"
               f"**{lot_delete_label(d)}**" + group_note)
    c_yes, c_no = st.columns(2)
    with c_yes:
        if st.button("✅ Да, удалить безвозвратно", type="primary",
                     key="delete_confirm", use_container_width=True):
            try:
                status, path = make_backup(force=True, manual=True)
                # Удаление необратимо: без свежей копии не удаляем вовсе.
                if status != "error" and path is not None:
                    apply_changes([], [], [chosen_id])
            except Exception as e:
                st.error(f"Не удалось удалить партию: {e}. Данные не изменены — попробуй ещё раз.")
            else:
                if status == "error" or path is None:
                    st.error("Удаление отменено: не удалось создать резервную копию. "
                             "Проверь папку backups и свободное место, затем попробуй снова.")
                else:
                    st.session_state.pop("pending_delete_id", None)
                    st.toast(f"Партия #{chosen_id} удалена. Бэкап: {path.name}", icon="🗑️")
                    st.rerun()
    with c_no:
        if st.button("Отмена", key="delete_cancel", use_container_width=True):
            st.session_state.pop("pending_delete_id", None)
            st.rerun()


# ===========================================================================
# ТОЧКА ВХОДА
# ===========================================================================

def main():
    st.set_page_config(page_title="Yev Steam Trading Ledger", page_icon="📒", layout="wide")
    init_db()
    # Снимок состояния на момент первого запуска за день — до любых правок в
    # этой сессии. Не чаще одной копии в сутки; ошибки бэкапа не мешают работе.
    make_backup()

    st.title("📒 Yev Steam Trading Ledger")
    st.caption("Личный журнал сделок: покупка за баланс Steam (₴), продажа на сайте ($). "
               "Раздельные курсы покупки и продажи, продажи по частям, итоги в обеих валютах. "
               "Данные хранятся локально.")

    deals = fetch_deals()

    render_purchase()
    st.divider()
    render_sell_from_holdings(deals)
    st.divider()
    render_sales_history(deals)
    st.divider()
    render_ledger(deals)
    st.divider()
    render_delete(deals)
    st.divider()
    render_summary(deals)
    st.divider()
    render_export(deals)
    st.divider()
    render_import()


if __name__ == "__main__":
    main()