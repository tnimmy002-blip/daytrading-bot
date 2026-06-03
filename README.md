# DayBot — $5,000 Day Trading Bot

> **⚠ PAPER TRADING MODE BY DEFAULT. Live trading requires explicit opt-in.**

A modular, rules-based intraday trading system built for a $5,000 account.
Capital preservation first. No hero trades. No holding overnight.

---

## Architecture

```
trading_bot/
├── config/
│   └── config.py          ← All parameters in one place
├── core/
│   └── models.py          ← Shared data types (Signal, Position, Trade, etc.)
├── data/
│   ├── market_data.py     ← Data feeds (Simulated + Alpaca)
│   └── scanner.py         ← Intraday stock scanner
├── strategies/
│   └── strategy_engine.py ← 3 strategies: ORB, VWAP Reclaim, Momentum
├── risk/
│   └── risk_manager.py    ← All risk rules enforced here
├── execution/
│   └── execution_engine.py ← Order placement, position management
├── logging/
│   └── trade_logger.py    ← CSV logs, performance analytics
├── bot.py                 ← Main orchestrator
├── requirements.txt
└── dashboard/
    └── trading_bot_dashboard.html  ← Live web dashboard
```

---

## Risk Rules (Non-Negotiable)

| Rule | Value |
|------|-------|
| Max risk per trade | 0.5% = **$25** |
| Max daily loss | 2.0% = **$100** |
| Max weekly loss | 5.0% = **$250** |
| Max open positions | **2** |
| Max trades per day | **5** |
| Consecutive loss lockout | **2 losses** |
| Minimum R:R ratio | **1.5x** |
| No overnight holds | **Enforced** |
| No averaging down | **Enforced** |
| No stop widening | **Enforced** |

---

## Strategies

### Strategy A: Opening Range Breakout (ORB)
- Define 5-minute opening range (9:30–9:35 AM)
- Enter long above OR high with volume confirmation
- Stop: OR midpoint
- Target: 2R

### Strategy B: VWAP Reclaim
- Gap up (>3%) or strong trend day (>2% above VWAP)
- Price pulls back to VWAP
- Last candle closes above VWAP with increased volume
- Stop: below VWAP or recent swing low
- Target: 2R

### Strategy C: Momentum Continuation
- Stock above VWAP
- Higher highs / higher lows pattern
- Relative volume > 2x
- Tight consolidation forming (< 3% range)
- Entry: above consolidation high
- Stop: below consolidation low
- Target: 1.75R

---

## Development Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Paper trading scanner + logger | ✅ Active |
| 2 | Strategy signal generation | ✅ Built |
| 3 | Simulated execution + backtest | ✅ Built |
| 4 | Connect Alpaca paper trading | 🔜 Next |
| 5 | Forward test 30 trading days | ⏳ |
| 6 | Live trading at 25% size | ⏳ |
| 7 | Optimize after 100+ trades | ⏳ |

---

## Quick Start

### Phase 1-3: Simulation Mode (No API key needed)
```bash
# Install dependencies
pip install -r requirements.txt

# Run simulation
cd trading_bot
python bot.py
```

### Phase 4+: Alpaca Paper Trading
```bash
# Set environment variables
export ALPACA_API_KEY=your_key_here
export ALPACA_SECRET_KEY=your_secret_here

# Edit config to use Alpaca
# In config/config.py:
#   BrokerConfig.api_key = "" (uses env var)
#   BrokerConfig.paper_trading = True

python bot.py
```

### Open Dashboard
Open `trading_bot_dashboard.html` in your browser.
The dashboard simulates real bot activity and shows all metrics.

---

## Live Trading Safety Checklist

Before enabling live trading (`live_trading_enabled = True`):

- [ ] 30+ days of paper trading with positive expectancy
- [ ] 100+ simulated trades logged
- [ ] Win rate > 45%, Profit factor > 1.3
- [ ] Max drawdown < 8% in paper trading
- [ ] All edge cases tested (API errors, stale data, etc.)
- [ ] Start at 25% position size
- [ ] Have emergency stop procedure ready

---

## Scanner Criteria

For a stock to be scanned:
- Price: $2.00 – $50.00
- Relative volume: > 2x daily average
- Avg daily volume: > 1,000,000 shares
- Bid/ask spread: < 0.5%
- Avoid: Halted stocks, penny stocks < $2

---

## Position Sizing Formula

```
Risk Per Share = Entry Price − Stop Price
Shares = Max Dollar Risk ($25) ÷ Risk Per Share
Max Shares also capped by buying power (2x equity)
```

Example:
- Entry: $15.50
- Stop: $15.00
- Risk per share: $0.50
- Shares = $25 ÷ $0.50 = **50 shares**
- Position value: $775

---

## Trade Management

1. Entry → bracket order placed (entry + stop + target)
2. At +1R: stop moved to breakeven
3. At +1.5R: consider partial exit (50%)
4. At +2R: full target exit
5. 3:45 PM: force-close all open positions (no overnight)
6. Stop NEVER widened
7. No adds to losing trades

---

## Log Files

Located in `logs/` directory:

| File | Content |
|------|---------|
| `trades.csv` | All closed trades with full detail |
| `signals.csv` | Every signal generated |
| `rejections.csv` | Rejected signals with reason |
| `performance.json` | Daily performance summary |
| `bot.log` | Full system log |

---

## Important Disclaimers

This software is for **educational purposes**. Day trading involves 
substantial risk of loss. The strategies implemented here are examples 
and do not guarantee profitability. Always test extensively in paper 
trading before risking real capital. Past simulated performance does 
not predict future results.

**Default state: PAPER TRADING ONLY**
Live trading is disabled until manually enabled after sufficient testing.
