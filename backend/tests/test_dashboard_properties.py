import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, query, params):
        self.calls.append((" ".join(str(query).lower().split()), params))
        return FakeResult(self.rows)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class DashboardPropertyTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_properties_filters_by_authenticated_tenant(self):
        try:
            properties = importlib.import_module("app.services.properties")
        except ModuleNotFoundError:
            self.fail("tenant-scoped property service is missing")

        session = FakeSession(
            [
                SimpleNamespace(
                    id="prop-004",
                    name="Lakeside Cottage",
                    timezone="America/New_York",
                )
            ]
        )

        with (
            patch.object(properties.db_pool, "initialize", AsyncMock()),
            patch.object(
                properties.db_pool,
                "get_session",
                Mock(return_value=FakeSessionContext(session)),
            ),
        ):
            result = await properties.list_properties("tenant-b")

        self.assertEqual(
            result,
            [
                {
                    "id": "prop-004",
                    "name": "Lakeside Cottage",
                    "timezone": "America/New_York",
                }
            ],
        )
        query, params = session.calls[0]
        self.assertIn("where tenant_id = :tenant_id", query)
        self.assertEqual(params, {"tenant_id": "tenant-b"})


if __name__ == "__main__":
    unittest.main()
