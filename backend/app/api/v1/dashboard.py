from typing import Any, Dict, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.auth import authenticate_request as get_current_user
from app.services.cache import get_revenue_summary
from app.services.properties import list_properties
from app.services.reservations import PropertyNotFoundError

router = APIRouter()


def _require_tenant_id(current_user: Any) -> str:
    tenant_id = getattr(current_user, "tenant_id", None)
    if tenant_id is None and isinstance(current_user, Mapping):
        tenant_id = current_user.get("tenant_id")

    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated tenant is required",
        )

    return tenant_id.strip()


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    year: int = Query(..., ge=1, le=9999),
    month: int = Query(..., ge=1, le=12),
    current_user: Any = Depends(get_current_user),
) -> Dict[str, Any]:
    tenant_id = _require_tenant_id(current_user)

    try:
        revenue_data = await get_revenue_summary(
            property_id,
            tenant_id,
            year,
            month,
        )
    except PropertyNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return {
        "property_id": revenue_data["property_id"],
        "year": revenue_data["year"],
        "month": revenue_data["month"],
        "property_timezone": revenue_data["property_timezone"],
        "total_revenue": revenue_data["total"],
        "currency": revenue_data["currency"],
        "reservations_count": revenue_data["count"],
    }


@router.get("/dashboard/properties")
async def get_dashboard_properties(
    current_user: Any = Depends(get_current_user),
) -> Dict[str, Any]:
    tenant_id = _require_tenant_id(current_user)
    return {"properties": await list_properties(tenant_id)}
