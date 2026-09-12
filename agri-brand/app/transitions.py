"""重划界过渡服务（虚构规则，品牌方人工确认）。

规则来源 TransitionRule（品牌方确认），系统只计算候选处置：
- 采收日 ≤ harvest_cutoff（含）：适用旧版本结论（旧规则结论可回看）；
  已使用标签保留；已调拨/未使用存量建议召回（人工决定）；待发暂停。
- 采收日 > harvest_cutoff：适用新版本核查；不合格则停发待发、存量建议召回；
  合格但跨边界时，新授权额度 = 原可追溯产量 × 新区内面积比例（向下取整）。

额度去重：新额度【替代】旧额度，剩余 = 新额度 − 历史已发总量，绝不叠加。
草案撤销不回滚已执行动作（DispositionAction 独立留痕）。
"""
import math
from datetime import datetime
from typing import Any

from shapely.geometry import shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, geometry
from .models import (
    Batch,
    BatchQuota,
    DispositionAction,
    DispositionDraft,
    LabelIssue,
    Parcel,
    ParcelCheck,
    ProtectedAreaVersion,
    TransitionRule,
)


class TransitionError(Exception):
    def __init__(self, message: str, code: str = "transition_error", status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def get_rule(db: Session, rule_id: int) -> TransitionRule:
    rule = db.get(TransitionRule, rule_id)
    if rule is None:
        raise TransitionError("过渡规则不存在", code="not_found", status_code=404)
    return rule


def applicable_version(db: Session, pa_code: str, on_date) -> ProtectedAreaVersion:
    stmt = select(ProtectedAreaVersion).where(
        ProtectedAreaVersion.code == pa_code,
        ProtectedAreaVersion.valid_from <= on_date,
        (ProtectedAreaVersion.valid_to.is_(None)) | (ProtectedAreaVersion.valid_to > on_date),
    ).order_by(ProtectedAreaVersion.valid_from.desc())
    pa = db.execute(stmt).scalars().first()
    if pa is None:
        raise TransitionError(f"{on_date} 没有生效的保护区版本", code="no_version", status_code=409)
    return pa


def recheck_parcel(db: Session, parcel: Parcel, version: ProtectedAreaVersion) -> ParcelCheck:
    """对指定保护区版本追加一条核查历史（不覆盖任何旧结论）。"""
    polygon = geometry.load_polygon(parcel.geometry, parcel.crs)
    pa_polygon = shape(version.geometry)
    r = geometry.check_parcel(polygon, pa_polygon, config.TOLERANCE_RATIO, config.TOLERANCE_SQM)

    check = ParcelCheck(
        parcel_id=parcel.id,
        pa_version_id=version.id,
        status=r["status"], qualified=r["qualified"], reason=r["reason"],
        total_area_sqm=r["total_area_sqm"], inside_area_sqm=r["inside_area_sqm"],
        outside_area_sqm=r["outside_area_sqm"], outside_ratio=r["outside_ratio"],
        centroid_inside=r["centroid_inside"], shared_edge_m=r["shared_edge_m"],
    )
    # 若为当前现行版本，同步 parcels 快照（批次资格仍按采收日版本取历史核查）
    db.add(check)
    db.flush()
    return check


def check_for(db: Session, parcel_id: int, version_id: int) -> ParcelCheck | None:
    stmt = (
        select(ParcelCheck)
        .where(ParcelCheck.parcel_id == parcel_id, ParcelCheck.pa_version_id == version_id)
        .order_by(ParcelCheck.checked_at.desc(), ParcelCheck.id.desc())
    )
    return db.execute(stmt).scalars().first()


def ensure_check(db: Session, parcel: Parcel, version: ProtectedAreaVersion) -> ParcelCheck:
    return check_for(db, parcel.id, version.id) or recheck_parcel(db, parcel, version)


# ── 标签三段统计 ───────────────────────────────────────────────────────────
def label_segments(db: Session, batch: Batch) -> dict[str, Any]:
    rows = db.execute(select(LabelIssue).where(LabelIssue.batch_id == batch.id)).scalars().all()
    stock = used = transferred_unused = 0
    segments = []
    for lab in rows:
        unused = lab.quantity - lab.used_count
        stock += unused
        used += lab.used_count
        if lab.status == "TRANSFERRED":
            transferred_unused += unused
        segments.append({
            "id": lab.id,
            "label_code": lab.label_code,
            "quantity": lab.quantity,
            "used_count": lab.used_count,
            "unused": unused,
            "status": lab.status,
            "holder": lab.holder,
        })
    return {
        "issued_total": sum(l.quantity for l in rows),
        "stock_unused": stock,
        "used_total": used,
        "transferred_unused": transferred_unused,
        "segments": segments,
    }


def current_quota(db: Session, batch: Batch) -> BatchQuota | None:
    stmt = (
        select(BatchQuota)
        .where(BatchQuota.batch_id == batch.id, BatchQuota.current.is_(True))
        .order_by(BatchQuota.id.desc())
    )
    return db.execute(stmt).scalars().first()


def set_transition_quota(db: Session, batch: Batch, rule: TransitionRule,
                         version: ProtectedAreaVersion, amount: int, note: str) -> BatchQuota:
    """登记替代额度：旧额度全部置 current=False，新额度与已发量按替代口径核算。"""
    for q in db.execute(select(BatchQuota).where(BatchQuota.batch_id == batch.id)).scalars():
        q.current = False
    quota = BatchQuota(
        batch_id=batch.id, basis="transition", pa_version_id=version.id,
        amount=amount, rule_id=rule.id, current=True, note=note,
    )
    db.add(quota)
    db.flush()
    return quota


def candidate(db: Session, batch: Batch, rule: TransitionRule, persist_check: bool = True) -> dict[str, Any]:
    """计算一个批次的候选处置（不产生任何执行效果）。"""
    if batch.parcel_id is None and not batch.sources:
        raise TransitionError(f"批次 {batch.batch_no} 缺少地块/来源谱系，无法适用规则", code="no_parcel")
    pa_code = rule.pa_code
    old_version = db.get(ProtectedAreaVersion, rule.from_version_id)
    new_version = db.get(ProtectedAreaVersion, rule.to_version_id)

    seg = "LEGACY" if batch.harvest_date <= rule.harvest_cutoff else "NEW"
    segs = label_segments(db, batch)

    # 来源谱系（合批）：任一来源地块在适用版本下不合格 → 整批不合格
    if batch.sources:
        source_results = []
        all_qualified = True
        min_ratio = 1.0
        for s in batch.sources:
            p = db.get(Parcel, s.source_parcel_id) if s.source_parcel_id else None
            if p is None:
                continue
            version = old_version if seg == "LEGACY" else new_version
            chk = ensure_check(db, p, version) if persist_check else (check_for(db, p.id, version.id))
            if chk is None:
                all_qualified = False
                source_results.append({"parcel_id": p.id, "status": "UNCHECKED", "qualified": False})
                continue
            source_results.append({
                "parcel_id": p.id, "code": p.code, "status": chk.status,
                "qualified": chk.qualified, "inside_ratio": round(1 - chk.outside_ratio, 6), "share": s.share,
            })
            all_qualified = all_qualified and chk.qualified
            min_ratio = min(min_ratio, 1 - chk.outside_ratio)
        parcel_status = "MIXED_SOURCES"
        qualified = all_qualified
        inside_ratio = min_ratio
        parcel_payload = {"sources": source_results}
    else:
        parcel = db.get(Parcel, batch.parcel_id)
        version = old_version if seg == "LEGACY" else new_version
        chk = ensure_check(db, parcel, version) if persist_check else check_for(db, parcel.id, version.id)
        if chk is None:
            raise TransitionError(f"地块 {parcel.code} 缺少版本 {version.version} 的核查，请先重查")
        parcel_status = chk.status
        qualified = chk.qualified
        inside_ratio = round(1 - chk.outside_ratio, 6)
        parcel_payload = {
            "parcel_code": parcel.code,
            "status": chk.status,
            "qualified": chk.qualified,
            "reason": chk.reason,
            "check_id": chk.id,
            "pa_version": version.version,
            "inside_area_sqm": chk.inside_area_sqm,
            "outside_area_sqm": chk.outside_area_sqm,
        }

    quota = current_quota(db, batch)
    quota_amount = quota.amount if quota else batch.traceable_output
    suggested: list[dict] = []
    new_quota: int | None = None

    if seg == "LEGACY":
        if rule.carry_used:
            suggested.append({"action": "KEEP_USED", "labels": segs["used_total"],
                              "note": "已使用标签随旧边界结论保留，不追溯"})
        if rule.quarantine_stock and segs["stock_unused"] > 0:
            suggested.append({"action": "RECALL_LABELS", "labels": segs["stock_unused"],
                              "transferred_unused": segs["transferred_unused"],
                              "note": "已调拨未使用标签建议召回（人工决定）；在社未使用标签冻结待核"})
        pending = max(0, quota_amount - segs["issued_total"])
        if rule.stop_pending and pending > 0:
            suggested.append({"action": "SUSPEND_PENDING", "labels": pending,
                              "note": "分界日前批次：待发额度暂停，等待人工处置"})
    else:
        pending = max(0, quota_amount - segs["issued_total"])
        if not qualified:
            if pending > 0:
                suggested.append({"action": "SUSPEND_PENDING", "labels": pending,
                                  "note": "地块按新边界不合格：停发待发标签"})
            if segs["stock_unused"] > 0:
                suggested.append({"action": "RECALL_LABELS", "labels": segs["stock_unused"],
                                  "transferred_unused": segs["transferred_unused"],
                                  "note": "已调拨/在社未使用标签建议召回（人工决定）"})
            if rule.carry_used:
                suggested.append({"action": "KEEP_USED", "labels": segs["used_total"],
                                  "note": "已使用标签是否追溯由人工决定，系统默认保留"})
        elif inside_ratio < 1.0 - 1e-9:
            # 合格但跨新边界：新额度按新区内面积比例折算，替代旧额度（不叠加）
            new_quota = math.floor(batch.traceable_output * inside_ratio)
            new_pending = max(0, new_quota - segs["issued_total"])
            suggested.append({
                "action": "NEW_QUOTA", "old_quota": quota_amount, "new_quota": new_quota,
                "issued": segs["issued_total"], "pending_after": new_pending,
                "note": f"新授权额度按新区内面积比例 {inside_ratio*100:.2f}% 折算并替代旧额度，不叠加",
            })
            if new_pending < pending:
                suggested.append({"action": "SUSPEND_PENDING", "labels": pending - new_pending,
                                  "note": "超出新额度的待发部分暂停"})
        else:
            suggested.append({"action": "NONE", "labels": 0, "note": "全部位于新边界内，无需处置"})

    return {
        "batch_id": batch.id,
        "batch_no": batch.batch_no,
        "segment": seg,
        "applicable_version": (old_version if seg == "LEGACY" else new_version).version,
        "harvest_date": batch.harvest_date,
        "parcel": parcel_payload,
        "parcel_status": parcel_status,
        "qualified": qualified,
        "inside_ratio_new": inside_ratio,
        "labels": segs,
        "quota": {"current": quota_amount, "issued": segs["issued_total"],
                  "pending": max(0, quota_amount - segs["issued_total"]),
                  "proposed_new": new_quota},
        "suggested": suggested,
    }


def create_draft(db: Session, batch_id: int, rule_id: int, operator: str = "系统计算") -> DispositionDraft:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise TransitionError("批次不存在", code="not_found", status_code=404)
    rule = get_rule(db, rule_id)

    existing = db.execute(select(DispositionDraft).where(
        DispositionDraft.batch_id == batch_id, DispositionDraft.rule_id == rule_id,
        DispositionDraft.status == "DRAFT")).scalar_one_or_none()
    if existing:
        raise TransitionError("该批次对本规则已有待确认草案", code="draft_exists")

    c = candidate(db, batch, rule)
    seq = db.execute(select(DispositionDraft).where(
        DispositionDraft.batch_id == batch_id, DispositionDraft.rule_id == rule_id)).scalars().all()
    suffix = len(seq) + 1
    draft = DispositionDraft(
        draft_no=f"CA-{rule_id}-{batch_id}-{suffix}",
        batch_id=batch_id, rule_id=rule_id,
        applicable_version_id=rule.from_version_id if c["segment"] == "LEGACY" else rule.to_version_id,
        status="DRAFT", segment=c["segment"], parcel_status=c["parcel_status"],
        inside_ratio_new=c["inside_ratio_new"],
        stock_labels=c["labels"]["stock_unused"], used_labels=c["labels"]["used_total"],
        pending_labels=c["quota"]["pending"],
        transferred_unused=c["labels"]["transferred_unused"],
        suggested=c["suggested"], created_by=operator,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def confirm_draft(db: Session, draft_id: int, operator: str,
                  actions: list[str] | None = None, note: str | None = None) -> DispositionDraft:
    """人工确认草案并执行勾选动作；已执行动作独立留痕。"""
    draft = db.get(DispositionDraft, draft_id)
    if draft is None:
        raise TransitionError("草案不存在", code="not_found", status_code=404)
    if draft.status != "DRAFT":
        raise TransitionError(f"草案已{draft.status == 'CONFIRMED' and '确认' or '撤销'}，不能重复操作",
                              code="draft_closed")
    chosen = set(actions or [s["action"] for s in draft.suggested if s["action"] != "NONE"])
    batch = db.get(Batch, draft.batch_id)
    rule = db.get(TransitionRule, draft.rule_id)
    new_version = db.get(ProtectedAreaVersion, rule.to_version_id)

    for item in draft.suggested:
        act = item["action"]
        if act == "NONE" or act not in chosen:
            continue
        if act in ("SUSPEND_PENDING",):
            batch.suspended = True
        elif act == "NEW_QUOTA":
            set_transition_quota(db, batch, rule, new_version, item["new_quota"], item["note"])
        elif act == "RECALL_LABELS":
            # 召回只记录动作与涉及标签段（是否实际退回由人工/包装厂流程处理）
            pass
        db.add(DispositionAction(
            draft_id=draft.id, batch_id=batch.id, action=act, detail=item, operator=operator))

    draft.status = "CONFIRMED"
    draft.reviewed_by = operator
    draft.decision_note = note
    draft.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(draft)
    return draft


def revoke_draft(db: Session, draft_id: int, operator: str, note: str | None = None) -> DispositionDraft:
    """撤销草案：仅作废候选，不回滚已执行的暂停/召回/额度动作。"""
    draft = db.get(DispositionDraft, draft_id)
    if draft is None:
        raise TransitionError("草案不存在", code="not_found", status_code=404)
    if draft.status != "DRAFT":
        raise TransitionError("只有待确认草案可撤销", code="draft_closed")
    draft.status = "REVOKED"
    draft.reviewed_by = operator
    draft.decision_note = note or "撤销处置草案（不影响已执行动作）"
    draft.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(draft)
    return draft
