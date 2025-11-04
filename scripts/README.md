# Scripts Directory

This directory contains standalone utility scripts for the trading bot project.

## 📊 summary.py

**Purpose:** Generate a detailed trading summary with real profit/loss data from Solana RPC.

### Overview

The `summary.py` script analyzes your trading history by:

1. **Reading trades/trades.log** - Loads logged trade entries (buy/sell actions)
2. **Querying Solana RPC** - Fetches REAL transaction data using the logged tx_hash values
3. **Calculating Real P&L** - Computes actual profit/loss from on-chain balance changes
4. **Generating Reports** - Creates a comprehensive summary comparing logged vs actual values

### Why This Script?

The values logged in `trades/trades.log` (price, amount, approx_sol_value) are approximations based on pre-transaction calculations. They may not reflect the actual SOL spent or received due to:

- Slippage during execution
- Price movements between calculation and execution
- Bonding curve state changes
- Transaction fees
- Rounding differences

This script retrieves **actual on-chain data** to show your true trading performance.

### How It Works

1. **Loads Environment Variables**
   - Reads `.env` from project root
   - Extracts `SOLANA_NODE_RPC_ENDPOINT` and `SOLANA_PRIVATE_KEY`
   - Derives wallet public key for transaction analysis

2. **Parses Trades Log**
   - Reads `trades/trades.log` line by line
   - Extracts token_address and tx_hash for each buy/sell
   - Pairs buy and sell transactions for the same token

3. **Queries Solana RPC**
   - Calls `getTransaction` for each tx_hash
   - Retrieves pre/post SOL balances for your wallet
   - Calculates actual SOL change (negative for buy, positive for sell)
   - Includes transaction fees in calculations

4. **Generates Summary**
   - Shows overall statistics (total real P&L, logged P&L, difference)
   - Provides detailed breakdown for each trade pair
   - Compares logged values vs actual on-chain values
   - Identifies profitable vs losing trades
   - Saves summary to `trades/summary.txt`

### Usage

```bash
# From project root
python scripts/summary.py

# Or with uv
uv run scripts/summary.py
```

### Requirements

**Environment Variables (in `.env`):**
- `SOLANA_NODE_RPC_ENDPOINT` - Your Solana RPC endpoint
- `SOLANA_PRIVATE_KEY` - Your wallet's private key (base58 encoded)

**Files:**
- `trades/trades.log` must exist with trade entries

### Output

The script generates two outputs:

1. **Console Output** - Real-time progress and final summary printed to terminal
2. **File Output** - Saved to `trades/summary.txt` for later reference

### Example Output

```
================================================================================
TRADING SUMMARY - REAL DATA FROM SOLANA RPC
================================================================================
Generated: 2025-11-04 12:00:00
Wallet: 7caCs237AKNa8A8saUGgBqsNT8QrfxdnM4xC15ayBZiH
Total Trade Pairs: 2

📊 OVERALL STATISTICS
--------------------------------------------------------------------------------
Successful Trade Pairs: 2/2
Failed/Incomplete Trades: 0

💰 REAL P&L (from RPC):     -0.020242 SOL
📝 Logged P&L (approx):     -0.020366 SOL
📏 Difference:               0.000124 SOL

================================================================================
DETAILED TRADE BREAKDOWN
================================================================================

[1] BRHOS - EQi2zrbPxzZJnSNvxsqavSWnQCUXNCwifPj6ygKupump
--------------------------------------------------------------------------------
  🟢 BUY:
     Timestamp:    2025-11-03T20:20:41.737035
     TX Hash:      5bXyRNbWbz3WUgR9ar7wp5mPZHAwnHAZyo27sTtYChHu4pb24VgQ7d6i86jNcGHr2aBcacPmvLziD3a9UAmibMYS
     Status:       ✅ SUCCESS
     Real SOL Change:  -0.070005 SOL
     Fee:              0.000005 SOL
     Pre Balance:      1.234567 SOL
     Post Balance:     1.164562 SOL
     Logged (approx):  0.070000 SOL

  🔴 SELL:
     Timestamp:    2025-11-03T20:20:54.135075
     TX Hash:      3avvAePBu8aobPZZ5m2sugbCCr7WuedS9pEuz7Ak6RARWwffS1bDFya9i8WUBLwddERkQYUoHf5qtJuMdNKYYjgA
     Status:       ✅ SUCCESS
     Real SOL Change:  +0.049635 SOL
     Fee:              0.000005 SOL
     Pre Balance:      1.164562 SOL
     Post Balance:     1.214197 SOL
     Logged (approx):  0.049634 SOL

  💎 PROFIT:
     Real P&L:         -0.020370 SOL
     Logged P&L:       -0.020366 SOL
     Difference:       0.000004 SOL
     Result:           ❌ LOSS
```

### Key Metrics Explained

- **Real SOL Change**: Actual SOL balance change from the transaction (includes fees)
- **Fee**: Transaction fee paid to Solana network
- **Pre/Post Balance**: Your wallet's SOL balance before/after the transaction
- **Logged (approx)**: The value recorded in trades.log (pre-transaction estimate)
- **Real P&L**: Calculated from actual on-chain balance changes (sell_change + buy_change)
- **Logged P&L**: Calculated from logged approximate values
- **Difference**: Shows how much logged values deviated from reality

### Important Notes

1. **Rate Limiting**: The script includes 0.1s delays between RPC calls to avoid overwhelming the endpoint
2. **Transaction Versions**: Supports both legacy and v0 transactions (maxSupportedTransactionVersion=0)
3. **Failed Transactions**: Identifies and reports failed transactions separately
4. **Incomplete Pairs**: Reports trades that don't have matching buy/sell pairs
5. **Accuracy**: Real P&L is always more accurate than logged values

### Troubleshooting

**"Transaction not found" errors:**
- RPC endpoint may not have historical data
- Transaction might be too old (try a different RPC provider)
- tx_hash might be incorrect

**"Wallet not found in transaction" errors:**
- The transaction doesn't involve your wallet
- Possible data corruption in trades.log

**RPC timeouts:**
- Your RPC endpoint may be slow or rate-limited
- Consider using a premium RPC provider (Helius, QuickNode, etc.)

### Integration with Bot

This is a **standalone** script that doesn't interfere with bot operation:
- ✅ Can run while bot is running
- ✅ Read-only operations (no state modification)
- ✅ Safe to run multiple times
- ✅ No dependencies on bot runtime

### Future Enhancements

Possible improvements:
- Export to CSV/JSON formats
- Tax reporting integration
- Performance metrics (win rate, average P&L, etc.)
- Token-specific statistics
- Time-based filtering (daily/weekly summaries)
- Price charts and visualization

---

## Adding More Scripts

When adding new scripts to this directory:

1. **Follow project rules** (see RULES.md)
2. **Write standalone** - Should work independently
3. **Document thoroughly** - Add section to this README
4. **Load .env properly** - Use dotenv from project root
5. **Handle errors gracefully** - Don't crash on missing data
6. **Respect write permissions** - Only write to scripts/ or designated output folders

---

**Last Updated:** 2025-11-04
