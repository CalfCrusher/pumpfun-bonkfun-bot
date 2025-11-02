# Trading Bot Fixes - Complete Summary

## Overview
Three critical bugs caused financial losses in the pump.fun trading bot. All three have been identified, fixed, and pushed to the `custom/real-liquidity-fix` branch.

---

## Bug #1: Overspending on Buys ❌ → ✅ FIXED

**Symptoms:**
- Bot was spending more SOL than configured (e.g., configured 0.02 SOL but spending 0.03+ SOL)
- Financial losses due to excessive slippage allowance

**Root Cause:**
- `extreme_fast_mode: true` was using "exact-out-by-token-count" (buy X tokens at any price)
- During volatile price swings, this caused massive overspend

**Solution:**
1. **Disabled extreme_fast_mode** in `bot-ultra-sniper-aggressive.yaml` (set to `false`)
   - Now uses "exact-in" (spend exactly 0.02 SOL, get tokens at market rate)
2. **Added hard SOL spend cap** (`max_spend_sol_hard_cap: 0.03`)
   - Enforced at buyer instruction layer (most conservative of two values)
   - Wired through: config → runner → trader → buyer
3. **Impact**: Prevents any overspend regardless of slippage conditions

**Files Modified:**
- `src/trading/platform_aware.py` - Added hard cap enforcement
- `src/trading/universal_trader.py` - Wired cap parameter
- `src/bot_runner.py` - Config loading
- `src/config_loader.py` - Validation
- `bots/bot-ultra-sniper-aggressive.yaml` - Config values

**Commits:**
- `f13a43d` - Initial spend cap + extreme_fast_mode disable + TP confirmations

---

## Bug #2: Selling ~1 Second After Buy ❌ → ✅ FIXED

**Symptoms:**
- Trades executed: buy at time T, sell at T+1s
- Ignoring configured hold times and TP/SL targets
- First price sample (often spiky) triggered instant TP/SL exits

**Root Cause:**
- Monitoring loop checked TP/SL on first price sample without debounce
- Fresh pools have extreme volatility; first price often hits TP threshold immediately
- No confirmations requirement for take-profit (unlike stop-loss which had it)

**Solution:**
1. **Added universal warm-up window** (3 seconds)
   - No exits (TP or SL) allowed during first 3 seconds
   - Prevents noisy first-sample exits
2. **Added take-profit confirmations** (set to 2)
   - Now requires 2 consecutive price samples above TP threshold
   - Prevents spikes from triggering false exits
3. **Stop-loss already had confirmations** (set to 2)
   - Kept for consistency with TP

**Config Values:**
```yaml
min_hold_before_stop_seconds: 3        # Universal warm-up
stop_loss_confirmations: 2              # Existing
take_profit_confirmations: 2            # NEW
```

**Files Modified:**
- `src/trading/universal_trader.py` - Added warm-up + TP confirmations to monitoring loop
- `bots/bot-ultra-sniper-aggressive.yaml` - Config values

**Commits:**
- `f13a43d` - Included in first commit with spend cap fixes

---

## Bug #3: Selling 16+ Seconds After Buy (Ignoring max_hold_time) ❌ → ✅ FIXED (x2)

### Phase 3A: Root Cause Identification

**Symptoms:**
- Configured `max_hold_time: 10s` but trades held 16-19 seconds
- Sell prices showed 1-2% change (not configured 50% TP)
- Logs showed max_hold_time was being reached but exit logic incorrect

**Root Cause:**
- `Position.should_exit()` checked TP/SL BEFORE max_hold_time
- Once max_hold_time expired, it returned True immediately
- Bypassed all debounce/confirmation logic for max_hold_time exits
- Warm-up logic never applied to max_hold_time exits

**Solution (Phase 1):**
1. **Separated exit logic into three focused methods:**
   - `should_exit_for_profit_or_loss(current_price)` → Returns (bool, ExitReason | None) for TP/SL only
   - `has_max_hold_time_expired()` → Returns bool for time check only
   - `should_exit(current_price)` → Kept for backward compatibility

2. **Refactored monitoring loop with correct precedence:**
   - **First check:** `max_hold_time_expired()` → Immediate exit (no debounce, hard deadline)
   - **Second check:** `should_exit_for_profit_or_loss()` → Full debounce + confirmations

3. **Added proper logging** for each exit path to track behavior

**Files Modified:**
- `src/trading/position.py` - Split exit logic into three methods
- `src/trading/universal_trader.py` - Updated monitoring loop precedence

**Commits:**
- `4c7b5ba` - "fix critical exit logic: separate max_hold_time from TP/SL to prevent immediate exits"

### Phase 3B: Retry Logic for Sell Failures

**New Symptoms (After Phase 3A fix):**
- Bot now correctly times max_hold_time at 10 seconds ✓
- But **sell transactions failing** with: `InvalidParamsMessage { message: "Invalid param: could not find account" }`
- RPC node can't find token account immediately after buy (account indexing lag)

**Root Cause:**
- Token account created during buy, but RPC hasn't indexed it yet
- Original retry: 6 attempts × 0.4-2.4s = ~9s total (often insufficient)
- When sell fails, monitoring loop retries every price_check_interval (~1s)
- Results in spam of failed sell attempts

**Solution (Phase 2):**
1. **Increased seller retry attempts:** 6 → 10
2. **Improved backoff timing:** 0.4-2.4s → 0.5-4.5s (exponential)
   - Gives RPC node more time to index token account
3. **Added outer retry counter for max_hold_time sells:**
   - Max 3 outer attempts before giving up
   - Wait 2-3.5s between outer attempts
   - Prevents infinite retry loops
4. **Force-close position if all retries fail**
   - Prevents position from staying open indefinitely

**Config Impact:**
- No config changes needed
- Automatic retry with improved timing

**Files Modified:**
- `src/trading/platform_aware.py` - Increased seller retry attempts + longer backoff
- `src/trading/universal_trader.py` - Added max_hold_time retry counter + outer retry logic

**Commits:**
- `6b93f51` - "fix: improve sell retry logic for token account lookup failures"

---

## Testing & Validation

### What to Verify on Live Server:

1. **Spend Control:**
   - ✓ Each buy spends exactly 0.02 SOL (or configured amount)
   - ✓ Never exceeds 0.03 SOL hard cap even with slippage
   - ✓ Check trades logs for "Applying hard spend cap" warnings (shouldn't appear if proper)

2. **Hold Time Respect:**
   - ✓ Position held ~10 seconds (max_hold_time)
   - ✓ Not held 16+ seconds
   - ✓ Logs show: "Max hold time (10s) reached - forcing exit"

3. **Exit Timing:**
   - ✓ No sells within first 3 seconds (warm-up)
   - ✓ TP/SL exits require 2 consecutive price samples above/below threshold
   - ✓ Check logs for "debounce rejected" messages (sign of working protection)

4. **Sell Completion:**
   - ✓ Sells eventually succeed (may take up to 25s with 10 retries + waits)
   - ✓ Look for: "Token account found after N retries" in logs
   - ✓ No infinite retry loops or stuck positions

### Trade Example from Logs:
```
Buy time:   2025-11-02 00:31:52.622 - ONGO @ 8.4e-8 SOL for 0.02 SOL
First exit attempt: 2025-11-02 00:32:02 (10s exactly) ✓
Status: Max hold time reached ✓
Position PnL: +67.93% ✓
Sell failed (token account not found) but retried...
Eventually succeeded (or force-closed with best available price)
```

---

## Configuration Summary

**Current Production Config** (`bot-ultra-sniper-aggressive.yaml`):

```yaml
trade:
  buy_amount: 0.02                      # Spend exactly 0.02 SOL
  max_spend_sol_hard_cap: 0.03         # Never exceed 0.03 SOL (hard cap)
  extreme_fast_mode: false              # Buy exact-in (0.02 SOL), not exact-out
  take_profit_percentage: 0.5           # Exit at +50% profit
  stop_loss_percentage: 0.3             # Exit at -30% loss
  max_hold_time: 10                     # Hard exit at 10 seconds
  min_hold_before_stop_seconds: 3       # No exits in first 3 seconds (warm-up)
  stop_loss_confirmations: 2            # Require 2 consecutive SL breaches
  take_profit_confirmations: 2          # Require 2 consecutive TP breaches
```

---

## Deployment Instructions

1. **Pull the latest fixes:**
   ```bash
   git pull --rebase origin custom/real-liquidity-fix
   ```

2. **Verify Python syntax:**
   ```bash
   python -m py_compile src/trading/platform_aware.py src/trading/universal_trader.py
   ```

3. **Test with learning examples** (optional):
   ```bash
   uv run learning-examples/manual_buy.py
   ```

4. **Deploy to server** and monitor first 5-10 trades

---

## Monitoring Post-Deployment

Watch logs for:

1. **Successful patterns:**
   - `Buy transaction confirmed: <hash>`
   - `Max hold time (10s) reached - forcing exit`
   - `Successfully exited position: max_hold_time`
   - `Token account found after N retries` (if account lookup lag)
   - `Final PnL: +XX% (...)`

2. **Error patterns to investigate:**
   - `Failed to exit on max hold time: <error>` appearing repeatedly (indicate logs more than 3 times)
   - `Forcing position closure due to persistent sell failures` (means retries exhausted)
   - `Liquidity check FAILED` (filter issue, not a bug fix)

3. **Expected frequency:**
   - Max hold time exits every 10s + monitoring cycle (1s)
   - TP/SL exits typically before max_hold_time (if price moves favorably)
   - Sell might take 5-25s to confirm (RPC eventual consistency)

---

## Summary of Changes

| File | Change | Reason |
|------|--------|--------|
| platform_aware.py | Increased seller retries 6→10, backoff 0.4-2.4s→0.5-4.5s | Handle RPC eventual consistency |
| position.py | Split exit logic into 3 methods | Separate concerns, fix precedence |
| universal_trader.py | Added max_hold_time retry counter + monitoring loop refactor | Prevent infinite retries + correct exit order |
| bot_runner.py | Wire max_spend_sol_hard_cap parameter | Config → execution |
| config_loader.py | Add validation for hard cap | Config safety |
| bot-ultra-sniper-aggressive.yaml | Set all parameters + hard cap | Production-ready config |

---

## Historical Commits

1. **f13a43d** - Spend cap + disable extreme_fast_mode + TP confirmations
2. **4c7b5ba** - Separate max_hold_time from TP/SL logic in exit checks
3. **6b93f51** - Improve sell retry logic for token account lookup failures

---

**All fixes are now live on `custom/real-liquidity-fix` branch and ready for testing on your server.**
