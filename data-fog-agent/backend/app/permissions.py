"""Who can see what. The UI, the agent and the MCP server all use these functions."""

from sqlalchemy import Select, select

from app.models import Asset, AssetPermission, User, UserGroup


def visible_asset_ids(user: User) -> Select:
    """Ids of the assets the user may see. Use as `.where(Asset.id.in_(visible_asset_ids(u)))`.

    Admins see every asset (row-level security already limits that to their org).
    Members see assets shared with one of their groups. Assets with no groups are admin-only.
    """
    if user.role == "admin":
        return select(Asset.id)
    return (
        select(AssetPermission.asset_id)
        .join(UserGroup, UserGroup.group_id == AssetPermission.group_id)
        .where(UserGroup.user_id == user.id)
    )


def can_review(user: User, asset: Asset) -> bool:
    """Admins approve anything. Owners approve proposals for their own assets."""
    return user.role == "admin" or asset.owner_user_id == user.id
