#!/usr/bin/env python3
"""
Trading Summary Script

This script reads trades.log and queries Solana RPC to retrieve REAL transaction data
and calculate actual profit/loss. It does NOT rely on the logged values which may not
be 100% accurate.

The script will:
1. Load RPC endpoint and private key from root .env file
2. Read trades/trades.log for token_address and tx_hash pairs
3. Query Solana RPC for actual transaction details (getTransaction)
4. Calculate real SOL spent/received by analyzing pre/post balances
5. Generate a detailed summary of actual trading performance

Usage:
    python scripts/summary.py                    # Analyze all trades
    python scripts/summary.py --limit 10         # Analyze last 10 trades
    python scripts/summary.py --limit 50         # Analyze last 50 trades
    
Environment Variables Required (from .env):
    SOLANA_NODE_RPC_ENDPOINT - Solana RPC endpoint URL
    SOLANA_PRIVATE_KEY - Your wallet's private key (for wallet address extraction)
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import base58
from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature

# Constants
LAMPORTS_PER_SOL = 1_000_000_000
TRADES_LOG_PATH = "trades/trades.log"


@dataclass
class TradeLog:
    """Represents a logged trade entry."""
    timestamp: str
    action: str  # "buy" or "sell"
    platform: str
    token_address: str
    symbol: str
    price: float
    amount: float
    tx_hash: str
    approx_sol_value: float
    configured_spend_sol: float | None = None


@dataclass
class RealTransactionData:
    """Represents real transaction data from Solana RPC."""
    signature: str
    slot: int
    block_time: int | None
    success: bool
    sol_change: float  # Positive for received, negative for spent
    fee: float
    pre_balance: float
    post_balance: float


@dataclass
class TradePair:
    """Represents a buy-sell pair of trades."""
    token_address: str
    symbol: str
    buy_log: TradeLog
    sell_log: TradeLog
    buy_real_tx: RealTransactionData | None
    sell_real_tx: RealTransactionData | None


class TradingSummary:
    """Analyzes trading logs and generates comprehensive summaries."""
    
    def __init__(self, rpc_endpoint: str, wallet_pubkey: Pubkey):
        self.rpc_endpoint = rpc_endpoint
        self.wallet_pubkey = wallet_pubkey
        self.client: AsyncClient | None = None
        
    async def __aenter__(self):
        self.client = AsyncClient(self.rpc_endpoint)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.close()
            
    def load_trades_log(self, log_path: str, limit: int | None = None) -> list[TradeLog]:
        """Load and parse the trades.log file.
        
        Args:
            log_path: Path to the trades log file
            limit: If specified, only return the last `limit` trades
            
        Returns:
            List of TradeLog objects (or last `limit` if limit is specified)
        """
        trades = []
        log_file = Path(log_path)
        
        if not log_file.exists():
            print(f"❌ Trades log not found: {log_path}")
            return trades
            
        with open(log_file) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                    
                try:
                    data = json.loads(line)
                    trade = TradeLog(
                        timestamp=data["timestamp"],
                        action=data["action"],
                        platform=data["platform"],
                        token_address=data["token_address"],
                        symbol=data["symbol"],
                        price=data["price"],
                        amount=data["amount"],
                        tx_hash=data["tx_hash"],
                        approx_sol_value=data["approx_sol_value"],
                        configured_spend_sol=data.get("configured_spend_sol")
                    )
                    trades.append(trade)
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"⚠️  Warning: Failed to parse line {line_num}: {e}")
                    continue
        
        # Return only the last `limit` trades if specified
        if limit is not None and limit > 0:
            trades = trades[-limit:]
                    
        return trades
        
    async def get_real_transaction_data(self, signature: str) -> RealTransactionData | None:
        """Query Solana RPC for real transaction data."""
        if not self.client:
            raise RuntimeError("Client not initialized")
            
        try:
            # Convert string signature to Signature object
            sig = Signature.from_string(signature)
            
            # Get transaction with maxSupportedTransactionVersion to handle v0 transactions
            response = await self.client.get_transaction(
                sig,
                encoding="jsonParsed",
                max_supported_transaction_version=0
            )
            
            if not response.value:
                print(f"⚠️  Transaction not found: {signature}")
                return None
                
            tx = response.value
            meta = tx.transaction.meta
            
            if not meta:
                print(f"⚠️  No metadata for transaction: {signature}")
                return None
                
            # Check if transaction succeeded
            success = meta.err is None
            
            # Get fee
            fee = meta.fee / LAMPORTS_PER_SOL if meta.fee else 0
            
            # Find wallet's balance change
            # The wallet should be in accountKeys
            account_keys = tx.transaction.transaction.message.account_keys
            wallet_index = None
            
            for idx, key in enumerate(account_keys):
                if str(key.pubkey) == str(self.wallet_pubkey):
                    wallet_index = idx
                    break
                    
            if wallet_index is None:
                print(f"⚠️  Wallet not found in transaction: {signature}")
                return None
                
            # Get pre and post balances
            pre_balances = meta.pre_balances
            post_balances = meta.post_balances
            
            if wallet_index >= len(pre_balances) or wallet_index >= len(post_balances):
                print(f"⚠️  Balance index out of range: {signature}")
                return None
                
            pre_balance = pre_balances[wallet_index] / LAMPORTS_PER_SOL
            post_balance = post_balances[wallet_index] / LAMPORTS_PER_SOL
            
            # Calculate net change (negative = spent, positive = received)
            sol_change = post_balance - pre_balance
            
            return RealTransactionData(
                signature=signature,
                slot=tx.slot,
                block_time=tx.block_time,
                success=success,
                sol_change=sol_change,
                fee=fee,
                pre_balance=pre_balance,
                post_balance=post_balance
            )
            
        except Exception as e:
            print(f"❌ Error fetching transaction {signature}: {e}")
            return None
            
    def pair_trades(self, trades: list[TradeLog]) -> list[TradePair]:
        """Pair buy and sell trades for the same token."""
        pairs = []
        buys = {t.token_address: t for t in trades if t.action == "buy"}
        sells = {t.token_address: t for t in trades if t.action == "sell"}
        
        for token_addr in buys:
            if token_addr in sells:
                pairs.append(TradePair(
                    token_address=token_addr,
                    symbol=buys[token_addr].symbol,
                    buy_log=buys[token_addr],
                    sell_log=sells[token_addr],
                    buy_real_tx=None,
                    sell_real_tx=None
                ))
                
        return pairs
        
    async def enrich_pairs_with_real_data(self, pairs: list[TradePair]) -> list[TradePair]:
        """Fetch real transaction data for all trade pairs."""
        print(f"\n📡 Fetching real transaction data for {len(pairs)} trade pairs...")
        
        for i, pair in enumerate(pairs, 1):
            print(f"  [{i}/{len(pairs)}] Processing {pair.symbol} ({pair.token_address[:8]}...)")
            
            # Fetch buy transaction
            pair.buy_real_tx = await self.get_real_transaction_data(pair.buy_log.tx_hash)
            await asyncio.sleep(0.1)  # Rate limiting
            
            # Fetch sell transaction
            pair.sell_real_tx = await self.get_real_transaction_data(pair.sell_log.tx_hash)
            await asyncio.sleep(0.1)  # Rate limiting
            
        return pairs
        
    def generate_summary(self, pairs: list[TradePair]) -> str:
        """Generate a detailed trading summary."""
        lines = []
        lines.append("=" * 80)
        lines.append("TRADING SUMMARY - REAL DATA FROM SOLANA RPC")
        lines.append("=" * 80)
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Wallet: {self.wallet_pubkey}")
        lines.append(f"Total Trade Pairs: {len(pairs)}")
        lines.append("")
        
        # Statistics
        total_real_profit = 0.0
        total_logged_profit = 0.0
        successful_pairs = 0
        failed_trades = 0
        
        for pair in pairs:
            if pair.buy_real_tx and pair.sell_real_tx:
                if pair.buy_real_tx.success and pair.sell_real_tx.success:
                    # Real profit = SOL received from sell + SOL spent on buy (negative)
                    real_profit = pair.sell_real_tx.sol_change + pair.buy_real_tx.sol_change
                    total_real_profit += real_profit
                    
                    # Logged profit (approximate)
                    logged_profit = pair.sell_log.approx_sol_value - pair.buy_log.approx_sol_value
                    total_logged_profit += logged_profit
                    
                    successful_pairs += 1
                else:
                    failed_trades += 1
            else:
                failed_trades += 1
                
        lines.append("📊 OVERALL STATISTICS")
        lines.append("-" * 80)
        lines.append(f"Successful Trade Pairs: {successful_pairs}/{len(pairs)}")
        lines.append(f"Failed/Incomplete Trades: {failed_trades}")
        lines.append(f"")
        lines.append(f"💰 REAL P&L (from RPC):     {total_real_profit:+.6f} SOL")
        lines.append(f"📝 Logged P&L (approx):     {total_logged_profit:+.6f} SOL")
        lines.append(f"📏 Difference:               {abs(total_real_profit - total_logged_profit):.6f} SOL")
        lines.append("")
        
        # Detailed breakdown per trade pair
        lines.append("=" * 80)
        lines.append("DETAILED TRADE BREAKDOWN")
        lines.append("=" * 80)
        lines.append("")
        
        for i, pair in enumerate(pairs, 1):
            lines.append(f"[{i}] {pair.symbol} - {pair.token_address}")
            lines.append("-" * 80)
            
            # Buy transaction
            lines.append(f"  🟢 BUY:")
            lines.append(f"     Timestamp:    {pair.buy_log.timestamp}")
            lines.append(f"     TX Hash:      {pair.buy_log.tx_hash}")
            
            if pair.buy_real_tx:
                if pair.buy_real_tx.success:
                    lines.append(f"     Status:       ✅ SUCCESS")
                else:
                    lines.append(f"     Status:       ❌ FAILED")
                    
                lines.append(f"     Real SOL Change:  {pair.buy_real_tx.sol_change:.6f} SOL")
                lines.append(f"     Fee:              {pair.buy_real_tx.fee:.6f} SOL")
                lines.append(f"     Pre Balance:      {pair.buy_real_tx.pre_balance:.6f} SOL")
                lines.append(f"     Post Balance:     {pair.buy_real_tx.post_balance:.6f} SOL")
                lines.append(f"     Logged (approx):  {pair.buy_log.approx_sol_value:.6f} SOL")
            else:
                lines.append(f"     Status:       ⚠️  NO RPC DATA")
                lines.append(f"     Logged (approx):  {pair.buy_log.approx_sol_value:.6f} SOL")
                
            lines.append("")
            
            # Sell transaction
            lines.append(f"  🔴 SELL:")
            lines.append(f"     Timestamp:    {pair.sell_log.timestamp}")
            lines.append(f"     TX Hash:      {pair.sell_log.tx_hash}")
            
            if pair.sell_real_tx:
                if pair.sell_real_tx.success:
                    lines.append(f"     Status:       ✅ SUCCESS")
                else:
                    lines.append(f"     Status:       ❌ FAILED")
                    
                lines.append(f"     Real SOL Change:  {pair.sell_real_tx.sol_change:.6f} SOL")
                lines.append(f"     Fee:              {pair.sell_real_tx.fee:.6f} SOL")
                lines.append(f"     Pre Balance:      {pair.sell_real_tx.pre_balance:.6f} SOL")
                lines.append(f"     Post Balance:     {pair.sell_real_tx.post_balance:.6f} SOL")
                lines.append(f"     Logged (approx):  {pair.sell_log.approx_sol_value:.6f} SOL")
            else:
                lines.append(f"     Status:       ⚠️  NO RPC DATA")
                lines.append(f"     Logged (approx):  {pair.sell_log.approx_sol_value:.6f} SOL")
                
            lines.append("")
            
            # Calculate profit for this pair
            if pair.buy_real_tx and pair.sell_real_tx and \
               pair.buy_real_tx.success and pair.sell_real_tx.success:
                real_profit = pair.sell_real_tx.sol_change + pair.buy_real_tx.sol_change
                logged_profit = pair.sell_log.approx_sol_value - pair.buy_log.approx_sol_value
                
                lines.append(f"  💎 PROFIT:")
                lines.append(f"     Real P&L:         {real_profit:+.6f} SOL")
                lines.append(f"     Logged P&L:       {logged_profit:+.6f} SOL")
                lines.append(f"     Difference:       {abs(real_profit - logged_profit):.6f} SOL")
                
                if real_profit > 0:
                    lines.append(f"     Result:           ✅ PROFIT")
                elif real_profit < 0:
                    lines.append(f"     Result:           ❌ LOSS")
                else:
                    lines.append(f"     Result:           ⚖️  BREAK EVEN")
            else:
                lines.append(f"  💎 PROFIT:        ⚠️  Cannot calculate (incomplete data)")
                
            lines.append("")
            lines.append("")
            
        lines.append("=" * 80)
        lines.append("END OF SUMMARY")
        lines.append("=" * 80)
        
        return "\n".join(lines)


async def main():
    """Main entry point for the summary script."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Generate trading summary from trades log and Solana RPC data"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit analysis to the last N trades (default: analyze all trades)"
    )
    args = parser.parse_args()
    
    print("🚀 Trading Summary Generator")
    print("=" * 80)
    
    if args.limit:
        print(f"📊 Mode: Last {args.limit} trades")
    else:
        print(f"📊 Mode: All trades")
    print("")
    
    # Load environment variables from root .env
    root_dir = Path(__file__).parent.parent
    env_path = root_dir / ".env"
    
    if not env_path.exists():
        print(f"❌ Error: .env file not found at {env_path}")
        print("   Please create a .env file with SOLANA_NODE_RPC_ENDPOINT and SOLANA_PRIVATE_KEY")
        sys.exit(1)
        
    load_dotenv(env_path)
    
    # Get required environment variables
    rpc_endpoint = os.getenv("SOLANA_NODE_RPC_ENDPOINT")
    private_key_str = os.getenv("SOLANA_PRIVATE_KEY")
    
    if not rpc_endpoint:
        print("❌ Error: SOLANA_NODE_RPC_ENDPOINT not found in .env")
        sys.exit(1)
        
    if not private_key_str:
        print("❌ Error: SOLANA_PRIVATE_KEY not found in .env")
        sys.exit(1)
        
    # Load wallet keypair to get public key
    try:
        private_key_bytes = base58.b58decode(private_key_str)
        keypair = Keypair.from_bytes(private_key_bytes)
        wallet_pubkey = keypair.pubkey()
    except Exception as e:
        print(f"❌ Error loading wallet keypair: {e}")
        sys.exit(1)
        
    print(f"✅ Loaded configuration from {env_path}")
    print(f"   RPC Endpoint: {rpc_endpoint}")
    print(f"   Wallet: {wallet_pubkey}")
    print("")
    
    # Load trades log
    trades_log_path = root_dir / TRADES_LOG_PATH
    
    if not trades_log_path.exists():
        print(f"❌ Error: Trades log not found at {trades_log_path}")
        sys.exit(1)
        
    # Initialize summary analyzer
    async with TradingSummary(str(rpc_endpoint), wallet_pubkey) as summary:
        # Load trades
        print(f"📖 Loading trades from {trades_log_path}...")
        trades = summary.load_trades_log(str(trades_log_path), limit=args.limit)
        print(f"   Found {len(trades)} trades to analyze")
        
        buy_count = sum(1 for t in trades if t.action == "buy")
        sell_count = sum(1 for t in trades if t.action == "sell")
        print(f"   Buys: {buy_count}, Sells: {sell_count}")
        print("")
        
        # Pair trades
        print("🔗 Pairing buy/sell transactions...")
        pairs = summary.pair_trades(trades)
        print(f"   Found {len(pairs)} complete trade pairs")
        print("")
        
        if not pairs:
            print("⚠️  No complete trade pairs found. Nothing to analyze.")
            sys.exit(0)
            
        # Fetch real data from Solana RPC
        await summary.enrich_pairs_with_real_data(pairs)
        print("✅ Real transaction data fetched")
        print("")
        
        # Generate summary
        summary_text = summary.generate_summary(pairs)
        
        # Print to console
        print(summary_text)
        
        # Save to file
        output_path = root_dir / "trades" / "summary.txt"
        output_path.parent.mkdir(exist_ok=True)
        
        with open(output_path, "w") as f:
            f.write(summary_text)
            
        print(f"\n💾 Summary saved to: {output_path}")
        

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
