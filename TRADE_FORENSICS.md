# DODO Trade Intelligence / Forensics

This integration adds an observability-only forensic journal to the existing DODO/BingX bot.

## Authority boundary

The forensic layer **does not** open, close, resize, change leverage, or modify SL/TP. The existing strategy and execution code remains authoritative.

## What is recorded

For every confirmed trade:

- immutable forensic Trade ID
- symbol, side, entry/exit times and prices
- quantity, SL, TP1, TP2
- raw entry score and STRONG/MEDIUM/WEAK classification
- available score components and original reason text
- trade type / entry type / classification
- ADX, DI+, DI-, ATR, RSI, MACD
- market regime
- Smart Money and Momentum Flow state
- Trade State Machine state
- continuation probability, confidence, thesis-failure score
- periodic ROE snapshots
- MFE / MAE
- milestones: 0.5, 1, 2, 3, 5, 10, 20, 30, 40, 60, 80, 100%
- TP/SL/trailing state
- exit reason and post-trade forensic classification

## Files

Runtime data is written under `TRADE_FORENSICS_DIR`:

- `trade_events.jsonl` — append-only entry/observation/milestone/exit events.
- `trade_summaries.jsonl` — one completed trade record per line.

The HTTP endpoint `/forensics` exposes current forensic status and recent completed summaries.

## Important limitation

This is deterministic trade forensics, not a self-modifying machine-learning model. It records the evidence needed to answer why a trade succeeded, failed, became a large trend, or was weak. Strategy changes remain a separate, explicit engineering decision.
