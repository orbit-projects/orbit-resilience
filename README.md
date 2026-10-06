# Orbit Resilience

`orbit-resilience` is an optional package of bounded async resilience utilities: deadlines,
token-bucket backpressure, retry policies, circuit breakers, bulkheads, and failure classification. These utilities are not
required to use Orbit Core; Core retains its own lifecycle, request, and cleanup timeouts.
`resilient_call` validates all supplied policies and the operation before rate-limit capacity is
acquired, so a malformed local configuration cannot consume provider quota.

```bash
pip install orbit-resilience
```

The package is provider-neutral. Classify vendor-specific exceptions through `FailureClassifier`,
retry only operations with documented idempotency semantics, and keep attempts within the caller's
deadline. The policies are process-local; distributed coordination requires a separate backend.

`AsyncRateLimiter` waits asynchronously for local token-bucket capacity before a service operation
proceeds. Its per-instance buckets are process-local and do not coordinate across worker processes;
use a separately installed adapter when a shared or distributed quota is required. Core's own
nonblocking limiter for HTTP/Admin responses remains part of `orbit-core` and can reject requests
with `429` without making request handling wait for refill.

```python
from orbit_resilience import AsyncRateLimiter, resilient_call

partner_api_limit = AsyncRateLimiter(limit=60, period=60)
result = await resilient_call(
    call_partner_api,
    rate_limiter=partner_api_limit,
    rate_limit_key="partner-api",
    rate_limit_timeout=2.0,
)
```

When combined with retries, each provider attempt acquires its own token. A rate-limit wait timeout
is classified as local resource pressure, not as an upstream transient failure, so the default
retry policy does not repeat it.

## Status

This package is pre-alpha; its API is not stable and it is not yet a published release. It supports
Python 3.11 through 3.14.

## Documentation

The package-specific guides cover [architecture](docs/architecture/overview.md), [operations and security](docs/operations/README.md), and [development](docs/development/README.md), with [security guidance](docs/security/overview.md). The [documentation index](docs/README.md) links to the full package overview and project policies.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
mypy
```

Licensed under Apache-2.0.

