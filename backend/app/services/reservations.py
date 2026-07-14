from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any
from zoneinfo import ZoneInfo

from app.core.database_pool import db_pool


class PropertyNotFoundError(Exception):
    """Raised when a property is not available within the requested tenant."""


class RevenueCurrencyError(Exception):
    """Raised when one revenue report would combine different currencies."""


def month_utc_bounds(
    year: int,
    month: int,
    property_timezone: str,
) -> tuple[datetime, datetime]:
    """Return the property's local calendar month as a half-open UTC range."""
    local_timezone = ZoneInfo(property_timezone)
    start_local = datetime(year, month, 1, tzinfo=local_timezone)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, tzinfo=local_timezone)
    else:
        end_local = datetime(year, month + 1, 1, tzinfo=local_timezone)

    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def format_currency_amount(amount: Decimal) -> str:
    """Round an exact aggregate once and format it with two decimal places."""
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}"


async def calculate_monthly_revenue(
    property_id: str,
    tenant_id: str,
    year: int,
    month: int,
) -> Dict[str, Any]:
    """Aggregate one property's revenue for its local calendar month."""
    await db_pool.initialize()

    async with db_pool.get_session() as session:
        from sqlalchemy import text

        property_result = await session.execute(
            text("""
                SELECT id, timezone
                FROM properties
                WHERE id = :property_id AND tenant_id = :tenant_id
            """),
            {
                "property_id": property_id,
                "tenant_id": tenant_id,
            },
        )
        property_row = property_result.fetchone()
        if property_row is None:
            raise PropertyNotFoundError(
                f"Property {property_id!r} was not found for tenant {tenant_id!r}"
            )

        property_timezone = property_row.timezone
        start_utc, end_utc = month_utc_bounds(
            year,
            month,
            property_timezone,
        )
        revenue_result = await session.execute(
            text("""
                SELECT
                    currency,
                    SUM(total_amount) AS total_revenue,
                    COUNT(*) AS reservation_count
                FROM reservations
                WHERE property_id = :property_id
                    AND tenant_id = :tenant_id
                    AND check_in_date >= :start_utc
                    AND check_in_date < :end_utc
                GROUP BY currency
            """),
            {
                "property_id": property_id,
                "tenant_id": tenant_id,
                "start_utc": start_utc,
                "end_utc": end_utc,
            },
        )
        rows = revenue_result.fetchall()

        if len(rows) > 1:
            currencies = ", ".join(sorted(str(row.currency) for row in rows))
            raise RevenueCurrencyError(
                "Monthly revenue contains multiple currencies: "
                f"{currencies}"
            )

        if rows:
            row = rows[0]
            total = format_currency_amount(Decimal(str(row.total_revenue)))
            currency = row.currency
            count = row.reservation_count
        else:
            total = "0.00"
            currency = "USD"
            count = 0

        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "year": year,
            "month": month,
            "property_timezone": property_timezone,
            "total": total,
            "currency": currency,
            "count": count,
        }


async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    await db_pool.initialize()

    async with db_pool.get_session() as session:
        # Use SQLAlchemy text for raw SQL
        from sqlalchemy import text

        query = text("""
            SELECT
                property_id,
                SUM(total_amount) as total_revenue,
                COUNT(*) as reservation_count
            FROM reservations
            WHERE property_id = :property_id AND tenant_id = :tenant_id
            GROUP BY property_id
        """)

        result = await session.execute(query, {
            "property_id": property_id,
            "tenant_id": tenant_id
        })
        row = result.fetchone()

        if row:
            total_revenue = Decimal(str(row.total_revenue))
            return {
                "property_id": property_id,
                "tenant_id": tenant_id,
                "total": str(total_revenue),
                "currency": "USD",
                "count": row.reservation_count
            }

        # No reservations found for this property
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": "0.00",
            "currency": "USD",
            "count": 0
        }
