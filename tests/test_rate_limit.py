# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Exercise async rate-limit backpressure and its adapter composition contract."""

from __future__ import annotations

import asyncio

import pytest

from orbit_resilience import (
    AsyncRateLimiter,
    RateLimiter,
    RateLimitExceededError,
    RetryPolicy,
    resilient_call,
)
from orbit_resilience.resilience import DefaultFailureClassifier, FailureKind


@pytest.mark.asyncio
async def test_async_limiter_allows_configured_burst_then_waits_for_refill() -> None:
    """A fresh key starts full and later acquisition waits asynchronously for refill."""
    limiter = AsyncRateLimiter(2, 0.02)
    assert isinstance(limiter, RateLimiter)
    await limiter.acquire("provider")
    await limiter.acquire("provider", cost=1)
    with pytest.raises(RateLimitExceededError) as error:
        await limiter.acquire("provider", timeout=0)
    assert error.value.retry_after > 0
    with pytest.raises(RateLimitExceededError):
        await limiter.acquire("provider", timeout=0.001)
    await limiter.acquire("provider", timeout=0.1)


@pytest.mark.asyncio
async def test_cancelled_wait_does_not_consume_a_token() -> None:
    """A waiter cancelled before refill leaves the next token available to another caller."""
    limiter = AsyncRateLimiter(1, 0.02)
    await limiter.acquire("provider")
    waiter = asyncio.create_task(limiter.acquire("provider"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.sleep(0.025)
    await limiter.acquire("provider", timeout=0)


@pytest.mark.asyncio
async def test_limiter_bounds_identity_state_and_supports_reset() -> None:
    """Key churn evicts the least-recently-used state and reset is explicit."""
    limiter = AsyncRateLimiter(2, 1, max_keys=2)
    await limiter.acquire("first")
    await limiter.acquire("second")
    await limiter.acquire("first")
    await limiter.acquire("third")
    assert limiter.key_count == 2
    assert await limiter.reset("first")
    assert limiter.key_count == 1
    assert not await limiter.reset("missing")


@pytest.mark.parametrize(
    "arguments",
    [
        (0, 1),
        (True, 1),
        (1, 0),
        (1, float("inf")),
        (1, 1, 0),
        (1, 1, True),
        (1, 1, 1_000_001),
    ],
)
def test_limiter_rejects_invalid_configuration(arguments: tuple[object, ...]) -> None:
    """Invalid capacities, refill periods, and key bounds fail at construction."""
    with pytest.raises(ValueError):
        if len(arguments) == 3:
            AsyncRateLimiter(arguments[0], arguments[1], max_keys=arguments[2])  # type: ignore[arg-type]
        else:
            AsyncRateLimiter(*arguments)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,cost,timeout",
    [
        ("", 1, None),
        ("a\n", 1, None),
        ("x" * 256, 1, None),
        ("provider", 0, None),
        ("provider", 2, None),
        ("provider", 1, -1),
        ("provider", 1, float("nan")),
    ],
)
async def test_acquire_rejects_invalid_request_values(key, cost, timeout) -> None:
    """Identity, token cost, and wait timeout are validated before state changes."""
    limiter = AsyncRateLimiter(1, 1)
    with pytest.raises(ValueError):
        await limiter.acquire(key, cost=cost, timeout=timeout)
    assert limiter.key_count == 0


@pytest.mark.asyncio
async def test_resilient_call_acquires_rate_capacity_before_provider_operation() -> None:
    """An optional capability implementation gates outbound work before invocation."""
    limiter = AsyncRateLimiter(1, 1)
    called = False

    async def operation() -> str:
        nonlocal called
        called = True
        return "ok"

    assert (
        await resilient_call(
            operation,
            rate_limiter=limiter,
            rate_limit_key="orders-api",
            rate_limit_timeout=0,
        )
        == "ok"
    )
    assert called


@pytest.mark.asyncio
async def test_resilient_call_validates_key_before_calling_the_adapter() -> None:
    """The shared capability validates identity input before plugin-controlled code runs."""
    limiter = AsyncRateLimiter(1, 1)
    with pytest.raises(ValueError, match="Rate-limit keys"):
        await resilient_call(
            lambda: "unreachable",
            rate_limiter=limiter,
            rate_limit_key="unsafe\nkey",
        )
    assert limiter.key_count == 0


@pytest.mark.asyncio
async def test_rate_limit_timeout_is_not_classified_as_provider_outage() -> None:
    """Quota exhaustion is resource pressure and is not retried as a transient outage."""
    classifier = DefaultFailureClassifier()
    assert classifier.classify(RateLimitExceededError(1)) is FailureKind.RESOURCE

    attempts = 0
    acquisitions = 0

    class RejectingLimiter:
        """Test adapter that rejects token requests as local resource pressure."""

        async def acquire(self, key: str, *, cost: int = 1, timeout: float | None = None) -> None:
            nonlocal acquisitions
            acquisitions += 1
            raise RateLimitExceededError(1)

    async def operation() -> None:
        nonlocal attempts
        attempts += 1

    with pytest.raises(RateLimitExceededError):
        await resilient_call(
            operation,
            rate_limiter=RejectingLimiter(),
            rate_limit_key="orders-api",
            retry_policy=RetryPolicy(max_attempts=4, initial_delay=0),
        )
    assert attempts == 0
    assert acquisitions == 1
