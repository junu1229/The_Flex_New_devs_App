from typing import Any, Dict, List

from app.core.database_pool import db_pool


async def list_properties(tenant_id: str) -> List[Dict[str, Any]]:
    """Return only properties owned by the authenticated tenant."""
    await db_pool.initialize()

    async with db_pool.get_session() as session:
        from sqlalchemy import text

        result = await session.execute(
            text("""
                SELECT id, name, timezone
                FROM properties
                WHERE tenant_id = :tenant_id
                ORDER BY name, id
            """),
            {"tenant_id": tenant_id},
        )
        return [
            {
                "id": row.id,
                "name": row.name,
                "timezone": row.timezone,
            }
            for row in result.fetchall()
        ]
