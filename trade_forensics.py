"""
BARON/DODO Trade Forensics & AI Journal
---------------------------------------
Observability-only trade intelligence layer.

It does NOT open, close, size, leverage, modify SL/TP, or alter strategy decisions.
It records the exact evidence available at entry, during the trade, and at exit,
then classifies the outcome for later research.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


MILESTONES = (0.5, 1, 2, 3, 5, 10, 20, 30, 40, 60, 80, 100)
COMPONENT_POINTS = {
    "sweep": 2.0,
    "choch_bos": 2.0,
    "retest": 2.0,
    "rejection": 1.5,
    "displacement": 1.5,
    "volume": 1.0,
    "rf": 2.0,
}


ASSET_CLASS_MAP = {
    "BTC": "CRYPTO", "ETH": "CRYPTO", "SOL": "CRYPTO", "DOGE": "CRYPTO", "XRP": "CRYPTO",
    "ADA": "CRYPTO", "AVAX": "CRYPTO", "DOT": "CRYPTO", "LINK": "CRYPTO", "MATIC": "CRYPTO",
    "GOLD": "METAL", "XAU": "METAL", "XAG": "METAL",
    "OIL": "ENERGY", "WTI": "ENERGY", "BRENT": "ENERGY",
    "US500": "INDEX", "SPX": "INDEX", "NDX": "INDEX", "DJI": "INDEX", "VIX": "INDEX",
    "US30": "INDEX", "DE40": "INDEX", "UK100": "INDEX", "JP225": "INDEX",
    "AAPL": "STOCK", "MSFT": "STOCK", "GOOGL": "STOCK", "AMZN": "STOCK", "NVDA": "STOCK",
    "META": "STOCK", "TSLA": "STOCK", "JPM": "STOCK", "MU": "STOCK", "PLTR": "STOCK",
}


def detect_asset_class(symbol: str) -> str:
    """Observational-only asset class detection from symbol."""
    if not symbol:
        return "UNKNOWN"
    base = symbol.split("/")[0].replace(":USDT", "").replace(":USD", "").upper()
    # Check known prefixes
    for prefix, asset_class in ASSET_CLASS_MAP.items():
        if base.startswith(prefix):
            return asset_class
    # Heuristic: if contains known crypto/stock patterns
    if any(c in base for c in ["USDT", "BTC", "ETH", "BNB", "SOL"]):
        return "CRYPTO"
    if any(c in base for c in ["GOLD", "XAU", "SILVER", "XAG"]):
        return "METAL"
    if any(c in base for c in ["OIL", "WTI", "BRENT", "NATGAS"]):
        return "ENERGY"
    if any(c in base for c in ["US500", "SPX", "NDX", "DJI", "VIX", "US30", "DE40", "UK100", "JP225"]):
        return "INDEX"
    return "UNKNOWN"


def _safe(v: Any) -> Any:
    if v is None or isinstance(v, (str, bool, int)):
        return v
    try:
        f = float(v)
        if f != f or abs(f) == float("inf"):
            return None
        return round(f, 8)
    except Exception:
        if isinstance(v, dict):
            return {str(k): _safe(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_safe(x) for x in v]
        return str(v)


def _contains(text: str, *terms: str) -> bool:
    t = text.lower()
    return any(x.lower() in t for x in terms)


class TradeForensics:
    """Persistent append-only forensic journal plus in-memory active trade."""

    def __init__(self, data_dir: Optional[str] = None, observation_interval: float = 10.0):
        self.data_dir = data_dir or os.getenv(
            "TRADE_FORENSICS_DIR",
            os.path.join(os.path.dirname(__file__), "trade_forensics"),
        )
        os.makedirs(self.data_dir, exist_ok=True)
        self.events_path = os.path.join(self.data_dir, "trade_events.jsonl")
        self.summary_path = os.path.join(self.data_dir, "trade_summaries.jsonl")
        self.observation_interval = max(1.0, float(observation_interval))
        self.lock = threading.RLock()
        self.active: Dict[str, Dict[str, Any]] = {}
        self.last_observe: Dict[str, float] = {}

    def _append(self, path: str, payload: Dict[str, Any]) -> None:
        record = dict(payload)
        record["recorded_at"] = datetime.now(timezone.utc).isoformat()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(_safe(record), ensure_ascii=False, separators=(",", ":")) + "\n")
            f.flush()

    @staticmethod
    def _score_breakdown(score: float, reason: Any) -> Dict[str, Any]:
        text = str(reason or "")
        found = {}
        if _contains(text, "sweep"):
            found["sweep"] = 2.0
        if _contains(text, "choch", "mss", "bos"):
            found["choch_bos"] = 2.0
        if _contains(text, "retest"):
            found["retest"] = 2.0
        if _contains(text, "rejection"):
            found["rejection"] = 1.5
        if _contains(text, "displacement"):
            found["displacement"] = 1.5
        if _contains(text, "volume"):
            found["volume"] = 1.0
        if _contains(text, "rf"):
            found["rf"] = 2.0
        return {
            "raw_score": float(score or 0),
            "known_components": found,
            "known_component_sum": round(sum(found.values()), 2),
            "component_schema": COMPONENT_POINTS,
            "reason_text": text,
        }

    @staticmethod
    def _indicators(state: Dict[str, Any], df=None) -> Dict[str, Any]:
        out = {
            "adx": state.get("adx_live"),
            "di_plus": state.get("di_plus_live"),
            "di_minus": state.get("di_minus_live"),
            "atr": state.get("atr"),
            "rsi": state.get("rsi_live"),
            "macd": state.get("macd_live"),
            "ema": state.get("ema_live"),
            "sma": state.get("sma_live"),
            "market_regime": state.get("market_regime"),
            "rf": state.get("rf_live"),
            "volume": state.get("volume_live"),
            "structure": state.get("structure_live"),
        }
        # Fallback to df computation only if not already in state (should not happen in normal flow)
        if df is not None:
            try:
                if out["rsi"] is None:
                    close = df["close"]
                    delta = close.diff()
                    gain = delta.clip(lower=0).rolling(14).mean()
                    loss = (-delta.clip(upper=0)).rolling(14).mean()
                    rs = gain / loss.replace(0, float("nan"))
                    out["rsi"] = float((100 - 100 / (1 + rs)).iloc[-1])
            except Exception:
                pass
            try:
                if out["macd"] is None:
                    ema12 = df["close"].ewm(span=12, adjust=False).mean()
                    ema26 = df["close"].ewm(span=26, adjust=False).mean()
                    macd = ema12 - ema26
                    signal = macd.ewm(span=9, adjust=False).mean()
                    out["macd"] = {
                        "value": float(macd.iloc[-1]),
                        "signal": float(signal.iloc[-1]),
                        "histogram": float((macd - signal).iloc[-1]),
                    }
            except Exception:
                pass
        return _safe(out)

    @staticmethod
    def _market_context(state: Dict[str, Any]) -> Dict[str, Any]:
        smart = state.get("smart_money") or {}
        momentum = state.get("momentum_flow") or {}
        thesis = state.get("trade_thesis") or {}
        return _safe({
            "smart_money": smart,
            "momentum": momentum,
            "trade_state": state.get("trade_state"),
            "continuation_probability": state.get("continuation_probability"),
            "hold_quality": state.get("hold_quality"),
            "counter_pressure": state.get("counter_pressure"),
            "reclaim_risk": state.get("reclaim_risk"),
            "trend_strength": state.get("trend_strength"),
            "continuation_pressure": state.get("continuation_pressure"),
            "thesis_failure_score": state.get("thesis_failure_score"),
            "thesis": thesis,
            "confidence": state.get("current_confidence"),
            "trade_personality": state.get("trade_personality"),
            "institutional_flow": state.get("institutional_flow"),
        })

    def begin_trade(
        self,
        *,
        state: Dict[str, Any],
        symbol: str,
        side: str,
        entry_price: float,
        qty: float,
        sl: float,
        tp1: float,
        tp2: float,
        score: float,
        reason: Any,
        trade_type: str,
        entry_type: str,
        classification: str,
        leverage: int = 10,
        df=None,
        asset_class: Optional[str] = None,
    ) -> str:
        with self.lock:
            trade_id = f"DODO-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
            now = time.time()
            numeric_score = float(score or 0)
            strength = "STRONG" if numeric_score >= 7 else ("MEDIUM" if numeric_score >= 4 else "WEAK")
            resolved_asset_class = asset_class or detect_asset_class(symbol)
            record = {
                "trade_id": trade_id,
                "symbol": symbol,
                "asset_class": resolved_asset_class,
                "side": side,
                "entry_time": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                "entry_price": entry_price,
                "qty": qty,
                "leverage": leverage,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "score": self._score_breakdown(score, reason),
                "strength": strength,
                "classification": classification,
                "trade_type": trade_type,
                "entry_type": entry_type,
                "entry_reason": reason,
                "indicators": self._indicators(state, df),
                "market_context": self._market_context(state),
                "mfe_roe": 0.0,
                "mae_roe": 0.0,
                "current_roe": 0.0,
                "max_price": entry_price,
                "min_price": entry_price,
                "milestones": [],
                "snapshots": 0,
                "exit": None,
            }
            self.active[symbol] = record
            self.last_observe[symbol] = 0.0
            self._append(self.events_path, {"event": "ENTRY", **record})
            return trade_id

    def observe(
        self,
        *,
        state: Dict[str, Any],
        symbol: str,
        price: float,
        df=None,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            rec = self.active.get(symbol)
            if not rec:
                return None
            now = time.time()
            if not force and now - self.last_observe.get(symbol, 0) < self.observation_interval:
                return rec
            self.last_observe[symbol] = now

            entry = float(rec["entry_price"])
            side = rec["side"]
            roe = ((price - entry) / entry * 100) if side == "BUY" else ((entry - price) / entry * 100)
            rec["current_roe"] = roe
            rec["mfe_roe"] = max(float(rec["mfe_roe"]), roe)
            rec["mae_roe"] = min(float(rec["mae_roe"]), roe)
            rec["max_price"] = max(float(rec["max_price"]), float(price))
            rec["min_price"] = min(float(rec["min_price"]), float(price))

            reached = set(rec["milestones"])
            for m in MILESTONES:
                if roe >= m and m not in reached:
                    rec["milestones"].append(m)
                    self._append(self.events_path, {
                        "event": "MILESTONE",
                        "trade_id": rec["trade_id"],
                        "symbol": symbol,
                        "milestone_pct": m,
                        "roe": roe,
                        "indicators": self._indicators(state, df),
                        "market_context": self._market_context(state),
                    })

            rec["snapshots"] += 1
            self._append(self.events_path, {
                "event": "OBSERVATION",
                "trade_id": rec["trade_id"],
                "symbol": symbol,
                "price": price,
                "roe": roe,
                "mfe": rec["mfe_roe"],
                "mae": rec["mae_roe"],
                "indicators": self._indicators(state, df),
                "market_context": self._market_context(state),
                "protection": {
                    "sl": state.get("synthetic_sl", state.get("sl")),
                    "tp1": state.get("synthetic_tp1", state.get("tp1_price")),
                    "tp2": state.get("tp2_price"),
                    "tp1_hit": state.get("tp1_hit"),
                    "tp2_hit": state.get("tp2_hit"),
                    "trail_active": state.get("trail_activated", state.get("trail_active")),
                    "trail_stop": state.get("trail_stop"),
                },
            })
            return rec

    def close_trade(
        self,
        *,
        state: Dict[str, Any],
        symbol: str,
        exit_price: float,
        pnl_pct: float,
        pnl_usdt: float,
        exit_reason: str = "UNKNOWN",
    ) -> Optional[Dict[str, Any]]:
        with self.lock:
            rec = self.active.pop(symbol, None)
            self.last_observe.pop(symbol, None)
            if not rec:
                return None

            result = "WIN" if pnl_pct >= 0 else "LOSS"
            forensic = self._classify_exit(rec, state, pnl_pct, exit_reason)
            exit_data = {
                "exit_time": datetime.now(timezone.utc).isoformat(),
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "pnl_usdt": pnl_usdt,
                "result": result,
                "reason": exit_reason,
                "classification": forensic,
            }
            rec["exit"] = exit_data
            self._append(self.events_path, {
                "event": "EXIT",
                "trade_id": rec["trade_id"],
                "symbol": symbol,
                "exit": exit_data,
                "mfe": rec["mfe_roe"],
                "mae": rec["mae_roe"],
                "milestones": rec["milestones"],
                "final_indicators": self._indicators(state),
                "final_market_context": self._market_context(state),
            })
            self._append(self.summary_path, rec)
            return rec

    @staticmethod
    def _classify_exit(rec: Dict[str, Any], state: Dict[str, Any], pnl_pct: float, reason: str) -> Dict[str, Any]:
        text = str(reason or "").upper()
        if pnl_pct >= 0:
            if rec.get("mfe_roe", 0) >= 20:
                category = "LARGE_TREND_WINNER"
            elif rec.get("mfe_roe", 0) >= 5:
                category = "STRONG_TREND_WINNER"
            else:
                category = "PROFITABLE_EXIT"
        elif "STOP" in text or "SL" in text:
            category = "STOP_LOSS"
        elif "LATE" in text:
            category = "LATE_ENTRY"
        elif "THESIS" in text or state.get("thesis_failure_score", 0) > 0:
            category = "THESIS_FAILURE"
        elif "MOMENTUM" in text or state.get("momentum_flow", {}).get("momentum_decay"):
            category = "MOMENTUM_FAILURE"
        elif "STRUCTURE" in text:
            category = "STRUCTURE_FAILURE"
        else:
            category = "LOSS_OTHER"

        return {
            "category": category,
            "trade_type": rec.get("trade_type"),
            "entry_strength": rec.get("classification"),
            "mfe_roe": rec.get("mfe_roe"),
            "mae_roe": rec.get("mae_roe"),
            "milestones": rec.get("milestones", []),
            "thesis_failure_score": state.get("thesis_failure_score"),
            "trade_state_at_exit": state.get("trade_state"),
            "confidence_at_exit": state.get("current_confidence"),
            "reason": reason,
        }

    def active_trade(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.active.get(symbol)

    def recent_summaries(self, limit: int = 20):
        rows = []
        try:
            with open(self.summary_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rows.append(json.loads(line))
        except FileNotFoundError:
            return []
        return rows[-max(1, int(limit)):]

    def status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "active_trades": len(self.active),
                "active_trade_ids": {k: v["trade_id"] for k, v in self.active.items()},
                "events_file": self.events_path,
                "summary_file": self.summary_path,
            }
