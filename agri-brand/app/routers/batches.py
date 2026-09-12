"""批次、可用标范围、标签领用与标签生命周期路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import Batch, BatchQuota, LabelIssue
from ..schemas import (
    BatchCreate,
    BatchOut,
    EligibilityOut,
    LabelIssueExtended,
    LabelIssueRequest,
    LabelIssueOut,
    LabelListOut,
    LegacyImportRequest,
    MergeRequest,
    RecallRequest,
    SplitRequest,
    TransferRequest,
    UseRequest,
)
from ..services import (
    BusinessError,
    eligibility_payload,
    import_legacy_labels,
    issue_labels,
    mark_label_used,
    merge_batches,
    recall_labels,
    split_batch,
    transfer_label,
)

router = APIRouter(tags=["批次与用标"])


def _batch_or_404(db: Session, batch_id: int) -> Batch:
    b = db.get(Batch, batch_id)
    if b is None:
        raise HTTPException(404, "批次不存在")
    return b


@router.post("/batches", response_model=BatchOut, status_code=201, summary="登记生产批次")
def create_batch(payload: BatchCreate, db: Session = Depends(get_db)):
    from ..models import Parcel

    if payload.parcel_id is not None and db.get(Parcel, payload.parcel_id) is None:
        raise HTTPException(404, f"地块 {payload.parcel_id} 不存在")
    if db.execute(select(Batch).where(Batch.batch_no == payload.batch_no)).scalar_one_or_none():
        raise HTTPException(409, f"批次号 {payload.batch_no} 已存在")
    batch = Batch(**payload.model_dump())
    db.add(batch)
    db.flush()
    # 原始授权额度 = 可追溯产量（重划界后的替代额度由处置草案登记）
    parcel = db.get(Parcel, batch.parcel_id) if batch.parcel_id else None
    db.add(BatchQuota(
        batch_id=batch.id, basis="original",
        pa_version_id=parcel.pa_version_id if parcel else 1,
        amount=batch.traceable_output, note="登记批次时按可追溯产量设定",
    ))
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/batches", response_model=list[BatchOut], summary="批次清单")
def list_batches(db: Session = Depends(get_db)):
    return db.execute(select(Batch).order_by(Batch.harvest_date, Batch.id)).scalars().all()


@router.get("/batches/{batch_id}/eligibility", response_model=EligibilityOut, summary="查询可用标范围")
def batch_eligibility(batch_id: int, db: Session = Depends(get_db)):
    return eligibility_payload(db, _batch_or_404(db, batch_id))


@router.post("/batches/{batch_id}/issue-labels", response_model=LabelIssueOut, summary="领用标签（受额度/资格/巡查约束）")
def batch_issue(batch_id: int, payload: LabelIssueRequest):
    """独立 Session 开写事务；并发最后额度由 BEGIN IMMEDIATE / FOR UPDATE 串行化。"""
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


@router.post("/batches/{batch_id}/import-legacy", summary="导入划界前已生产批次的存量标签（历史事实）")
def batch_import_legacy(batch_id: int, payload: LegacyImportRequest):
    db = SessionLocal()
    try:
        recs = import_legacy_labels(db, batch_id, payload.total, payload.used,
                                    payload.transferred_unused, payload.operator)
        return {"imported": [{"id": r.id, "label_code": r.label_code, "quantity": r.quantity,
                              "used_count": r.used_count, "status": r.status, "holder": r.holder}
                             for r in recs]}
    except BusinessError as exc:
        db.rollback()
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})
    finally:
        db.close()


@router.post("/batches/split", response_model=BatchOut, summary="拆批（原批次关闭，来源份额保留）")
def do_split(payload: SplitRequest, db: Session = Depends(get_db)):
    try:
        return split_batch(db, payload.source_batch_id,
                           payload.new_batch_no, payload.quantity, payload.operator)
    except BusinessError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.post("/batches/merge", response_model=BatchOut, summary="合批（任一来源不合格即拒绝）")
def do_merge(payload: MergeRequest, db: Session = Depends(get_db)):
    try:
        return merge_batches(db, payload.source_batch_ids, payload.new_batch_no, payload.operator)
    except BusinessError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


# ── 标签生命周期 ───────────────────────────────────────────────────────────
@router.post("/labels/{label_id}/transfer", response_model=LabelIssueExtended,
             summary="标签调拨到包装厂（持有人变更，仍跟随原批次）")
def do_transfer(label_id: int, payload: TransferRequest, db: Session = Depends(get_db)):
    try:
        rec, _ = transfer_label(db, label_id, payload.to_party, payload.quantity, payload.operator)
        return _extended(rec, db)
    except BusinessError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.post("/labels/{label_id}/use", response_model=LabelIssueExtended, summary="登记标签已使用")
def do_use(label_id: int, payload: UseRequest, db: Session = Depends(get_db)):
    try:
        rec = mark_label_used(db, label_id, payload.used, payload.operator)
        return _extended(rec, db)
    except BusinessError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


def _extended(rec: LabelIssue, db: Session) -> LabelIssueExtended:
    b = db.get(Batch, rec.batch_id)
    return LabelIssueExtended(
        id=rec.id, label_code=rec.label_code, batch_id=rec.batch_id, batch_no=b.batch_no,
        quantity=rec.quantity, auth_id=rec.auth_id, report_id=rec.report_id,
        issued_at=rec.issued_at, operator=rec.operator,
        used_count=rec.used_count, status=rec.status, holder=rec.holder,
        source_batch_id=rec.source_batch_id,
    )


@router.post("/labels/recall", summary="人工召回未使用标签段（已使用保留）")
def do_recall(payload: RecallRequest, db: Session = Depends(get_db)):
    try:
        return recall_labels(db, payload.label_ids, payload.operator, payload.note)
    except BusinessError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


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


@router.get("/label-segments", response_model=list[LabelIssueExtended],
            summary="标签段清单（含状态/持有人/已用未用/来源批次）")
def list_segments(batch_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(LabelIssue).order_by(LabelIssue.id)
    if batch_id is not None:
        stmt = stmt.where(LabelIssue.batch_id == batch_id)
    return [_extended(rec, db) for rec in db.execute(stmt).scalars()]
