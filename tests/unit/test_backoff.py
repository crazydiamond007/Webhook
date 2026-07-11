"""The retry schedule (FR-12).

FR-12's acceptance is two claims, and they need different tests:

* successive delays follow `min(cap, base * 2**attempt)` -- pinned with a fake
  RNG that returns its upper bound, so the *ceiling* is what is under test;
* a batch that fails together does not retry together -- the property that makes
  jitter worth having at all.
"""

from __future__ import annotations

import pytest

from webhook_receiver.adapters.rng import Rng, SeededRng, SystemRng, create_rng
from webhook_receiver.domain.backoff import MAX_EXPONENT, next_delay_seconds

BASE = 1.0
CAP = 300.0


class CeilingRng:
    """Always returns the top of the range, exposing the pre-jitter ceiling.

    Testing the ceiling and the jitter separately: with a real RNG the delay is a
    sample from a distribution, and asserting on a sample is how you get a test
    that fails one time in twenty.
    """

    def uniform(self, low: float, high: float) -> float:
        return high


class FloorRng:
    def uniform(self, low: float, high: float) -> float:
        return low


def delay(attempt: int, rng: Rng, *, cap: float = CAP) -> float:
    return next_delay_seconds(attempt=attempt, base_seconds=BASE, cap_seconds=cap, rng=rng)


class TestSchedule:
    def test_the_ceiling_doubles_each_attempt(self) -> None:
        rng = CeilingRng()

        assert [delay(n, rng) for n in (1, 2, 3, 4, 5)] == [2.0, 4.0, 8.0, 16.0, 32.0]

    def test_the_ceiling_is_capped(self) -> None:
        # Without the cap, attempt 20 would be 1048576 seconds -- twelve days.
        rng = CeilingRng()

        assert delay(20, rng) == CAP
        assert delay(50, rng) == CAP

    def test_a_huge_attempt_number_does_not_overflow_to_infinity(self) -> None:
        # 2**attempt is an integer, so it does not overflow -- it just becomes
        # enormous, and `base * 2**5000` IS an OverflowError on the float
        # multiply. Clamping the exponent keeps a misconfigured max_attempts from
        # crashing the worker instead of scheduling a retry.
        assert delay(MAX_EXPONENT + 10_000, CeilingRng()) == CAP

    def test_the_delay_is_never_negative(self) -> None:
        assert delay(1, FloorRng()) == 0.0

    def test_attempt_is_one_based(self) -> None:
        with pytest.raises(ValueError, match="1-based"):
            delay(0, CeilingRng())

    def test_the_first_retry_is_not_instantaneous_on_average(self) -> None:
        # The delay after the first failure is drawn from [0, base*2), not
        # [0, base). A retry that lands immediately arrives while the downstream
        # is still on the floor.
        assert delay(1, CeilingRng()) == 2 * BASE


class TestFullJitter:
    def test_the_delay_is_drawn_from_the_whole_range(self) -> None:
        rng = SeededRng(7)

        samples = [delay(3, rng) for _ in range(200)]

        assert all(0.0 <= s <= 8.0 for s in samples)
        # Full jitter, not "delay +/- a bit": the low end must be reachable. A
        # floor would leave the retries clustered in a band, which is the exact
        # thing jitter exists to break.
        assert min(samples) < 1.0
        assert max(samples) > 7.0

    def test_a_batch_failing_together_does_not_retry_together(self) -> None:
        # FR-12's real acceptance. Ten events fail at the same instant on the same
        # attempt. With a deterministic schedule every one of them would come back
        # at the identical moment and knock the recovering downstream over again.
        rng = SeededRng(99)

        delays = [delay(2, rng) for _ in range(10)]

        assert len(set(delays)) == 10  # no two events retry at the same instant
        assert max(delays) - min(delays) > 1.0  # and they are genuinely spread


class TestSeeding:
    def test_the_same_seed_gives_the_same_schedule(self) -> None:
        # SPEC §6.4: the schedule has to be reproducible, or a bug in it is not
        # findable.
        first = [delay(n, SeededRng(42)) for n in (1, 2, 3)]
        second = [delay(n, SeededRng(42)) for n in (1, 2, 3)]

        assert first == second

    def test_different_seeds_give_different_schedules(self) -> None:
        assert [delay(n, SeededRng(1)) for n in (1, 2, 3)] != [
            delay(n, SeededRng(2)) for n in (1, 2, 3)
        ]

    def test_no_seed_means_real_entropy(self) -> None:
        assert isinstance(create_rng(None), SystemRng)

    def test_a_seed_means_a_reproducible_rng(self) -> None:
        assert isinstance(create_rng(5), SeededRng)

    def test_two_system_rngs_do_not_share_state(self) -> None:
        # Not `random.uniform` at module level: a process-global RNG means a test
        # that seeds it silently changes every other test in the process.
        a, b = SystemRng(), SystemRng()

        assert [a.uniform(0, 1e9) for _ in range(5)] != [b.uniform(0, 1e9) for _ in range(5)]
