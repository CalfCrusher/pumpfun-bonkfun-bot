# Custom Modifications to Chainstack Pump.fun Bot

**⚠️ WARNING: This file tracks ALL modifications made to the original Chainstack codebase.**

**Repository:** https://github.com/chainstacklabs/pump-fun-bot  
**Last Sync:** November 1, 2025  
**Branch:** main (commit: b6928d1)

---

## 🎯 Purpose of Modifications

Add market cap filtering to avoid instant rug pulls and improve profitability.

---

## 📝 Modification Log

### [2025-11-01] - Added Market Cap Filtering

**Approach:** Simple wait & check bonding curve liquidity before buying.

**Files Modified:**

#### 1. `src/trading/universal_trader.py`
- **Functions Modified:** `__init__()` constructor, `_handle_token()`
- **Lines Modified:** 
  - Constructor parameters (~85): Added `min_market_cap_sol`, `wait_before_buy`
  - Instance variables (~193): Stored new parameters
  - Market cap check (~407-457): Added filtering logic with **fail-safe behavior** (skip on error)
- **Changes:**
  - Added configurable wait time after token detection
  - Added market cap check using bonding curve virtual SOL reserves
  - Added minimum market cap threshold filter
  - Skip tokens below minimum market cap
  - **CRITICAL**: Skip tokens if market cap check fails (fail-safe mode)

#### 2. `src/bot_runner.py`
- **Function Modified:** `start_bot()`
- **Lines Modified:** ~138-139 (after `yolo_mode`)
- **Changes:**
  - Added parameter extraction from YAML config: `min_market_cap_sol` and `wait_before_buy`
  - Pass parameters to `UniversalTrader` constructor
  - Default values: `min_market_cap_sol=0.0`, `wait_before_buy=0`

#### 3. Bot Configuration Files (YAML)
- **Files:** All bot configs in `bots/` directory
- **New Parameters Added:**
  ```yaml
  filters:
    min_market_cap_sol: 0.5      # Minimum market cap in SOL
    wait_before_buy: 5            # Seconds to wait before buying
  ```

**Rationale:**
- Instant sniping = 95%+ rug pulls
- Waiting 5-10 seconds allows market cap to develop
- Only buy tokens that attract real buyers
- Filters out scam tokens that die immediately

**Trade-offs:**
- ❌ Not first buyer anymore
- ❌ Slower execution (5-10s delay)
- ✅ Better success rate
- ✅ Fewer rug pulls
- ✅ Lower SOL burn rate

---

## 🔄 How to Sync with Upstream

When Chainstack updates their repo:

```bash
# Add upstream remote (one time)
git remote add upstream https://github.com/chainstacklabs/pump-fun-bot.git

# Fetch latest changes
git fetch upstream

# Review changes
git log HEAD..upstream/main

# Merge upstream changes
git merge upstream/main

# Resolve conflicts in modified files:
# - src/trading/universal_trader.py (market cap filtering logic)
# - src/bot_runner.py (config parameter extraction)
# - bots/*.yaml (if new templates added)

# Test after merge
pump_bot
```

---

## 📋 Modified Files Summary

| File | Original | Modified | Status |
|------|----------|----------|--------|
| `src/trading/universal_trader.py` | ✓ | ✓ | **MODIFIED** |
| `src/bot_runner.py` | ✓ | ✓ | **MODIFIED** |
| `bots/bot-ultra-sniper-TEST.yaml` | ✗ | ✓ | **CUSTOM** |
| `bots/bot-ultra-sniper-aggressive.yaml` | ✗ | ✓ | **CUSTOM** |
| `bots/bot-sniper-3-blocks.yaml` | ✓ | ✓ | **DISABLED** |

**Legend:**
- **MODIFIED**: Original file with custom changes
- **CUSTOM**: New file created by us
- **DISABLED**: Original file, disabled only

---

## 🧪 Testing Checklist

After modifications:

- [ ] Bot starts without errors
- [ ] Detects new tokens
- [ ] Waits configured time before buying
- [ ] Checks market cap correctly
- [ ] Skips low market cap tokens
- [ ] Buys tokens above threshold
- [ ] Sells correctly
- [ ] Logs all decisions

---

## 🛠️ Rollback Instructions

To revert to original Chainstack code:

```bash
# Revert modified files
git checkout b6928d1 src/trading/universal_trader.py

# Remove custom configs (keep backups)
mv bots/bot-ultra-sniper-TEST.yaml bots/BACKUP/
mv bots/bot-ultra-sniper-aggressive.yaml bots/BACKUP/

# Re-enable original bot
# Edit bots/bot-sniper-2-logs.yaml: enabled: true
```

---

## 📊 Performance Tracking

Track modification effectiveness:

| Metric | Before Mod | After Mod | Change |
|--------|------------|-----------|--------|
| Total Trades | TBD | TBD | TBD |
| Successful Trades | TBD | TBD | TBD |
| Rug Pulls Avoided | TBD | TBD | TBD |
| Win Rate | TBD | TBD | TBD |
| Net Profit/Loss | TBD | TBD | TBD |

---

**Last Updated:** 2025-11-01 19:20 UTC  
**Next Review:** After 100 trades or 1 week
