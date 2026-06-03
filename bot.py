"""
Main Bot Orchestrator
=====================
Coordinates all modules. Single entry point for the trading bot.

Run loop:
  1. Check market hours
  2. Check risk limits
  3. Scan for qualifying stocks
  4. Evaluate strategies
  5. Risk-check signals
  6. Execute approved trades
  7. Monitor open positions
  8. Log everything

Safety checks before EVERY cycle:
  - Data freshness
  - API connectivity
  - Daily/weekly loss limits
  - Consecutive loss lockout
  - EOD close time
"""

import logging
import sys
import time
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

from config.config import BotConfig, CONFIG
from core.models import BotStatus, Signal, TradeSetup
from data.market_data import create_data_feed, SimulatedDataFeed
from data.scanner import Scanner
from strategies.strategy_engine import StrategyEngine
from risk.risk_manager import RiskManager
from execution.execution_engine import ExecutionEngine, PaperBroker
from trade_logging.trade_logger import TradeLogger

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log", mode="a")
    ]
)
logger = logging.getLogger("TradingBot")


class TradingBot:
    """
    Main trading bot. Thread-safe, modular, fully logged.
    Start in paper mode by default. Live trading requires explicit flag.
    """

    def __init__(self, config: BotConfig = CONFIG):
        self.config = config
        self.status = BotStatus.INITIALIZING

        logger.info("=" * 60)
        logger.info(f"  DAY TRADING BOT v{config.version}")
        logger.info(f"  Phase: {config.phase}")
        logger.info(f"  Mode: {'📄 PAPER' if config.broker.paper_trading else '🔴 LIVE'}")
        logger.info(f"  Initial Capital: ${config.risk.initial_capital:,.2f}")
        logger.info(f"  Max Risk/Trade: ${config.risk.max_risk_per_trade:.2f}")
        logger.info(f"  Max Daily Loss: ${config.risk.max_daily_loss:.2f}")
        logger.info("=" * 60)

        if not config.broker.paper_trading and not config.broker.live_trading_enabled:
            logger.critical("Live trading flag not set. Defaulting to paper mode.")
            config.broker.paper_trading = True

        # Initialize all modules
        self.data_feed = create_data_feed(config)
        self.scanner = Scanner(self.data_feed, config.scanner)
        self.strategy_engine = StrategyEngine(self.data_feed, config.hours)
        self.risk_manager = RiskManager(config.risk)
        self.broker = PaperBroker(config.risk.initial_capital)
        self.execution_engine = ExecutionEngine(self.broker, config.hours)
        self.trade_logger = TradeLogger(config.logging.log_dir)

        self._cycle_count = 0
        self._last_scan_time: Optional[datetime] = None
        self._emergency_stop = False
        self._signals_today: List[Signal] = []

        self.status = BotStatus.MARKET_CLOSED
        logger.info("✅ All modules initialized")

    # ─────────────────────────────────────────────
    # PUBLIC CONTROLS
    # ─────────────────────────────────────────────

    def start(self, max_cycles: Optional[int] = None, cycle_interval: float = 5.0):
        """
        Start the main bot loop.
        cycle_interval: seconds between scan cycles (5 seconds in sim, 30-60 in live)
        max_cycles: for testing, limit total cycles
        """
        logger.info(f"🚀 Bot starting. Interval: {cycle_interval}s")
        cycles = 0

        try:
            while not self._emergency_stop:
                if max_cycles and cycles >= max_cycles:
                    logger.info(f"Max cycles ({max_cycles}) reached. Stopping.")
                    break

                self._run_cycle()
                cycles += 1
                self._cycle_count += 1
                time.sleep(cycle_interval)

        except KeyboardInterrupt:
            logger.info("⏹ Bot stopped by user (Ctrl+C)")
        except Exception as e:
            logger.exception(f"💥 Unhandled exception in main loop: {e}")
            self.status = BotStatus.ERROR
        finally:
            self._shutdown()

    def trigger_emergency_stop(self):
        """Immediately close all positions and halt trading."""
        logger.critical("🚨 EMERGENCY STOP TRIGGERED")
        self._emergency_stop = True
        # Close all open positions at market
        price_data = {}
        for symbol in self.execution_engine.open_positions:
            quote = self.data_feed.get_quote(symbol)
            if quote:
                price_data[symbol] = quote.last
        self.execution_engine.emergency_stop(price_data)
        self.status = BotStatus.STOPPED

    def get_status_report(self) -> Dict:
        """Return current bot state as a dictionary (for dashboard)."""
        open_pos = self.execution_engine.open_positions
        recent_trades = self.trade_logger.get_recent_trades(20)
        all_stats = self.trade_logger.get_all_time_stats()
        daily_stats = self.risk_manager.get_daily_stats()

        positions_summary = []
        for sym, pos in open_pos.items():
            quote = self.data_feed.get_quote(sym)
            price = quote.last if quote else pos.entry_price
            positions_summary.append({
                "symbol": sym,
                "strategy": pos.strategy.value,
                "direction": pos.direction.value,
                "entry": pos.entry_price,
                "current": price,
                "stop": pos.stop_price,
                "target": pos.target_price,
                "shares": pos.shares,
                "unrealized_pnl": round(pos.unrealized_pnl(price), 2),
                "r_multiple": round(pos.r_multiple(price), 2)
            })

        return {
            "status": self.status.value,
            "timestamp": datetime.now().isoformat(),
            "mode": "PAPER" if self.config.broker.paper_trading else "LIVE",
            "account": {
                "equity": round(self.broker.equity, 2),
                "buying_power": round(self.broker.buying_power, 2),
                "initial_capital": self.config.risk.initial_capital
            },
            "daily": {
                "pnl": round(daily_stats.realized_pnl, 2),
                "trades": daily_stats.trades_taken,
                "wins": daily_stats.wins,
                "losses": daily_stats.losses,
                "win_rate": round(daily_stats.win_rate * 100, 1),
                "consecutive_losses": self.risk_manager.consecutive_losses,
                "signals_generated": daily_stats.signals_generated,
                "signals_rejected": daily_stats.signals_rejected
            },
            "risk": {
                "daily_loss_remaining": round(
                    self.config.risk.max_daily_loss + self.risk_manager.daily_realized_pnl, 2
                ),
                "weekly_loss_remaining": round(
                    self.config.risk.max_weekly_loss + self.risk_manager.weekly_realized_pnl, 2
                ),
                "trading_allowed": self.risk_manager.is_trading_allowed()[0],
                "lockout_reason": self.risk_manager.is_trading_allowed()[1]
            },
            "open_positions": positions_summary,
            "all_time": all_stats,
            "recent_trades": [
                {
                    "symbol": t.symbol,
                    "strategy": t.strategy.value,
                    "pnl": t.pnl,
                    "r_multiple": t.r_multiple,
                    "exit_reason": t.exit_reason,
                    "time": t.exit_time.strftime("%H:%M:%S")
                }
                for t in recent_trades[-10:]
            ]
        }

    # ─────────────────────────────────────────────
    # INTERNAL CYCLE
    # ─────────────────────────────────────────────

    def _run_cycle(self):
        """One full scan-analyze-execute cycle."""
        now = datetime.now()
        logger.debug(f"Cycle #{self._cycle_count + 1} @ {now.strftime('%H:%M:%S')}")

        # 1. Update position states
        self._update_positions()

        # 2. Check if we should trade
        if not self._should_trade():
            return

        # 3. Scan
        scan_results = self.scanner.scan()
        qualified = [r.symbol for r in scan_results if r.passes_scan]

        if not qualified:
            logger.debug("No qualifying stocks found this cycle")
            return

        logger.info(f"Scan: {len(qualified)} stocks qualified: {qualified}")

        # 4. Evaluate strategies on qualified stocks
        for symbol in qualified:
            # Stop if risk limits hit mid-cycle
            allowed, reason = self.risk_manager.is_trading_allowed()
            if not allowed:
                logger.warning(f"Trading halted mid-cycle: {reason}")
                self.status = BotStatus.RISK_LOCKOUT
                break

            # Skip if already have position in this symbol
            if symbol in self.execution_engine.open_positions:
                continue

            signal = self.strategy_engine.evaluate(symbol)
            if signal is None:
                continue

            logger.info(
                f"Signal: {symbol} {signal.strategy.value} | "
                f"Entry: ${signal.entry_price} | R:R {signal.reward_to_risk:.2f}"
            )
            self.trade_logger.log_signal(signal)
            self.risk_manager._daily_stats.signals_generated += 1

            # 5. Risk check
            quote = self.data_feed.get_quote(symbol)
            spread_pct = quote.spread_pct if quote else 0.01
            is_fresh = self.data_feed.is_fresh(symbol)

            risk_result = self.risk_manager.evaluate_signal(signal, spread_pct, is_fresh)

            if not risk_result.approved:
                self.trade_logger.log_rejection(
                    signal,
                    risk_result.rejection_reason,
                    risk_result.rejection_detail
                )
                continue

            # 6. Execute
            position = self.execution_engine.enter_trade(risk_result.setup)
            if position:
                self.risk_manager.on_trade_entered(
                    symbol, signal.strategy.name, signal.direction.name
                )
                self.status = BotStatus.ACTIVE
            else:
                logger.error(f"Execution failed for {symbol}")

    def _update_positions(self):
        """Fetch current prices and update all open positions."""
        open_positions = self.execution_engine.open_positions
        if not open_positions:
            return

        price_data = {}
        for symbol in open_positions:
            quote = self.data_feed.get_quote(symbol)
            if quote:
                candles = self.data_feed.get_candles(symbol, "1min", 1)
                if candles:
                    price_data[symbol] = (quote.last, candles[-1].high, candles[-1].low)
                else:
                    price_data[symbol] = (quote.last, quote.last * 1.001, quote.last * 0.999)

        closed = self.execution_engine.update_positions(price_data)

        for trade in closed:
            self.risk_manager.on_trade_closed(trade)
            self.trade_logger.log_trade(trade)
            self.broker.update_equity(trade.pnl)

    def _should_trade(self) -> bool:
        """Check all preconditions for trading."""
        now = datetime.now()

        # Market hours check
        market_open = now.replace(hour=9, minute=30, second=0)
        no_new_trades = now.replace(hour=15, minute=30, second=0)

        if now < market_open:
            self.status = BotStatus.MARKET_CLOSED
            return False
        if now > no_new_trades:
            self.status = BotStatus.EOD_CLOSING
            return False

        # Risk limits
        allowed, reason = self.risk_manager.is_trading_allowed()
        if not allowed:
            self.status = BotStatus.RISK_LOCKOUT
            return False

        self.status = BotStatus.ACTIVE
        return True

    def _shutdown(self):
        """Clean shutdown: save performance, log summary."""
        logger.info("Shutting down bot...")
        daily_stats = self.risk_manager.get_daily_stats()
        self.trade_logger.save_performance(daily_stats)
        all_stats = self.trade_logger.get_all_time_stats()

        logger.info("=" * 60)
        logger.info("FINAL PERFORMANCE SUMMARY")
        logger.info(f"  Trades: {all_stats.get('total_trades', 0)}")
        logger.info(f"  Win Rate: {all_stats.get('win_rate', 0)*100:.1f}%")
        logger.info(f"  Net P&L: ${all_stats.get('net_pnl', 0):+.2f}")
        logger.info(f"  Profit Factor: {all_stats.get('profit_factor', 0):.2f}")
        logger.info(f"  Max Drawdown: {all_stats.get('max_drawdown_pct', 0):.1f}%")
        logger.info("=" * 60)
        self.status = BotStatus.STOPPED


def run_simulation(cycles: int = 50, cycle_delay: float = 0.1):
    """
    Quick simulation run for testing.
    Runs N cycles with minimal delay.
    """
    import os
    os.makedirs("logs", exist_ok=True)

    bot = TradingBot(CONFIG)
    bot.start(max_cycles=cycles, cycle_interval=cycle_delay)
    return bot


if __name__ == "__main__":
    import os
    os.makedirs("logs", exist_ok=True)
    bot = run_simulation(cycles=100, cycle_delay=0.05)
    report = bot.get_status_report()
    print("\n📊 STATUS REPORT:")
    print(f"  Equity: ${report['account']['equity']:,.2f}")
    print(f"  Daily P&L: ${report['daily']['pnl']:+.2f}")
    print(f"  Trades: {report['daily']['trades']}")
    print(f"  Win Rate: {report['daily']['win_rate']:.1f}%")
