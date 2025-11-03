"""
Local smoke test for trade logging.

This script avoids any network calls by directly invoking the internal
logging method used by the trading coordinator with dummy data.

It verifies that trades/trades.log is created and appended with entries
for both a buy and a sell.
"""

import sys
from pathlib import Path

# Add src to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from types import SimpleNamespace
import json

from interfaces.core import Platform
from trading.universal_trader import UniversalTrader


class DummyMint:
    def __init__(self, s: str):
        self._s = s

    def __str__(self) -> str:
        return self._s


def main():
    # Create a minimal token-like object (only fields used by _log_trade)
    token_info = SimpleNamespace(
        platform=Platform.PUMP_FUN,
        mint=DummyMint("6M6zJAJt2YxW9cXU6bq2rZz8Cz8mG1S8dXc8Lh3QaD8Q"),
        symbol="TESTCOIN",
    )

    # Create a minimal dummy trader instance stub to pass buy_amount
    class DummyTrader:
        buy_amount = 0.01

    trader = DummyTrader()

    # Invoke the internal logger (unbound method call with dummy instance)
    UniversalTrader._log_trade(
        trader,
        "buy",
        token_info,
        price=0.00000123,
        amount=10000,
        tx_hash="BUY_SIG_DUMMY_111",
    )

    UniversalTrader._log_trade(
        trader,
        "sell",
        token_info,
        price=0.00000150,
        amount=10000,
        tx_hash="SELL_SIG_DUMMY_222",
    )

    log_file = Path("trades") / "trades.log"
    assert log_file.exists(), "trades.log was not created"

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "trades.log is empty"
    last_two = lines[-2:]

    # Basic structure sanity check
    for line in last_two:
        entry = json.loads(line)
        assert "action" in entry and entry["action"] in {"buy", "sell"}
        assert entry.get("platform") == Platform.PUMP_FUN.value
        assert entry.get("symbol") == "TESTCOIN"
        assert "tx_hash" in entry

    print("Smoke test passed. Last two trade log entries:")
    for l in last_two:
        print(l)


if __name__ == "__main__":
    main()
