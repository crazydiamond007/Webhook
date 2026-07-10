"""Settings must fail loudly at boot, not silently at 3am.

SPEC §NFR-11 puts every threshold in config; these tests pin the bounds that
make a misconfigured threshold a startup error rather than a runtime pathology.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from webhook_receiver.config import Environment, LogLevel, Settings

_DSN = "postgresql+asyncpg://user:pw@localhost:5432/db"


def _settings(**overrides: object) -> Settings:
    """Build Settings from explicit kwargs, ignoring any ambient .env file."""
    base: dict[str, object] = {
        "database_url": _DSN,
        "admin_api_key": "test-admin-key",
        "_env_file": None,
    }
    return Settings(**(base | overrides))  # type: ignore[arg-type]  # kwargs are validated by pydantic


class TestDefaults:
    def test_defaults_match_the_spec(self) -> None:
        settings = _settings()

        assert settings.signature_timestamp_tolerance_seconds == 300  # FR-4
        assert settings.max_attempts == 5  # FR-13
        assert settings.backoff_base_seconds == 1.0  # FR-12
        assert settings.backoff_cap_seconds == 300.0  # FR-12
        assert settings.environment is Environment.LOCAL
        assert settings.log_level is LogLevel.INFO

    def test_jitter_is_unseeded_by_default(self) -> None:
        # Seeded jitter in production would re-synchronise a failing batch,
        # which is the thundering herd that FR-12's jitter exists to prevent.
        assert _settings().jitter_seed is None


class TestSecrets:
    def test_admin_key_is_not_exposed_by_repr(self) -> None:
        # NFR-6: secrets must not leak via a traceback or a logged repr.
        assert "test-admin-key" not in repr(_settings())

    def test_webhook_secret_is_not_exposed_by_repr(self) -> None:
        settings = _settings(webhook_secrets={"stripe": "whsec_topsecret"})
        assert "whsec_topsecret" not in repr(settings)

    def test_secret_for_source_returns_the_key(self) -> None:
        settings = _settings(webhook_secrets={"stripe": "whsec_abc"})
        secret = settings.secret_for_source("stripe")

        assert secret is not None
        assert secret.get_secret_value() == "whsec_abc"

    def test_secret_for_unknown_source_is_none(self) -> None:
        # The caller turns this into the same 401 as a bad signature, so probing
        # for configured sources reveals nothing.
        assert _settings(webhook_secrets={"stripe": "x"}).secret_for_source("nope") is None


class TestBounds:
    def test_max_attempts_must_allow_at_least_one_attempt(self) -> None:
        with pytest.raises(ValidationError):
            _settings(max_attempts=0)

    def test_timestamp_tolerance_must_be_positive(self) -> None:
        # A tolerance of 0 would reject every request, since some time always
        # passes between the provider signing and us verifying.
        with pytest.raises(ValidationError):
            _settings(signature_timestamp_tolerance_seconds=0)

    def test_poll_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _settings(poll_batch_size=0)

    def test_backoff_base_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _settings(backoff_base_seconds=0)

    def test_cap_below_base_is_rejected(self) -> None:
        # Otherwise min(cap, base * 2**n) would shorten the *first* retry rather
        # than bound the last one -- the opposite of what a cap is for.
        with pytest.raises(ValidationError, match="must be >="):
            _settings(backoff_base_seconds=10.0, backoff_cap_seconds=5.0)

    def test_cap_equal_to_base_is_allowed(self) -> None:
        settings = _settings(backoff_base_seconds=5.0, backoff_cap_seconds=5.0)
        assert settings.backoff_cap_seconds == 5.0


class TestStrictness:
    def test_unknown_key_is_rejected(self) -> None:
        # extra="forbid": a typo'd env var must not silently fall back to a default.
        with pytest.raises(ValidationError):
            _settings(bakcoff_base_seconds=2.0)

    def test_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Settings(admin_api_key="k", _env_file=None)  # database_url omitted on purpose

    def test_settings_are_immutable(self) -> None:
        settings = _settings()
        with pytest.raises(ValidationError):
            settings.max_attempts = 99  # type: ignore[misc]  # frozen model, asserting it stays frozen

    def test_non_postgres_dsn_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(database_url="mysql://user:pw@localhost/db")
