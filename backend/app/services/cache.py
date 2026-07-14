import json
import redis.asyncio as redis
from typing import Dict, Any
import os

# Initialize Redis client (typically configured centrally).
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

async def get_revenue_summary(
    property_id: str,
    tenant_id: str,
    year: int,
    month: int,
) -> Dict[str, Any]:
    """
    Fetches revenue summary, utilizing caching to improve performance.
    """
    cache_key = (
        f"revenue:v2:{tenant_id}:{property_id}:{year}:{month:02d}"
    )
    
    # Try to get from cache
    cached = await redis_client.get(cache_key)
    if cached:
        try:
            cached_result = json.loads(cached)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            cached_result = None

        expected_metadata = {
            "tenant_id": tenant_id,
            "property_id": property_id,
            "year": year,
            "month": month,
        }
        if isinstance(cached_result, dict) and all(
            field in cached_result
            and type(cached_result[field]) is type(expected_value)
            and cached_result[field] == expected_value
            for field, expected_value in expected_metadata.items()
        ):
            return cached_result
    
    # Revenue calculation is delegated to the reservation service.
    from app.services.reservations import calculate_monthly_revenue
    
    # Calculate revenue
    result = await calculate_monthly_revenue(
        property_id=property_id,
        tenant_id=tenant_id,
        year=year,
        month=month,
    )
    
    # Cache the result for 5 minutes
    await redis_client.setex(cache_key, 300, json.dumps(result))
    
    return result
