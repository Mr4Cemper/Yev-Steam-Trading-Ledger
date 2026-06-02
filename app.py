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
BACKUP_KEEP = 10                 # сколько последних ежедневных копий хранить
BACKUP_PREFIX = "deals.db."      # имя копии: deals.db.YYYY-MM-DD.bak
BACKUP_SUFFIX = ".bak"

DEFAULT_DEPOSIT_PROFIT = 50.0   # чистый плюс пополнения Steam, %
DEFAULT_SALES_FEE = 2.0         # комиссия сайта за продажу, %
DEFAULT_RATE = 44.44            # сколько ₴ в 1 $

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
    "sell_uah_per_usd", "lot_group",
]


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
    """Безопасно приводит к float (NaN/мусор -> default)."""
    try:
        return float(_num(value, default))
    except (TypeError, ValueError):
        return default


def _to_int(value, default=1):
    """Безопасно приводит к int (NaN/мусор -> default)."""
    try:
        return int(float(_num(value, default)))
    except (TypeError, ValueError):
        return default


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
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _to_iso(value):
    """date / Timestamp / строку -> ISO-строка 'YYYY-MM-DD' или ''."""
    d = _parse_iso(value)
    return d.isoformat() if d else ""


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
                lot_group           TEXT    NOT NULL DEFAULT ''
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


def list_backups():
    """Список существующих файлов бэкапов, новейшие первыми.

    Имена с одной датой (ежедневные) и с датой-временем (ручные) сортируются
    лексикографически по ISO-метке, что совпадает с хронологией.
    """
    if not BACKUP_DIR.exists():
        return []
    files = [p for p in BACKUP_DIR.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}") if p.is_file()]
    return sorted(files, key=lambda p: p.name, reverse=True)


def prune_backups(keep=BACKUP_KEEP):
    """Оставляет только последние keep копий (любых — ежедневных и ручных).

    Возвращает число удалённых файлов. Ошибки удаления отдельных файлов
    игнорируются (бэкап не должен мешать работе приложения).
    """
    removed = 0
    for old in list_backups()[keep:]:
        try:
            old.unlink()
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

    После успешной копии прореживает старые до BACKUP_KEEP.

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
          (обратная совместимость) или из курса продажи, если задан только он;
        * курс продажи для ОТКРЫТОЙ партии НЕ выдумывается и остаётся 0 (пустым):
          так его нельзя по ошибке принять за реальный курс будущей продажи. Курс
          продажи подставляется (из курса покупки) только если партия ПРОДАНА, а
          сам курс продажи не задан — чтобы у закрытой сделки расчёт был полным.
    """
    sold_flag = _to_bool(d.get("sold"))
    buy_rate = max(0.0, _to_float(d.get("buy_uah_per_usd"), 0.0))
    sell_rate = max(0.0, _to_float(d.get("sell_uah_per_usd"), 0.0))
    # Обратная совместимость: старый единый ключ uah_per_usd.
    legacy = max(0.0, _to_float(d.get("uah_per_usd"), 0.0))
    if buy_rate == 0.0 and legacy > 0:
        buy_rate = legacy
    if sell_rate == 0.0 and legacy > 0 and sold_flag:
        sell_rate = legacy
    # Если задан только курс продажи — это и есть курс покупки (миграция/ввод).
    if buy_rate == 0.0 and sell_rate > 0:
        buy_rate = sell_rate
    # Курс продажи заполняем из курса покупки ТОЛЬКО для проданных партий.
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
    else:
        sell_date_val = _to_iso(d.get("sell_date", ""))
        site_sell_val = max(0.0, _to_float(d.get("site_sell_price"), 0.0))
        sales_fee_val = max(0.0, _to_float(d.get("sales_fee_pct"), 0.0))
    return {
        "item_name": str(_num(d.get("item_name"), "") or "").strip(),
        "buy_date": _to_iso(d.get("buy_date", "")),
        "steam_buy_price": max(0.0, _to_float(d.get("steam_buy_price"), 0.0)),
        "quantity": max(1, _to_int(d.get("quantity"), 1)),
        "deposit_profit_pct": _to_float(d.get("deposit_profit_pct"), 0.0),
        "buy_uah_per_usd": buy_rate,
        "sold": 1 if sold_flag else 0,
        "sell_date": sell_date_val,
        "site_sell_price": site_sell_val,
        "sales_fee_pct": sales_fee_val,
        "sell_uah_per_usd": sell_rate,
        "lot_group": str(_num(d.get("lot_group"), "") or "").strip(),
    }


_INSERT_SQL = """
    INSERT INTO deals (item_name, buy_date, steam_buy_price, quantity,
                       deposit_profit_pct, buy_uah_per_usd, sold, sell_date,
                       site_sell_price, sales_fee_pct, sell_uah_per_usd, lot_group)
    VALUES (:item_name, :buy_date, :steam_buy_price, :quantity,
            :deposit_profit_pct, :buy_uah_per_usd, :sold, :sell_date,
            :site_sell_price, :sales_fee_pct, :sell_uah_per_usd, :lot_group)
"""

_UPDATE_SQL = """
    UPDATE deals SET
        item_name=:item_name, buy_date=:buy_date, steam_buy_price=:steam_buy_price,
        quantity=:quantity, deposit_profit_pct=:deposit_profit_pct,
        buy_uah_per_usd=:buy_uah_per_usd, sold=:sold, sell_date=:sell_date,
        site_sell_price=:site_sell_price, sales_fee_pct=:sales_fee_pct,
        sell_uah_per_usd=:sell_uah_per_usd, lot_group=:lot_group
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
                          site_sell_price, sales_fee_pct, sell_rate):
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

def diff_editor_state(original_deals, editor_state):
    """Преобразует состояние st.data_editor в (updates, inserts, delete_ids).

    original_deals — список партий В ТОМ ЖЕ порядке, в каком строки поданы в
    редактор (позиция строки = индекс в этом списке). editor_state — словарь
    из st.session_state[key] с ключами 'edited_rows', 'added_rows',
    'deleted_rows'. Возвращает изменения, привязанные к стабильным id.

    Пустые/частично пустые добавленные строки отсекаются (нужны название или
    положительная цена покупки).
    """
    edited = editor_state.get("edited_rows", {}) or {}
    added = editor_state.get("added_rows", []) or []
    deleted = editor_state.get("deleted_rows", []) or []

    # Удаления -> id из исходного списка по позиции.
    delete_ids = []
    for pos in deleted:
        if 0 <= pos < len(original_deals):
            delete_ids.append(int(original_deals[pos]["id"]))

    # Изменения существующих строк -> применяем поверх исходных значений.
    # Если позиция выходит за пределы исходного списка (некоторые версии
    # Streamlit так представляют ПРАВКИ только что добавленных строк), трактуем
    # такую строку как новую вставку, а не отбрасываем её.
    updates = []
    edited_as_inserts = []
    for pos, changes in edited.items():
        pos = int(pos)
        if 0 <= pos < len(original_deals):
            base = dict(original_deals[pos])
            base.update(changes)  # перезаписываем только изменённые поля
            base["id"] = int(original_deals[pos]["id"])
            updates.append(base)
        else:
            # Правка строки за пределами исходных данных = добавленная строка.
            if _meaningful_row(changes):
                edited_as_inserts.append(dict(changes))

    # Новые строки -> INSERT (с фильтром пустых).
    inserts = []
    for row in added:
        if _meaningful_row(row):
            inserts.append(row)
    inserts.extend(edited_as_inserts)

    return updates, inserts, delete_ids


def _meaningful_row(row):
    """True, если в строке есть осмысленная сделка.

    Критерий шире прежнего: строка считается заполненной, если задано НАЗВАНИЕ,
    либо положительная цена покупки, либо указано количество > 1, либо есть
    данные продажи (дата/цена), либо проставлен любой курс. Это защищает от
    случайного удаления «бесплатных» дропов (цена 0) при наличии других данных.
    """
    name = str(_num(row.get("item_name"), "") or "").strip()
    price = _to_float(row.get("steam_buy_price"), 0.0)
    qty = _to_int(row.get("quantity"), 1)
    has_sale = (not _is_blank(row.get("sell_date"))) or _to_float(row.get("site_sell_price"), 0.0) > 0
    has_rate = _to_float(row.get("buy_uah_per_usd"), 0.0) > 0 or _to_float(row.get("sell_uah_per_usd"), 0.0) > 0
    sold_flag = _to_bool(row.get("sold"))
    return bool(name) or price > 0 or qty > 1 or has_sale or has_rate or sold_flag


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
        * партия с нулевым/пустым курсом покупки (долларовые суммы будут неверны).
    """
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

        if _to_float(d.get("buy_uah_per_usd")) <= 0:
            warnings.append(f"«{name}»: не задан курс покупки (₴/$) — долларовые суммы будут некорректны.")
        if sold and _to_float(d.get("sell_uah_per_usd")) <= 0:
            warnings.append(f"«{name}»: продано, но не задан курс продажи (₴/$) — выручка в ₴ будет неверной.")

        # Проверки уже сохранённых «мусорных» значений (например, после ручных
        # правок или из старой базы): количество, отрицательные суммы, проценты.
        if _to_int(d.get("quantity"), 0) < 1:
            warnings.append(f"«{name}»: некорректное количество (меньше 1).")
        if _to_float(d.get("steam_buy_price")) < 0:
            warnings.append(f"«{name}»: отрицательная цена покупки.")
        if _to_float(d.get("site_sell_price")) < 0:
            warnings.append(f"«{name}»: отрицательная цена продажи.")
        fee = _to_float(d.get("sales_fee_pct"))
        if fee < 0 or fee > 100:
            warnings.append(f"«{name}»: комиссия продажи вне диапазона 0–100% ({fee:g}%).")
        # Чистый плюс -100% и ниже делает множитель (1 + плюс/100) нулевым или
        # отрицательным, и реальная стоимость обнуляется — отчёты исказятся.
        dep = _to_float(d.get("deposit_profit_pct"))
        if dep <= -100.0:
            warnings.append(f"«{name}»: чистый плюс {dep:g}% (≤ −100%) обнуляет себестоимость — "
                            "проверь значение, иначе прибыль и ROI будут неверны.")
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
            "Чистый плюс, %": v["dep"],
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
        records.append({
            "_id": d.get("id"),
            "_lot_group": d.get("lot_group", ""),
            "item_name": d.get("item_name", ""),
            "buy_date": _parse_iso(d.get("buy_date")),
            "steam_buy_price": _to_float(d.get("steam_buy_price")),
            "quantity": _to_int(d.get("quantity"), 1),
            "deposit_profit_pct": _to_float(d.get("deposit_profit_pct")),
            "buy_uah_per_usd": _to_float(d.get("buy_uah_per_usd")),
            "sold": sold,
            "sell_date": _parse_iso(d.get("sell_date")),
            "site_sell_price": _to_float(d.get("site_sell_price")),
            "sales_fee_pct": _to_float(d.get("sales_fee_pct")),
            "sell_uah_per_usd": _to_float(d.get("sell_uah_per_usd")),
            "real_cost_uah": round(calc["real_cost_uah"], 2),
            "profit_uah": round(calc["profit_uah"], 2) if sold else None,
            "profit_usd": round(calc["profit_usd"], 2) if sold else None,
            "roi_pct": round(roi, 1) if (sold and roi is not None) else None,
            "holding_days": holding_days(d.get("buy_date"), d.get("sell_date")),
            "status": "Закрыта" if sold else "Открыта",
        })

    columns = ["_id", "_lot_group"] + INPUT_COLUMNS + COMPUTED_COLUMNS
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
        "deposit_profit_pct": st.column_config.NumberColumn("Чистый плюс, %", min_value=-99.9, step=0.01, format="%.2f"),
        "buy_uah_per_usd": st.column_config.NumberColumn("Курс покупки ₴/$", min_value=0.0, step=0.01, format="%.2f"),
        "sold": st.column_config.CheckboxColumn("Продано"),
        "sell_date": st.column_config.DateColumn("Дата продажи", format="YYYY-MM-DD"),
        "site_sell_price": st.column_config.NumberColumn("Продажа на сайте, $", min_value=0.0, step=0.01, format="%.2f"),
        "sales_fee_pct": st.column_config.NumberColumn("Комиссия сайта, %", min_value=0.0, max_value=100.0, step=0.01, format="%.2f"),
        "sell_uah_per_usd": st.column_config.NumberColumn("Курс продажи ₴/$", min_value=0.0, step=0.01, format="%.2f"),
        "real_cost_uah": st.column_config.NumberColumn("Реальная стоимость, ₴", format="%.2f"),
        "profit_uah": st.column_config.NumberColumn("Прибыль, ₴", format="%.2f"),
        "profit_usd": st.column_config.NumberColumn("Прибыль, $", format="%.2f"),
        "roi_pct": st.column_config.NumberColumn("ROI, %", format="%.1f"),
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


def _delta_str(roi):
    """Строка для delta метрики: '+12.3%' или None, если ROI не определён."""
    return f"{roi:+.1f}%" if roi is not None else None


# ===========================================================================
# UI: ПОКУПКА (создание партии; опционально — часть/всё уже продано)
# ===========================================================================

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
        deposit_profit = st.number_input("Чистый плюс пополнения Steam, %",
                                         min_value=-99.9, value=DEFAULT_DEPOSIT_PROFIT,
                                         step=0.01, format="%.2f", key="buy_deposit",
                                         help="Потратил 10, получил 15 на баланс → 50%.")
        buy_rate = st.number_input("Курс на момент покупки: ₴ в 1 $",
                                   min_value=0.01, value=DEFAULT_RATE, step=0.01, format="%.2f", key="buy_rate",
                                   help="Фиксирует долларовую себестоимость. Не меняется при будущих продажах.")

    with col_sell:
        st.markdown("#### 💳 Уже продано? (необязательно)")
        sold_now = st.checkbox("Часть или всё уже продано", value=False, key="buy_sold_now",
                               help="Включи, если оформляешь уже завершённую сделку. "
                                    "Иначе продажу можно записать позже в «Продать из остатков».")
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
        c3.metric("Прибыль, $", f"{sale['profit_usd']:,.2f} $")
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
        if sold_now and will_sell > 0:
            # Закрытая часть и остаток одной покупки записываются АТОМАРНО и с
            # общей группой (insert_deals_atomic назначит её по id первой части).
            lots = [{**purchase, "quantity": will_sell, "sold": 1,
                     "sell_date": _to_iso(sell_d), "site_sell_price": float(site_price),
                     "sales_fee_pct": float(sales_fee), "sell_uah_per_usd": float(sell_rate)}]
            remaining = int(qty_bought) - will_sell
            if remaining > 0:
                lots.append({**purchase, "quantity": remaining, "sold": 0, "sell_date": "",
                             "site_sell_price": 0.0, "sales_fee_pct": 0.0, "sell_uah_per_usd": 0.0})
            insert_deals_atomic(lots)
        else:
            insert_deal({**purchase, "quantity": int(qty_bought), "sold": 0, "sell_date": "",
                         "site_sell_price": 0.0, "sales_fee_pct": 0.0, "sell_uah_per_usd": 0.0})
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
    c3.metric("Прибыль, $", f"{sale['profit_usd']:,.2f} $")
    remaining = open_qty - int(sell_qty)
    if remaining > 0:
        st.caption(f"После продажи на руках останется {remaining} шт (открытая позиция).")
    else:
        st.caption("Продаётся весь остаток — позиция закроется полностью.")

    if st.button("💾 Записать продажу", type="primary", use_container_width=True):
        updates, inserts, dels = replace_lot_with_sale(
            deals, selected_id, int(sell_qty), _to_iso(sell_d),
            float(site_price), float(sales_fee), float(sell_rate))
        apply_changes(updates, inserts, dels)
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


def render_ledger(deals):
    """Редактируемая таблица всех партий: правка любых полей и удаление строк.

    Сохранение — точечными UPDATE/INSERT/DELETE по стабильным id (id строк не
    меняются). Предупреждения и сверка показываются и по сохранённым данным, и
    по предварительному состоянию из текущих правок редактора.
    """
    st.subheader("📒 Журнал партий")

    if not deals:
        st.info("Журнал пуст. Добавь первую покупку выше.")
        return

    st.caption("Каждая строка — партия (часть покупки). Любое поле, включая количество и оба "
               "курса, можно править прямо здесь; строки можно удалять (выдели строку → Delete). "
               "Серые колонки считаются автоматически. После правок нажми «Сохранить изменения». "
               "Правки ручные и не пересчитывают другие партии — сверяйся с блоком ниже.")

    df = build_dataframe(deals)
    # Динамический ключ: при изменении числа строк (после добавления покупки/продажи)
    # Streamlit пересоздаёт редактор, а не переиспользует устаревшее внутреннее
    # состояние со смещёнными индексами строк.
    editor_key = f"ledger_editor_{len(df)}"
    column_order = INPUT_COLUMNS + COMPUTED_COLUMNS  # _id и _lot_group скрыты
    edited_df = st.data_editor(
        df, column_config=column_config(), disabled=COMPUTED_COLUMNS,
        num_rows="dynamic", hide_index=True, use_container_width=True,
        key=editor_key, column_order=column_order,
    )

    # Предварительная проверка по ТЕКУЩИМ правкам в редакторе (а не только по БД).
    preview_deals = dataframe_to_preview_deals(edited_df)

    col_save, col_info = st.columns([1, 3])
    with col_save:
        if st.button("💾 Сохранить изменения", type="primary", use_container_width=True):
            state = st.session_state.get(editor_key, {})
            updates, inserts, dels = diff_editor_state(deals, state)
            if not updates and not inserts and not dels:
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
                    apply_changes(updates, inserts, dels)
                    st.session_state["_last_save_sig"] = sig
                    st.success(f"Сохранено: изменено {len(updates)}, добавлено {len(inserts)}, удалено {len(dels)}.")
                    st.rerun()
    with col_info:
        st.caption("Изменения в таблице не сохранятся, пока не нажата кнопка.")

    # Сначала — проверка по тому, что СЕЙЧАС на экране (предпросмотр изменений).
    render_data_checks(preview_deals, label_suffix="предпросмотр изменений на экране")


def dataframe_to_preview_deals(df):
    """Преобразует ТЕКУЩИЙ датафрейм редактора в список партий для предпросмотра.

    Используется только для предварительной проверки/сверки (не для записи).
    Сохраняет id, если он есть, чтобы сверка совпадала с журналом.
    """
    deals = []
    for _, row in df.iterrows():
        if not _meaningful_row(row):
            continue
        d = _normalize_deal({
            "item_name": row.get("item_name"),
            "buy_date": row.get("buy_date"),
            "steam_buy_price": row.get("steam_buy_price"),
            "quantity": row.get("quantity"),
            "deposit_profit_pct": row.get("deposit_profit_pct"),
            "buy_uah_per_usd": row.get("buy_uah_per_usd"),
            "sold": row.get("sold"),
            "sell_date": row.get("sell_date"),
            "site_sell_price": row.get("site_sell_price"),
            "sales_fee_pct": row.get("sales_fee_pct"),
            "sell_uah_per_usd": row.get("sell_uah_per_usd"),
        })
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
    # «Реализованными» считаем только ПОЛНЫЕ продажи: отмечены проданными и есть
    # цена продажи. Неполные (галочка «Продано» без цены) в реализованную прибыль,
    # ROI и win-rate не включаются — иначе одна случайная галочка исказила бы итог
    # и расходилась бы с помесячным графиком (туда нужна ещё и дата продажи).
    is_sold = df["status"] == "Закрыта"
    has_price = df["site_sell_price"].fillna(0) > 0
    closed = df[is_sold & has_price]
    incomplete = df[is_sold & ~has_price]
    open_pos = df[df["status"] == "Открыта"]

    realized_profit_uah = float(closed["profit_uah"].fillna(0).sum()) if not closed.empty else 0.0
    realized_profit_usd = float(closed["profit_usd"].fillna(0).sum()) if not closed.empty else 0.0
    closed_cost_uah = float(closed["real_cost_uah"].fillna(0).sum()) if not closed.empty else 0.0
    open_cost_uah = float(open_pos["real_cost_uah"].fillna(0).sum()) if not open_pos.empty else 0.0
    overall_roi = (realized_profit_uah / closed_cost_uah * 100.0) if closed_cost_uah > 0 else 0.0
    wins = int((closed["profit_uah"].fillna(0) > 0).sum()) if not closed.empty else 0
    win_rate = (wins / len(closed) * 100.0) if len(closed) > 0 else 0.0
    qty_open = int(open_pos["quantity"].sum()) if not open_pos.empty else 0

    m1, m2, m3 = st.columns(3)
    m1.metric("Реализованная прибыль", f"{realized_profit_uah:,.2f} ₴", delta=f"{overall_roi:+.1f}% ROI")
    m1.caption(f"≈ {realized_profit_usd:,.2f} $")
    m2.metric("В остатках на руках", f"{open_cost_uah:,.2f} ₴")
    m2.caption(f"{qty_open} шт в {len(open_pos)} открытых партиях")
    m3.metric("Доля прибыльных", f"{win_rate:.0f}%")
    m3.caption(f"{wins} из {len(closed)} завершённых продаж")

    if not incomplete.empty:
        st.warning(f"Не учтено в итогах: {len(incomplete)} партий отмечены проданными, но без цены "
                   "продажи. Заполни цену в журнале, чтобы они попали в реализованную прибыль.")

    if not closed.empty:
        monthly = closed.dropna(subset=["sell_date"]).copy()
        if not monthly.empty:
            monthly["Месяц"] = monthly["sell_date"].dt.strftime("%Y-%m")
            by_month = monthly.groupby("Месяц")["profit_uah"].sum().reset_index()
            by_month = by_month.rename(columns={"profit_uah": "Прибыль, ₴"})
            st.markdown("##### Прибыль по месяцам (₴)")
            st.bar_chart(by_month, x="Месяц", y="Прибыль, ₴", use_container_width=True)


# ===========================================================================
# UI: ЭКСПОРТ
# ===========================================================================

def render_export(deals):
    """Выгрузка журнала в CSV и Excel для бэкапа/внешнего просмотра."""
    if not deals:
        return
    st.subheader("💾 Экспорт")
    df = build_dataframe(deals).drop(columns=["_id", "_lot_group"])
    export_df = df.copy()
    export_df["buy_date"] = export_df["buy_date"].dt.strftime("%Y-%m-%d")
    export_df["sell_date"] = export_df["sell_date"].dt.strftime("%Y-%m-%d")

    col_csv, col_xlsx = st.columns(2)
    with col_csv:
        csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ Скачать CSV", data=csv_bytes, file_name="steam_ledger.csv",
                           mime="text/csv", use_container_width=True)
    with col_xlsx:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            export_df.to_excel(writer, index=False, sheet_name="Сделки")
        st.download_button("⬇️ Скачать Excel", data=buffer.getvalue(), file_name="steam_ledger.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)
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
            st.caption(f"Копий: {len(backups)} (хранится до {BACKUP_KEEP}). "
                       f"Свежая: {newest}. Папка: {BACKUP_DIR.name}/")
        else:
            st.caption(f"Копий пока нет. Бэкап создаётся автоматически раз в сутки при запуске; "
                       f"папка: {BACKUP_DIR.name}/")
        st.caption("Бэкап при запуске — это снимок на начало дня (до правок в текущей сессии). "
                   "Чтобы откатиться, закрой приложение и замени deals.db выбранной копией из папки backups.")


# ===========================================================================
# ТОЧКА ВХОДА
# ===========================================================================

def main():
    st.set_page_config(page_title="Yev Steam Trading Ledger", page_icon="📒", layout="wide")
    init_db()
    # Снимок «как было на начало дня» — до любых правок в этой сессии.
    # Не чаще одной копии в сутки; ошибки бэкапа не мешают работе приложения.
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
    render_ledger(deals)
    st.divider()
    render_summary(deals)
    st.divider()
    render_export(deals)


if __name__ == "__main__":
    main()