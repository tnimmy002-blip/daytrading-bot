"""
Core data models for the trading bot.
All shared types defined here to avoid circular imports.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List


class BotStatus(Enum):
    INITIALIZING = "Initializing"
    ACTIVE = "Active"
    PAUSED = "Paused"
    RISK_LOCKOUT = "Risk Lockout"
    EOD_CLOSING = "EOD Closing"
    MARKET_CLOSED = "Market Closed"
    ERROR = "Error"
    STOPPED = "Stopped"


class OrderStatus(Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TradeDirection(Enum):
    LONG = "long"
    SHORT = "short"


class StrategyType(Enum):
    ORB = "Opening Range Breakout"
    VWAP_RECLAIM = "VWAP Reclaim"
    MOMENTUM_CONTINUATION = "Momentum Continuation"


class TradeState(Enum):
    WAITING_ENTRY = "waiting_entry"
    ENTERED = "entered"
    BREAKEVEN_STOP = "breakeven_stop"
    PARTIAL_EXIT = "partial_exit"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class RejectionReason(Enum):
    RISK_TOO_HIGH = "Risk exceeds per-trade limit"
    DAILY_LOSS_EXCEEDED = "Daily loss limit reached"
    WEEKLY_LOSS_EXCEEDED = "Weekly loss limit reached"
    MAX_TRADES_REACHED = "Max trades per day reached"
    MAX_POSITIONS_REACHED = "Max open positions reached"
    CONSECUTIVE_LOSSES = "Consecutive loss limit triggered"
    RR_TOO_LOW = "Reward-to-risk below minimum"
    SPREAD_TOO_WIDE = "Spread too wide"
    OUTSIDE_HOURS = "Outside trading hours"
    STALE_DATA = "Data feed stale"
    DUPLICATE_ORDER = "Duplicate order detected"
    NO_STOP_DEFINED = "No stop-loss defined"
    NO_TARGET_DEFINED = "No profit target defined"
    PRICE_OUT_OF_RANGE = "Price outside scanner range"
    LOW_VOLUME = "Insufficient volume"
    API_ERROR = "API error"


@dataclass
class Candle:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open


@dataclass
class Quote:
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    last: float
    volume: int
    avg_volume: int = 0
    relative_volume: float = 0.0
    vwap: float = 0.0
    pre_market_gap_pct: float = 0.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        return self.spread / self.last if self.last > 0 else 999

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class Signal:
    symbol: str
    strategy: StrategyType
    direction: TradeDirection
    timestamp: datetime
    entry_price: float
    stop_price: float
    target_price: float
    confidence: float = 0.0         # 0.0 - 1.0
    notes: str = ""

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def reward_per_share(self) -> float:
        return abs(self.target_price - self.entry_price)

    @property
    def reward_to_risk(self) -> float:
        if self.risk_per_share == 0:
            return 0
        return self.reward_per_share / self.risk_per_share


@dataclass
class TradeSetup:
    signal: Signal
    shares: int
    dollar_risk: float
    dollar_target: float
    position_value: float

    @property
    def r_multiple_actual(self) -> Optional[float]:
        return None  # calculated after close


@dataclass
class Order:
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    filled_qty: int = 0
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    broker_order_id: Optional[str] = None


@dataclass
class Position:
    symbol: str
    direction: TradeDirection
    entry_price: float
    shares: int
    stop_price: float
    target_price: float
    strategy: StrategyType
    entry_time: datetime
    state: TradeState = TradeState.ENTERED
    partial_exit_done: bool = False
    breakeven_stop_set: bool = False
    entry_order_id: Optional[str] = None
    stop_order_id: Optional[str] = None
    target_order_id: Optional[str] = None

    @property
    def cost_basis(self) -> float:
        return self.entry_price * self.shares

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def total_risk(self) -> float:
        return self.risk_per_share * self.shares

    def unrealized_pnl(self, current_price: float) -> float:
        if self.direction == TradeDirection.LONG:
            return (current_price - self.entry_price) * self.shares
        return (self.entry_price - current_price) * self.shares

    def r_multiple(self, current_price: float) -> float:
        if self.risk_per_share == 0:
            return 0
        pnl_per_share = current_price - self.entry_price
        if self.direction == TradeDirection.SHORT:
            pnl_per_share = -pnl_per_share
        return pnl_per_share / self.risk_per_share


@dataclass
class ClosedTrade:
    symbol: str
    strategy: StrategyType
    direction: TradeDirection
    entry_price: float
    exit_price: float
    shares: int
    entry_time: datetime
    exit_time: datetime
    stop_price: float
    target_price: float
    pnl: float
    r_multiple: float
    exit_reason: str
    signal_notes: str = ""

    @property
    def win(self) -> bool:
        return self.pnl > 0


@dataclass
class DailyStats:
    date: str
    starting_equity: float
    current_equity: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trades_taken: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    consecutive_losses: int = 0
    signals_generated: int = 0
    signals_rejected: int = 0

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def win_rate(self) -> float:
        if self.trades_taken == 0:
            return 0
        return self.wins / self.trades_taken

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0:
            return float('inf') if self.gross_profit > 0 else 0
        return self.gross_profit / abs(self.gross_loss)

    @property
    def avg_win(self) -> float:
        if self.wins == 0:
            return 0
        return self.gross_profit / self.wins

    @property
    def avg_loss(self) -> float:
        if self.losses == 0:
            return 0
        return self.gross_loss / self.losses
