"""
BARON/DODO Trade Forensics & AI Journal
---------------------------------------
Observability-only trade intelligence layer.

It does NOT open, close, size, leverage, modify SL/TP, or alter strategy decisions.
It records the exact evidence available at entry, during the trade, and at exit,
then classifies the outcome for later research.

Trade Management Forensic Auditor (added):
Analyzes trade lifecycle for management failures - determines whether the trade
was bad or the trade management was defective. Purely observational.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List


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
                "runtime_config": {
                    "breakeven_after_pct": state.get("breakeven_after_pct", 0.30),
                    "trail_activate_pct": state.get("trail_activate_pct", 0.60),
                    "tp1_pct": state.get("tp1_pct", 0.40),
                    "tp1_close_frac": state.get("tp1_close_frac", 0.50),
                    "atr_mult_trail": state.get("atr_mult_trail", 1.6),
                    "trail_mult_strong": state.get("trail_mult_strong", 2.0),
                    "trail_mult_med": state.get("trail_mult_med", 1.5),
                    "trail_mult_chop": state.get("trail_mult_chop", 1.0),
                },
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


# =====================================================================
# TRADE MANAGEMENT FORENSIC AUDITOR
# =====================================================================
# Observability-only: analyzes trade lifecycle for management failures.
# Determines whether trade was bad or trade management was defective.
# NEVER opens/closes/modifies trades, SL/TP, leverage, or position size.
# =====================================================================

class TradeManagementAuditor:
    """
    Forensic auditor for trade management behavior.
    
    Analyzes completed trades to detect:
    - Profit protection failures (breakeven, trailing, TP1/TP2)
    - Stale protection states
    - Stop-loss protection failures
    - Exit management failures
    
    Purely observational. No trading authority.
    """

    # Management failure categories
    FAILURE_CATEGORIES = {
        "PROFIT_PROTECTION_FAILURE",
        "TRAILING_PROTECTION_FAILURE",
        "BREAKEVEN_PROTECTION_FAILURE",
        "TP1_MANAGEMENT_FAILURE",
        "TP2_MANAGEMENT_FAILURE",
        "STOP_PROTECTION_FAILURE",
        "STALE_PROTECTION_STATE",
        "EXIT_MANAGEMENT_FAILURE",
    }

    # Outcome classifications
    OUTCOME_CLASSIFICATIONS = {
        "ENTRY_THESIS_FAILURE",
        "MANAGEMENT_FAILURE",
        "EXECUTION_FAILURE",
        "PROTECTION_FAILURE",
        "MARKET_REGIME_FAILURE",
        "NORMAL_LOSS",
        "NORMAL_WIN",
        "UNKNOWN",
    }

    # Audit status levels
    STATUS_LEVELS = {"HEALTHY", "WARNING", "FAILURE", "INSUFFICIENT_EVIDENCE"}

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or os.getenv(
            "TRADE_FORENSICS_DIR",
            os.path.join(os.path.dirname(__file__), "trade_forensics"),
        )
        self.audit_path = os.path.join(self.data_dir, "trade_management_audits.jsonl")
        self.events_path = os.path.join(self.data_dir, "trade_events.jsonl")
        self.summary_path = os.path.join(self.data_dir, "trade_summaries.jsonl")
        self.aggregate_path = os.path.join(self.data_dir, "management_issues_aggregate.json")
        self.lock = threading.RLock()
        self._aggregate = self._load_aggregate()

    def _load_aggregate(self) -> Dict[str, Any]:
        try:
            with open(self.aggregate_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {
                "repeated_issues": {},
                "total_audited": 0,
                "failure_counts": {},
                "last_updated": None,
            }

    def _save_aggregate(self) -> None:
        with open(self.aggregate_path, "w", encoding="utf-8") as f:
            json.dump(self._aggregate, f, ensure_ascii=False, indent=2)

    def _append_audit(self, audit: Dict[str, Any]) -> None:
        record = dict(audit)
        record["recorded_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(_safe(record), ensure_ascii=False, separators=(",", ":")) + "\n")
            f.flush()

    def _read_events(self, trade_id: str) -> List[Dict[str, Any]]:
        """Read all events for a specific trade_id from trade_events.jsonl."""
        events = []
        events_path = os.path.join(self.data_dir, "trade_events.jsonl")
        try:
            with open(events_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        evt = json.loads(line)
                        if evt.get("trade_id") == trade_id:
                            events.append(evt)
        except FileNotFoundError:
            pass
        return events

    def _read_summary(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Read the summary record for a specific trade_id."""
        try:
            with open(self.summary_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rec = json.loads(line)
                        if rec.get("trade_id") == trade_id:
                            return rec
        except FileNotFoundError:
            pass
        return None

    def _extract_runtime_config(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract relevant runtime configuration from STATE for expected behavior."""
        return {
            "breakeven_after_pct": state.get("breakeven_after_pct", 0.30),
            "trail_activate_pct": state.get("trail_activate_pct", 0.60),
            "tp1_pct": state.get("tp1_pct", 0.40),
            "tp1_close_frac": state.get("tp1_close_frac", 0.50),
            "atr_mult_trail": state.get("atr_mult_trail", 1.6),
            "trail_mult_strong": state.get("trail_mult_strong", 2.0),
            "trail_mult_med": state.get("trail_mult_med", 1.5),
            "trail_mult_chop": state.get("trail_mult_chop", 1.0),
        }

    def _build_management_timeline(self, events: List[Dict[str, Any]], summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Construct a forensic timeline from entry to exit."""
        timeline = []
        
        # Entry event
        entry_evt = next((e for e in events if e.get("event") == "ENTRY"), None)
        if entry_evt:
            timeline.append({
                "timestamp": entry_evt.get("recorded_at"),
                "phase": "ENTRY",
                "roe": 0.0,
                "mfe": 0.0,
                "mae": 0.0,
                "details": {
                    "entry_price": entry_evt.get("entry_price"),
                    "sl": entry_evt.get("sl"),
                    "tp1": entry_evt.get("tp1"),
                    "tp2": entry_evt.get("tp2"),
                    "leverage": entry_evt.get("leverage"),
                    "score": entry_evt.get("score", {}).get("raw_score"),
                    "classification": entry_evt.get("classification"),
                }
            })

        # Milestone events
        for evt in events:
            if evt.get("event") == "MILESTONE":
                timeline.append({
                    "timestamp": evt.get("recorded_at"),
                    "phase": "MILESTONE",
                    "milestone_pct": evt.get("milestone_pct"),
                    "roe": evt.get("roe"),
                    "details": {}
                })

        # Observation events with protection state
        for evt in events:
            if evt.get("event") == "OBSERVATION":
                protection = evt.get("protection", {})
                timeline.append({
                    "timestamp": evt.get("recorded_at"),
                    "phase": "OBSERVATION",
                    "roe": evt.get("roe"),
                    "mfe": evt.get("mfe"),
                    "mae": evt.get("mae"),
                    "details": {
                        "price": evt.get("price"),
                        "sl": protection.get("sl"),
                        "tp1": protection.get("tp1"),
                        "tp2": protection.get("tp2"),
                        "tp1_hit": protection.get("tp1_hit"),
                        "tp2_hit": protection.get("tp2_hit"),
                        "trail_active": protection.get("trail_active"),
                        "trail_stop": protection.get("trail_stop"),
                    }
                })

        # Exit event
        exit_evt = next((e for e in events if e.get("event") == "EXIT"), None)
        if exit_evt:
            exit_data = exit_evt.get("exit", {})
            timeline.append({
                "timestamp": exit_evt.get("recorded_at"),
                "phase": "EXIT",
                "roe": exit_data.get("pnl_pct"),
                "details": {
                    "exit_price": exit_data.get("exit_price"),
                    "pnl_pct": exit_data.get("pnl_pct"),
                    "result": exit_data.get("result"),
                    "reason": exit_data.get("reason"),
                    "classification": exit_data.get("classification", {}).get("category"),
                }
            })

        return timeline

    def _analyze_profit_protection(self, timeline: List[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze whether profit protection (breakeven/trailing) activated as expected."""
        findings = {
            "breakeven": {"expected": False, "actual": False, "evidence": {}},
            "trailing": {"expected": False, "actual": False, "evidence": {}},
            "tp1": {"expected": False, "actual": False, "evidence": {}},
        }
        
        be_threshold = config.get("breakeven_after_pct", 0.30) * 100  # Convert to percentage
        trail_threshold = config.get("trail_activate_pct", 0.60) * 100
        tp1_threshold = config.get("tp1_pct", 0.40) * 100
        
        max_roe_seen = 0.0
        be_activated_at = None
        trail_activated_at = None
        tp1_hit_at = None
        
        for evt in timeline:
            roe = evt.get("roe", 0.0)
            max_roe_seen = max(max_roe_seen, roe)
            details = evt.get("details", {})
            
            # Check breakeven expectation
            if not findings["breakeven"]["expected"] and roe >= be_threshold:
                findings["breakeven"]["expected"] = True
                findings["breakeven"]["evidence"]["activation_threshold_pct"] = be_threshold
                findings["breakeven"]["evidence"]["roe_at_check"] = roe
            
            if findings["breakeven"]["expected"] and not findings["breakeven"]["actual"]:
                if details.get("trail_active") is True or details.get("tp1_hit") is True:
                    findings["breakeven"]["actual"] = True
                    be_activated_at = evt.get("timestamp")
                    findings["breakeven"]["evidence"]["activated_at"] = be_activated_at
            
            # Check trailing expectation
            if not findings["trailing"]["expected"] and roe >= trail_threshold:
                findings["trailing"]["expected"] = True
                findings["trailing"]["evidence"]["activation_threshold_pct"] = trail_threshold
                findings["trailing"]["evidence"]["roe_at_check"] = roe
            
            if findings["trailing"]["expected"] and not findings["trailing"]["actual"]:
                if details.get("trail_active") is True:
                    findings["trailing"]["actual"] = True
                    trail_activated_at = evt.get("timestamp")
                    findings["trailing"]["evidence"]["activated_at"] = trail_activated_at
                    findings["trailing"]["evidence"]["trail_stop"] = details.get("trail_stop")
            
            # Check TP1 expectation
            if not findings["tp1"]["expected"] and roe >= tp1_threshold:
                findings["tp1"]["expected"] = True
                findings["tp1"]["evidence"]["activation_threshold_pct"] = tp1_threshold
                findings["tp1"]["evidence"]["roe_at_check"] = roe
            
            if findings["tp1"]["expected"] and not findings["tp1"]["actual"]:
                if details.get("tp1_hit") is True:
                    findings["tp1"]["actual"] = True
                    tp1_hit_at = evt.get("timestamp")
                    findings["tp1"]["evidence"]["activated_at"] = tp1_hit_at
        
        # Determine failures
        failures = []
        if findings["breakeven"]["expected"] and not findings["breakeven"]["actual"] and max_roe_seen >= be_threshold:
            failures.append({
                "type": "BREAKEVEN_PROTECTION_FAILURE",
                "severity": "HIGH",
                "evidence": {
                    "max_roe": max_roe_seen,
                    "threshold": be_threshold,
                    "breakeven_activated": False,
                    "tp1_hit": findings["tp1"]["actual"],
                    "trail_activated": findings["trailing"]["actual"],
                }
            })
        
        if findings["trailing"]["expected"] and not findings["trailing"]["actual"] and max_roe_seen >= trail_threshold:
            failures.append({
                "type": "TRAILING_PROTECTION_FAILURE",
                "severity": "HIGH",
                "evidence": {
                    "max_roe": max_roe_seen,
                    "threshold": trail_threshold,
                    "trail_activated": False,
                    "last_sl": timeline[-1].get("details", {}).get("sl") if timeline else None,
                }
            })
        
        if findings["tp1"]["expected"] and not findings["tp1"]["actual"] and max_roe_seen >= tp1_threshold:
            failures.append({
                "type": "TP1_MANAGEMENT_FAILURE",
                "severity": "MEDIUM",
                "evidence": {
                    "max_roe": max_roe_seen,
                    "threshold": tp1_threshold,
                    "tp1_hit": False,
                }
            })
        
        return {"findings": findings, "failures": failures}

    def _analyze_stale_protection(self, timeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect stale protection states."""
        failures = []
        if len(timeline) < 3:
            return failures
        
        # Look for periods where price moved significantly but protection didn't update
        for i in range(1, len(timeline)):
            prev = timeline[i-1]
            curr = timeline[i]
            
            prev_details = prev.get("details", {})
            curr_details = curr.get("details", {})
            
            prev_sl = prev_details.get("sl")
            curr_sl = curr_details.get("sl")
            prev_trail = prev_details.get("trail_stop")
            curr_trail = curr_details.get("trail_stop")
            prev_roe = prev.get("roe", 0)
            curr_roe = curr.get("roe", 0)
            roe_change = abs(curr_roe - prev_roe)
            
            # If ROE changed significantly (>0.5%) but SL/trail didn't update
            if roe_change > 0.5 and prev_sl is not None and curr_sl is not None:
                if prev_sl == curr_sl and curr_roe > prev_roe:  # Price moved favorably but SL static
                    failures.append({
                        "type": "STALE_PROTECTION_STATE",
                        "severity": "MEDIUM",
                        "evidence": {
                            "roe_change": roe_change,
                            "prev_sl": prev_sl,
                            "curr_sl": curr_sl,
                            "prev_roe": prev_roe,
                            "curr_roe": curr_roe,
                            "timestamp": curr.get("timestamp"),
                            "trail_stop_changed": prev_trail != curr_trail,
                        }
                    })
        
        return failures

    def _analyze_stop_protection(self, timeline: List[Dict[str, Any]], summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Analyze stop-loss protection effectiveness."""
        failures = []
        
        exit_data = summary.get("exit", {})
        exit_reason = exit_data.get("reason", "").upper()
        pnl_pct = exit_data.get("pnl_pct", 0)
        
        if "STOP" in exit_reason or "SL" in exit_reason:
            # Trade hit stop loss - check if protection was adequate
            mfe = summary.get("mfe_roe", 0)
            mae = summary.get("mae_roe", 0)
            
            # If trade had significant profit but then hit SL, protection may have failed
            if mfe > 1.0 and pnl_pct < 0:
                failures.append({
                    "type": "STOP_PROTECTION_FAILURE",
                    "severity": "HIGH",
                    "evidence": {
                        "mfe_roe": mfe,
                        "final_pnl_pct": pnl_pct,
                        "mae_roe": mae,
                        "profit_gave_back": mfe + abs(pnl_pct),
                        "exit_reason": exit_reason,
                    }
                })
        
        return failures

    def _analyze_exit_management(self, timeline: List[Dict[str, Any]], summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Analyze exit management quality."""
        failures = []
        
        exit_data = summary.get("exit", {})
        pnl_pct = exit_data.get("pnl_pct", 0)
        mfe = summary.get("mfe_roe", 0)
        mae = summary.get("mae_roe", 0)
        
        # Exit management failure: had good MFE but ended with small profit/loss
        if mfe >= 2.0 and pnl_pct < mfe * 0.3:
            failures.append({
                "type": "EXIT_MANAGEMENT_FAILURE",
                "severity": "MEDIUM",
                "evidence": {
                    "mfe_roe": mfe,
                    "final_pnl_pct": pnl_pct,
                    "retention_ratio": pnl_pct / mfe if mfe > 0 else 0,
                    "exit_reason": exit_data.get("reason"),
                }
            })
        
        return failures

    def _classify_outcome(self, failures: List[Dict[str, Any]], summary: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        """Classify the overall outcome: was it entry failure or management failure?"""
        pnl_pct = summary.get("exit", {}).get("pnl_pct", 0)
        exit_reason = summary.get("exit", {}).get("reason", "").upper()
        
        management_failures = [f for f in failures if f["type"] in self.FAILURE_CATEGORIES]
        
        if pnl_pct >= 0:
            # Winning trade
            if management_failures:
                return {
                    "classification": "NORMAL_WIN",
                    "management_issues_present": True,
                    "confidence": "MEDIUM",
                }
            return {
                "classification": "NORMAL_WIN",
                "management_issues_present": False,
                "confidence": "HIGH",
            }
        
        # Losing trade - distinguish
        if management_failures:
            # Management failure is primary
            failure_types = [f["type"] for f in management_failures]
            if "BREAKEVEN_PROTECTION_FAILURE" in failure_types or "TRAILING_PROTECTION_FAILURE" in failure_types:
                return {
                    "classification": "MANAGEMENT_FAILURE",
                    "sub_types": failure_types,
                    "confidence": "HIGH",
                }
            if "STOP_PROTECTION_FAILURE" in failure_types:
                return {
                    "classification": "PROTECTION_FAILURE",
                    "sub_types": failure_types,
                    "confidence": "HIGH",
                }
            if "EXIT_MANAGEMENT_FAILURE" in failure_types:
                return {
                    "classification": "MANAGEMENT_FAILURE",
                    "sub_types": failure_types,
                    "confidence": "MEDIUM",
                }
            return {
                "classification": "MANAGEMENT_FAILURE",
                "sub_types": failure_types,
                "confidence": "MEDIUM",
            }
        
        # No management failures detected - check for entry/thesis failure
        if "THESIS" in exit_reason or (state.get("thesis_failure_score") or 0) > 0:
            return {
                "classification": "ENTRY_THESIS_FAILURE",
                "confidence": "HIGH",
            }
        if "MOMENTUM" in exit_reason or (state.get("momentum_flow") or {}).get("momentum_decay"):
            return {
                "classification": "ENTRY_THESIS_FAILURE",
                "confidence": "MEDIUM",
            }
        if "STRUCTURE" in exit_reason:
            return {
                "classification": "MARKET_REGIME_FAILURE",
                "confidence": "MEDIUM",
            }
        if "LATE" in exit_reason:
            return {
                "classification": "ENTRY_THESIS_FAILURE",
                "confidence": "MEDIUM",
            }
        if "STOP" in exit_reason or "SL" in exit_reason:
            return {
                "classification": "NORMAL_LOSS",
                "confidence": "HIGH",
            }
        
        return {
            "classification": "UNKNOWN",
            "confidence": "LOW",
        }

    def audit_trade(self, trade_id: str, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Perform full management audit for a closed trade.
        
        Returns comprehensive audit result with:
        - management_timeline
        - expected_vs_actual
        - failure_detections
        - outcome_classification
        - overall_status
        """
        with self.lock:
            # Read events and summary
            events = self._read_events(trade_id)
            summary = self._read_summary(trade_id)
            
            if not summary:
                return {"error": "Trade not found", "trade_id": trade_id}
            
            # Build timeline
            timeline = self._build_management_timeline(events, summary)
            
            # Get runtime config from summary (stored at entry time)
            config = summary.get("runtime_config", {})
            runtime_state = state or summary.get("market_context", {})
            if not config:
                # Fallback to state or defaults
                config = self._extract_runtime_config(runtime_state)
            
            # Run analyses
            protection_analysis = self._analyze_profit_protection(timeline, config)
            stale_failures = self._analyze_stale_protection(timeline)
            stop_failures = self._analyze_stop_protection(timeline, summary)
            exit_failures = self._analyze_exit_management(timeline, summary)
            
            all_failures = protection_analysis["failures"] + stale_failures + stop_failures + exit_failures
            
            # Classify outcome
            outcome = self._classify_outcome(all_failures, summary, runtime_state)
            
            # Expected vs Actual matrix
            expected_vs_actual = {
                "breakeven": {
                    "expected": protection_analysis["findings"]["breakeven"]["expected"],
                    "actual": protection_analysis["findings"]["breakeven"]["actual"],
                    "evidence": protection_analysis["findings"]["breakeven"]["evidence"],
                },
                "trailing": {
                    "expected": protection_analysis["findings"]["trailing"]["expected"],
                    "actual": protection_analysis["findings"]["trailing"]["actual"],
                    "evidence": protection_analysis["findings"]["trailing"]["evidence"],
                },
                "tp1": {
                    "expected": protection_analysis["findings"]["tp1"]["expected"],
                    "actual": protection_analysis["findings"]["tp1"]["actual"],
                    "evidence": protection_analysis["findings"]["tp1"]["evidence"],
                },
            }
            
            # Overall status
            if not all_failures:
                overall_status = "HEALTHY"
            else:
                severities = [f.get("severity", "LOW") for f in all_failures]
                if "CRITICAL" in severities:
                    overall_status = "FAILURE"
                elif "HIGH" in severities:
                    overall_status = "FAILURE"
                elif "MEDIUM" in severities:
                    overall_status = "WARNING"
                else:
                    overall_status = "WARNING"
            
            # Build audit record
            audit = {
                "trade_id": trade_id,
                "symbol": summary.get("symbol"),
                "side": summary.get("side"),
                "entry_time": summary.get("entry_time"),
                "exit_time": summary.get("exit", {}).get("exit_time"),
                "pnl_pct": summary.get("exit", {}).get("pnl_pct"),
                "mfe_roe": summary.get("mfe_roe"),
                "mae_roe": summary.get("mae_roe"),
                "management_timeline": timeline,
                "expected_vs_actual": expected_vs_actual,
                "failure_detections": all_failures,
                "outcome_classification": outcome,
                "overall_status": overall_status,
                "protection_quality": self._compute_protection_quality(protection_analysis),
                "exit_quality": self._compute_exit_quality(summary),
            }
            
            # Persist audit
            self._append_audit(audit)
            
            # Update aggregate
            self._update_aggregate(audit)
            
            return audit

    def _compute_protection_quality(self, protection_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Compute a protection quality score (0-100)."""
        score = 100
        findings = protection_analysis["findings"]
        
        if findings["breakeven"]["expected"]:
            if findings["breakeven"]["actual"]:
                score += 0  # Good
            else:
                score -= 30  # Missing breakeven
        
        if findings["trailing"]["expected"]:
            if findings["trailing"]["actual"]:
                score += 0
            else:
                score -= 25
        
        if findings["tp1"]["expected"]:
            if findings["tp1"]["actual"]:
                score += 0
            else:
                score -= 15
        
        return {
            "score": max(0, min(100, score)),
            "breakeven_activated": findings["breakeven"]["actual"],
            "trailing_activated": findings["trailing"]["actual"],
            "tp1_hit": findings["tp1"]["actual"],
        }

    def _compute_exit_quality(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Compute exit quality metrics."""
        mfe = summary.get("mfe_roe", 0)
        pnl = summary.get("exit", {}).get("pnl_pct", 0)
        
        if mfe > 0:
            retention = pnl / mfe
        else:
            retention = 0.0
        
        return {
            "mfe_roe": mfe,
            "final_pnl_pct": pnl,
            "retention_ratio": retention,
            "quality": "GOOD" if retention >= 0.5 else ("FAIR" if retention >= 0.2 else "POOR"),
        }

    def _update_aggregate(self, audit: Dict[str, Any]) -> None:
        """Update aggregate statistics for repeated issue detection."""
        with self.lock:
            self._aggregate["total_audited"] = self._aggregate.get("total_audited", 0) + 1
            self._aggregate["last_updated"] = datetime.now(timezone.utc).isoformat()
            
            failures = audit.get("failure_detections", [])
            for f in failures:
                ftype = f.get("type", "UNKNOWN")
                self._aggregate["failure_counts"][ftype] = self._aggregate["failure_counts"].get(ftype, 0) + 1
                
                # Track repeated issues
                if ftype not in self._aggregate["repeated_issues"]:
                    self._aggregate["repeated_issues"][ftype] = {
                        "count": 0,
                        "affected_trades": [],
                        "severity": f.get("severity", "UNKNOWN"),
                    }
                
                issue = self._aggregate["repeated_issues"][ftype]
                issue["count"] = issue.get("count", 0) + 1
                trade_ref = f"{audit.get('trade_id')} ({audit.get('symbol')})"
                if trade_ref not in issue["affected_trades"]:
                    issue["affected_trades"].append(trade_ref)
            
            self._save_aggregate()

    def get_aggregate_stats(self) -> Dict[str, Any]:
        """Get aggregate management issue statistics."""
        with self.lock:
            return dict(self._aggregate)

    def get_recent_audits(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent management audits."""
        audits = []
        try:
            with open(self.audit_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        audits.append(json.loads(line))
        except FileNotFoundError:
            return []
        return audits[-max(1, int(limit)):]

    def get_audit_for_trade(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Get specific audit by trade_id."""
        try:
            with open(self.audit_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        audit = json.loads(line)
                        if audit.get("trade_id") == trade_id:
                            return audit
        except FileNotFoundError:
            return None
        return None
