"""异常巡查路由：暂停新增用标（已发记录保留），整改复核后解除。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Batch, InspectionEvent
from ..schemas import InspectionCreate, InspectionOut, InspectionResolve

router = APIRouter(tags=["异常巡查"])


@router.post("/inspections", response_model=InspectionOut, status_code=201, summary="登记异常巡查（暂停新增用标）")
def create_inspection(payload: InspectionCreate, db: Session = Depends(get_db)):
    if payload.batch_id is None and not payload.cooperative:
        raise HTTPException(422, "必须指定批次或合作社之一")
    if payload.batch_id is not None and db.get(Batch, payload.batch_id) is None:
        raise HTTPException(404, f"批次 {payload.batch_id} 不存在")
    if db.execute(select(InspectionEvent).where(InspectionEvent.event_no == payload.event_no)).scalar_one_or_none():
        raise HTTPException(409, f"巡查事件 {payload.event_no} 已存在")
    event = InspectionEvent(**payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.post("/inspections/{event_id}/resolve", response_model=InspectionOut, summary="整改复核通过，解除暂停")
def resolve_inspection(event_id: int, payload: InspectionResolve, db: Session = Depends(get_db)):
    event = db.get(InspectionEvent, event_id)
    if event is None:
        raise HTTPException(404, "巡查事件不存在")
    event.active = False
    event.resolved_note = payload.note
    event.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(event)
    return event


@router.get("/inspections", response_model=list[InspectionOut], summary="巡查事件清单")
def list_inspections(active_only: bool = False, db: Session = Depends(get_db)):
    stmt = select(InspectionEvent).order_by(InspectionEvent.id.desc())
    if active_only:
        stmt = stmt.where(InspectionEvent.active.is_(True))
    return db.execute(stmt).scalars().all()
