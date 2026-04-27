from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth_utils.auth_func import get_current_user
from database import get_async_session
from utils.page_permissions import get_user_permission_names

from cognilabsai.tables import COGNILABSAI_CHAT_PERMISSION, COGNILABSAI_INTEGRATIONS_PERMISSION


def require_cognilabsai_permission(permission_name: str):
    async def dependency(
        current_user=Depends(get_current_user),
        session: AsyncSession = Depends(get_async_session),
    ):
        permissions = set(await get_user_permission_names(session, current_user.id))
        if permission_name not in permissions and not getattr(current_user, "is_superuser", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission_name}",
            )
        return current_user

    return dependency


require_cognilabsai_chat = require_cognilabsai_permission(COGNILABSAI_CHAT_PERMISSION)
require_cognilabsai_integrations = require_cognilabsai_permission(COGNILABSAI_INTEGRATIONS_PERMISSION)

