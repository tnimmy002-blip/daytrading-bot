"""
Market Data Layer
=================
Abstracts all data fetching. Supports:
  - Alpaca Markets API (real & paper)
  - Simulated data for backtesting / Phase 1-3 testing

All data goes through a freshness check. If data is stale > 60s,
the bot will not trade.
"""

import logging
import random
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from collections import defaultdict
import math

from core.models import Candle, Quote

logger = logging.getLogger(__name__)

# Data is considered stale after this many seconds
DATA_STALENESS_THRESHOLD = 60


class DataFeed:
    """Base class for data feeds."""

    def get_quote(self, symbol: str) -> Optional[Quote]:
        raise NotImplementedError

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> List[Candle]:
        raise NotImplementedError

    def get_vwap(self, symbol: str) -> float:
        raise NotImplementedError

    def is_fresh(self, symbol: str) -> bool:
        raise NotImplementedError

    def get_relative_volume(self, symbol: str) -> float:
        raise NotImplementedError

    def get_pre_market_gap(self, symbol: str) -> float:
        raise NotImplementedError


class SimulatedDataFeed(DataFeed):
    """
    Simulated data feed for Phase 1-3 development and testing.
    Generates realistic intraday price action with:
    - Trend days
    - Choppy days
    - Volume patterns
    - VWAP drift
    """

    def __init__(self, seed: int = 42):
        random.seed(seed)
        self._last_update: Dict[str, datetime] = {}
        self._price_cache: Dict[str, float] = {}
        self._volume_cache: Dict[str, int] = {}
        self._candle_cache: Dict[str, List[Candle]] = defaultdict(list)
        self._session_start = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)

        # Pre-generate some realistic tickers for scanning
        self._universe = self._generate_universe()

    def _generate_universe(self) -> Dict[str, Dict]:
        """Generate a fake universe of scannable stocks."""
        tickers = [
            "AAPL", "TSLA", "AMD", "NVDA", "META", "SNAP", "COIN",
            "MARA", "RIOT", "PLTR", "SOFI", "GME", "AMC", "BBBY",
            "SNDL", "CLOV", "WISH", "TLRY", "CGC", "ACB"
        ]
        universe = {}
        for t in tickers:
            base_price = random.uniform(3.0, 45.0)
            avg_vol = random.randint(2_000_000, 20_000_000)
            rel_vol = random.uniform(0.8, 5.0)
            universe[t] = {
                "base_price": base_price,
                "avg_volume": avg_vol,
                "relative_volume": rel_vol,
                "float": random.randint(5_000_000, 500_000_000),
                "trend": random.choice(["up", "down", "sideways"]),
                "gap_pct": random.uniform(-0.15, 0.25),
            }
        return universe

    def scan_universe(self) -> List[str]:
        """Return list of all symbols in universe."""
        return list(self._universe.keys())

    def get_quote(self, symbol: str) -> Optional[Quote]:
        if symbol not in self._universe:
            return None

        info = self._universe[symbol]
        now = datetime.now()

        # Simulate price movement
        if symbol in self._price_cache:
            last = self._price_cache[symbol]
            change_pct = random.gauss(0.0001, 0.002)
            if info["trend"] == "up":
                change_pct += 0.0003
            elif info["trend"] == "down":
                change_pct -= 0.0003
            new_price = max(0.01, last * (1 + change_pct))
        else:
            new_price = info["base_price"] * (1 + info["gap_pct"])

        self._price_cache[symbol] = new_price
        self._last_update[symbol] = now

        spread_pct = random.uniform(0.001, 0.004)
        spread = new_price * spread_pct
        bid = new_price - spread / 2
        ask = new_price + spread / 2

        current_vol = info["avg_volume"] * info["relative_volume"]

        return Quote(
            symbol=symbol,
            timestamp=now,
            bid=round(bid, 2),
            ask=round(ask, 2),
            last=round(new_price, 2),
            volume=int(current_vol * random.uniform(0.4, 1.0)),
            avg_volume=info["avg_volume"],
            relative_volume=info["relative_volume"],
            vwap=round(new_price * random.uniform(0.98, 1.02), 2),
            pre_market_gap_pct=info["gap_pct"]
        )

    def get_candles(self, symbol: str, timeframe: str = "5min", limit: int = 20) -> List[Candle]:
        """Generate synthetic OHLCV candles."""
        if symbol not in self._universe:
            return []

        quote = self.get_quote(symbol)
        if not quote:
            return []

        candles = []
        price = quote.last
        interval_map = {"1min": 1, "5min": 5, "15min": 15}
        minutes = interval_map.get(timeframe, 5)
        now = datetime.now()

        for i in range(limit, 0, -1):
            ts = now - timedelta(minutes=minutes * i)
            change = random.gauss(0, 0.008)
            open_p = price * (1 + random.gauss(0, 0.003))
            close_p = open_p * (1 + change)
            high_p = max(open_p, close_p) * (1 + abs(random.gauss(0, 0.002)))
            low_p = min(open_p, close_p) * (1 - abs(random.gauss(0, 0.002)))
            vol = int(self._universe[symbol]["avg_volume"] / (390 / minutes) * random.uniform(0.5, 2.0))
            vwap = (open_p + high_p + low_p + close_p) / 4

            candles.append(Candle(
                symbol=symbol,
                timestamp=ts,
                open=round(open_p, 2),
                high=round(high_p, 2),
                low=round(low_p, 2),
                close=round(close_p, 2),
                volume=vol,
                vwap=round(vwap, 2)
            ))
            price = close_p

        return candles

    def get_vwap(self, symbol: str) -> float:
        quote = self.get_quote(symbol)
        return quote.vwap if quote else 0.0

    def get_relative_volume(self, symbol: str) -> float:
        if symbol in self._universe:
            return self._universe[symbol]["relative_volume"]
        return 1.0

    def get_pre_market_gap(self, symbol: str) -> float:
        if symbol in self._universe:
            return self._universe[symbol]["gap_pct"]
        return 0.0

    def is_fresh(self, symbol: str) -> bool:
        if symbol not in self._last_update:
            return True  # sim data is always "fresh"
        age = (datetime.now() - self._last_update[symbol]).seconds
        return age < DATA_STALENESS_THRESHOLD

    def get_opening_range(self, symbol: str, minutes: int = 5) -> Optional[Dict]:
        """
        Calculate opening range high/low from first N minutes of candles.
        Returns dict with high, low, mid keys.
        """
        candles = self.get_candles(symbol, "1min", limit=minutes + 5)
        if len(candles) < minutes:
            return None

        or_candles = candles[:minutes]
        or_high = max(c.high for c in or_candles)
        or_low = min(c.low for c in or_candles)
        or_mid = (or_high + or_low) / 2

        return {
            "high": round(or_high, 2),
            "low": round(or_low, 2),
            "mid": round(or_mid, 2),
            "range": round(or_high - or_low, 2)
        }


class AlpacaDataFeed(DataFeed):
    """
    Alpaca Markets data feed.
    Requires alpaca-trade-api or alpaca-py installed.
    Used in Phase 4+ when connecting to real paper/live trading.
    """

    def __init__(self, api_key: str, api_secret: str, base_url: str, data_feed: str = "iex"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.data_feed = data_feed
        self._last_update: Dict[str, datetime] = {}
        self._api = None
        self._connected = False
        self._connect()

    def _connect(self):
        try:
            import alpaca_trade_api as tradeapi
            self._api = tradeapi.REST(
                self.api_key,
                self.api_secret,
                self.base_url,
                api_version="v2"
            )
            account = self._api.get_account()
            self._connected = True
            logger.info(f"✅ Alpaca connected. Account: {account.id}")
        except ImportError:
            logger.error("alpaca-trade-api not installed. Run: pip install alpaca-trade-api")
        except Exception as e:
            logger.error(f"Alpaca connection failed: {e}")

    def get_quote(self, symbol: str) -> Optional[Quote]:
        if not self._connected or not self._api:
            return None
        try:
            quote_data = self._api.get_last_quote(symbol)
            trade_data = self._api.get_last_trade(symbol)
            bars = self._api.get_bars(symbol, "1Min", limit=1).df

            avg_vol = 1_000_000  # Would fetch from historical
            rel_vol = 1.0

            self._last_update[symbol] = datetime.now()
            return Quote(
                symbol=symbol,
                timestamp=datetime.now(),
                bid=float(quote_data.bidprice),
                ask=float(quote_data.askprice),
                last=float(trade_data.price),
                volume=int(trade_data.size),
                avg_volume=avg_vol,
                relative_volume=rel_vol
            )
        except Exception as e:
            logger.error(f"Quote fetch error for {symbol}: {e}")
            return None

    def get_candles(self, symbol: str, timeframe: str = "5Min", limit: int = 20) -> List[Candle]:
        if not self._connected or not self._api:
            return []
        try:
            tf_map = {"1min": "1Min", "5min": "5Min", "15min": "15Min"}
            alpaca_tf = tf_map.get(timeframe, "5Min")
            bars = self._api.get_bars(symbol, alpaca_tf, limit=limit).df
            candles = []
            for ts, row in bars.iterrows():
                candles.append(Candle(
                    symbol=symbol,
                    timestamp=ts.to_pydatetime(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                    vwap=float(row.get("vwap", row["close"]))
                ))
            return candles
        except Exception as e:
            logger.error(f"Candle fetch error for {symbol}: {e}")
            return []

    def get_vwap(self, symbol: str) -> float:
        candles = self.get_candles(symbol, "1min", 1)
        return candles[-1].vwap if candles and candles[-1].vwap else 0.0

    def get_relative_volume(self, symbol: str) -> float:
        # Would compare current volume pace vs 20-day avg at same time
        return 1.0

    def get_pre_market_gap(self, symbol: str) -> float:
        return 0.0

    def is_fresh(self, symbol: str) -> bool:
        if symbol not in self._last_update:
            return False
        age = (datetime.now() - self._last_update[symbol]).seconds
        return age < DATA_STALENESS_THRESHOLD

    def get_opening_range(self, symbol: str, minutes: int = 5) -> Optional[Dict]:
        candles = self.get_candles(symbol, "1min", limit=minutes + 5)
        if len(candles) < minutes:
            return None
        or_candles = candles[:minutes]
        or_high = max(c.high for c in or_candles)
        or_low = min(c.low for c in or_candles)
        return {
            "high": round(or_high, 2),
            "low": round(or_low, 2),
            "mid": round((or_high + or_low) / 2, 2),
            "range": round(or_high - or_low, 2)
        }


def create_data_feed(config) -> DataFeed:
    """Factory function. Returns appropriate feed based on config."""
    if config.broker.paper_trading and not config.broker.api_key:
        logger.info("Using simulated data feed (Phase 1-3 mode)")
        return SimulatedDataFeed()

    if config.broker.broker == "alpaca":
        import os
        api_key = config.broker.api_key or os.getenv("ALPACA_API_KEY", "")
        api_secret = config.broker.api_secret or os.getenv("ALPACA_SECRET_KEY", "")
        if api_key and api_secret:
            return AlpacaDataFeed(api_key, api_secret, config.broker.base_url, config.broker.data_feed)

    logger.warning("No broker credentials found, falling back to simulated feed")
    return SimulatedDataFeed()
