"""
Account Pool with Round-Robin Load Balancing and Error Cooldown.

Features:
- Atomic round-robin selection for even load distribution
- Error cooldown: quota errors (429) -> 1 hour, consecutive errors (3+) -> 1 minute
- Automatic cooldown expiration and recovery
- Thread-safe operations with asyncio locks
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from enum import IntEnum


class CooldownReason(IntEnum):
    """Reason for account cooldown."""
    NONE = 0
    CONSECUTIVE_ERRORS = 1  # 3+ consecutive errors -> 1 minute cooldown
    QUOTA_EXCEEDED = 2      # 429 error -> 1 hour cooldown
    TOKEN_EXPIRED = 3       # Token expired -> until refreshed


@dataclass
class AccountStats:
    """Runtime statistics for an account."""
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    consecutive_errors: int = 0
    total_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    last_used: float = 0.0
    cooldown_until: float = 0.0
    cooldown_reason: CooldownReason = CooldownReason.NONE


class AccountPool:
    """
    Account pool with round-robin selection and error cooldown.

    Usage:
        pool = AccountPool()
        await pool.reload(accounts)  # Load accounts from database

        account = await pool.get_next()  # Get next available account
        if account:
            try:
                # Use account...
                await pool.record_success(account['id'], input_tokens=100, output_tokens=50)
            except QuotaError:
                await pool.record_error(account['id'], is_quota_error=True)
            except Exception:
                await pool.record_error(account['id'], is_quota_error=False)
    """

    # Cooldown durations (in seconds)
    CONSECUTIVE_ERROR_COOLDOWN = 60       # 1 minute for 3+ consecutive errors
    QUOTA_ERROR_COOLDOWN = 3600           # 1 hour for quota/rate limit errors
    TOKEN_EXPIRY_BUFFER = 300             # 5 minutes before token expiry
    MAX_CONSECUTIVE_ERRORS = 3            # Trigger cooldown after this many errors

    def __init__(self):
        self._accounts: List[Dict[str, Any]] = []
        self._stats: Dict[str, AccountStats] = {}
        self._current_index: int = 0
        self._lock = asyncio.Lock()

    async def reload(self, accounts: List[Dict[str, Any]]) -> None:
        """Reload accounts from database, preserving runtime stats."""
        async with self._lock:
            self._accounts = list(accounts)
            # Initialize stats for new accounts, preserve existing stats
            account_ids = {acc['id'] for acc in accounts}
            # Remove stats for deleted accounts
            self._stats = {k: v for k, v in self._stats.items() if k in account_ids}
            # Add stats for new accounts
            for acc in accounts:
                if acc['id'] not in self._stats:
                    self._stats[acc['id']] = AccountStats()

    async def get_next(self) -> Optional[Dict[str, Any]]:
        """
        Get the next available account using round-robin selection.
        Skips accounts that are in cooldown or have expired tokens.

        Returns:
            Account dict if available, None if no accounts are available.
        """
        async with self._lock:
            if not self._accounts:
                return None

            now = time.time()
            n = len(self._accounts)

            # Try to find an available account using round-robin
            for _ in range(n):
                self._current_index = (self._current_index + 1) % n
                acc = self._accounts[self._current_index]
                acc_id = acc['id']

                # Skip disabled accounts
                if not acc.get('enabled', True):
                    continue

                stats = self._stats.get(acc_id)
                if stats:
                    # Skip accounts in cooldown
                    if stats.cooldown_until > now:
                        continue

                    # Clear expired cooldown and reset error counter
                    if stats.cooldown_until > 0 and stats.cooldown_until <= now:
                        stats.cooldown_until = 0.0
                        stats.cooldown_reason = CooldownReason.NONE
                        stats.consecutive_errors = 0

                # Skip accounts with soon-to-expire tokens
                expires_at = acc.get('expires_at')
                if expires_at:
                    try:
                        # Parse ISO format timestamp as UTC
                        dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        exp_time = dt.timestamp()
                        if now > exp_time - self.TOKEN_EXPIRY_BUFFER:
                            continue
                    except (ValueError, TypeError, AttributeError):
                        pass  # Invalid timestamp format, allow account

                # Found available account
                if stats:
                    stats.last_used = now
                return acc.copy()

            # No available account found - return None to let caller handle
            return None

    async def record_success(
        self,
        account_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0
    ) -> None:
        """
        Record a successful request. Clears cooldown and resets consecutive errors.
        """
        async with self._lock:
            if account_id not in self._stats:
                self._stats[account_id] = AccountStats()

            stats = self._stats[account_id]
            stats.request_count += 1
            stats.success_count += 1
            stats.consecutive_errors = 0
            stats.total_tokens += input_tokens + output_tokens
            stats.total_input_tokens += input_tokens
            stats.total_output_tokens += output_tokens
            stats.last_used = time.time()

            # Clear cooldown on success
            stats.cooldown_until = 0.0
            stats.cooldown_reason = CooldownReason.NONE

    async def record_error(
        self,
        account_id: str,
        is_quota_error: bool = False
    ) -> None:
        """
        Record a failed request. May trigger cooldown based on error type.

        Args:
            account_id: The account ID
            is_quota_error: True if the error is a rate limit (429) or quota error
        """
        async with self._lock:
            if account_id not in self._stats:
                self._stats[account_id] = AccountStats()

            stats = self._stats[account_id]
            stats.request_count += 1
            stats.error_count += 1
            stats.consecutive_errors += 1
            stats.last_used = time.time()

            now = time.time()

            if is_quota_error:
                # Quota error: 1 hour cooldown
                stats.cooldown_until = now + self.QUOTA_ERROR_COOLDOWN
                stats.cooldown_reason = CooldownReason.QUOTA_EXCEEDED
            elif stats.consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                # 3+ consecutive errors: 1 minute cooldown
                stats.cooldown_until = now + self.CONSECUTIVE_ERROR_COOLDOWN
                stats.cooldown_reason = CooldownReason.CONSECUTIVE_ERRORS

    async def set_token_expired(self, account_id: str) -> None:
        """Mark an account as having an expired token."""
        async with self._lock:
            if account_id not in self._stats:
                self._stats[account_id] = AccountStats()

            stats = self._stats[account_id]
            stats.cooldown_until = float('inf')  # Until token is refreshed
            stats.cooldown_reason = CooldownReason.TOKEN_EXPIRED

    async def clear_cooldown(self, account_id: str) -> None:
        """Clear cooldown for an account (e.g., after token refresh)."""
        async with self._lock:
            if account_id in self._stats:
                stats = self._stats[account_id]
                stats.cooldown_until = 0.0
                stats.cooldown_reason = CooldownReason.NONE
                stats.consecutive_errors = 0

    async def get_stats(self, account_id: str) -> Optional[AccountStats]:
        """Get runtime statistics for an account."""
        async with self._lock:
            return self._stats.get(account_id)

    async def get_all_stats(self) -> Dict[str, AccountStats]:
        """Get runtime statistics for all accounts."""
        async with self._lock:
            return dict(self._stats)

    def count(self) -> int:
        """Return total number of accounts."""
        return len(self._accounts)

    async def available_count(self) -> int:
        """Return number of accounts not in cooldown."""
        async with self._lock:
            now = time.time()
            count = 0
            for acc in self._accounts:
                if not acc.get('enabled', True):
                    continue
                stats = self._stats.get(acc['id'])
                if not stats or stats.cooldown_until <= now:
                    count += 1
            return count

    async def get_pool_status(self) -> Dict[str, Any]:
        """Get overall pool status for monitoring."""
        async with self._lock:
            now = time.time()
            total = len(self._accounts)
            enabled = sum(1 for acc in self._accounts if acc.get('enabled', True))
            in_cooldown = 0
            cooldown_by_reason = {
                CooldownReason.CONSECUTIVE_ERRORS: 0,
                CooldownReason.QUOTA_EXCEEDED: 0,
                CooldownReason.TOKEN_EXPIRED: 0,
            }

            total_requests = 0
            total_successes = 0
            total_errors = 0
            total_tokens = 0

            for acc in self._accounts:
                stats = self._stats.get(acc['id'])
                if stats:
                    if stats.cooldown_until > now:
                        in_cooldown += 1
                        if stats.cooldown_reason in cooldown_by_reason:
                            cooldown_by_reason[stats.cooldown_reason] += 1

                    total_requests += stats.request_count
                    total_successes += stats.success_count
                    total_errors += stats.error_count
                    total_tokens += stats.total_tokens

            return {
                'total_accounts': total,
                'enabled_accounts': enabled,
                'available_accounts': enabled - in_cooldown,
                'in_cooldown': in_cooldown,
                'cooldown_by_reason': {
                    'consecutive_errors': cooldown_by_reason[CooldownReason.CONSECUTIVE_ERRORS],
                    'quota_exceeded': cooldown_by_reason[CooldownReason.QUOTA_EXCEEDED],
                    'token_expired': cooldown_by_reason[CooldownReason.TOKEN_EXPIRED],
                },
                'total_requests': total_requests,
                'total_successes': total_successes,
                'total_errors': total_errors,
                'success_rate': (total_successes / total_requests * 100) if total_requests > 0 else 0,
                'total_tokens': total_tokens,
            }


# Global pool instance
_pool: Optional[AccountPool] = None


def get_pool() -> AccountPool:
    """Get the global account pool instance."""
    global _pool
    if _pool is None:
        _pool = AccountPool()
    return _pool
