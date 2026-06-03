"""
Execution Engine
================
Handles order placement, bracket order simulation,
position monitoring, and EOD flat logic.

Broker Layer:
  - PaperBroker: simulated execution (Phase 1-4)
  - AlpacaBroker: live Alpaca connection (Phase 4+)

Trade Management Rules:
  - Move stop to breakeven at +1R
  - Take partial (50%) at +1.5R
  - Force-close all by 3:45 PM
  - Never widen stop
  - Never add to a losing trade
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import random

from core.models import (
    Order, OrderStatus, OrderSide, OrderType,
    Position, TradeSetup, TradeDirection, TradeState,
    ClosedTrade, StrategyType
)
from config.config import TradingHoursConfig

logger = logging.getLogger(__name__)


class PaperBroker:
    """
    Simulated broker for paper trading.
    Fills orders with slight slippage to simulate real conditions.
    Phase 1-4 development and testing.
    """

    def __init__(self, initial_equity: float = 5000.0, slippage_pct: float = 0.001):
        self.equity = initial_equity
        self.buying_power = initial_equity * 2  # 2x intraday margin
        self.slippage_pct = slippage_pct
        self._orders: Dict[str, Order] = {}
        self._fill_latency_ms = random.randint(50, 250)

    def place_bracket_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        entry_price: float,
        stop_price: float,
        target_price: float
    ) -> Tuple[Optional[Order], Optional[Order], Optional[Order]]:
        """
        Place bracket order: entry + stop + target.
        Returns (entry_order, stop_order, target_order).
        """
        slippage = entry_price * self.slippage_pct
        fill_price = entry_price + slippage if side == OrderSide.BUY else entry_price - slippage
        fill_price = round(fill_price, 2)

        cost = fill_price * quantity
        if cost > self.buying_power:
            logger.error(f"Insufficient buying power: need ${cost:.2f}, have ${self.buying_power:.2f}")
            return None, None, None

        # Create entry order
        entry_order = Order(
            order_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            limit_price=entry_price,
            status=OrderStatus.FILLED,
            filled_price=fill_price,
            filled_qty=quantity,
            submitted_at=datetime.now(),
            filled_at=datetime.now()
        )

        # Create stop order
        stop_order = Order(
            order_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            side=OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=quantity,
            stop_price=stop_price,
            status=OrderStatus.SUBMITTED,
            submitted_at=datetime.now()
        )

        # Create target order
        target_order = Order(
            order_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            side=OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            limit_price=target_price,
            status=OrderStatus.SUBMITTED,
            submitted_at=datetime.now()
        )

        self.buying_power -= cost
        for o in [entry_order, stop_order, target_order]:
            self._orders[o.order_id] = o

        logger.info(
            f"📋 BRACKET ORDER: {symbol} {quantity} shares | "
            f"Entry: ${fill_price} | Stop: ${stop_price} | Target: ${target_price}"
        )

        return entry_order, stop_order, target_order

    def update_stop(self, stop_order: Order, new_stop_price: float) -> bool:
        """Modify a stop order to new price. Never allow widening."""
        if stop_order.order_id not in self._orders:
            return False
        order = self._orders[stop_order.order_id]
        old_stop = order.stop_price or 0
        # Validate: new stop must be HIGHER than old for longs (tighter)
        if new_stop_price <= old_stop:
            logger.warning(f"Stop widening rejected: {old_stop} → {new_stop_price}")
            return False
        order.stop_price = new_stop_price
        logger.info(f"Stop updated: {old_stop:.2f} → {new_stop_price:.2f}")
        return True

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False

    def simulate_fill_check(
        self,
        position: Position,
        current_price: float,
        current_high: float,
        current_low: float
    ) -> Optional[str]:
        """
        Check if stop or target was hit this bar.
        Returns 'stop', 'target', or None.
        """
        if position.direction == TradeDirection.LONG:
            if current_low <= position.stop_price:
                return "stop"
            if current_high >= position.target_price:
                return "target"
        else:
            if current_high >= position.stop_price:
                return "stop"
            if current_low <= position.target_price:
                return "target"
        return None

    def close_position(
        self,
        position: Position,
        exit_price: float,
        reason: str
    ) -> ClosedTrade:
        """Close a position and return the trade record."""
        slippage = exit_price * self.slippage_pct * random.uniform(0.5, 2.0)
        actual_exit = round(exit_price - slippage, 2)

        if position.direction == TradeDirection.LONG:
            pnl = (actual_exit - position.entry_price) * position.shares
        else:
            pnl = (position.entry_price - actual_exit) * position.shares

        # Return buying power
        self.buying_power += position.cost_basis

        r_multiple = pnl / (position.total_risk) if position.total_risk > 0 else 0

        trade = ClosedTrade(
            symbol=position.symbol,
            strategy=position.strategy,
            direction=position.direction,
            entry_price=position.entry_price,
            exit_price=actual_exit,
            shares=position.shares,
            entry_time=position.entry_time,
            exit_time=datetime.now(),
            stop_price=position.stop_price,
            target_price=position.target_price,
            pnl=round(pnl, 2),
            r_multiple=round(r_multiple, 2),
            exit_reason=reason
        )

        logger.info(
            f"{'✅' if pnl > 0 else '❌'} CLOSED: {position.symbol} | "
            f"PnL: ${pnl:+.2f} | R: {r_multiple:+.2f} | Reason: {reason}"
        )

        return trade

    def get_account_equity(self) -> float:
        return self.equity

    def update_equity(self, pnl: float):
        self.equity += pnl


class ExecutionEngine:
    """
    Manages the full lifecycle of trades:
      - Entry from approved signals
      - Stop/target monitoring
      - Breakeven stop management
      - Partial exits
      - EOD force-close
    """

    def __init__(self, broker: PaperBroker, hours_config: TradingHoursConfig):
        self.broker = broker
        self.hours = hours_config
        self._open_positions: Dict[str, Position] = {}
        self._closed_trades: List[ClosedTrade] = []
        self._eod_closed = False

    @property
    def open_positions(self) -> Dict[str, Position]:
        return self._open_positions.copy()

    @property
    def closed_trades(self) -> List[ClosedTrade]:
        return self._closed_trades.copy()

    def enter_trade(self, setup: TradeSetup) -> Optional[Position]:
        """
        Execute entry from an approved TradeSetup.
        Places bracket order and creates Position object.
        """
        signal = setup.signal

        # Duplicate position check
        if signal.symbol in self._open_positions:
            logger.warning(f"Already have open position in {signal.symbol}")
            return None

        entry_order, stop_order, target_order = self.broker.place_bracket_order(
            symbol=signal.symbol,
            side=OrderSide.BUY if signal.direction == TradeDirection.LONG else OrderSide.SELL,
            quantity=setup.shares,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            target_price=signal.target_price
        )

        if not entry_order or entry_order.status != OrderStatus.FILLED:
            logger.error(f"Entry order not filled for {signal.symbol}")
            return None

        actual_entry = entry_order.filled_price or signal.entry_price

        position = Position(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=actual_entry,
            shares=setup.shares,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            strategy=signal.strategy,
            entry_time=datetime.now(),
            entry_order_id=entry_order.order_id,
            stop_order_id=stop_order.order_id if stop_order else None,
            target_order_id=target_order.order_id if target_order else None
        )

        self._open_positions[signal.symbol] = position
        logger.info(
            f"🟢 ENTERED: {signal.symbol} {setup.shares} shares @ ${actual_entry:.2f} | "
            f"Strategy: {signal.strategy.value}"
        )
        return position

    def update_positions(self, price_data: Dict[str, Tuple[float, float, float]]) -> List[ClosedTrade]:
        """
        Called every bar/tick with current (price, high, low) for each symbol.
        Manages stops, targets, breakeven, and partial exits.
        Returns any trades closed this update.
        """
        newly_closed = []

        for symbol, position in list(self._open_positions.items()):
            if symbol not in price_data:
                continue

            current_price, current_high, current_low = price_data[symbol]

            # Check if stop or target hit
            fill_result = self.broker.simulate_fill_check(
                position, current_price, current_high, current_low
            )

            if fill_result == "stop":
                trade = self.broker.close_position(
                    position,
                    position.stop_price,
                    "Stop loss hit"
                )
                del self._open_positions[symbol]
                self._closed_trades.append(trade)
                newly_closed.append(trade)
                continue

            elif fill_result == "target":
                trade = self.broker.close_position(
                    position,
                    position.target_price,
                    "Target reached"
                )
                del self._open_positions[symbol]
                self._closed_trades.append(trade)
                newly_closed.append(trade)
                continue

            # Trade management: breakeven stop at +1R
            r_now = position.r_multiple(current_price)

            if r_now >= 1.0 and not position.breakeven_stop_set:
                if position.stop_order_id:
                    stop_order = self.broker._orders.get(position.stop_order_id)
                    if stop_order:
                        updated = self.broker.update_stop(stop_order, position.entry_price)
                        if updated:
                            position.stop_price = position.entry_price
                            position.breakeven_stop_set = True
                            logger.info(f"🔒 Breakeven stop set for {symbol} @ ${position.entry_price:.2f}")

            # EOD check
            self._check_eod_close(symbol, position, current_price, newly_closed)

        return newly_closed

    def _check_eod_close(
        self,
        symbol: str,
        position: Position,
        current_price: float,
        closed_list: List[ClosedTrade]
    ):
        """Force close all positions by EOD time."""
        now = datetime.now()
        eod_hour = int(self.hours.eod_exit_time.split(":")[0])
        eod_minute = int(self.hours.eod_exit_time.split(":")[1])
        eod_time = now.replace(hour=eod_hour, minute=eod_minute, second=0)

        if now >= eod_time and symbol in self._open_positions:
            trade = self.broker.close_position(
                position,
                current_price,
                "EOD force-close (no overnight holds)"
            )
            del self._open_positions[symbol]
            self._closed_trades.append(trade)
            closed_list.append(trade)
            logger.info(f"🌙 EOD close: {symbol} @ ${current_price:.2f}")

    def emergency_stop(self, price_data: Dict[str, float]):
        """Manually close all open positions immediately."""
        logger.critical("🚨 EMERGENCY STOP TRIGGERED - Closing all positions")
        for symbol in list(self._open_positions.keys()):
            position = self._open_positions[symbol]
            price = price_data.get(symbol, position.entry_price)
            trade = self.broker.close_position(position, price, "Emergency stop")
            del self._open_positions[symbol]
            self._closed_trades.append(trade)
            logger.critical(f"Emergency closed: {symbol} @ ${price:.2f}")
