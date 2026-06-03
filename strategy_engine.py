"""
Strategy Engine
===============
Three intraday strategies:

Strategy A – Opening Range Breakout (ORB)
  - 5-min or 15-min opening range
  - Long above range high with volume confirmation
  - Stop: below OR midpoint or OR low
  - Target: 1.5R–2R

Strategy B – VWAP Reclaim
  - Stock gaps up or trends strongly
  - Pulls back to VWAP
  - Reclaims VWAP on increased volume
  - Entry after candle close above VWAP
  - Stop: below VWAP or recent swing low

Strategy C – Momentum Continuation
  - Above VWAP
  - Higher highs / higher lows
  - RelVol > 2x
  - Entry on consolidation breakout
  - Stop: below consolidation base
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict

from core.models import Signal, StrategyType, TradeDirection, Candle, Quote
from data.market_data import DataFeed
from config.config import TradingHoursConfig

logger = logging.getLogger(__name__)


def _higher_highs_higher_lows(candles: List[Candle], lookback: int = 4) -> bool:
    """Check if recent candles form HH/HL pattern."""
    if len(candles) < lookback:
        return False
    recent = candles[-lookback:]
    highs = [c.high for c in recent]
    lows = [c.low for c in recent]
    hh = all(highs[i] >= highs[i-1] for i in range(1, len(highs)))
    hl = all(lows[i] >= lows[i-1] for i in range(1, len(lows)))
    return hh and hl


def _volume_confirmation(candles: List[Candle], lookback: int = 3) -> bool:
    """Current candle volume > avg of prior N candles."""
    if len(candles) < lookback + 1:
        return False
    prior_avg = sum(c.volume for c in candles[-(lookback+1):-1]) / lookback
    current_vol = candles[-1].volume
    return current_vol > prior_avg * 1.2


def _find_consolidation(candles: List[Candle], n: int = 5) -> Optional[Dict]:
    """
    Identify a tight consolidation zone in the last N candles.
    Returns {high, low} of the base, or None if no consolidation.
    """
    if len(candles) < n:
        return None
    recent = candles[-n:]
    highs = [c.high for c in recent]
    lows = [c.low for c in recent]
    base_high = max(highs)
    base_low = min(lows)
    base_range_pct = (base_high - base_low) / base_low if base_low > 0 else 99

    # Tight consolidation = range < 3%
    if base_range_pct < 0.03:
        return {"high": base_high, "low": base_low, "range_pct": base_range_pct}
    return None


class StrategyEngine:
    """
    Evaluates a single symbol against all three strategies.
    Returns a Signal if a setup is found, else None.
    """

    def __init__(self, data_feed: DataFeed, hours_config: TradingHoursConfig):
        self.data_feed = data_feed
        self.hours = hours_config

    def evaluate(self, symbol: str) -> Optional[Signal]:
        """
        Run all strategies on a symbol.
        Returns the first valid signal found (prioritized by strategy order).
        """
        quote = self.data_feed.get_quote(symbol)
        if quote is None:
            return None

        # Try Strategy A first (highest priority at open)
        signal = self.strategy_orb(symbol, quote)
        if signal:
            return signal

        # Try Strategy B
        signal = self.strategy_vwap_reclaim(symbol, quote)
        if signal:
            return signal

        # Try Strategy C
        signal = self.strategy_momentum_continuation(symbol, quote)
        if signal:
            return signal

        return None

    # ─────────────────────────────────────────────
    # STRATEGY A: Opening Range Breakout
    # ─────────────────────────────────────────────

    def strategy_orb(self, symbol: str, quote: Quote) -> Optional[Signal]:
        """
        5-min ORB:
        1. Get opening range (first 5 candles of day)
        2. Current price must break above OR high
        3. Volume must confirm (current candle > avg)
        4. Stop: below OR midpoint
        5. Target: OR high + (OR high - OR midpoint) * 2 → 2R
        """
        now = datetime.now()

        # Only valid after 9:35 AM (after 5-min OR forms)
        market_open = now.replace(
            hour=int(self.hours.market_open.split(":")[0]),
            minute=int(self.hours.market_open.split(":")[1]),
            second=0
        )
        or_end = market_open.replace(minute=35)  # 9:30 + 5 min
        if now < or_end:
            return None

        # Get opening range
        opening_range = self.data_feed.get_opening_range(symbol, self.hours.opening_range_minutes)
        if not opening_range:
            return None

        or_high = opening_range["high"]
        or_low = opening_range["low"]
        or_mid = opening_range["mid"]

        # Price must be at or above OR high
        current_price = quote.ask  # Enter at ask
        if current_price < or_high * 0.999:
            return None

        # Get candles for volume confirmation
        candles = self.data_feed.get_candles(symbol, "5min", limit=10)
        if not candles or not _volume_confirmation(candles):
            return None

        # Price must be above VWAP too
        vwap = quote.vwap
        if vwap > 0 and current_price < vwap:
            return None

        # Entry, stop, target
        entry = round(or_high + 0.01, 2)   # penny above breakout
        stop = round(or_mid, 2)             # stop at OR midpoint
        risk_per_share = entry - stop

        if risk_per_share <= 0:
            return None

        # 2R target
        target = round(entry + risk_per_share * 2.0, 2)

        logger.info(
            f"📊 ORB Signal: {symbol} | Entry: ${entry} | "
            f"Stop: ${stop} | Target: ${target} | "
            f"OR: ${or_low}–${or_high}"
        )

        return Signal(
            symbol=symbol,
            strategy=StrategyType.ORB,
            direction=TradeDirection.LONG,
            timestamp=now,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            confidence=0.75,
            notes=(
                f"ORB breakout above {or_high:.2f} | "
                f"OR range: {or_low:.2f}–{or_high:.2f} | "
                f"Vol confirmed: yes"
            )
        )

    # ─────────────────────────────────────────────
    # STRATEGY B: VWAP Reclaim
    # ─────────────────────────────────────────────

    def strategy_vwap_reclaim(self, symbol: str, quote: Quote) -> Optional[Signal]:
        """
        1. Stock must have gapped up OR been trending strongly (> +3% on day)
        2. Price pulled back to VWAP
        3. Last candle closes ABOVE VWAP with increased volume
        4. Entry above last candle high
        5. Stop: below VWAP or recent swing low
        """
        now = datetime.now()

        vwap = quote.vwap
        if vwap <= 0:
            return None

        current_price = quote.last

        # Condition: gapped up or strong trend day
        gap_pct = quote.pre_market_gap_pct
        price_vs_vwap = (current_price - vwap) / vwap if vwap > 0 else 0

        is_gap_day = gap_pct >= 0.03
        is_strong_trend = price_vs_vwap >= 0.02

        if not (is_gap_day or is_strong_trend):
            return None

        # Get 5-min candles
        candles = self.data_feed.get_candles(symbol, "5min", limit=15)
        if len(candles) < 3:
            return None

        last_candle = candles[-1]
        prev_candle = candles[-2]

        # Previous candle must have been AT or BELOW VWAP (the pullback)
        if prev_candle.close > vwap * 1.005:
            return None

        # Last candle must CLOSE above VWAP (the reclaim)
        if last_candle.close <= vwap:
            return None

        # Volume must be increasing
        if not _volume_confirmation(candles):
            return None

        # Entry: above last candle high
        entry = round(last_candle.high + 0.01, 2)

        # Stop: below VWAP or last candle low (whichever is lower)
        swing_low = min(c.low for c in candles[-5:])
        stop = round(min(vwap - 0.02, swing_low), 2)

        risk_per_share = entry - stop
        if risk_per_share <= 0:
            return None

        # 1.5R minimum target
        target = round(entry + risk_per_share * 2.0, 2)

        logger.info(
            f"📊 VWAP Reclaim Signal: {symbol} | Entry: ${entry} | "
            f"Stop: ${stop} | VWAP: ${vwap:.2f} | Gap: {gap_pct*100:.1f}%"
        )

        return Signal(
            symbol=symbol,
            strategy=StrategyType.VWAP_RECLAIM,
            direction=TradeDirection.LONG,
            timestamp=now,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            confidence=0.70,
            notes=(
                f"VWAP reclaim after pullback | VWAP: {vwap:.2f} | "
                f"Gap: {gap_pct*100:.1f}% | Candle close: {last_candle.close:.2f}"
            )
        )

    # ─────────────────────────────────────────────
    # STRATEGY C: Momentum Continuation
    # ─────────────────────────────────────────────

    def strategy_momentum_continuation(self, symbol: str, quote: Quote) -> Optional[Signal]:
        """
        1. Stock must be above VWAP
        2. Forming higher highs / higher lows
        3. RelVol > 2x
        4. Tight consolidation forming
        5. Entry on breakout above consolidation high
        6. Stop: below consolidation low
        """
        now = datetime.now()

        vwap = quote.vwap
        if vwap <= 0:
            return None

        current_price = quote.last

        # Must be above VWAP
        if current_price <= vwap:
            return None

        # Must have relative volume > 2x
        if quote.relative_volume < 2.0:
            return None

        # Get candles for pattern check
        candles = self.data_feed.get_candles(symbol, "5min", limit=20)
        if len(candles) < 8:
            return None

        # Higher highs / higher lows in recent candles
        if not _higher_highs_higher_lows(candles, lookback=4):
            return None

        # Consolidation in most recent candles
        consolidation = _find_consolidation(candles, n=4)
        if not consolidation:
            return None

        base_high = consolidation["high"]
        base_low = consolidation["low"]

        # Price must be at or near the consolidation high (breakout forming)
        if current_price < base_high * 0.99:
            return None

        # Entry: above consolidation high
        entry = round(base_high + 0.01, 2)

        # Stop: below consolidation low
        stop = round(base_low - 0.01, 2)

        risk_per_share = entry - stop
        if risk_per_share <= 0:
            return None

        # 1.5R target minimum
        target = round(entry + risk_per_share * 1.75, 2)

        logger.info(
            f"📊 Momentum Continuation Signal: {symbol} | Entry: ${entry} | "
            f"Stop: ${stop} | Base: {base_low:.2f}–{base_high:.2f} | "
            f"RelVol: {quote.relative_volume:.1f}x"
        )

        return Signal(
            symbol=symbol,
            strategy=StrategyType.MOMENTUM_CONTINUATION,
            direction=TradeDirection.LONG,
            timestamp=now,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            confidence=0.65,
            notes=(
                f"Momentum continuation breakout | "
                f"Base: {base_low:.2f}–{base_high:.2f} | "
                f"VWAP: {vwap:.2f} | RelVol: {quote.relative_volume:.1f}x"
            )
        )
