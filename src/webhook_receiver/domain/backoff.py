"""When to try again (FR-12).

    delay = random(0, min(cap, base * 2**attempt))

Two ideas, and the second is the one that matters.

**Exponential** backs off fast enough that a struggling downstream gets a
geometrically shrinking load rather than a constant hammering, and the `cap`
stops the delay growing to something absurd on attempt 20.

**Full jitter** -- `random(0, delay)`, not `delay ± a bit` -- is what stops the
retries from *synchronising*. This is the failure mode worth being precise about,
because it is the one that turns a blip into an outage:

A downstream goes down for 30 seconds. Every event in flight fails at roughly the
same instant. With a deterministic schedule, all of them wait exactly `base * 2`
seconds and all of them retry *at the same instant*. The downstream, which has
just come back up, is hit by the entire backlog simultaneously, falls over again,
and the fleet re-synchronises even harder on the next round. The retry policy has
become a self-inflicted DDoS with a metronome.

Spreading the retries uniformly over `[0, delay)` breaks the lockstep. It is
counter-intuitive -- the *expected* delay is halved, so we retry sooner on
average, which feels like the wrong direction -- but the variance is the point,
not the mean. AWS's "Exponential Backoff and Jitter" measured this: full jitter
beats both no jitter and the more conservative "equal jitter" on completion time
*and* on load. See ADR-0005.

Pure: an attempt number and a `Rng` in, seconds out. No clock, no I/O, no
`Settings` -- the thresholds are passed by the caller, so this stays a function
you can reason about and a schedule you can assert on.
"""

from __future__ import annotations

from webhook_receiver.adapters.rng import Rng

# 2**attempt overflows into absurdity long before it overflows a float, and a
# `cap` of 300s is reached by attempt 9 with a 1s base. Clamping the exponent
# keeps `base * 2**attempt` from producing an inf (and then a NaN delay) if
# someone ever raises max_attempts to 2000.
MAX_EXPONENT = 32


def next_delay_seconds(
    *,
    attempt: int,
    base_seconds: float,
    cap_seconds: float,
    rng: Rng,
) -> float:
    """Seconds to wait before attempt `attempt + 1`.

    `attempt` is 1-based: the delay after the *first* failure is drawn from
    `[0, base * 2)`, not `[0, base)`. That is deliberate -- the first retry of a
    genuinely transient failure should not be instantaneous, or it lands while
    the downstream is still on the floor.
    """
    if attempt < 1:
        msg = f"attempt is 1-based; got {attempt}"
        raise ValueError(msg)

    exponent = min(attempt, MAX_EXPONENT)
    ceiling = min(cap_seconds, base_seconds * (2**exponent))

    # Full jitter. The low bound is 0, not `ceiling / 2`: a floor would leave the
    # retries clustered in a band, which is the very thing jitter exists to break.
    return rng.uniform(0.0, ceiling)
