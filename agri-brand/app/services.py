"""用标资格判定与标签领用。

可用标范围由三件事共同决定（均以批次采收日期为判定时点）：
  ① 地块核查结论合格（INSIDE / CROSS_TOLERATED，绑定当时有效保护区版本）；
  ② 检测报告品种一致，且报告在采收日有效、当前未过期（可配宽限天数）；
  ③ 授权主体/品种一致，采收日落在授权期间内，授权未被撤销。
另外：用标数量不得超过批次“可追溯产量 - 已发数量”；
      异常巡查命中的批次暂停新增用标；已发记录保留。
"""
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .models import (
    Authorization,
    Batch,
    BatchQuota,
    BatchSource,
    InspectionEvent,
    InspectionReport,
    LabelIssue,
    LabelTransfer,
    Parcel,
    ProtectedAreaVersion,
)


class BusinessError(Exception):
    def __init__(self, message: str, code: str = "business_error", status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def active_inspection(db: Session, batch: Batch) -> InspectionEvent | None:
    stmt = select(InspectionEvent).where(InspectionEvent.active.is_(True))
    stmt = stmt.where(
        (InspectionEvent.batch_id == batch.id)
        | ((InspectionEvent.batch_id.is_(None)) & (InspectionEvent.cooperative == batch.cooperative))
    )
    return db.execute(stmt.limit(1)).scalar_one_or_none()


def _find_report(db: Session, parcel_id: int, variety: str, harvest: date, today: date) -> InspectionReport | None:
    stmt = (
        select(InspectionReport)
        .where(
            InspectionReport.parcel_id == parcel_id,
            InspectionReport.variety == variety,
            InspectionReport.valid_from <= harvest,
            InspectionReport.valid_until >= harvest,
        )
        .order_by(InspectionReport.valid_until.desc())
    )
    report = db.execute(stmt).scalars().first()
    # 报告除覆盖采收日外，当前也必须未过期
    if report and report.valid_until >= today:
        return report
    return None


def _find_auth(db: Session, cooperative: str, variety: str, harvest: date) -> Authorization | None:
    stmt = select(Authorization).where(
        Authorization.cooperative == cooperative,
        Authorization.variety == variety,
        Authorization.start_date <= harvest,
        Authorization.end_date >= harvest,
        Authorization.revoked.is_(False),
    )
    return db.execute(stmt).scalars().first()


def _pa_version_for(db: Session, parcel: Parcel, on_date: date) -> ProtectedAreaVersion:
    pa_code = db.get(ProtectedAreaVersion, parcel.pa_version_id).code
    stmt = select(ProtectedAreaVersion).where(
        ProtectedAreaVersion.code == pa_code,
        ProtectedAreaVersion.valid_from <= on_date,
        (ProtectedAreaVersion.valid_to.is_(None)) | (ProtectedAreaVersion.valid_to > on_date),
    ).order_by(ProtectedAreaVersion.valid_from.desc())
    return db.execute(stmt).scalars().first()


def _parcel_status(db: Session, parcel: Parcel, on_date: date) -> tuple[bool, str, str, ProtectedAreaVersion]:
    """按采收日适用的保护区版本取核查结论（旧规则结论可回看）。"""
    version = _pa_version_for(db, parcel, on_date)
    from .transitions import check_for

    chk = check_for(db, parcel.id, version.id)
    if chk is not None:
        return chk.qualified, chk.status, chk.reason, version
    # 没有历史核查行时，地块快照仅在其版本与适用版本一致时才采信
    if parcel.pa_version_id == version.id:
        return parcel.qualified, parcel.status, parcel.reason, version
    return False, "UNCHECKED", f"地块 {parcel.code} 缺少版本 {version.version} 的核查，请先重查", version


def _geometry_ok(db: Session, batch: Batch) -> tuple[bool, str]:
    """批次几何资格（按采收日适用版本的旧规则结论可回看）。

    重划界后的“候选处置”只提示不自动阻断：当地块在【新】版本下结论变差，
    但尚无已确认处置时允许继续签发；确认 SUSPEND_PENDING 后由 batch.suspended 停发。
    """
    from .models import DispositionDraft, TransitionRule

    def parcel_blocker(p: Parcel) -> tuple[bool, str]:
        version = _pa_version_for(db, p, batch.harvest_date)
        from .transitions import check_for
        chk = check_for(db, p.id, version.id)
        if chk is not None:
            if not chk.qualified:
                # 新核查不合格本身不阻断（重划界处置必须由人工确认）；
                # 只有在该批次已被确认处置（batch.suspended）或有已确认草案时才停发。
                rule = db.execute(
                    select(TransitionRule).where(TransitionRule.to_version_id == version.id)
                ).scalars().first()
                confirmed = None
                if rule:
                    confirmed = db.execute(
                        select(DispositionDraft).where(
                            DispositionDraft.batch_id == batch.id,
                            DispositionDraft.rule_id == rule.id,
                            DispositionDraft.status == "CONFIRMED")
                    ).scalar_one_or_none()
                if batch.suspended or confirmed is not None:
                    return False, (
                        f"地块按适用版本 {version.version} 核查不合格（{chk.status}）"
                        + (f"，处置草案 {confirmed.draft_no} 已确认" if confirmed else "，已被暂停")
                        + f"：{chk.reason}")
            return True, f"{p.code}({version.version}:{chk.status})"
        if p.pa_version_id == version.id:
            return p.qualified, f"{p.code}({version.version}:{p.status})"
        return False, f"地块 {p.code} 缺少版本 {version.version} 的核查，请先重查"

    if batch.sources:
        detail, ok_all = [], True
        for s in batch.sources:
            if s.source_parcel_id is None:
                continue
            p = db.get(Parcel, s.source_parcel_id)
            ok, txt = parcel_blocker(p)
            detail.append(f"{txt}，份额{s.share*100:.0f}%")
            ok_all = ok_all and ok
        if not ok_all:
            return False, "合批来源中存在已确认不合格的地块，不能借合批洗掉：" + "、".join(detail)
        return True, "、".join(detail)
    if batch.parcel_id is None:
        return False, "批次缺少地块或来源谱系"
    return parcel_blocker(db.get(Parcel, batch.parcel_id))


@dataclass
class Eligibility:
    eligible: bool
    suspended: bool
    blockers: list[str] = field(default_factory=list)
    report: InspectionReport | None = None
    authorization: Authorization | None = None
    event: InspectionEvent | None = None
    geometry_detail: str = ""


def evaluate(db: Session, batch: Batch, today: date | None = None) -> Eligibility:
    today = today or config.CURRENT_DATE
    blockers: list[str] = []

    event = active_inspection(db, batch)
    suspended = event is not None or batch.suspended
    if event is not None:
        blockers.append(f"异常巡查 {event.event_no} 处理中：暂停新增用标（已发标签保留）")
    if batch.suspended and event is None:
        blockers.append("批次已被过渡处置/管理措施暂停新增用标（已发标签保留）")

    if batch.closed:
        blockers.append("批次已关闭（拆批/合批后原批次封存），不能再领用标签")

    geo_ok, geo_detail = _geometry_ok(db, batch)
    if not geo_ok:
        blockers.append(geo_detail)

    # 检测报告：合批时任一来源地块有覆盖品种/采收日且当前有效的报告即可（报告编号随标签段留存）
    parcel_ids = [batch.parcel_id] if batch.parcel_id else [s.source_parcel_id for s in batch.sources]
    report = None
    for pid in parcel_ids:
        if pid is None:
            continue
        report = _find_report(db, pid, batch.variety, batch.harvest_date, today)
        if report is not None:
            break
    if report is None:
        blockers.append(
            f"缺少在 {today} 仍有效、品种={batch.variety} 且覆盖采收日 {batch.harvest_date} 的检测报告（报告已过期或品种不符）"
        )

    auth = _find_auth(db, batch.cooperative, batch.variety, batch.harvest_date)
    if auth is None:
        blockers.append(
            f"缺少覆盖主体={batch.cooperative}、品种={batch.variety}、采收日 {batch.harvest_date} 的有效授权"
        )

    return Eligibility(
        eligible=not blockers,
        suspended=suspended,
        blockers=blockers,
        report=report,
        authorization=auth,
        event=event,
        geometry_detail=geo_detail,
    )


def current_quota_amount(db: Session, batch: Batch) -> int:
    """当前授权额度：有 BatchQuota（含过渡替代额度）取之，否则取可追溯产量。"""
    q = db.execute(
        select(BatchQuota).where(BatchQuota.batch_id == batch.id, BatchQuota.current.is_(True))
        .order_by(BatchQuota.id.desc())
    ).scalars().first()
    return q.amount if q else batch.traceable_output


def eligibility_payload(db: Session, batch: Batch, today: date | None = None) -> dict:
    today = today or config.CURRENT_DATE
    ev = evaluate(db, batch, today)
    geo_ok, _geo_detail = _geometry_ok(db, batch)
    quota_amount = current_quota_amount(db, batch)
    remaining = max(0, quota_amount - batch.labels_issued)

    sources_payload = None
    if batch.sources:
        sources_payload = []
        for s in batch.sources:
            p = db.get(Parcel, s.source_parcel_id) if s.source_parcel_id else None
            sources_payload.append({
                "source_batch_id": s.source_batch_id,
                "parcel_code": p.code if p else None,
                "share": s.share,
                "labels_attributed": s.labels_attributed,
            })

    parcel_payload: dict
    if batch.parcel_id:
        parcel = db.get(Parcel, batch.parcel_id)
        ok, status, reason, version = _parcel_status(db, parcel, batch.harvest_date)
        parcel_payload = {
            "code": parcel.code,
            "name": parcel.name,
            "status": status,
            "qualified": ok,
            "blocking_issue": not geo_ok,
            "inside_area_sqm": parcel.inside_area_sqm,
            "outside_area_sqm": parcel.outside_area_sqm,
            "outside_ratio": parcel.outside_ratio,
            "centroid_inside": parcel.centroid_inside,
            "applicable_version": version.version,
            "reason": reason,
        }
    else:
        parcel_payload = {"code": None, "merged_sources": sources_payload,
                          "qualified": geo_ok, "blocking_issue": not geo_ok,
                          "geometry_detail": ev.geometry_detail}

    return {
        "batch_no": batch.batch_no,
        "variety": batch.variety,
        "cooperative": batch.cooperative,
        "harvest_date": batch.harvest_date,
        "eligible": ev.eligible,
        "suspended": ev.suspended,
        "closed": batch.closed,
        "legacy": batch.is_legacy,
        "parcel": parcel_payload,
        "sources": sources_payload,
        "report": None
        if ev.report is None
        else {
            "report_no": ev.report.report_no,
            "variety": ev.report.variety,
            "valid_from": ev.report.valid_from,
            "valid_until": ev.report.valid_until,
            "expired": ev.report.valid_until < today,
        },
        "authorization": None
        if ev.authorization is None
        else {
            "auth_no": ev.authorization.auth_no,
            "variety": ev.authorization.variety,
            "start_date": ev.authorization.start_date,
            "end_date": ev.authorization.end_date,
        },
        "quota": {
            "traceable_output": batch.traceable_output,
            "current_quota": quota_amount,
            "labels_issued": batch.labels_issued,
            "remaining": remaining,
        },
        "blockers": ev.blockers,
    }


def _next_label_code(db: Session, batch: Batch, quantity: int) -> str:
    """batch_no-起始序号~结束序号（5 位）。序号以已发总数计。"""
    start = batch.labels_issued + 1
    end = batch.labels_issued + quantity
    return f"{batch.batch_no}-{start:05d}~{end:05d}"


def issue_labels(db: Session, batch_id: int, quantity: int, operator: str, today: date | None = None) -> LabelIssue:
    """在单写事务内执行：锁批次行 → 复核资格/暂停 → 扣额度 → 写流水。

    SQLite 通过 BEGIN IMMEDIATE 串行化写事务；PostgreSQL 用 SELECT ... FOR UPDATE 行锁。
    """
    today = today or config.CURRENT_DATE
    if quantity <= 0:
        raise BusinessError("领用数量必须为正整数", code="bad_quantity", status_code=422)

    stmt = select(Batch).where(Batch.id == batch_id)
    if not config.IS_SQLITE:
        stmt = stmt.with_for_update()
    batch = db.execute(stmt).scalar_one_or_none()
    if batch is None:
        raise BusinessError("批次不存在", code="not_found", status_code=404)

    ev = evaluate(db, batch, today)
    if ev.suspended:
        raise BusinessError(ev.blockers[0], code="suspended", status_code=409)
    if not ev.eligible:
        raise BusinessError("；".join(ev.blockers), code="not_eligible", status_code=409)

    quota_amount = current_quota_amount(db, batch)
    remaining = quota_amount - batch.labels_issued
    if quantity > remaining:
        raise BusinessError(
            f"领用 {quantity} 枚超过当前授权额度剩余（当前额度 {quota_amount}，"
            f"已发 {batch.labels_issued}，剩余 {remaining}；重划界后新额度替代旧额度，不叠加）",
            code="quota_exceeded",
            status_code=409,
        )

    code = _next_label_code(db, batch, quantity)
    record = LabelIssue(
        label_code=code,
        batch_id=batch.id,
        quantity=quantity,
        holder=batch.cooperative,
        auth_id=ev.authorization.id,
        report_id=ev.report.id,
        operator=operator,
        issued_at=datetime.utcnow(),
    )
    batch.labels_issued += quantity
    # 合批：按来源份额把本批标签归因到各来源（四舍五入，尾差计入首个来源）
    if batch.sources:
        alloc = [round(quantity * s.share) for s in batch.sources]
        diff = quantity - sum(alloc)
        if alloc:
            alloc[0] += diff
        for s, n in zip(batch.sources, alloc):
            s.labels_attributed += n
            record.source_batch_id = record.source_batch_id or s.source_batch_id
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:  # 标签号唯一约束兜底
        db.rollback()
        raise BusinessError("并发领用冲突，请重试", code="conflict", status_code=409) from exc
    db.refresh(record)
    return record


# ── 标签生命周期：调拨 / 使用 / 召回 ───────────────────────────────────────
def _get_label(db: Session, label_id: int) -> LabelIssue:
    lab = db.get(LabelIssue, label_id)
    if lab is None:
        raise BusinessError("标签段不存在", code="not_found", status_code=404)
    return lab


def transfer_label(db: Session, label_id: int, to_party: str, quantity: int | None,
                   operator: str) -> tuple[LabelIssue, LabelIssue]:
    """整段调拨到包装厂：持有人变化，batch_id 不变（标签跟随原批次）。

    quantity=None 调拨全部未使用数量；部分调拨拆分出新标签段，父子段同批次。
    """
    lab = _get_label(db, label_id)
    unused = lab.quantity - lab.used_count
    if quantity is None or quantity == unused:
        lab.status = "TRANSFERRED"
        lab.holder = to_party
        tr = LabelTransfer(label_issue_id=lab.id, from_party=lab.batch.cooperative,
                           to_party=to_party, quantity=unused, operator=operator)
        db.add(tr)
        db.commit()
        db.refresh(lab)
        return lab, tr

    if quantity <= 0 or quantity > unused:
        raise BusinessError(f"调拨数量须为 1~未使用数({unused})", code="bad_quantity", status_code=422)

    # 部分调拨：父段保留在社，拆出同批次子段给包装厂（batch_id 不变）
    parent_qty = lab.quantity - quantity
    parent_code = lab.label_code
    lab.quantity = parent_qty
    child = LabelIssue(
        label_code=f"{parent_code}/T{lab.id}",
        batch_id=lab.batch_id, quantity=quantity, holder=to_party, status="TRANSFERRED",
        auth_id=lab.auth_id, report_id=lab.report_id, source_batch_id=lab.source_batch_id,
        operator=operator,
    )
    tr = LabelTransfer(label_issue_id=lab.id, from_party=lab.batch.cooperative,
                       to_party=to_party, quantity=quantity, operator=operator)
    db.add_all([child, tr])
    db.commit()
    db.refresh(child)
    return child, tr


def mark_label_used(db: Session, label_id: int, used: int, operator: str) -> LabelIssue:
    lab = _get_label(db, label_id)
    unused = lab.quantity - lab.used_count
    if used <= 0 or used > unused:
        raise BusinessError(f"使用数量须为 1~未使用数({unused})", code="bad_quantity", status_code=422)
    lab.used_count += used
    if lab.used_count >= lab.quantity:
        lab.status = "USED"
    db.commit()
    db.refresh(lab)
    return lab


def recall_labels(db: Session, label_ids: list[int], operator: str, note: str = "") -> dict:
    """人工召回：未使用标签段标记 RECALLED（状态独立，不再可使用）；已使用数量保留。

    这是【人工执行】的动作；仅撤销处置草案不会触发召回回滚。
    """
    recalled = []
    for lid in label_ids:
        lab = _get_label(db, lid)
        unused = lab.quantity - lab.used_count
        if unused <= 0:
            continue
        lab.status = "RECALLED"
        lab.holder = f"召回封存（{operator}）"
        recalled.append({"label_code": lab.label_code, "unused": unused, "note": note})
    db.commit()
    return {"recalled": recalled}


def import_legacy_labels(db: Session, batch_id: int, total: int, used: int,
                         transferred_unused: int, operator: str = "历史导入") -> list[LabelIssue]:
    """导入重划界前已生产批次的存量标签（不再做资格/额度校验，属历史事实）。

    在社未使用 / 已调拨包装厂未使用 / 已使用拆成标签段，直接累加 labels_issued。
    返回生成的标签段列表。
    """
    if total <= 0 or used < 0 or transferred_unused < 0 or used + transferred_unused > total:
        raise BusinessError("历史标签数量不合法（已用+在厂未用 ≤ 总量）", code="bad_quantity", status_code=422)
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise BusinessError("批次不存在", code="not_found", status_code=404)
    in_coop = total - used - transferred_unused
    segs = []

    def _seg(suffix: str, qty: int, used_cnt: int, status: str, holder: str):
        rec = LabelIssue(
            label_code=f"{batch.batch_no}-{suffix}", batch_id=batch.id, quantity=qty,
            used_count=used_cnt, status=status, holder=holder, operator=operator,
        )
        db.add(rec)
        segs.append(rec)

    if in_coop:
        _seg("00001~" + f"{in_coop:05d}", in_coop, 0, "ISSUED", batch.cooperative)
    if transferred_unused:
        _seg(f"{in_coop + 1:05d}~{in_coop + transferred_unused:05d}", transferred_unused, 0,
             "TRANSFERRED", "雾岭镇包装厂")
    if used:
        start = in_coop + transferred_unused + 1
        _seg(f"{start:05d}~{total:05d}", used, used, "USED", batch.cooperative)
    batch.labels_issued += total
    db.commit()
    for r in segs:
        db.refresh(r)
    return segs


# ── 拆批 / 合批 ────────────────────────────────────────────────────────────
def split_batch(db: Session, source_batch_id: int, new_batch_no: str, quantity: int,
                operator: str = "品牌管理方") -> Batch:
    """拆批：从来源批次切出 quantity 枚可追溯产量到新批次，新批次带来源份额 100%。

    原批次关闭（不再签发）；新批次继承地块/品种/采收日，标签仍可回溯到来源。
    """
    src = db.get(Batch, source_batch_id)
    if src is None:
        raise BusinessError("来源批次不存在", code="not_found", status_code=404)
    if src.closed:
        raise BusinessError("来源批次已关闭", code="closed")
    if quantity <= 0 or quantity > src.traceable_output - src.labels_issued:
        raise BusinessError(
            f"拆出产量须为 1~来源剩余产量({src.traceable_output - src.labels_issued})",
            code="bad_quantity", status_code=422)

    child = Batch(
        batch_no=new_batch_no, parcel_id=src.parcel_id, cooperative=src.cooperative,
        variety=src.variety, harvest_date=src.harvest_date, traceable_output=quantity,
        parent_batch_id=src.id,
    )
    db.add(child)
    db.flush()
    db.add(BatchSource(batch_id=child.id, source_batch_id=src.id,
                       source_parcel_id=src.parcel_id, share=1.0))
    db.add(BatchQuota(batch_id=child.id, basis="original",
                      pa_version_id=src.parcel.pa_version_id if src.parcel_id else 1,
                      amount=quantity, note=f"拆批自 {src.batch_no}"))
    src.traceable_output -= quantity
    src.closed = True
    db.commit()
    db.refresh(child)
    return child


def merge_batches(db: Session, source_batch_ids: list[int], new_batch_no: str,
                  operator: str = "品牌管理方") -> Batch:
    """合批：各来源剩余产量按份额并入新批次。

    硬规则：任一来源地块在其采收日适用边界下不合格 → 拒绝（不能借合批洗掉不合格来源）。
    新批次签发时按 BatchSource.share 归因到每个来源批次/地块。
    """
    if len(set(source_batch_ids)) < 2:
        raise BusinessError("合批至少需要两个不同来源批次", code="bad_input", status_code=422)
    srcs = []
    total_remaining = 0
    for bid in source_batch_ids:
        b = db.get(Batch, bid)
        if b is None:
            raise BusinessError(f"来源批次 {bid} 不存在", code="not_found", status_code=404)
        if b.closed:
            raise BusinessError(f"来源批次 {b.batch_no} 已关闭", code="closed")
        srcs.append(b)
        total_remaining += b.traceable_output - b.labels_issued

    # 来源几何硬校验（按各来源采收日适用版本）
    bad = []
    for b in srcs:
        ok, detail = _geometry_ok(db, b)
        if not ok:
            bad.append(detail)
    if bad:
        raise BusinessError("合批被拒：" + "；".join(bad), code="dirty_source", status_code=409)

    varieties = {b.variety for b in srcs}
    if len(varieties) > 1:
        raise BusinessError("仅允许同品种批次合批", code="bad_input", status_code=422)
    coops = {b.cooperative for b in srcs}
    if len(coops) > 1:
        raise BusinessError("仅允许同一合作社批次合批", code="bad_input", status_code=422)

    base = srcs[0]
    merged = Batch(
        batch_no=new_batch_no, parcel_id=None, cooperative=base.cooperative,
        variety=base.variety, harvest_date=base.harvest_date, traceable_output=total_remaining,
    )
    db.add(merged)
    db.flush()
    for b in srcs:
        remaining = b.traceable_output - b.labels_issued
        share = remaining / total_remaining if total_remaining else 0.0
        db.add(BatchSource(batch_id=merged.id, source_batch_id=b.id,
                           source_parcel_id=b.parcel_id, share=round(share, 6)))
        b.closed = True
    first_parcel = db.get(Parcel, srcs[0].parcel_id) if srcs[0].parcel_id else None
    db.add(BatchQuota(batch_id=merged.id, basis="original",
                      pa_version_id=first_parcel.pa_version_id if first_parcel else 1,
                      amount=total_remaining,
                      note="合批剩余额度=" + "+".join(str(b.traceable_output - b.labels_issued) for b in srcs)))
    db.commit()
    db.refresh(merged)
    return merged
