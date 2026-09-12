"""批次、可用标范围与标签领用路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Batch, LabelIssue, Parcel
from ..schemas import (
    BatchCreate,
    BatchOut,
    EligibilityOut,
    LabelIssueRequest,
    LabelIssueOut,
    LabelListOut,
)
from ..services import BusinessError, eligibility_payload, issue_labels

router = APIRouter(tags=["批次与用标"])


@router.post("/batches", response_model=BatchOut, status_code=201, summary="登记生产批次")
def create_batch(payload: BatchCreate, db: Session = Depends(get_db)):
    parcel = db.get(Parcel, payload.parcel_id)
    if parcel is None:
        raise HTTPException(404, f"地块 {payload.parcel_id} 不存在")
    if db.execute(select(Batch).where(Batch.batch_no == payload.batch_no)).scalar_one_or_none():
        raise HTTPException(409, f"批次号 {payload.batch_no} 已存在")
    batch = Batch(**payload.model_dump())
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/batches", response_model=list[BatchOut], summary="批次清单")
def list_batches(db: Session = Depends(get_db)):
    return db.execute(select(Batch).order_by(Batch.id)).scalars().all()


@router.get("/batches/{batch_id}/eligibility", response_model=EligibilityOut, summary="查询可用标范围")
def batch_eligibility(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "批次不存在")
    return eligibility_payload(db, batch)


@router.post("/batches/{batch_id}/issue-labels", response_model=LabelIssueOut, summary="领用标签（受额度/资格/巡查约束）")
def batch_issue(batch_id: int, payload: LabelIssueRequest):
    """用独立 Session 开启单写事务；并发最后额度由 BEGIN IMMEDIATE / FOR UPDATE 串行化。"""
    db = SessionLocal()
    try:
        record = issue_labels(db, batch_id, payload.quantity, payload.operator)
        return LabelIssueOut(
            id=record.id,
            label_code=record.label_code,
            batch_id=record.batch_id,
            batch_no=db.get(Batch, record.batch_id).batch_no,
            quantity=record.quantity,
            auth_id=record.auth_id,
            report_id=record.report_id,
            issued_at=record.issued_at,
            operator=record.operator,
        )
    except BusinessError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})
    finally:
        db.close()


@router.get("/labels", response_model=list[LabelListOut], summary="标签发到了哪些批次（用标清单）")
def list_labels(batch_id: int | None = None, db: Session = Depends(get_db)):
    stmt = (
        select(LabelIssue, Batch)
        .join(Batch, LabelIssue.batch_id == Batch.id)
        .order_by(LabelIssue.id)
    )
    if batch_id is not None:
        stmt = stmt.where(LabelIssue.batch_id == batch_id)
    rows = db.execute(stmt).all()
    return [
        LabelListOut(
            id=rec.id,
            label_code=rec.label_code,
            batch_id=rec.batch_id,
            batch_no=b.batch_no,
            quantity=rec.quantity,
            auth_id=rec.auth_id,
            report_id=rec.report_id,
            issued_at=rec.issued_at,
            operator=rec.operator,
            cooperative=b.cooperative,
            variety=b.variety,
        )
        for rec, b in rows
    ]
