"""FR-21: liveness and readiness.

The interesting assertion is the negative one -- `/readyz` must fail when the
database is unreachable, and `/healthz` must *not*. Getting that backwards means
a database blip restarts every app pod instead of draining traffic from them.

No Docker needed: a closed port is a genuinely unreachable database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from webhook_receiver.api.app import create_app
from webhook_receiver.config import Settings

# Port 1 is reserved and nothing listens there: connections are refused at once,
# so these tests are fast and deterministic rather than waiting on a timeout.
UNREACHABLE_DSN = "postgresql+asyncpg://user:pw@127.0.0.1:1/nodb"


def _settings(dsn: str) -> Settings:
    return Settings(
        database_url=dsn,
        admin_api_key="test-admin-key",
        _env_file=None,
    )


@pytest.fixture
async def client_without_database() -> AsyncIterator[AsyncClient]:
    app = create_app(_settings(UNREACHABLE_DSN))
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        # Enter the lifespan so app.state is populated, as in production.
        app.router.lifespan_context(app),
    ):
        yield client


class TestLiveness:
    async def test_healthz_is_200_even_with_no_database(
        self, client_without_database: AsyncClient
    ) -> None:
        # If this ever depends on the database, an outage becomes a restart loop.
        response = await client_without_database.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "alive"}


class TestReadiness:
    async def test_readyz_is_503_when_the_database_is_unreachable(
        self, client_without_database: AsyncClient
    ) -> None:
        response = await client_without_database.get("/readyz")

        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "database": "unreachable"}

    async def test_readyz_does_not_leak_the_dsn_or_password(
        self, client_without_database: AsyncClient
    ) -> None:
        # NFR-6: a probe response is often scraped and stored. Keep it boring.
        body = (await client_without_database.get("/readyz")).text

        assert "pw" not in body
        assert "127.0.0.1" not in body
