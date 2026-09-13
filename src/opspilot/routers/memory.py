"""Service memory read + correction endpoints.

v0.4 Phase 1 shipped the write path and the admin delete (a bad entry must
never be permanent). Phase 2 adds the read side: the per-service "what does
the agent know" listing the dashboard's memory view reads.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from opspilot.auth import require_api_key
from opspilot.db import get_db
from opspilot.memory import list_service_memory
from opspilot.models import Service, ServiceMemory
from opspilot.schemas import ServiceMemoryOut

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.get("/services/{name}/memory", response_model=list[ServiceMemoryOut])
def get_service_memory(name: str, db: Session = Depends(get_db)) -> list[ServiceMemory]:
    service = db.query(Service).filter_by(name=name).one_or_none()
    if service is None:
        raise HTTPException(status_code=404, detail=f"No service named '{name}'.")
    return list_service_memory(db, service.id)


@router.delete("/memory/{memory_id}", status_code=204)
def delete_memory_entry(memory_id: int, db: Session = Depends(get_db)) -> None:
    entry = db.query(ServiceMemory).filter_by(id=memory_id).one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No memory entry with id {memory_id}.")
    db.delete(entry)
    db.commit()
