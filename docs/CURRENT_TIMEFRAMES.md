# Current Hardcoded Timeframes in Project

This document lists all hardcoded timeframes used throughout the trading bot system.

## Token Detection & Pre-Buy

- **wait_before_buy**: 5s (check liquidity after token detected)
- **max_token_age**: 12s (accept tokens up to 12s old)
- **Liquidity retry backoff**: 0.2s, 0.4s, 0.6s (3 attempts)
- **wait_after_creation**: 0s (disabled in aggressive mode)

## Price Monitoring

- **price_check_interval**: 1s (check price every second)
- **max_hold_time**: 45s (force exit after 45 seconds)
- **wait_after_buy**: 5s (delay before starting price checks)
- **min_hold_before_stop_seconds**: 3s (warm-up period before SL/TP)
- **stop_loss_confirmations**: 2 checks needed
- **take_profit_confirmations**: 2 checks needed

## Between Trades

- **wait_before_new_token**: 5s (pause between consecutive trades in YOLO mode)

## WebSocket & Listeners

- **Reconnect delay**: 5s (all listeners)
- **Ping interval**: ~30s (WebSocket keepalive, varies by listener)
- **Ping timeout**: 10s

## Cleanup

- **RPC sync wait**: 15s (before closing token accounts)

## Max Hold Retry

- **Retry backoff**: 2s + 0.5s per attempt (up to 3 attempts)

---

**Note**: These values are based on the `bot-ultra-sniper-aggressive-optimized.yaml` configuration and core implementation in `src/trading/universal_trader.py`.
