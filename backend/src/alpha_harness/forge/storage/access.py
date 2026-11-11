"""Recording that an Alpha's daily PnL was read, so :mod:`.cleanup` knows it is not idle.

Call sites are every place that reads ``alpha_pnl`` for a specific Alpha (a chart, a
correlation sweep): ``api/vault.py``'s single-alpha PnL endpoint, ``api/portfolio.py`` and
``api/lab_tasks.py``'s bulk correlation reads (both upstream, each with a one-line ``# forge:``
touch), and the Forge-owned Nuggets/Analytics screens.
"""

from typing import TYPE_CHECKING

from ...db.models import utcnow
from .models import StorageAccess

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...db.sqlite import Database


async def touch_pnl(db: Database, alpha_ids: Sequence[str]) -> None:
    """Mark these Alphas' PnL as accessed now. Safe to call with duplicates or an empty list."""
    unique = {a for a in alpha_ids if a}
    if not unique:
        return
    now = utcnow()
    async with db.session() as session:
        for alpha_id in unique:
            await session.merge(StorageAccess(alpha_id=alpha_id, last_accessed_at=now))
