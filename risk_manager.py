"""
Risk Manager
============
The most critical module. Every trade MUST pass through here.
No trade executes without risk manager approval.

Rules enforced:
- Max risk per trade: 0.5% of account ($25 on $5k)
- Max daily loss: 2% ($100)
- Max weekly loss: 5% ($250)
- Max 2 consecutive losses → stop trading
- Min 1.5R reward-to-risk
- Spread check
- Duplicate order prevention
- No trade without stop AND target defined
"""

import logging
from datetime import datetime, date
from typing import Optional, Tuple, Dict, Set
from dataclasses import dataclass

from core.models import (
    Signal, TradeSetup, RejectionReason, DailyStats,
    ClosedTrade, TradeDirection
)
from config.config import RiskConfig, CONFIG

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    approved: bool
    rejection_reason: Optional[RejectionReason] = None
    rejection_detail: str = ""
    setup: Optional[TradeSetup] = None


class RiskManager:
    """
    Stateful risk manager. Tracks daily/weekly P&L,
    consecutive losses, open positions count, and trade count.
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self.current_equity = config.initial_capital
        self.starting_daily_equity = config.initial_capital
        self.starting_weekly_equity = config.initial_capital

        self.daily_realized_pnl = 0.0
        self.weekly_realized_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.open_positions_count = 0
        self.current_date = date.today()
        self.current_week = date.today().isocalendar()[1]

        self._pending_order_keys: Set[str] = set()   # duplicate prevention
        self._daily_stats = DailyStats(
            date=str(date.today()),
            starting_equity=self.current_equity,
            current_equity=self.current_equity
        )

    # ─────────────────────────────────────────────
    # PUBLIC: PRIMARY GATE
    # ─────────────────────────────────────────────

    def evaluate_signal(
        self,
        signal: Signal,
        current_spread_pct: float,
        is_data_fresh: bool = True
    ) -> RiskCheckResult:
        """
        Full risk check on a signal. Returns approved=True only if ALL
        conditions pass. Also calculates position size if approved.
        """
        # 1. Data freshness
        if not is_data_fresh:
            return self._reject(RejectionReason.STALE_DATA, "Data feed stale - no trades")

        # 2. Stop AND target must exist
        if signal.stop_price <= 0:
            return self._reject(RejectionReason.NO_STOP_DEFINED, "No stop-loss price on signal")
        if signal.target_price <= 0:
            return self._reject(RejectionReason.NO_TARGET_DEFINED, "No profit target on signal")

        # 3. Daily loss check
        if self.daily_loss_exceeded():
            return self._reject(
                RejectionReason.DAILY_LOSS_EXCEEDED,
                f"Daily loss {self.daily_realized_pnl:.2f} exceeds limit "
                f"${self.config.max_daily_loss:.2f}"
            )

        # 4. Weekly loss check
        if self.weekly_loss_exceeded():
            return self._reject(
                RejectionReason.WEEKLY_LOSS_EXCEEDED,
                f"Weekly loss {self.weekly_realized_pnl:.2f} exceeds limit "
                f"${self.config.max_weekly_loss:.2f}"
            )

        # 5. Consecutive losses
        if self.consecutive_losses >= self.config.max_consecutive_losses:
            return self._reject(
                RejectionReason.CONSECUTIVE_LOSSES,
                f"{self.consecutive_losses} consecutive losses - trading paused"
            )

        # 6. Max trades per day
        if self.trades_today >= self.config.max_trades_per_day:
            return self._reject(
                RejectionReason.MAX_TRADES_REACHED,
                f"Already took {self.trades_today} trades today (max {self.config.max_trades_per_day})"
            )

        # 7. Max open positions
        if self.open_positions_count >= self.config.max_open_positions:
            return self._reject(
                RejectionReason.MAX_POSITIONS_REACHED,
                f"{self.open_positions_count} positions open (max {self.config.max_open_positions})"
            )

        # 8. Reward-to-risk check
        rr = signal.reward_to_risk
        if rr < self.config.min_reward_to_risk:
            return self._reject(
                RejectionReason.RR_TOO_LOW,
                f"R:R = {rr:.2f} below minimum {self.config.min_reward_to_risk}"
            )

        # 9. Spread check
        if current_spread_pct > self.config.max_spread_pct:
            return self._reject(
                RejectionReason.SPREAD_TOO_WIDE,
                f"Spread {current_spread_pct*100:.2f}% > max {self.config.max_spread_pct*100:.2f}%"
            )

        # 10. Duplicate order check
        order_key = f"{signal.symbol}_{signal.strategy.name}_{signal.direction.name}"
        if order_key in self._pending_order_keys:
            return self._reject(
                RejectionReason.DUPLICATE_ORDER,
                f"Duplicate order detected for {signal.symbol}"
            )

        # 11. Calculate position size
        setup = self._calculate_position_size(signal)
        if setup is None:
            return self._reject(
                RejectionReason.RISK_TOO_HIGH,
                "Position size calculation resulted in 0 shares"
            )

        # All checks passed
        self._pending_order_keys.add(order_key)
        logger.info(
            f"✅ APPROVED: {signal.symbol} {signal.strategy.name} | "
            f"{setup.shares} shares @ ${signal.entry_price:.2f} | "
            f"Risk: ${setup.dollar_risk:.2f} | R:R {rr:.2f}"
        )
        return RiskCheckResult(approved=True, setup=setup)

    # ─────────────────────────────────────────────
    # POSITION SIZE CALCULATOR
    # ─────────────────────────────────────────────

    def _calculate_position_size(self, signal: Signal) -> Optional[TradeSetup]:
        """
        Position size = Max $ risk / Risk per share
        Never exceed buying power.
        """
        risk_per_share = signal.risk_per_share
        if risk_per_share <= 0:
            return None

        max_dollar_risk = self.config.max_risk_per_trade  # e.g. $25
        shares = int(max_dollar_risk / risk_per_share)

        if shares <= 0:
            return None

        # Ensure we don't exceed buying power (simple: 2x leverage max)
        position_value = shares * signal.entry_price
        buying_power = self.current_equity * 2
        if position_value > buying_power:
            shares = int(buying_power / signal.entry_price)

        if shares <= 0:
            return None

        actual_dollar_risk = shares * risk_per_share
        dollar_target = shares * signal.reward_per_share

        return TradeSetup(
            signal=signal,
            shares=shares,
            dollar_risk=actual_dollar_risk,
            dollar_target=dollar_target,
            position_value=shares * signal.entry_price
        )

    # ─────────────────────────────────────────────
    # STATE UPDATES (called by execution engine)
    # ─────────────────────────────────────────────

    def on_trade_entered(self, symbol: str, strategy_name: str, direction_name: str):
        """Call when a trade is confirmed entered."""
        self.trades_today += 1
        self.open_positions_count += 1
        logger.info(f"Risk state: {self.trades_today} trades today, "
                    f"{self.open_positions_count} open positions")

    def on_trade_closed(self, trade: ClosedTrade):
        """Update risk state when a trade closes."""
        self.daily_realized_pnl += trade.pnl
        self.weekly_realized_pnl += trade.pnl
        self.current_equity += trade.pnl
        self.open_positions_count = max(0, self.open_positions_count - 1)

        # Remove from pending keys
        key = f"{trade.symbol}_{trade.strategy.name}_{trade.direction.name}"
        self._pending_order_keys.discard(key)

        # Consecutive loss tracking
        if trade.pnl < 0:
            self.consecutive_losses += 1
            logger.warning(f"Loss #{self.consecutive_losses} consecutive: "
                           f"{trade.symbol} ${trade.pnl:.2f}")
        else:
            self.consecutive_losses = 0

        self._daily_stats.realized_pnl = self.daily_realized_pnl
        self._daily_stats.current_equity = self.current_equity
        if trade.pnl > 0:
            self._daily_stats.wins += 1
            self._daily_stats.gross_profit += trade.pnl
        else:
            self._daily_stats.losses += 1
            self._daily_stats.gross_loss += trade.pnl
        self._daily_stats.trades_taken += 1

        # Check lockout conditions
        if self.daily_loss_exceeded():
            logger.critical("🔴 DAILY LOSS LIMIT HIT - TRADING STOPPED FOR TODAY")
        if self.consecutive_losses >= self.config.max_consecutive_losses:
            logger.critical(f"🔴 {self.consecutive_losses} CONSECUTIVE LOSSES - TRADING PAUSED")

    def reset_daily(self, equity: float):
        """Call at start of each trading day."""
        self.current_date = date.today()
        self.starting_daily_equity = equity
        self.daily_realized_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self._daily_stats = DailyStats(
            date=str(date.today()),
            starting_equity=equity,
            current_equity=equity
        )
        logger.info(f"Daily reset: equity={equity:.2f}")

    def reset_weekly(self, equity: float):
        """Call at start of each trading week."""
        self.current_week = date.today().isocalendar()[1]
        self.starting_weekly_equity = equity
        self.weekly_realized_pnl = 0.0
        logger.info(f"Weekly reset: equity={equity:.2f}")

    # ─────────────────────────────────────────────
    # STATUS CHECKS
    # ─────────────────────────────────────────────

    def daily_loss_exceeded(self) -> bool:
        return self.daily_realized_pnl <= -self.config.max_daily_loss

    def weekly_loss_exceeded(self) -> bool:
        return self.weekly_realized_pnl <= -self.config.max_weekly_loss

    def consecutive_loss_lockout(self) -> bool:
        return self.consecutive_losses >= self.config.max_consecutive_losses

    def is_trading_allowed(self) -> Tuple[bool, str]:
        """Returns (allowed, reason_if_not)"""
        if self.daily_loss_exceeded():
            return False, f"Daily loss limit hit (${abs(self.daily_realized_pnl):.2f} lost)"
        if self.weekly_loss_exceeded():
            return False, f"Weekly loss limit hit (${abs(self.weekly_realized_pnl):.2f} lost)"
        if self.consecutive_loss_lockout():
            return False, f"{self.consecutive_losses} consecutive losses"
        if self.trades_today >= self.config.max_trades_per_day:
            return False, f"Max trades reached ({self.trades_today})"
        return True, ""

    def get_daily_stats(self) -> DailyStats:
        return self._daily_stats

    # ─────────────────────────────────────────────
    # INTERNAL
    # ─────────────────────────────────────────────

    def _reject(self, reason: RejectionReason, detail: str) -> RiskCheckResult:
        logger.warning(f"❌ REJECTED: {reason.value} - {detail}")
        self._daily_stats.signals_rejected += 1
        return RiskCheckResult(
            approved=False,
            rejection_reason=reason,
            rejection_detail=detail
        )
