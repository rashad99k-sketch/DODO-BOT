# DODO Bot — Windows CMD Run Guide

## 1. First run

Double-click:

`RUN_DODO_WINDOWS.bat`

or from CMD:

```bat
cd /d "C:\path\to\DODO-bot-1-main"
RUN_DODO_WINDOWS.bat
```

The launcher will:

1. Check Python.
2. Create `.venv` if missing.
3. Install `requirements.txt`.
4. Create `.env` from `env.example` only if `.env` does not already exist.
5. Compile-check the bot and the forensic module.
6. Force the launcher process into PAPER mode for safety.
7. Start `test cv new.py`.

## 2. Dashboard

After startup, open:

`http://127.0.0.1:5000`

The bot itself owns the Flask server and uses the `PORT` environment variable when provided.

## 3. Forensics

Trade forensic records are written to:

`trade_forensics\trade_events.jsonl`

`trade_forensics\trade_summaries.jsonl`

These files are append-only JSON Lines records produced by the forensic layer.

## 4. Live trading safety

The launcher does **not** inject BingX credentials and does **not** enable LIVE trading.

Keep the first validation in PAPER mode. Do not enable LIVE until the strategy, execution, SL/TP protection, and forensic logging have been separately validated.

## 5. Stopping

Use `Ctrl+C` in the same CMD window where the bot is running.

Do not use a global `taskkill /IM python.exe`, because that could terminate unrelated Python processes.


## Configuration safety

The launcher explicitly starts the bot in PAPER mode. The bot loads the project-local `.env` before reading configuration, and `PAPER_MODE=True` is interpreted as PAPER. Placeholder BingX credentials in `env.example` therefore do not enable LIVE trading.

The launcher and Flask application use `PORT=5000` from `.env`, so the expected dashboard is `http://127.0.0.1:5000`.
