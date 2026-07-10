"""FR-21, happy path: `/readyz` reports ready against a real Postgres.

The 503 path is covered without Docker in tests/unit/test_health.py. Only the
200 path needs a server to actually answer `SELECT 1`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from webhook_receiver.api.app import create_app
from webhook_receiver.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(database_url: str) -> AsyncIterator[AsyncClient]:
    app = create_app(
        Settings(database_url=database_url, admin_api_key="test-admin-key", _env_file=None)
    )
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
        app.router.lifespan_context(app),
    ):
        yield http


async def test_readyz_is_200_when_the_database_answers(client: AsyncClient) -> None:
    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


async def test_healthz_is_200(client: AsyncClient) -> None:
    assert (await client.get("/healthz")).status_code == 200
