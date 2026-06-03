"""
Trading Bot Configuration
All parameters in one place. Edit here, not in code.
"""

from dataclasses import dataclass, field
from typing import List

# ─────────────────────────────────────────────
# ACCOUNT & RISK PARAMETERS
# ─────────────────────────────────────────────
INITIAL_CAPITAL = 5000.0

@dataclass
class RiskConfig:
    initial_capital: float = INITIAL_CAPITAL
    max_risk_per_trade_pct: float = 0.005      # 0.5% = $25 on $5k
    max_daily_loss_pct: float = 0.02           # 2% = $100
    max_weekly_loss_pct: float = 0.05          # 5% = $250
    max_open_positions: int = 2
    max_trades_per_day: int = 5
    max_consecutive_losses: int = 2
    min_reward_to_risk: float = 1.5
    max_spread_pct: float = 0.005              # 0.5% spread max
    no_overnight_holds: bool = True
    no_averaging_down: bool = True

    @property
    def max_risk_per_trade(self) -> float:
        return self.initial_capital * self.max_risk_per_trade_pct

    @property
    def max_daily_loss(self) -> float:
        return self.initial_capital * self.max_daily_loss_pct

    @property
    def max_weekly_loss(self) -> float:
        return self.initial_capital * self.max_weekly_loss_pct


@dataclass
class ScannerConfig:
    min_price: float = 2.0
    max_price: float = 50.0
    min_relative_volume: float = 2.0
    min_avg_daily_volume: int = 1_000_000
    max_spread_pct: float = 0.005
    avoid_low_float: bool = True
    low_float_threshold: int = 10_000_000    # shares


@dataclass
class TradingHoursConfig:
    market_open: str = "09:30"
    market_close: str = "16:00"
    eod_exit_time: str = "15:45"             # force-close by 3:45 PM
    no_new_trades_after: str = "15:30"       # no new entries after this
    opening_range_minutes: int = 5           # 5-min OR for ORB strategy
    opening_range_minutes_alt: int = 15      # 15-min OR alternative


@dataclass
class BrokerConfig:
    broker: str = "alpaca"                   # alpaca | ibkr | tradier
    paper_trading: bool = True               # ALWAYS start in paper mode
    live_trading_enabled: bool = False       # must be manually set True
    api_key: str = "PK5QMZJYVUSIN4QFCEO2E5ERNQ"                        # set via environment variable
    api_secret: str = "AnXXYcLSj71XPGXmanHs7JtsXmFUQDaFiStGhHFba47t"                     # set via environment variable
    base_url: str = "https://paper-api.alpaca.markets"
    data_feed: str = "iex"                   # iex | sip


@dataclass
class LoggingConfig:
    log_dir: str = "logs"
    trade_log_file: str = "trades.csv"
    signal_log_file: str = "signals.csv"
    rejection_log_file: str = "rejections.csv"
    performance_log_file: str = "performance.json"
    screenshot_dir: str = "screenshots"


@dataclass
class BotConfig:
    risk: RiskConfig = field(default_factory=RiskConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    hours: TradingHoursConfig = field(default_factory=TradingHoursConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    version: str = "1.0.0"
    phase: int = 1       # Development phase 1-7


# Singleton config
CONFIG = BotConfig()
