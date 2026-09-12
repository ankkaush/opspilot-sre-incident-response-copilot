"""Admin correction endpoint for service memory (v0.4 Phase 1).

The blueprint's Security requirement for this phase is narrow: a way to
remove a wrong entry, gated the same as every other write in this system.
Retrieval into the agent, the per-service "what does it know" dashboard
view, and a richer correction UI are v0.4 Phase 2's job — this phase only
needs the delete path so a bad memory row is never permanent.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from opspilot.auth import require_api_key
from opspilot.db import get_db
from opspilot.models import ServiceMemory

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.delete("/memory/{memory_id}", status_code=204)
def delete_memory_entry(memory_id: int, db: Session = Depends(get_db)) -> None:
    entry = db.query(ServiceMemory).filter_by(id=memory_id).one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No memory entry with id {memory_id}.")
    db.delete(entry)
    db.commit()
