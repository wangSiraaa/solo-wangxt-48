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
    InspectionEvent,
    InspectionReport,
    LabelIssue,
    Parcel,
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


@dataclass
class Eligibility:
    eligible: bool
    suspended: bool
    blockers: list[str] = field(default_factory=list)
    report: InspectionReport | None = None
    authorization: Authorization | None = None
    event: InspectionEvent | None = None


def evaluate(db: Session, batch: Batch, today: date | None = None) -> Eligibility:
    today = today or config.CURRENT_DATE
    blockers: list[str] = []

    parcel = db.get(Parcel, batch.parcel_id)
    event = active_inspection(db, batch)
    suspended = event is not None
    if suspended:
        blockers.append(f"异常巡查 {event.event_no} 处理中：暂停新增用标（已发标签保留）")

    if not parcel.qualified:
        blockers.append(f"地块核查不合格（{parcel.code} {parcel.status}）：{parcel.reason}")

    report = _find_report(db, batch.parcel_id, batch.variety, batch.harvest_date, today)
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
    )


def eligibility_payload(db: Session, batch: Batch, today: date | None = None) -> dict:
    today = today or config.CURRENT_DATE
    parcel = db.get(Parcel, batch.parcel_id)
    ev = evaluate(db, batch, today)
    remaining = max(0, batch.traceable_output - batch.labels_issued)
    return {
        "batch_no": batch.batch_no,
        "variety": batch.variety,
        "cooperative": batch.cooperative,
        "harvest_date": batch.harvest_date,
        "eligible": ev.eligible,
        "suspended": ev.suspended,
        "parcel": {
            "code": parcel.code,
            "name": parcel.name,
            "status": parcel.status,
            "qualified": parcel.qualified,
            "inside_area_sqm": parcel.inside_area_sqm,
            "outside_area_sqm": parcel.outside_area_sqm,
            "outside_ratio": parcel.outside_ratio,
            "centroid_inside": parcel.centroid_inside,
            "pa_version_id": parcel.pa_version_id,
            "reason": parcel.reason,
        },
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

    remaining = batch.traceable_output - batch.labels_issued
    if quantity > remaining:
        raise BusinessError(
            f"领用 {quantity} 枚超过可追溯产量剩余额度（上限 {batch.traceable_output}，"
            f"已发 {batch.labels_issued}，剩余 {remaining}）",
            code="quota_exceeded",
            status_code=409,
        )

    code = _next_label_code(db, batch, quantity)
    record = LabelIssue(
        label_code=code,
        batch_id=batch.id,
        quantity=quantity,
        auth_id=ev.authorization.id,
        report_id=ev.report.id,
        operator=operator,
        issued_at=datetime.utcnow(),
    )
    batch.labels_issued += quantity
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:  # 标签号唯一约束兜底
        db.rollback()
        raise BusinessError("并发领用冲突，请重试", code="conflict", status_code=409) from exc
    db.refresh(record)
    return record
