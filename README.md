# Yev Steam Trading Ledger

A personal, local-only journal for tracking CS2 skin trades, built with Python (Streamlit + SQLite). You record each purchase paid from your Steam balance (topped up "at a profit") and each sale on a third-party site withdrawn to real money — and the app computes your **real** profit, keeps a sale history so you never lose track of what you sold last, and shows a running summary in both currencies.

> ⚠️ **This is a niche personal tool, not a product.** It was built to scratch one specific itch — keeping my own CS2 trades in order instead of losing them in a spreadsheet — and is published here mainly so the code doesn't live only on my machine. It is **not** designed for a mass audience, has **no** multi-user support, and makes assumptions that fit one particular trading workflow (buy with a profitably topped-up Steam balance in ₴, sell on a site in $). If that isn't your exact setup, it may not fit you — and that's fine.

## What it is for

The core problem it solves: when you buy skins with a Steam balance that was topped up **cheaper** than face value, and later sell them on another site at a **different** exchange rate, your true profit is not obvious. Naive tracking either ignores the top-up discount or lets currency devaluation masquerade as trading profit. This ledger separates all of that out.

It also answers a smaller but nagging question — *"which sale did I record last, and where do I continue?"* — with a dedicated sale history, so you don't have to reconstruct it from memory.

## Sections

| Section | What it does |
|---------|--------------|
| ➕ **Purchase** | Record a buy — creates an open lot (stock on hand). Optionally mark part or all as already sold. |
| 💰 **Sell from holdings** | Sell a chosen open lot fully or partially; the remainder stays on hand for a later sale at a different price. |
| 📜 **Sale history** | "Where did I leave off" — the last *recorded* sale up top, plus a filterable list of closed lots in record order or by sale date. |
| 📒 **Lot journal** | An editable table of every lot: fix any field (including quantity and both rates), delete rows, search and sort. |
| 📈 **Summary** | Realized profit and ROI in both ₴ and $, capital still on hand, win rate, and a per-month profit chart. |
| 💾 **Export** | One CSV (the exchange format that Import reads back) plus a view-only Excel file. |
| 📥 **Import from CSV** | Full overwrite (restore) or append, with the same validation as manual entry. |
| 🗑 **Delete lot** | Two-step confirmed deletion of a single lot, with a backup taken first. |

## Key features

### The "lot" model
One purchase of *N* identical items can be sold off in parts — on different days, at different prices — while the rest stays unsold. The unit of accounting is a **lot**: a quantity of items from a single purchase with a shared cost basis. A purchase creates one open lot; a sale splits off a closed lot with its own price and date, keeping stable IDs so your row numbers never "run away."

### Two currencies, two independent rates
- The Steam balance is in hryvnia (₴); the site sale is in dollars ($).
- Every lot carries **two** independent exchange rates:
  - `buy_uah_per_usd` — the rate at the moment of **purchase** (fixes the dollar cost basis of the balance);
  - `sell_uah_per_usd` — the rate at the moment of **sale** (converts revenue to ₴).
- The dollar cost basis is computed at the **purchase** rate, so hryvnia devaluation does **not** create phantom dollar profit.

### Honest profit accounting
- **Real cost** accounts for the top-up discount: `cost₴ = (Steam price × qty) ÷ (1 + top-up% / 100)`.
- **Both ROIs shown side by side** — ₴-ROI and $-ROI. The hryvnia figure rises with devaluation even at zero dollar return; the dollar figure shows the true trading yield. Seeing both keeps you from mistaking a weaker currency for a good trade.
- **No phantom loss** — a closed sale with no rate on record shows "—", not a fabricated 100% loss, and is excluded from the totals rather than dragging them down.
- **Consistent rules everywhere** — the journal, the history and the summary all use the same calculation and the same "complete sale" definition, so they can't contradict each other.

### Sale history that survives forgetting
- The last *recorded* sale is shown prominently (recorded time, not just sale date — so it answers "where did I stop" even when several sales share a date).
- Two orderings: **by record order** (where you left off) and **by sale date** (business chronology).
- If the newest sale by date is a *different* lot (a back-dated entry), it's flagged separately so you don't get confused.

### One import/export format, with guards
- A single CSV holds the raw fields (including lot group and the record timestamp) — the very same file that Import reads back. Computed columns are included for the eye and **ignored** on import.
- Excel export is **view-only** and cannot be imported.
- Import understands files saved by Excel in a CIS locale: **cp1251** encoding, **`;`** column separator, decimal **comma** (`45,05`), and even headers where Excel mangled the ₴ sign into `?`.
- Local date formats are accepted (`14.07.2026`, with or without a time component), not just ISO.

## Safety & data integrity

Because this touches your own financial records, it is deliberately cautious:

- **Local-first, no network, no accounts, no tracking.** Everything is stored in a local SQLite file (`deals.db`) next to the script. Nothing is sent anywhere; there are no analytics and no telemetry.
- **Automatic daily backups.** A snapshot is taken on the first launch each day (kept for a rolling window), plus a manual "back up now" button. A backup is also taken **before** any import or deletion — and if that backup can't be written, the destructive action is **cancelled**, not forced through.
- **Atomic, all-or-nothing writes.** Imports run in a single transaction; on any error the database is rolled back to its previous state (verified by tests, including a mid-write failure during full overwrite).
- **CSV formula-injection guard.** An item name like `=SUM(A1)` is written with a leading apostrophe so a spreadsheet can't execute it as a formula, and the apostrophe is stripped back on import so the exchange stays lossless.
- **Import limits.** Files over 5 MB or 20,000 rows are rejected; empty/foreign/garbage files are refused with a clear message. Imported data is parsed as **data, never executed**, and passes through the same validation and normalization as manual entry.
- **Guardrails against silent data loss.** Clearing a "Sold" checkbox (which would wipe sale data) requires an explicit confirmation; filling sale fields without ticking "Sold" blocks the save instead of quietly erasing them; partial-purchase groups warn before you delete one part.
- **A validation panel** flags out-of-range fees, quantities below 1, open lots missing a purchase date, and inconsistencies between parts of the same purchase — so bad data is visible rather than hidden.

> None of this makes the tool bulletproof. It is a single-user local utility. **Keep your own backups of `deals.db`, and try the first import on a copy of your database.** The `backups/` folder is a safety net, not a guarantee.

## Quick Start

```bash
git clone https://github.com/Mr4Cemper/<repo>.git
cd <repo>
pip install -r requirements.txt
streamlit run ledger.py
```

**Requirements:** Python 3.9+, `streamlit` and `pandas`. Excel export additionally needs `openpyxl` (optional — the button simply disables itself with a hint if it's missing; CSV export always works). Everything else is the standard library.

The database file `deals.db` and the `backups/` folder are created automatically next to the script on first run.

## Scope & limitations (please read)

- **Single user, single machine.** There is no concurrency handling, no server mode, no login. Don't point two sessions at the same file.
- **Opinionated workflow.** It assumes buying via a profitably topped-up Steam balance (₴) and selling on a site ($). Other flows may not map cleanly onto the model.
- **Not audited financial software.** The math is tested extensively, but it's a personal utility, not accounting software. Verify anything that matters.
- **Russian-language UI.** The interface is in Russian only (the author's working language); there is currently no localization.
- **The interface can't be run-tested headless**, so the logic is covered by tests while the Streamlit UI is verified by review — expect the occasional rough edge, and report anything odd.

## Legal Disclaimer

**Not affiliated with Valve Corp.** This application is an independent, personal utility created for educational and record-keeping purposes. It is NOT affiliated with, endorsed, sponsored, or specifically approved by Valve Corporation. "Counter-Strike", "CS2", "Steam", and their respective logos are trademarks and/or registered trademarks of Valve Corporation.

All figures produced by this tool are **estimates for personal bookkeeping only**. Nothing here is financial, investment, tax, or accounting advice. You are solely responsible for your own records and decisions — **use at your own risk**.

Trading virtual items may be subject to the terms of service of the platforms you use and to the laws of your jurisdiction; complying with those is your responsibility.

Any trademarks contained in the source code, binaries, and/or documentation are the sole property of their respective owners.

## License

This project is licensed under the GNU Affero General Public License v3.0 or later.

Copyright (c) 2026 Bohdan Yevtushenko (Mr4Cemper)

You are free to use, modify, and redistribute this project under the terms of the AGPLv3. If you run a modified version over a network, you must provide the corresponding source code to users interacting with it.

The full license text is available in the `LICENSE` file.
