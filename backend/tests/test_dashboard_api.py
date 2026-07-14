import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.api.v1 import dashboard
from app.services.reservations import PropertyNotFoundError


class DashboardApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(dashboard.router, prefix="/api/v1")
        self.set_authenticated_tenant("tenant-a")

    def set_authenticated_tenant(self, tenant_id):
        self.app.dependency_overrides[dashboard.get_current_user] = (
            lambda: SimpleNamespace(tenant_id=tenant_id)
        )

    async def get(self, path):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)

    async def test_summary_requires_year_and_month(self):
        revenue_summary = AsyncMock()

        with patch.object(
            dashboard,
            "get_revenue_summary",
            revenue_summary,
        ):
            missing_year = await self.get(
                "/api/v1/dashboard/summary?property_id=prop-001&month=3"
            )
            missing_month = await self.get(
                "/api/v1/dashboard/summary?property_id=prop-001&year=2024"
            )

        self.assertEqual(missing_year.status_code, 422)
        self.assertEqual(missing_month.status_code, 422)
        revenue_summary.assert_not_awaited()

    async def test_summary_rejects_month_outside_calendar_range(self):
        revenue_summary = AsyncMock()

        with patch.object(
            dashboard,
            "get_revenue_summary",
            revenue_summary,
        ):
            response = await self.get(
                "/api/v1/dashboard/summary"
                "?property_id=prop-001&year=2024&month=13"
            )

        self.assertEqual(response.status_code, 422)
        revenue_summary.assert_not_awaited()

    async def test_summary_rejects_missing_or_blank_authenticated_tenant(self):
        for tenant_id in (None, "", "   "):
            with self.subTest(tenant_id=tenant_id):
                self.set_authenticated_tenant(tenant_id)
                revenue_summary = AsyncMock()

                with patch.object(
                    dashboard,
                    "get_revenue_summary",
                    revenue_summary,
                ):
                    response = await self.get(
                        "/api/v1/dashboard/summary"
                        "?property_id=prop-001&year=2024&month=3"
                    )

                self.assertEqual(response.status_code, 403)
                revenue_summary.assert_not_awaited()

    async def test_summary_maps_tenant_scoped_property_miss_to_404(self):
        property_miss = PropertyNotFoundError("property unavailable")
        revenue_summary = AsyncMock(side_effect=property_miss)

        with patch.object(
            dashboard,
            "get_revenue_summary",
            revenue_summary,
        ):
            response = await self.get(
                "/api/v1/dashboard/summary"
                "?property_id=prop-404&year=2024&month=3"
            )

        self.assertEqual(response.status_code, 404)
        revenue_summary.assert_awaited_once_with(
            "prop-404",
            "tenant-a",
            2024,
            3,
        )

    async def test_summary_preserves_fixed_decimal_period_and_timezone(self):
        revenue_summary = AsyncMock(
            return_value={
                "property_id": "prop-001",
                "tenant_id": "tenant-a",
                "year": 2024,
                "month": 3,
                "property_timezone": "Europe/Paris",
                "total": "2250.00",
                "currency": "USD",
                "count": 4,
            }
        )

        with patch.object(
            dashboard,
            "get_revenue_summary",
            revenue_summary,
        ):
            response = await self.get(
                "/api/v1/dashboard/summary"
                "?property_id=prop-001&year=2024&month=3"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "property_id": "prop-001",
                "year": 2024,
                "month": 3,
                "property_timezone": "Europe/Paris",
                "total_revenue": "2250.00",
                "currency": "USD",
                "reservations_count": 4,
            },
        )
        self.assertIsInstance(response.json()["total_revenue"], str)
        revenue_summary.assert_awaited_once_with(
            "prop-001",
            "tenant-a",
            2024,
            3,
        )

    async def test_properties_returns_only_authenticated_tenant_properties(self):
        tenant_properties = [
            {
                "id": "prop-001",
                "name": "Beach House Alpha",
                "timezone": "Europe/Paris",
            }
        ]
        property_list = AsyncMock(return_value=tenant_properties)

        with patch.object(
            dashboard,
            "list_properties",
            property_list,
            create=True,
        ):
            response = await self.get("/api/v1/dashboard/properties")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"properties": tenant_properties})
        property_list.assert_awaited_once_with("tenant-a")

if __name__ == "__main__":
    unittest.main()
