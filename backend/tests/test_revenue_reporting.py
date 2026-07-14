import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.services import reservations


class FakeResult:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = [] if rows is None else rows

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeSession:
    def __init__(self, results=None, error=None):
        self._results = list(results or [])
        self._error = error
        self.calls = []

    async def execute(self, query, params):
        self.calls.append((str(query), params))
        if self._error is not None:
            raise self._error
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def normalized_sql(query):
    return " ".join(query.lower().split())


class RevenueTimeBoundsTests(unittest.TestCase):
    def test_march_bounds_follow_paris_daylight_saving_time(self):
        start, end = reservations.month_utc_bounds(
            2024,
            3,
            "Europe/Paris",
        )

        self.assertEqual(start, datetime(2024, 2, 29, 23, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2024, 3, 31, 22, tzinfo=timezone.utc))
        self.assertIs(start.tzinfo, timezone.utc)
        self.assertIs(end.tzinfo, timezone.utc)

    def test_march_bounds_follow_new_york_daylight_saving_time(self):
        start, end = reservations.month_utc_bounds(
            2024,
            3,
            "America/New_York",
        )

        self.assertEqual(start, datetime(2024, 3, 1, 5, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2024, 4, 1, 4, tzinfo=timezone.utc))


class CurrencyFormattingTests(unittest.TestCase):
    def test_half_cent_rounds_up_to_two_decimal_places(self):
        self.assertEqual(
            reservations.format_currency_amount(Decimal("1.005")),
            "1.01",
        )

    def test_exact_aggregate_is_rounded_only_after_summing(self):
        exact_total = sum(
            (Decimal("333.333"), Decimal("333.333"), Decimal("333.334")),
            start=Decimal("0"),
        )

        self.assertEqual(exact_total, Decimal("1000.000"))
        self.assertEqual(
            reservations.format_currency_amount(exact_total),
            "1000.00",
        )


class MonthlyRevenueTests(unittest.IsolatedAsyncioTestCase):
    async def call_with_session(self, session, *, year=2024, month=3):
        initialize = AsyncMock()
        get_session = Mock(return_value=FakeSessionContext(session))

        with (
            patch.object(reservations.db_pool, "initialize", initialize),
            patch.object(reservations.db_pool, "get_session", get_session),
        ):
            result = await reservations.calculate_monthly_revenue(
                "prop-001",
                "tenant-a",
                year,
                month,
            )

        initialize.assert_awaited_once_with()
        get_session.assert_called_once_with()
        return result

    async def test_march_report_uses_tenant_scoped_property_and_utc_half_open_window(self):
        session = FakeSession(
            [
                FakeResult(
                    row=SimpleNamespace(
                        id="prop-001",
                        timezone="Europe/Paris",
                    )
                ),
                FakeResult(
                    rows=[
                        SimpleNamespace(
                            currency="USD",
                            total_revenue=Decimal("2250.000"),
                            reservation_count=4,
                        )
                    ]
                ),
            ]
        )

        result = await self.call_with_session(session)

        self.assertEqual(
            result,
            {
                "property_id": "prop-001",
                "tenant_id": "tenant-a",
                "year": 2024,
                "month": 3,
                "property_timezone": "Europe/Paris",
                "total": "2250.00",
                "currency": "USD",
                "count": 4,
            },
        )
        self.assertEqual(len(session.calls), 2)

        property_sql, property_params = session.calls[0]
        self.assertIn("from properties", normalized_sql(property_sql))
        self.assertIn("id = :property_id", normalized_sql(property_sql))
        self.assertIn("tenant_id = :tenant_id", normalized_sql(property_sql))
        self.assertEqual(
            property_params,
            {"property_id": "prop-001", "tenant_id": "tenant-a"},
        )

        revenue_sql, revenue_params = session.calls[1]
        revenue_sql = normalized_sql(revenue_sql)
        self.assertIn("sum(total_amount)", revenue_sql)
        self.assertIn("group by currency", revenue_sql)
        self.assertIn("property_id = :property_id", revenue_sql)
        self.assertIn("tenant_id = :tenant_id", revenue_sql)
        self.assertIn("check_in_date >= :start_utc", revenue_sql)
        self.assertIn("check_in_date < :end_utc", revenue_sql)
        self.assertEqual(
            revenue_params,
            {
                "property_id": "prop-001",
                "tenant_id": "tenant-a",
                "start_utc": datetime(
                    2024,
                    2,
                    29,
                    23,
                    tzinfo=timezone.utc,
                ),
                "end_utc": datetime(
                    2024,
                    3,
                    31,
                    22,
                    tzinfo=timezone.utc,
                ),
            },
        )

    async def test_april_report_returns_schema_default_for_no_reservations(self):
        session = FakeSession(
            [
                FakeResult(
                    row=SimpleNamespace(
                        id="prop-001",
                        timezone="Europe/Paris",
                    )
                ),
                FakeResult(rows=[]),
            ]
        )

        result = await self.call_with_session(session, month=4)

        self.assertEqual(
            result,
            {
                "property_id": "prop-001",
                "tenant_id": "tenant-a",
                "year": 2024,
                "month": 4,
                "property_timezone": "Europe/Paris",
                "total": "0.00",
                "currency": "USD",
                "count": 0,
            },
        )
        self.assertEqual(
            session.calls[1][1]["start_utc"],
            datetime(2024, 3, 31, 22, tzinfo=timezone.utc),
        )
        self.assertEqual(
            session.calls[1][1]["end_utc"],
            datetime(2024, 4, 30, 22, tzinfo=timezone.utc),
        )

    async def test_property_missing_for_tenant_fails_before_reservation_query(self):
        session = FakeSession([FakeResult(row=None)])
        initialize = AsyncMock()
        get_session = Mock(return_value=FakeSessionContext(session))

        with (
            patch.object(reservations.db_pool, "initialize", initialize),
            patch.object(reservations.db_pool, "get_session", get_session),
        ):
            with self.assertRaises(reservations.PropertyNotFoundError) as raised:
                await reservations.calculate_monthly_revenue(
                    "prop-001",
                    "tenant-without-this-property",
                    2024,
                    3,
                )

        self.assertIn("prop-001", str(raised.exception))
        self.assertIn("tenant-without-this-property", str(raised.exception))
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(
            session.calls[0][1],
            {
                "property_id": "prop-001",
                "tenant_id": "tenant-without-this-property",
            },
        )

    async def test_mixed_currency_aggregate_fails_closed(self):
        session = FakeSession(
            [
                FakeResult(
                    row=SimpleNamespace(
                        id="prop-001",
                        timezone="Europe/Paris",
                    )
                ),
                FakeResult(
                    rows=[
                        SimpleNamespace(
                            currency="EUR",
                            total_revenue=Decimal("20.000"),
                            reservation_count=1,
                        ),
                        SimpleNamespace(
                            currency="USD",
                            total_revenue=Decimal("10.000"),
                            reservation_count=1,
                        ),
                    ]
                ),
            ]
        )

        with self.assertRaises(reservations.RevenueCurrencyError) as raised:
            await self.call_with_session(session)

        message = str(raised.exception)
        self.assertIn("multiple currencies", message.lower())
        self.assertIn("EUR", message)
        self.assertIn("USD", message)

    async def test_database_initialization_error_propagates(self):
        database_error = RuntimeError("database unavailable")
        initialize = AsyncMock(side_effect=database_error)
        get_session = Mock()

        with (
            patch.object(reservations.db_pool, "initialize", initialize),
            patch.object(reservations.db_pool, "get_session", get_session),
        ):
            with self.assertRaises(RuntimeError) as raised:
                await reservations.calculate_monthly_revenue(
                    "prop-001",
                    "tenant-a",
                    2024,
                    3,
                )

        self.assertIs(raised.exception, database_error)
        get_session.assert_not_called()

    async def test_session_error_propagates(self):
        database_error = RuntimeError("query failed")
        session = FakeSession(error=database_error)

        with self.assertRaises(RuntimeError) as raised:
            await self.call_with_session(session)

        self.assertIs(raised.exception, database_error)

    async def test_aggregation_query_error_propagates(self):
        database_error = RuntimeError("aggregation failed")
        session = FakeSession(
            [
                FakeResult(
                    row=SimpleNamespace(
                        id="prop-001",
                        timezone="Europe/Paris",
                    )
                ),
                database_error,
            ]
        )

        with self.assertRaises(RuntimeError) as raised:
            await self.call_with_session(session)

        self.assertIs(raised.exception, database_error)
        self.assertEqual(len(session.calls), 2)


if __name__ == "__main__":
    unittest.main()
