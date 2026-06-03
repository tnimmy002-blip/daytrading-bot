"""
Trade Logger & Analytics
========================
Logs every decision with full context.
Produces CSV logs for signals, trades, and rejections.
Calculates performance metrics.
"""

import csv
import json
import logging
import os
from datetime import datetime, date
from typing import List, Dict, Optional
from pathlib import Path

from core.models import (
    Signal, ClosedTrade, RejectionReason, DailyStats
)

logger = logging.getLogger(__name__)


class TradeLogger:
    """Structured logger for all trading activity."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.trades_file = self.log_dir / "trades.csv"
        self.signals_file = self.log_dir / "signals.csv"
        self.rejections_file = self.log_dir / "rejections.csv"
        self.performance_file = self.log_dir / "performance.json"

        self._init_files()
        self._trade_history: List[ClosedTrade] = []

    def _init_files(self):
        """Create CSV files with headers if they don't exist."""
        if not self.trades_file.exists():
            with open(self.trades_file, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "date", "time", "symbol", "strategy", "direction",
                    "entry_price", "exit_price", "shares", "stop_price",
                    "target_price", "pnl", "r_multiple", "exit_reason",
                    "entry_time", "exit_time", "notes"
                ])

        if not self.signals_file.exists():
            with open(self.signals_file, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "timestamp", "symbol", "strategy", "direction",
                    "entry_price", "stop_price", "target_price",
                    "reward_to_risk", "confidence", "notes"
                ])

        if not self.rejections_file.exists():
            with open(self.rejections_file, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "timestamp", "symbol", "strategy",
                    "rejection_reason", "detail",
                    "entry_price", "stop_price", "target_price"
                ])

    def log_signal(self, signal: Signal):
        """Log a generated signal (before risk check)."""
        with open(self.signals_file, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                signal.timestamp.isoformat(),
                signal.symbol,
                signal.strategy.value,
                signal.direction.value,
                signal.entry_price,
                signal.stop_price,
                signal.target_price,
                round(signal.reward_to_risk, 2),
                round(signal.confidence, 2),
                signal.notes
            ])
        logger.debug(f"Signal logged: {signal.symbol} {signal.strategy.value}")

    def log_rejection(
        self,
        signal: Signal,
        reason: RejectionReason,
        detail: str
    ):
        """Log a rejected trade signal."""
        with open(self.rejections_file, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                datetime.now().isoformat(),
                signal.symbol,
                signal.strategy.value,
                reason.value,
                detail,
                signal.entry_price,
                signal.stop_price,
                signal.target_price
            ])

    def log_trade(self, trade: ClosedTrade):
        """Log a completed trade."""
        self._trade_history.append(trade)

        with open(self.trades_file, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                trade.entry_time.date(),
                trade.entry_time.strftime("%H:%M:%S"),
                trade.symbol,
                trade.strategy.value,
                trade.direction.value,
                trade.entry_price,
                trade.exit_price,
                trade.shares,
                trade.stop_price,
                trade.target_price,
                round(trade.pnl, 2),
                round(trade.r_multiple, 2),
                trade.exit_reason,
                trade.entry_time.isoformat(),
                trade.exit_time.isoformat(),
                trade.signal_notes
            ])

        result = "WIN" if trade.pnl > 0 else "LOSS"
        logger.info(
            f"Trade logged [{result}]: {trade.symbol} | "
            f"P&L: ${trade.pnl:+.2f} | R: {trade.r_multiple:+.2f}"
        )

    def save_performance(self, stats: DailyStats):
        """Save daily performance metrics to JSON."""
        perf = {}
        if self.performance_file.exists():
            try:
                with open(self.performance_file) as f:
                    perf = json.load(f)
            except Exception:
                perf = {}

        perf[stats.date] = {
            "starting_equity": stats.starting_equity,
            "current_equity": stats.current_equity,
            "realized_pnl": round(stats.realized_pnl, 2),
            "trades": stats.trades_taken,
            "wins": stats.wins,
            "losses": stats.losses,
            "win_rate": round(stats.win_rate, 3),
            "profit_factor": round(stats.profit_factor, 2),
            "avg_win": round(stats.avg_win, 2),
            "avg_loss": round(stats.avg_loss, 2),
            "signals_generated": stats.signals_generated,
            "signals_rejected": stats.signals_rejected
        }

        with open(self.performance_file, "w") as f:
            json.dump(perf, f, indent=2)

    def get_all_time_stats(self) -> Dict:
        """Calculate aggregate performance statistics."""
        if not self._trade_history:
            return {}

        wins = [t for t in self._trade_history if t.pnl > 0]
        losses = [t for t in self._trade_history if t.pnl <= 0]
        total = len(self._trade_history)

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = sum(t.pnl for t in losses)

        r_multiples = [t.r_multiple for t in self._trade_history]

        # Max drawdown calculation
        equity_curve = [5000.0]
        for t in self._trade_history:
            equity_curve.append(equity_curve[-1] + t.pnl)

        peak = equity_curve[0]
        max_dd = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / total if total > 0 else 0,
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_pnl": round(gross_profit + gross_loss, 2),
            "profit_factor": round(gross_profit / abs(gross_loss), 2) if gross_loss < 0 else 0,
            "avg_win": round(gross_profit / len(wins), 2) if wins else 0,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
            "avg_r": round(sum(r_multiples) / len(r_multiples), 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "equity_curve": equity_curve,
            "strategies": self._strategy_breakdown()
        }

    def _strategy_breakdown(self) -> Dict:
        """Break down performance by strategy."""
        breakdown = {}
        for trade in self._trade_history:
            strat = trade.strategy.value
            if strat not in breakdown:
                breakdown[strat] = {"trades": 0, "wins": 0, "net_pnl": 0}
            breakdown[strat]["trades"] += 1
            if trade.pnl > 0:
                breakdown[strat]["wins"] += 1
            breakdown[strat]["net_pnl"] += trade.pnl
        return breakdown

    def get_recent_trades(self, n: int = 10) -> List[ClosedTrade]:
        return self._trade_history[-n:]
