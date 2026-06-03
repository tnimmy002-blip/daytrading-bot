"""
Stock Scanner
=============
Scans the universe for stocks meeting our intraday criteria.

Criteria:
  - Price $2–$50
  - Relative volume > 2x
  - Spread < 0.5%
  - Avg daily volume > 1M
  - Intraday volume surge
  - Gap data (catalyst preferred)
  - Avoid low-float halts (Phase 1)
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from data.market_data import DataFeed
from core.models import Quote
from config.config import ScannerConfig

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    symbol: str
    quote: Quote
    passes_scan: bool
    reasons_passed: List[str]
    reasons_failed: List[str]
    scan_time: datetime

    @property
    def summary(self) -> str:
        status = "✅" if self.passes_scan else "❌"
        return (
            f"{status} {self.symbol} | ${self.quote.last:.2f} | "
            f"RelVol: {self.quote.relative_volume:.1f}x | "
            f"Gap: {self.quote.pre_market_gap_pct*100:.1f}% | "
            f"Spread: {self.quote.spread_pct*100:.2f}%"
        )


class Scanner:
    """
    Intraday stock scanner. Runs on a configurable interval
    and returns stocks that qualify for strategy evaluation.
    """

    def __init__(self, data_feed: DataFeed, config: ScannerConfig):
        self.data_feed = data_feed
        self.config = config
        self._scan_history: List[ScanResult] = []
        self._qualified_cache: List[str] = []
        self._last_scan_time: Optional[datetime] = None

    def scan(self, symbols: Optional[List[str]] = None) -> List[ScanResult]:
        """
        Run full scan across symbol universe.
        Returns all results with pass/fail detail.
        """
        if symbols is None:
            # Use data feed's universe
            if hasattr(self.data_feed, 'scan_universe'):
                symbols = self.data_feed.scan_universe()
            else:
                logger.warning("No symbol universe available")
                return []

        results = []
        qualified = []

        for symbol in symbols:
            quote = self.data_feed.get_quote(symbol)
            if quote is None:
                continue

            result = self._evaluate(symbol, quote)
            results.append(result)

            if result.passes_scan:
                qualified.append(symbol)
                logger.debug(result.summary)

        self._qualified_cache = qualified
        self._last_scan_time = datetime.now()
        self._scan_history.extend(results)

        passed = [r for r in results if r.passes_scan]
        logger.info(
            f"Scan complete: {len(results)} scanned, {len(passed)} qualified | "
            f"{[r.symbol for r in passed]}"
        )

        return results

    def get_qualified(self) -> List[str]:
        """Return last cached list of qualifying symbols."""
        return self._qualified_cache.copy()

    def _evaluate(self, symbol: str, quote: Quote) -> ScanResult:
        passed = []
        failed = []

        # 1. Price range
        if self.config.min_price <= quote.last <= self.config.max_price:
            passed.append(f"Price ${quote.last:.2f} in range")
        else:
            failed.append(f"Price ${quote.last:.2f} outside ${self.config.min_price}–${self.config.max_price}")

        # 2. Relative volume
        if quote.relative_volume >= self.config.min_relative_volume:
            passed.append(f"RelVol {quote.relative_volume:.1f}x ≥ {self.config.min_relative_volume}x")
        else:
            failed.append(f"RelVol {quote.relative_volume:.1f}x < {self.config.min_relative_volume}x")

        # 3. Average daily volume
        if quote.avg_volume >= self.config.min_avg_daily_volume:
            passed.append(f"AvgVol {quote.avg_volume:,} ≥ 1M")
        else:
            failed.append(f"AvgVol {quote.avg_volume:,} < 1M")

        # 4. Spread
        if quote.spread_pct <= self.config.max_spread_pct:
            passed.append(f"Spread {quote.spread_pct*100:.2f}% ≤ {self.config.max_spread_pct*100:.2f}%")
        else:
            failed.append(f"Spread {quote.spread_pct*100:.2f}% too wide")

        # 5. Not zero price (sanity)
        if quote.last > 0:
            passed.append("Price > 0")
        else:
            failed.append("Invalid price")

        qualifies = len(failed) == 0

        return ScanResult(
            symbol=symbol,
            quote=quote,
            passes_scan=qualifies,
            reasons_passed=passed,
            reasons_failed=failed,
            scan_time=datetime.now()
        )

    def get_top_movers(self, n: int = 5) -> List[ScanResult]:
        """Return top N by relative volume from last scan."""
        if not self._scan_history:
            return []
        recent = [r for r in self._scan_history[-100:] if r.passes_scan]
        return sorted(recent, key=lambda r: r.quote.relative_volume, reverse=True)[:n]
