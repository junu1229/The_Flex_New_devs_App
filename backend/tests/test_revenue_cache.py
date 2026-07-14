import json
import unittest
from unittest.mock import AsyncMock, call, patch

from app.services import cache, reservations


class FakeRedis:
    def __init__(self, values=None, *, setex_error=None):
        self.values = dict(values or {})
        self.setex_error = setex_error
        self.get_calls = []
        self.setex_calls = []

    async def get(self, key):
        self.get_calls.append(key)
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        if self.setex_error is not None:
            raise self.setex_error
        self.values[key] = value
        return True


def revenue_result(
    tenant_id,
    month,
    *,
    total,
    count,
    year=2024,
    property_id="prop-001",
):
    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "year": year,
        "month": month,
        "property_timezone": "Europe/Paris",
        "total": total,
        "currency": "USD",
        "count": count,
    }


class RevenueCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_property_is_isolated_for_both_tenant_call_orders(self):
        tenant_results = {
            "tenant-a": revenue_result(
                "tenant-a",
                3,
                total="101.10",
                count=1,
            ),
            "tenant-b": revenue_result(
                "tenant-b",
                3,
                total="202.20",
                count=2,
            ),
        }

        for tenant_order in (
            ("tenant-a", "tenant-b"),
            ("tenant-b", "tenant-a"),
        ):
            with self.subTest(tenant_order=tenant_order):
                fake_redis = FakeRedis()

                async def calculate(*, property_id, tenant_id, year, month):
                    self.assertEqual(property_id, "prop-001")
                    self.assertEqual(year, 2024)
                    self.assertEqual(month, 3)
                    return tenant_results[tenant_id]

                calculator = AsyncMock(side_effect=calculate)
                with (
                    patch.object(cache, "redis_client", fake_redis),
                    patch.object(
                        reservations,
                        "calculate_monthly_revenue",
                        calculator,
                    ),
                ):
                    requested_tenants = tenant_order + tenant_order
                    observed = [
                        await cache.get_revenue_summary(
                            "prop-001",
                            tenant_id,
                            2024,
                            3,
                        )
                        for tenant_id in requested_tenants
                    ]

                self.assertEqual(
                    observed,
                    [
                        tenant_results[tenant_id]
                        for tenant_id in requested_tenants
                    ],
                )
                self.assertEqual(
                    calculator.await_args_list,
                    [
                        call(
                            property_id="prop-001",
                            tenant_id=tenant_id,
                            year=2024,
                            month=3,
                        )
                        for tenant_id in tenant_order
                    ],
                )

    async def test_march_and_april_reports_do_not_share_a_cache_entry(self):
        fake_redis = FakeRedis()

        async def calculate(*, property_id, tenant_id, year, month):
            return revenue_result(
                tenant_id,
                month,
                total="300.00" if month == 3 else "400.00",
                count=month,
                year=year,
                property_id=property_id,
            )

        calculator = AsyncMock(side_effect=calculate)
        with (
            patch.object(cache, "redis_client", fake_redis),
            patch.object(
                reservations,
                "calculate_monthly_revenue",
                calculator,
            ),
        ):
            march = await cache.get_revenue_summary(
                "prop-001",
                "tenant-a",
                2024,
                3,
            )
            april = await cache.get_revenue_summary(
                "prop-001",
                "tenant-a",
                2024,
                4,
            )
            march_again = await cache.get_revenue_summary(
                "prop-001",
                "tenant-a",
                2024,
                3,
            )
            april_again = await cache.get_revenue_summary(
                "prop-001",
                "tenant-a",
                2024,
                4,
            )

        self.assertEqual(march["month"], 3)
        self.assertEqual(march["total"], "300.00")
        self.assertEqual(april["month"], 4)
        self.assertEqual(april["total"], "400.00")
        self.assertEqual(march_again, march)
        self.assertEqual(april_again, april)
        self.assertEqual(
            calculator.await_args_list,
            [
                call(
                    property_id="prop-001",
                    tenant_id="tenant-a",
                    year=2024,
                    month=3,
                ),
                call(
                    property_id="prop-001",
                    tenant_id="tenant-a",
                    year=2024,
                    month=4,
                ),
            ],
        )

    async def test_exact_repeat_is_returned_from_cache_without_recalculation(self):
        fake_redis = FakeRedis()
        expected = revenue_result(
            "tenant-a",
            3,
            total="1234.50",
            count=7,
        )
        calculator = AsyncMock(return_value=expected)

        with (
            patch.object(cache, "redis_client", fake_redis),
            patch.object(
                reservations,
                "calculate_monthly_revenue",
                calculator,
            ),
        ):
            first = await cache.get_revenue_summary(
                "prop-001",
                "tenant-a",
                2024,
                3,
            )
            second = await cache.get_revenue_summary(
                "prop-001",
                "tenant-a",
                2024,
                3,
            )

        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        calculator.assert_awaited_once_with(
            property_id="prop-001",
            tenant_id="tenant-a",
            year=2024,
            month=3,
        )

    async def test_cache_uses_v2_period_key_and_five_minute_ttl(self):
        fake_redis = FakeRedis()
        expected = revenue_result(
            "tenant-a",
            3,
            total="55.50",
            count=5,
        )
        calculator = AsyncMock(return_value=expected)

        with (
            patch.object(cache, "redis_client", fake_redis),
            patch.object(
                reservations,
                "calculate_monthly_revenue",
                calculator,
            ),
        ):
            result = await cache.get_revenue_summary(
                "prop-001",
                "tenant-a",
                2024,
                3,
            )

        expected_key = "revenue:v2:tenant-a:prop-001:2024:03"
        self.assertEqual(result, expected)
        self.assertEqual(fake_redis.get_calls, [expected_key])
        self.assertEqual(len(fake_redis.setex_calls), 1)
        key, ttl, payload = fake_redis.setex_calls[0]
        self.assertEqual(key, expected_key)
        self.assertEqual(ttl, 300)
        self.assertEqual(json.loads(payload), expected)

    async def test_untrusted_cached_payloads_are_recomputed_and_overwritten(self):
        expected_key = "revenue:v2:tenant-a:prop-001:2024:03"
        trusted = revenue_result(
            "tenant-a",
            3,
            total="900.00",
            count=9,
        )
        mismatched_payloads = {}
        for field, value in (
            ("tenant_id", "tenant-b"),
            ("property_id", "prop-002"),
            ("year", 2023),
            ("month", 4),
        ):
            payload = dict(trusted)
            payload[field] = value
            mismatched_payloads[f"{field} mismatch"] = json.dumps(payload)

        missing_month = dict(trusted)
        missing_month.pop("month")

        untrusted_payloads = {
            **mismatched_payloads,
            "metadata missing": json.dumps(missing_month),
            "malformed json": "{not-valid-json",
        }
        for name, untrusted_payload in untrusted_payloads.items():
            with self.subTest(name=name):
                fake_redis = FakeRedis({expected_key: untrusted_payload})
                calculator = AsyncMock(return_value=trusted)

                with (
                    patch.object(cache, "redis_client", fake_redis),
                    patch.object(
                        reservations,
                        "calculate_monthly_revenue",
                        calculator,
                    ),
                ):
                    result = await cache.get_revenue_summary(
                        "prop-001",
                        "tenant-a",
                        2024,
                        3,
                    )

                self.assertEqual(result, trusted)
                calculator.assert_awaited_once_with(
                    property_id="prop-001",
                    tenant_id="tenant-a",
                    year=2024,
                    month=3,
                )
                self.assertEqual(len(fake_redis.setex_calls), 1)
                key, ttl, replacement = fake_redis.setex_calls[0]
                self.assertEqual((key, ttl), (expected_key, 300))
                self.assertEqual(json.loads(replacement), trusted)
                self.assertEqual(json.loads(fake_redis.values[expected_key]), trusted)

    async def test_calculator_error_propagates_without_caching(self):
        fake_redis = FakeRedis()
        calculator_error = RuntimeError("calculation failed")
        calculator = AsyncMock(side_effect=calculator_error)

        with (
            patch.object(cache, "redis_client", fake_redis),
            patch.object(
                reservations,
                "calculate_monthly_revenue",
                calculator,
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                await cache.get_revenue_summary(
                    "prop-001",
                    "tenant-a",
                    2024,
                    3,
                )

        self.assertIs(raised.exception, calculator_error)
        self.assertEqual(fake_redis.setex_calls, [])
        self.assertEqual(fake_redis.values, {})

    async def test_redis_write_error_propagates(self):
        redis_error = RuntimeError("redis write failed")
        fake_redis = FakeRedis(setex_error=redis_error)
        expected = revenue_result(
            "tenant-a",
            3,
            total="77.70",
            count=7,
        )
        calculator = AsyncMock(return_value=expected)

        with (
            patch.object(cache, "redis_client", fake_redis),
            patch.object(
                reservations,
                "calculate_monthly_revenue",
                calculator,
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                await cache.get_revenue_summary(
                    "prop-001",
                    "tenant-a",
                    2024,
                    3,
                )

        self.assertIs(raised.exception, redis_error)
        calculator.assert_awaited_once_with(
            property_id="prop-001",
            tenant_id="tenant-a",
            year=2024,
            month=3,
        )


if __name__ == "__main__":
    unittest.main()
