"""
Universal trading coordinator that works with any platform.
Cleaned up to remove all platform-specific hardcoding.
"""

import asyncio
import signal
import json
from datetime import datetime
from pathlib import Path
from time import monotonic

import uvloop
from solders.pubkey import Pubkey

from cleanup.modes import (
    handle_cleanup_after_failure,
    handle_cleanup_after_sell,
    handle_cleanup_post_session,
)
from core.client import SolanaClient
from core.priority_fee.manager import PriorityFeeManager
from core.wallet import Wallet
from interfaces.core import Platform, TokenInfo
from monitoring.listener_factory import ListenerFactory
from platforms import get_platform_implementations
from trading.base import TradeResult
from trading.platform_aware import PlatformAwareBuyer, PlatformAwareSeller
from trading.position import Position
from utils.logger import get_logger

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

logger = get_logger(__name__)


class UniversalTrader:
    """Universal trading coordinator that works with any supported platform."""

    def __init__(
        self,
        rpc_endpoint: str,
        wss_endpoint: str,
        private_key: str,
        buy_amount: float,
        buy_slippage: float,
        sell_slippage: float,
        # Platform configuration
        platform: Platform | str = Platform.PUMP_FUN,
        # Listener configuration
        listener_type: str = "logs",
        geyser_endpoint: str | None = None,
        geyser_api_token: str | None = None,
        geyser_auth_type: str = "x-token",
        pumpportal_url: str = "wss://pumpportal.fun/api/data",
        # Trading configuration
        extreme_fast_mode: bool = False,
        extreme_fast_token_amount: int = 30,
        # Exit strategy configuration
        exit_strategy: str = "time_based",
        take_profit_percentage: float | None = None,
        stop_loss_percentage: float | None = None,
        max_hold_time: int | None = None,
        price_check_interval: int = 10,
    # Exit strategy safety (debounce) options
    min_hold_before_stop_seconds: int = 2,
    stop_loss_confirmations: int = 2,
        # Priority fee configuration
        enable_dynamic_priority_fee: bool = False,
        enable_fixed_priority_fee: bool = True,
        fixed_priority_fee: int = 200_000,
        extra_priority_fee: float = 0.0,
        hard_cap_prior_fee: int = 200_000,
        # Retry and timeout settings
        max_retries: int = 3,
        wait_time_after_creation: int = 15,
        wait_time_after_buy: int = 15,
        wait_time_before_new_token: int = 15,
        max_token_age: int | float = 0.001,
        token_wait_timeout: int = 30,
        # Cleanup settings
        cleanup_mode: str = "disabled",
        cleanup_force_close_with_burn: bool = False,
        cleanup_with_priority_fee: bool = False,
        # Trading filters
        match_string: str | None = None,
        bro_address: str | None = None,
        marry_mode: bool = False,
        yolo_mode: bool = False,
        # Liquidity filtering
        min_market_cap_sol: float = 0.0,  # deprecated alias, kept for backward compatibility
        min_real_liquidity_sol: float | None = None,
        wait_before_buy: int = 0,
        # Compute unit configuration
        compute_units: dict | None = None,
    ):
        """Initialize the universal trader."""
        # Core components
        self.solana_client = SolanaClient(rpc_endpoint)
        self.wallet = Wallet(private_key)
        self.priority_fee_manager = PriorityFeeManager(
            client=self.solana_client,
            enable_dynamic_fee=enable_dynamic_priority_fee,
            enable_fixed_fee=enable_fixed_priority_fee,
            fixed_fee=fixed_priority_fee,
            extra_fee=extra_priority_fee,
            hard_cap=hard_cap_prior_fee,
        )

        # Platform setup
        if isinstance(platform, str):
            self.platform = Platform(platform)
        else:
            self.platform = platform

        logger.info(f"Initialized Universal Trader for platform: {self.platform.value}")

        # Validate platform support
        try:
            from platforms import platform_factory

            if not platform_factory.registry.is_platform_supported(self.platform):
                raise ValueError(f"Platform {self.platform.value} is not supported")
        except Exception:
            logger.exception("Platform validation failed")
            raise

        # Get platform-specific implementations
        self.platform_implementations = get_platform_implementations(
            self.platform, self.solana_client
        )

        # Store compute unit configuration
        self.compute_units = compute_units or {}

        # Create platform-aware traders
        self.buyer = PlatformAwareBuyer(
            self.solana_client,
            self.wallet,
            self.priority_fee_manager,
            buy_amount,
            buy_slippage,
            max_retries,
            extreme_fast_token_amount,
            extreme_fast_mode,
            compute_units=self.compute_units,
        )

        self.seller = PlatformAwareSeller(
            self.solana_client,
            self.wallet,
            self.priority_fee_manager,
            sell_slippage,
            max_retries,
            compute_units=self.compute_units,
        )

        # Initialize the appropriate listener with platform filtering
        self.token_listener = ListenerFactory.create_listener(
            listener_type=listener_type,
            wss_endpoint=wss_endpoint,
            geyser_endpoint=geyser_endpoint,
            geyser_api_token=geyser_api_token,
            geyser_auth_type=geyser_auth_type,
            pumpportal_url=pumpportal_url,
            platforms=[self.platform],  # Only listen for our platform
        )

        # Trading parameters
        self.buy_amount = buy_amount
        self.buy_slippage = buy_slippage
        self.sell_slippage = sell_slippage
        self.max_retries = max_retries
        self.extreme_fast_mode = extreme_fast_mode
        self.extreme_fast_token_amount = extreme_fast_token_amount

        # Exit strategy parameters
        self.exit_strategy = exit_strategy.lower()
        self.take_profit_percentage = take_profit_percentage
        self.stop_loss_percentage = stop_loss_percentage
        self.max_hold_time = max_hold_time
        self.price_check_interval = price_check_interval
        # Debounce safeguards
        self.min_hold_before_stop_seconds = max(0, min_hold_before_stop_seconds)
        self.stop_loss_confirmations = max(1, stop_loss_confirmations)

        # Timing parameters
        self.wait_time_after_creation = wait_time_after_creation
        self.wait_time_after_buy = wait_time_after_buy
        self.wait_time_before_new_token = wait_time_before_new_token
        self.max_token_age = max_token_age
        self.token_wait_timeout = token_wait_timeout

        # Cleanup parameters
        self.cleanup_mode = cleanup_mode
        self.cleanup_force_close_with_burn = cleanup_force_close_with_burn
        self.cleanup_with_priority_fee = cleanup_with_priority_fee

        # Trading filters/modes
        self.match_string = match_string
        self.bro_address = bro_address
        self.marry_mode = marry_mode
        self.yolo_mode = yolo_mode
        
        # Liquidity filtering configuration
        # Prefer explicit real-liquidity threshold when provided; otherwise fall back to the legacy
        # min_market_cap_sol value for backward compatibility.
        self.min_liquidity_sol = (
            float(min_real_liquidity_sol)
            if (min_real_liquidity_sol is not None and min_real_liquidity_sol > 0)
            else float(min_market_cap_sol)
        )
        self.wait_before_buy = wait_before_buy

        # State tracking
        self.traded_mints: set[Pubkey] = set()
        self.token_queue: asyncio.Queue = asyncio.Queue()
        self.processing: bool = False
        self.processed_tokens: set[str] = set()
        self.token_timestamps: dict[str, float] = {}
        # Shutdown control
        self._shutdown: bool = False
        self._shutdown_event: asyncio.Event = asyncio.Event()

    def _request_shutdown(self) -> None:
        """Signal-safe request to stop operations ASAP."""
        if not self._shutdown:
            logger.info("Shutdown requested. Halting new token acceptance and trades...")
            self._shutdown = True
            try:
                # Wake any waiters (e.g., token waiter) immediately
                self._shutdown_event.set()
            except Exception:
                logger.debug("Failed to set shutdown event", exc_info=True)
        else:
            logger.debug("Shutdown already in progress.")

    async def start(self) -> None:
        """Start the trading bot and listen for new tokens."""
        logger.info(f"Starting Universal Trader for {self.platform.value}")
        logger.info(
            f"Match filter: {self.match_string if self.match_string else 'None'}"
        )
        logger.info(
            f"Creator filter: {self.bro_address if self.bro_address else 'None'}"
        )
        logger.info(f"Marry mode: {self.marry_mode}")
        logger.info(f"YOLO mode: {self.yolo_mode}")
        logger.info(f"Exit strategy: {self.exit_strategy}")

        if self.exit_strategy == "tp_sl":
            logger.info(
                f"Take profit: {self.take_profit_percentage * 100 if self.take_profit_percentage else 'None'}%"
            )
            logger.info(
                f"Stop loss: {self.stop_loss_percentage * 100 if self.stop_loss_percentage else 'None'}%"
            )
            logger.info(
                f"Max hold time: {self.max_hold_time if self.max_hold_time else 'None'} seconds"
            )

        logger.info(f"Max token age: {self.max_token_age} seconds")
        # Visibility for liquidity filtering configuration
        logger.info(
            f"Liquidity filter (real reserves preferred): min {self.min_liquidity_sol} SOL, wait_before_buy: {self.wait_before_buy}s"
        )

        # Setup signal handlers to ensure we don't buy after user interruption
        try:
            loop = asyncio.get_running_loop()
            try:
                loop.add_signal_handler(signal.SIGINT, self._request_shutdown)
                loop.add_signal_handler(signal.SIGTERM, self._request_shutdown)
            except NotImplementedError:
                # Signal handlers may not be available on some platforms
                pass
        except Exception:
            logger.debug("Failed to set up signal handlers", exc_info=True)

        try:
            health_resp = await self.solana_client.get_health()
            logger.info(f"RPC warm-up successful (getHealth passed: {health_resp})")
        except Exception as e:
            logger.warning(f"RPC warm-up failed: {e!s}")

        try:
            # Choose operating mode based on yolo_mode
            if not self.yolo_mode:
                # Single token mode: process one token and exit
                logger.info(
                    "Running in single token mode - will process one token and exit"
                )
                token_info = await self._wait_for_token()
                if self._shutdown:
                    logger.info("Shutdown requested before handling token. Aborting...")
                elif token_info:
                    logger.info(
                        f"Invoking buy pipeline for: {token_info.symbol} ({token_info.mint})"
                    )
                    await self._handle_token(token_info)
                    logger.info("Finished processing single token. Exiting...")
                else:
                    logger.info(
                        f"No suitable token found within timeout period ({self.token_wait_timeout}s). Exiting..."
                    )
            else:
                # Continuous mode: process tokens until interrupted
                logger.info(
                    "Running in continuous mode - will process tokens until interrupted"
                )
                processor_task = asyncio.create_task(self._process_token_queue())

                try:
                    await self.token_listener.listen_for_tokens(
                        lambda token: self._queue_token(token),
                        self.match_string,
                        self.bro_address,
                    )
                except Exception:
                    logger.exception("Token listening stopped due to error")
                finally:
                    processor_task.cancel()
                    try:
                        await processor_task
                    except asyncio.CancelledError:
                        pass

        except Exception:
            logger.exception("Trading stopped due to error")

        finally:
            await self._cleanup_resources()
            logger.info("Universal Trader has shut down")

    async def _wait_for_token(self) -> TokenInfo | None:
        """Wait for a single token to be detected."""
        # Create a one-time event to signal when a token is found
        token_found = asyncio.Event()
        found_token = None

        async def token_callback(token: TokenInfo) -> None:
            nonlocal found_token
            token_key = str(token.mint)

            # If we've already selected a token for this cycle, ignore others
            if found_token is not None or token_found.is_set():
                logger.debug(
                    f"Ignoring {token.symbol} - token already selected for this cycle"
                )
                return

            # Do not accept new tokens when shutting down
            if self._shutdown:
                logger.info(f"Ignoring token {token.symbol} due to shutdown request")
                return

            # Only process if not already processed and fresh
            if token_key not in self.processed_tokens:
                # LIQUIDITY FILTERING - Check BEFORE accepting token
                if self.wait_before_buy > 0 and self.min_liquidity_sol > 0:
                    try:
                        logger.info(f"New token detected: {token.symbol}. Waiting {self.wait_before_buy}s to check liquidity...")
                        await asyncio.sleep(self.wait_before_buy)

                        if self._shutdown:
                            logger.info("Shutdown requested during wait. Skipping token.")
                            self.processed_tokens.add(token_key)
                            return
                        
                        curve_manager = self.platform_implementations.curve_manager
                        address_provider = self.platform_implementations.address_provider

                        # Try multiple candidate addresses for early curve reads
                        candidates: list[Pubkey] = []
                        if getattr(token, "bonding_curve", None):
                            candidates.append(token.bonding_curve)
                        if getattr(token, "associated_bonding_curve", None):
                            candidates.append(token.associated_bonding_curve)
                        # Derive associated bonding curve if not present
                        if getattr(token, "bonding_curve", None) and getattr(token, "mint", None):
                            try:
                                derived = address_provider.derive_associated_bonding_curve(
                                    token.mint, token.bonding_curve
                                )
                                if derived and derived not in candidates:
                                    candidates.append(derived)
                            except Exception:
                                logger.debug("Failed to derive associated bonding curve", exc_info=True)

                        pool_state: dict | None = None
                        used_address: Pubkey | None = None
                        for addr in candidates:
                            try:
                                pool_state = await curve_manager.get_pool_state(addr)
                                used_address = addr
                                break
                            except Exception:
                                continue

                        if pool_state is not None:
                            # Prefer REAL SOL reserves as a proxy for actual on-chain liquidity.
                            # Fall back to virtual reserves if real reserves are not yet available.
                            real_sol_reserves_lamports = pool_state.get("real_sol_reserves", 0)
                            virtual_sol_reserves_lamports = pool_state.get("virtual_sol_reserves", 0)

                            liquidity_sol = (
                                real_sol_reserves_lamports / 1_000_000_000
                                if real_sol_reserves_lamports and real_sol_reserves_lamports > 0
                                else virtual_sol_reserves_lamports / 1_000_000_000
                            )

                            logger.info(
                                (
                                    "Token %s liquidity check: %.4f SOL "
                                    "(real_sol_reserves: %s lamports, virtual_sol_reserves: %s lamports) [addr: %s]"
                                )
                                % (
                                    token.symbol,
                                    liquidity_sol,
                                    real_sol_reserves_lamports,
                                    virtual_sol_reserves_lamports,
                                    used_address,
                                )
                            )

                            if liquidity_sol < self.min_liquidity_sol:
                                logger.info(
                                    f"Liquidity check FAILED for {token.symbol}: {liquidity_sol:.4f} SOL < {self.min_liquidity_sol} SOL"
                                )
                                logger.info(
                                    f"Skipping {token.symbol} - Liquidity {liquidity_sol:.4f} SOL below minimum {self.min_liquidity_sol} SOL"
                                )
                                self.processed_tokens.add(token_key)
                                return
                            else:
                                logger.info(
                                    f"Liquidity check passed: {liquidity_sol:.4f} SOL >= {self.min_liquidity_sol} SOL"
                                )
                        else:
                            logger.warning(
                                f"Liquidity check unavailable for {token.symbol}: no readable curve state (tried {len(candidates)} address(es)). Skipping token for safety."
                            )
                            self.processed_tokens.add(token_key)
                            return
                    except Exception as e:
                        logger.warning(f"Failed to check liquidity for {token.symbol}: {e}. Skipping token for safety.")
                        self.processed_tokens.add(token_key)
                        return
                
                # Token passed all filters - accept it
                if self._shutdown:
                    logger.info("Shutdown requested after checks. Not accepting token.")
                    self.processed_tokens.add(token_key)
                    return
                self.token_timestamps[token_key] = monotonic()
                found_token = token
                self.processed_tokens.add(token_key)
                logger.info(
                    f"Accepted token after checks: {token.symbol} ({token.mint}). Proceeding to buy pipeline."
                )
                token_found.set()

        listener_task = asyncio.create_task(
            self.token_listener.listen_for_tokens(
                token_callback,
                self.match_string,
                self.bro_address,
            )
        )

        # Wait for a token or shutdown, with timeout
        try:
            logger.info(
                f"Waiting for a suitable token (timeout: {self.token_wait_timeout}s)..."
            )
            token_wait_task = asyncio.create_task(token_found.wait())
            shutdown_wait_task = asyncio.create_task(self._shutdown_event.wait())

            done, pending = await asyncio.wait(
                {token_wait_task, shutdown_wait_task},
                timeout=self.token_wait_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel any pending waiters
            for p in pending:
                p.cancel()

            if shutdown_wait_task in done and self._shutdown:
                logger.info("Shutdown requested while waiting for token. Exiting wait early.")
                return None

            if token_wait_task in done and token_found.is_set() and found_token is not None:
                logger.info(f"Found token: {found_token.symbol} ({found_token.mint})")
                if self._shutdown:
                    logger.info("Shutdown requested before returning token. Ignoring token.")
                    return None
                return found_token

            logger.info(
                f"Timed out after waiting {self.token_wait_timeout}s for a token"
            )
            return None
        finally:
            # Ensure listener is cancelled promptly to proceed to buy pipeline
            listener_task.cancel()
            try:
                # Don't hang indefinitely if listener misbehaves on cancel
                await asyncio.wait_for(listener_task, timeout=2)
            except asyncio.TimeoutError:
                logger.warning("Listener task did not shut down within timeout; proceeding anyway.")
            except asyncio.CancelledError:
                pass

    async def _cleanup_resources(self) -> None:
        """Perform cleanup operations before shutting down."""
        if self.traded_mints:
            try:
                logger.info(f"Cleaning up {len(self.traded_mints)} traded token(s)...")
                await handle_cleanup_post_session(
                    self.solana_client,
                    self.wallet,
                    list(self.traded_mints),
                    self.priority_fee_manager,
                    self.cleanup_mode,
                    self.cleanup_with_priority_fee,
                    self.cleanup_force_close_with_burn,
                )
            except Exception:
                logger.exception("Error during cleanup")

        old_keys = {k for k in self.token_timestamps if k not in self.processed_tokens}
        for key in old_keys:
            self.token_timestamps.pop(key, None)

        await self.solana_client.close()

    async def _queue_token(self, token_info: TokenInfo) -> None:
        """Queue a token for processing if not already processed."""
        token_key = str(token_info.mint)

        if token_key in self.processed_tokens:
            logger.debug(f"Token {token_info.symbol} already processed. Skipping...")
            return

        # Record timestamp when token was discovered
        self.token_timestamps[token_key] = monotonic()

        await self.token_queue.put(token_info)
        logger.info(
            f"Queued new token: {token_info.symbol} ({token_info.mint}) on {token_info.platform.value}"
        )

    async def _process_token_queue(self) -> None:
        """Continuously process tokens from the queue, only if they're fresh."""
        while True:
            try:
                if self._shutdown:
                    logger.info("Shutdown requested. Stopping token queue processing...")
                    break
                token_info = await self.token_queue.get()
                token_key = str(token_info.mint)

                # Check if token is still "fresh"
                current_time = monotonic()
                token_age = current_time - self.token_timestamps.get(
                    token_key, current_time
                )

                if token_age > self.max_token_age:
                    logger.info(
                        f"Skipping token {token_info.symbol} - too old ({token_age:.1f}s > {self.max_token_age}s)"
                    )
                    continue

                # LIQUIDITY FILTERING for YOLO/queue mode
                # Ensure we also enforce the same pre-acceptance liquidity check here
                # so continuous mode behaves safely like single-token mode.
                if self.wait_before_buy > 0 and self.min_liquidity_sol > 0:
                    try:
                        logger.info(
                            f"New token detected (queue): {token_info.symbol}. Waiting {self.wait_before_buy}s to check liquidity..."
                        )
                        await asyncio.sleep(self.wait_before_buy)

                        if self._shutdown:
                            logger.info("Shutdown requested during wait. Skipping token from queue.")
                            self.processed_tokens.add(token_key)
                            continue

                        curve_manager = self.platform_implementations.curve_manager
                        address_provider = self.platform_implementations.address_provider

                        candidates: list[Pubkey] = []
                        if getattr(token_info, "bonding_curve", None):
                            candidates.append(token_info.bonding_curve)
                        if getattr(token_info, "associated_bonding_curve", None):
                            candidates.append(token_info.associated_bonding_curve)
                        if getattr(token_info, "bonding_curve", None) and getattr(token_info, "mint", None):
                            try:
                                derived = address_provider.derive_associated_bonding_curve(
                                    token_info.mint, token_info.bonding_curve
                                )
                                if derived and derived not in candidates:
                                    candidates.append(derived)
                            except Exception:
                                logger.debug("Failed to derive associated bonding curve", exc_info=True)

                        pool_state: dict | None = None
                        used_address: Pubkey | None = None
                        for addr in candidates:
                            try:
                                pool_state = await curve_manager.get_pool_state(addr)
                                used_address = addr
                                break
                            except Exception:
                                continue

                        if pool_state is not None:
                            real_sol_reserves_lamports = pool_state.get("real_sol_reserves", 0)
                            virtual_sol_reserves_lamports = pool_state.get("virtual_sol_reserves", 0)

                            liquidity_sol = (
                                real_sol_reserves_lamports / 1_000_000_000
                                if real_sol_reserves_lamports and real_sol_reserves_lamports > 0
                                else virtual_sol_reserves_lamports / 1_000_000_000
                            )

                            logger.info(
                                (
                                    "Token %s liquidity check: %.4f SOL "
                                    "(real_sol_reserves: %s lamports, virtual_sol_reserves: %s lamports) [addr: %s]"
                                )
                                % (
                                    token_info.symbol,
                                    liquidity_sol,
                                    real_sol_reserves_lamports,
                                    virtual_sol_reserves_lamports,
                                    used_address,
                                )
                            )

                            if liquidity_sol < self.min_liquidity_sol:
                                logger.info(
                                    f"Liquidity check FAILED for {token_info.symbol}: {liquidity_sol:.4f} SOL < {self.min_liquidity_sol} SOL"
                                )
                                logger.info(
                                    f"Skipping {token_info.symbol} - Liquidity {liquidity_sol:.4f} SOL below minimum {self.min_liquidity_sol} SOL"
                                )
                                self.processed_tokens.add(token_key)
                                continue
                            else:
                                logger.info(
                                    f"Liquidity check passed: {liquidity_sol:.4f} SOL >= {self.min_liquidity_sol} SOL"
                                )
                        else:
                            logger.warning(
                                f"Liquidity check unavailable for {token_info.symbol}: no readable curve state (tried {len(candidates)} address(es)). Skipping token for safety."
                            )
                            self.processed_tokens.add(token_key)
                            continue
                    except Exception as e:
                        logger.warning(
                            f"Failed to check liquidity for {token_info.symbol}: {e}. Skipping token for safety."
                        )
                        self.processed_tokens.add(token_key)
                        continue

                # Mark as processed only once we accept it
                self.processed_tokens.add(token_key)

                logger.info(
                    f"Processing fresh token: {token_info.symbol} (age: {token_age:.1f}s)"
                )
                await self._handle_token(token_info)

            except asyncio.CancelledError:
                logger.info("Token queue processor was cancelled")
                break
            except Exception:
                logger.exception("Error in token queue processor")
            finally:
                self.token_queue.task_done()

    async def _handle_token(self, token_info: TokenInfo) -> None:
        """Handle a new token creation event."""
        try:
            logger.info(
                f"Entered buy handler for {token_info.symbol} ({token_info.mint}) on {token_info.platform.value}"
            )
            if self._shutdown:
                logger.info("Shutdown requested. Skipping buy operation.")
                return
            # Validate that token is for our platform
            if token_info.platform != self.platform:
                logger.warning(
                    f"Token platform mismatch: expected {self.platform.value}, got {token_info.platform.value}"
                )
                return

            # CUSTOM MODIFICATION: Market cap filtering removed from here
            # Market cap is now checked in _wait_for_token() callback BEFORE accepting token
            # This ensures we check market cap immediately when token is detected, not later
            # END CUSTOM MODIFICATION

            # Wait for pool/curve to stabilize (unless in extreme fast mode)
            if not self.extreme_fast_mode:
                await self._save_token_info(token_info)
                logger.info(
                    f"Waiting for {self.wait_time_after_creation} seconds for the pool/curve to stabilize..."
                )
                await asyncio.sleep(self.wait_time_after_creation)

            # Final pre-buy safety: re-check liquidity without extra wait
            # This protects against sudden rugs between detection and buy.
            if self.min_liquidity_sol > 0:
                try:
                    logger.info(
                        f"Beginning buy pipeline for {token_info.symbol} - performing pre-buy liquidity check"
                    )
                    curve_manager = self.platform_implementations.curve_manager
                    address_provider = self.platform_implementations.address_provider
                    
                    # Try both bonding_curve and associated_bonding_curve for resilience
                    candidates: list[Pubkey] = []
                    if getattr(token_info, "bonding_curve", None):
                        candidates.append(token_info.bonding_curve)
                    if getattr(token_info, "associated_bonding_curve", None):
                        candidates.append(token_info.associated_bonding_curve)
                    if getattr(token_info, "bonding_curve", None) and getattr(token_info, "mint", None):
                        try:
                            derived = address_provider.derive_associated_bonding_curve(
                                token_info.mint, token_info.bonding_curve
                            )
                            if derived and derived not in candidates:
                                candidates.append(derived)
                        except Exception:
                            logger.debug("Failed to derive associated bonding curve", exc_info=True)

                    pool_state: dict | None = None
                    for addr in candidates:
                        try:
                            pool_state = await curve_manager.get_pool_state(addr)
                            break
                        except Exception:
                            continue
                    if pool_state is None:
                        raise ValueError("Could not read curve state from any candidate address")
                    real_sol_reserves_lamports = pool_state.get("real_sol_reserves", 0)
                    virtual_sol_reserves_lamports = pool_state.get("virtual_sol_reserves", 0)
                    liquidity_sol = (
                        real_sol_reserves_lamports / 1_000_000_000
                        if real_sol_reserves_lamports and real_sol_reserves_lamports > 0
                        else virtual_sol_reserves_lamports / 1_000_000_000
                    )
                    logger.info(
                        (
                            "Pre-buy liquidity for %s: %.4f SOL "
                            "(real_sol_reserves: %s lamports, virtual_sol_reserves: %s lamports)"
                        )
                        % (
                            token_info.symbol,
                            liquidity_sol,
                            real_sol_reserves_lamports,
                            virtual_sol_reserves_lamports,
                        )
                    )
                    if liquidity_sol < self.min_liquidity_sol:
                        logger.info(
                            f"Pre-buy liquidity check FAILED for {token_info.symbol}: {liquidity_sol:.4f} SOL < {self.min_liquidity_sol} SOL"
                        )
                        logger.info(
                            f"Aborting buy: Liquidity {liquidity_sol:.4f} SOL below minimum {self.min_liquidity_sol} SOL"
                        )
                        self.processed_tokens.add(str(token_info.mint))
                        return
                except Exception as e:
                    logger.warning(
                        f"Pre-buy liquidity check failed for {token_info.symbol}: {e}. Skipping buy for safety."
                    )
                    self.processed_tokens.add(str(token_info.mint))
                    return

            # Buy token (only if not shutting down)
            if self._shutdown:
                logger.info("Shutdown requested. Aborting buy.")
                return
            logger.info(
                f"Buying {self.buy_amount:.6f} SOL worth of {token_info.symbol} on {token_info.platform.value}..."
            )
            buy_result: TradeResult = await self.buyer.execute(token_info)

            if buy_result.success:
                await self._handle_successful_buy(token_info, buy_result)
            else:
                await self._handle_failed_buy(token_info, buy_result)

            # Only wait for next token in yolo mode
            if self.yolo_mode:
                logger.info(
                    f"YOLO mode enabled. Waiting {self.wait_time_before_new_token} seconds before looking for next token..."
                )
                await asyncio.sleep(self.wait_time_before_new_token)

        except Exception:
            logger.exception(f"Error handling token {token_info.symbol}")

    async def _handle_successful_buy(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle successful token purchase."""
        logger.info(
            f"Successfully bought {token_info.symbol} on {token_info.platform.value}"
        )
        self._log_trade(
            "buy",
            token_info,
            buy_result.price,
            buy_result.amount,
            buy_result.tx_signature,
        )
        self.traded_mints.add(token_info.mint)

        # Choose exit strategy
        if not self.marry_mode:
            if self.exit_strategy == "tp_sl":
                await self._handle_tp_sl_exit(token_info, buy_result)
            elif self.exit_strategy == "time_based":
                await self._handle_time_based_exit(token_info)
            elif self.exit_strategy == "manual":
                logger.info("Manual exit strategy - position will remain open")
        else:
            logger.info("Marry mode enabled. Skipping sell operation.")

    async def _handle_failed_buy(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle failed token purchase."""
        logger.error(f"Failed to buy {token_info.symbol}: {buy_result.error_message}")
        # Close ATA if enabled
        await handle_cleanup_after_failure(
            self.solana_client,
            self.wallet,
            token_info.mint,
            self.priority_fee_manager,
            self.cleanup_mode,
            self.cleanup_with_priority_fee,
            self.cleanup_force_close_with_burn,
        )

    async def _handle_tp_sl_exit(
        self, token_info: TokenInfo, buy_result: TradeResult
    ) -> None:
        """Handle take profit/stop loss exit strategy."""
        # Determine an accurate entry price; in extreme-fast mode the buyer's price is synthetic
        entry_price = buy_result.price
        try:
            pool_address = self._get_pool_address(token_info)
            curve_manager = self.platform_implementations.curve_manager
            refreshed_price = await curve_manager.calculate_price(pool_address)
            if refreshed_price and refreshed_price > 0:
                entry_price = refreshed_price
        except Exception:
            logger.debug("Could not refresh entry price post-buy; using reported price")

        # Create position
        position = Position.create_from_buy_result(
            mint=token_info.mint,
            symbol=token_info.symbol,
            entry_price=entry_price,
            quantity=buy_result.amount,
            take_profit_percentage=self.take_profit_percentage,
            stop_loss_percentage=self.stop_loss_percentage,
            max_hold_time=self.max_hold_time,
        )

        logger.info(f"Created position: {position}")
        if position.take_profit_price:
            logger.info(
                f"Take profit target: {position.take_profit_price:.8f} SOL per token"
            )
        if position.stop_loss_price:
            logger.info(
                f"Stop loss target: {position.stop_loss_price:.8f} SOL per token"
            )

        # Monitor position until exit condition is met
        await self._monitor_position_until_exit(token_info, position)

    async def _handle_time_based_exit(self, token_info: TokenInfo) -> None:
        """Handle legacy time-based exit strategy."""
        logger.info(f"Waiting for {self.wait_time_after_buy} seconds before selling...")
        await asyncio.sleep(self.wait_time_after_buy)

        logger.info(f"Selling {token_info.symbol}...")
        sell_result: TradeResult = await self.seller.execute(token_info)

        if sell_result.success:
            logger.info(f"Successfully sold {token_info.symbol}")
            self._log_trade(
                "sell",
                token_info,
                sell_result.price,
                sell_result.amount,
                sell_result.tx_signature,
            )
            # Close ATA if enabled
            await handle_cleanup_after_sell(
                self.solana_client,
                self.wallet,
                token_info.mint,
                self.priority_fee_manager,
                self.cleanup_mode,
                self.cleanup_with_priority_fee,
                self.cleanup_force_close_with_burn,
            )
        else:
            logger.error(
                f"Failed to sell {token_info.symbol}: {sell_result.error_message}"
            )

    async def _monitor_position_until_exit(
        self, token_info: TokenInfo, position: Position
    ) -> None:
        """Monitor a position until exit conditions are met."""
        logger.info(
            f"Starting position monitoring (check interval: {self.price_check_interval}s)"
        )

        # Get pool address for price monitoring using platform-agnostic method
        pool_address = self._get_pool_address(token_info)
        curve_manager = self.platform_implementations.curve_manager

        # Debounce state for stop-loss confirmation
        consecutive_stop_breaches = 0

        while position.is_active:
            try:
                # Get current price from pool/curve
                current_price = await curve_manager.calculate_price(pool_address)

                # Skip invalid/glitch prices
                if current_price <= 0:
                    logger.debug("Ignoring non-positive price sample during monitoring")
                    await asyncio.sleep(self.price_check_interval)
                    continue

                # Check if position should be exited
                should_exit, exit_reason = position.should_exit(current_price)

                if should_exit and exit_reason:
                    # Apply debounce for stop-loss to avoid instant exit on noisy first sample
                    if exit_reason.value == "stop_loss":
                        elapsed = (datetime.utcnow() - position.entry_time).total_seconds()
                        # Enforce minimum hold before stop-loss
                        if elapsed < self.min_hold_before_stop_seconds:
                            logger.debug(
                                f"Stop-loss breach ignored during warm-up ({elapsed:.2f}s < {self.min_hold_before_stop_seconds}s)"
                            )
                            consecutive_stop_breaches = 0
                            await asyncio.sleep(self.price_check_interval)
                            continue

                        consecutive_stop_breaches += 1
                        if consecutive_stop_breaches < self.stop_loss_confirmations:
                            logger.debug(
                                f"Stop-loss breach {consecutive_stop_breaches}/{self.stop_loss_confirmations} — waiting for confirmation"
                            )
                            await asyncio.sleep(self.price_check_interval)
                            continue

                    logger.info(f"Exit condition met: {exit_reason.value}")
                    logger.info(f"Current price: {current_price:.8f} SOL per token")

                    # Log PnL before exit
                    pnl = position.get_pnl(current_price)
                    logger.info(
                        f"Position PnL: {pnl['price_change_pct']:.2f}% ({pnl['unrealized_pnl_sol']:.6f} SOL)"
                    )

                    # Execute sell
                    sell_result = await self.seller.execute(token_info)

                    if sell_result.success:
                        # Close position with actual exit price
                        position.close_position(sell_result.price, exit_reason)

                        logger.info(
                            f"Successfully exited position: {exit_reason.value}"
                        )
                        self._log_trade(
                            "sell",
                            token_info,
                            sell_result.price,
                            sell_result.amount,
                            sell_result.tx_signature,
                        )

                        # Log final PnL
                        final_pnl = position.get_pnl()
                        logger.info(
                            f"Final PnL: {final_pnl['price_change_pct']:.2f}% ({final_pnl['unrealized_pnl_sol']:.6f} SOL)"
                        )

                        # Close ATA if enabled
                        await handle_cleanup_after_sell(
                            self.solana_client,
                            self.wallet,
                            token_info.mint,
                            self.priority_fee_manager,
                            self.cleanup_mode,
                            self.cleanup_with_priority_fee,
                            self.cleanup_force_close_with_burn,
                        )
                        # Done monitoring on successful exit
                        break
                    else:
                        logger.error(
                            f"Failed to exit position: {sell_result.error_message}"
                        )
                        # Keep monitoring in case sell can be retried
                        # Reset breach counter to avoid immediate re-trigger spam
                        consecutive_stop_breaches = 0
                else:
                    # Log current status
                    pnl = position.get_pnl(current_price)
                    logger.debug(
                        f"Position status: {current_price:.8f} SOL per token ({pnl['price_change_pct']:+.2f}%)"
                    )
                    # Reset stop-loss breach counter if not currently breaching
                    if (
                        position.stop_loss_price is None
                        or current_price > position.stop_loss_price
                    ):
                        consecutive_stop_breaches = 0

                # Wait before next price check
                await asyncio.sleep(self.price_check_interval)

            except Exception:
                logger.exception("Error monitoring position")
                await asyncio.sleep(
                    self.price_check_interval
                )  # Continue monitoring despite errors

    def _get_pool_address(self, token_info: TokenInfo) -> Pubkey:
        """Get the pool/curve address for price monitoring using platform-agnostic method."""
        address_provider = self.platform_implementations.address_provider

        # Use platform-specific logic to get the appropriate address
        if hasattr(token_info, "bonding_curve") and token_info.bonding_curve:
            return token_info.bonding_curve
        elif hasattr(token_info, "pool_state") and token_info.pool_state:
            return token_info.pool_state
        else:
            # Fallback to deriving the address using platform provider
            return address_provider.derive_pool_address(token_info.mint)

    async def _save_token_info(self, token_info: TokenInfo) -> None:
        """Save token information to a file."""
        try:
            trades_dir = Path("trades")
            trades_dir.mkdir(exist_ok=True)
            file_path = trades_dir / f"{token_info.mint}.txt"

            # Convert to dictionary for saving - platform-agnostic
            token_dict = {
                "name": token_info.name,
                "symbol": token_info.symbol,
                "uri": token_info.uri,
                "mint": str(token_info.mint),
                "platform": token_info.platform.value,
                "user": str(token_info.user) if token_info.user else None,
                "creator": str(token_info.creator) if token_info.creator else None,
                "creation_timestamp": token_info.creation_timestamp,
            }

            # Add platform-specific fields only if they exist
            platform_fields = {
                "bonding_curve": token_info.bonding_curve,
                "associated_bonding_curve": token_info.associated_bonding_curve,
                "creator_vault": token_info.creator_vault,
                "pool_state": token_info.pool_state,
                "base_vault": token_info.base_vault,
                "quote_vault": token_info.quote_vault,
            }

            for field_name, field_value in platform_fields.items():
                if field_value is not None:
                    token_dict[field_name] = str(field_value)

            file_path.write_text(json.dumps(token_dict, indent=2))

            logger.info(f"Token information saved to {file_path}")
        except OSError:
            logger.exception("Failed to save token information")

    def _log_trade(
        self,
        action: str,
        token_info: TokenInfo,
        price: float,
        amount: float,
        tx_hash: str | None,
    ) -> None:
        """Log trade information."""
        try:
            trades_dir = Path("trades")
            trades_dir.mkdir(exist_ok=True)

            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "action": action,
                "platform": token_info.platform.value,
                "token_address": str(token_info.mint),
                "symbol": token_info.symbol,
                "price": price,
                "amount": amount,
                "tx_hash": str(tx_hash) if tx_hash else None,
            }

            log_file_path = trades_dir / "trades.log"
            with log_file_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(log_entry) + "\n")
        except OSError:
            logger.exception("Failed to log trade information")


# Backward compatibility alias
PumpTrader = UniversalTrader  # Legacy name for backward compatibility
