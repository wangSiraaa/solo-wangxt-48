"""重划界过渡规则、候选处置与草案路由。

候选处置只做计算与展示；暂停/召回/降额均需人工确认草案后才执行。
撤销草案不回滚已执行动作（见 dispositions/{id}/actions）。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Batch,
    DispositionAction,
    DispositionDraft,
    ProtectedAreaVersion,
    TransitionRule,
)
from ..schemas import (
    ActionOut,
    DraftConfirm,
    DraftOut,
    DraftRevoke,
    TransitionRuleCreate,
    TransitionRuleOut,
)
from ..transitions import TransitionError, candidate, confirm_draft, create_draft, get_rule, revoke_draft

router = APIRouter(tags=["划界过渡"])


@router.post("/transition-rules", response_model=TransitionRuleOut, status_code=201,
             summary="登记品牌方确认的过渡规则")
def create_rule(payload: TransitionRuleCreate, db: Session = Depends(get_db)):
    if db.execute(select(TransitionRule).where(TransitionRule.rule_no == payload.rule_no)).scalar_one_or_none():
        raise HTTPException(409, f"规则号 {payload.rule_no} 已存在")
    if db.get(ProtectedAreaVersion, payload.from_version_id) is None or \
       db.get(ProtectedAreaVersion, payload.to_version_id) is None:
        raise HTTPException(404, "保护区版本不存在")
    rule = TransitionRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/transition-rules", response_model=list[TransitionRuleOut], summary="过渡规则清单")
def list_rules(db: Session = Depends(get_db)):
    return db.execute(select(TransitionRule).order_by(TransitionRule.id)).scalars().all()


@router.get("/rules/{rule_id}/candidates/{batch_id}", summary="计算批次候选处置（不落库、不执行）")
def rule_candidate(rule_id: int, batch_id: int, db: Session = Depends(get_db)):
    rule = get_rule(db, rule_id)
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "批次不存在")
    try:
        return candidate(db, batch, rule)
    except TransitionError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.get("/rules/{rule_id}/candidates", summary="全部批次的候选处置（标签段级影响）")
def all_candidates(rule_id: int, only_affected: bool = True, db: Session = Depends(get_db)):
    rule = get_rule(db, rule_id)
    out = []
    for batch in db.execute(select(Batch).order_by(Batch.harvest_date, Batch.batch_no)).scalars():
        try:
            c = candidate(db, batch, rule)
        except TransitionError:
            continue
        if only_affected and all(s["action"] == "NONE" for s in c["suggested"]):
            continue
        out.append(c)
    return out


@router.post("/rules/{rule_id}/drafts/{batch_id}", response_model=DraftOut, status_code=201,
             summary="为批次生成处置草案（待人工确认）")
def make_draft(rule_id: int, batch_id: int, db: Session = Depends(get_db)):
    try:
        return create_draft(db, batch_id, rule_id)
    except TransitionError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.get("/dispositions", response_model=list[DraftOut], summary="处置草案清单")
def list_drafts(status: str | None = None, rule_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(DispositionDraft).order_by(DispositionDraft.id.desc())
    if status:
        stmt = stmt.where(DispositionDraft.status == status)
    if rule_id:
        stmt = stmt.where(DispositionDraft.rule_id == rule_id)
    return db.execute(stmt).scalars().all()


@router.post("/dispositions/{draft_id}/confirm", response_model=DraftOut,
             summary="人工确认草案并执行勾选动作（暂停/召回/降额）")
def confirm(draft_id: int, payload: DraftConfirm, db: Session = Depends(get_db)):
    try:
        return confirm_draft(db, draft_id, payload.operator, payload.actions, payload.note)
    except TransitionError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.post("/dispositions/{draft_id}/revoke", response_model=DraftOut,
             summary="撤销草案（不影响已经执行的暂停/召回/降额）")
def revoke(draft_id: int, payload: DraftRevoke, db: Session = Depends(get_db)):
    try:
        return revoke_draft(db, draft_id, payload.operator, payload.note)
    except TransitionError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.get("/dispositions/{draft_id}/actions", response_model=list[ActionOut],
            summary="草案已执行动作留痕（撤销草案也保留）")
def draft_actions(draft_id: int, db: Session = Depends(get_db)):
    if db.get(DispositionDraft, draft_id) is None:
        raise HTTPException(404, "草案不存在")
    return db.execute(
        select(DispositionAction).where(DispositionAction.draft_id == draft_id)
        .order_by(DispositionAction.id)
    ).scalars().all()
