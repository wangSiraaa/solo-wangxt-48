"""ORM 模型。几何统一以 GeoJSON（SQLite TEXT / PostgreSQL JSONB）存储，
Shapely 在应用层做权威几何判定；时间一律 UTC 日期。"""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _json_col():  # PostgreSQL 下 SQLAlchemy 会用 JSONB
    return mapped_column(JSON, nullable=False)


class ProtectedAreaVersion(Base):
    """保护区版本（虚构）。版本区间 [valid_from, valid_to)，最新版本无 valid_to。"""

    __tablename__ = "protected_area_versions"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_pa_code_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    geometry: Mapped[dict] = _json_col()  # GeoJSON Polygon, EPSG:4326
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    parcels: Mapped[list["Parcel"]] = relationship(back_populates="pa_version")


class Parcel(Base):
    """合作社申报地块 + 申报当时的核查快照（绑定保护区版本，可追溯）。"""

    __tablename__ = "parcels"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    cooperative: Mapped[str] = mapped_column(String(128), nullable=False)
    crs: Mapped[str] = mapped_column(String(32), nullable=False)
    geometry: Mapped[dict] = _json_col()
    declared_area_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)

    pa_version_id: Mapped[int] = mapped_column(ForeignKey("protected_area_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    qualified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    total_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    inside_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    outside_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    outside_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_inside: Mapped[bool] = mapped_column(Boolean, nullable=False)
    shared_edge_m: Mapped[float] = mapped_column(Float, default=0.0)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    pa_version: Mapped[ProtectedAreaVersion] = relationship(back_populates="parcels")
    checks: Mapped[list["ParcelCheck"]] = relationship(back_populates="parcel")
    batches: Mapped[list["Batch"]] = relationship(back_populates="parcel")


class ParcelCheck(Base):
    """地块核查历史：每次对某保护区版本核查生成一行，旧版本结论永久保留可回看。

    parcels 表上的快照字段始终等于该地块最新一次核查，批次资格判定则按
    采收日适用的保护区版本取对应核查行（见 transitions.py）。
    """

    __tablename__ = "parcel_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    parcel_id: Mapped[int] = mapped_column(ForeignKey("parcels.id"), nullable=False, index=True)
    pa_version_id: Mapped[int] = mapped_column(ForeignKey("protected_area_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    qualified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    total_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    inside_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    outside_area_sqm: Mapped[float] = mapped_column(Float, nullable=False)
    outside_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_inside: Mapped[bool] = mapped_column(Boolean, nullable=False)
    shared_edge_m: Mapped[float] = mapped_column(Float, default=0.0)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    parcel: Mapped["Parcel"] = relationship(back_populates="checks")


class InspectionReport(Base):
    """检测报告（品种、有效期）。[valid_from, valid_until] 闭区间有效。"""

    __tablename__ = "inspection_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_no: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    parcel_id: Mapped[int] = mapped_column(ForeignKey("parcels.id"), nullable=False)
    variety: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    issued_by: Mapped[str] = mapped_column(String(128), nullable=False)
    conclusion: Mapped[str] = mapped_column(String(256), default="合格")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Authorization(Base):
    """合作社品牌用标授权（主体 + 品种 + 期间）。[start_date, end_date] 闭区间。"""

    __tablename__ = "authorizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    auth_no: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    cooperative: Mapped[str] = mapped_column(String(128), nullable=False)
    brand: Mapped[str] = mapped_column(String(128), nullable=False)
    variety: Mapped[str] = mapped_column(String(64), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Batch(Base):
    """生产批次：绑定地块/品种/采收日期，可追溯产量即标签可发上限。

    拆批：parent_batch_id 指向原批次，原批次关闭；合批同理，来源份额见 BatchSource。
    历史导入批次（重划界前已生产）is_legacy=True，其存量标签参与过渡处置候选计算。
    """

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    parcel_id: Mapped[int | None] = mapped_column(ForeignKey("parcels.id"), nullable=True)
    cooperative: Mapped[str] = mapped_column(String(128), nullable=False)
    variety: Mapped[str] = mapped_column(String(64), nullable=False)
    harvest_date: Mapped[date] = mapped_column(Date, nullable=False)
    traceable_output: Mapped[int] = mapped_column(Integer, nullable=False)  # 枚，标签上限
    labels_issued: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_legacy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    parent_batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    parcel: Mapped["Parcel | None"] = relationship(back_populates="batches")
    labels: Mapped[list["LabelIssue"]] = relationship(back_populates="batch", foreign_keys="LabelIssue.batch_id")
    sources: Mapped[list["BatchSource"]] = relationship(
        back_populates="batch", foreign_keys="BatchSource.batch_id")
    quotas: Mapped[list["BatchQuota"]] = relationship(back_populates="batch")


class BatchSource(Base):
    """拆批/合批来源份额：新批次每枚标签都能回溯到来源批次及其份额比例。

    合批时只要任一来源地块按新边界不合格，整批签发即被阻断（不能借合批洗掉来源）。
    """

    __tablename__ = "batch_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    source_batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    source_parcel_id: Mapped[int | None] = mapped_column(ForeignKey("parcels.id"), nullable=True)
    share: Mapped[float] = mapped_column(Float, nullable=False)  # 来源份额比例 0~1
    labels_attributed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    batch: Mapped[Batch] = relationship(back_populates="sources", foreign_keys=[batch_id])


class BatchQuota(Base):
    """批次在某适用规则/核查口径下的授权额度。

    重划界后对同一批次登记新额度时，旧额度置 current=False：新额度【替代】旧额度，
    剩余 = 新额度 − 历史已发总量，绝不把新旧额度相加。
    """

    __tablename__ = "batch_quotas"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    basis: Mapped[str] = mapped_column(String(32), nullable=False)  # original / transition
    pa_version_id: Mapped[int] = mapped_column(ForeignKey("protected_area_versions.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("transition_rules.id"), nullable=True)
    current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    batch: Mapped[Batch] = relationship(back_populates="quotas")


class LabelIssue(Base):
    """标签段（一次领用一段连续标签号）。

    生命周期：ISSUED（在合作社）→ TRANSFERRED（已调拨包装厂，仍跟随原批次）→ USED。
    已使用 used_count 不可召回；未使用 = quantity - used_count。
    """

    __tablename__ = "label_issues"
    __table_args__ = (UniqueConstraint("label_code", name="uq_label_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    label_code: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ISSUED", nullable=False)  # ISSUED/TRANSFERRED/USED
    holder: Mapped[str] = mapped_column(String(128), default="", nullable=False)  # 当前持有方
    auth_id: Mapped[int | None] = mapped_column(ForeignKey("authorizations.id"), nullable=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("inspection_reports.id"), nullable=True)
    source_batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    operator: Mapped[str] = mapped_column(String(64), default="品牌管理方")

    batch: Mapped[Batch] = relationship(back_populates="labels", foreign_keys=[batch_id])


class LabelTransfer(Base):
    """标签调拨流水：合作社 → 包装厂，持有人变化，标签仍跟随原批次。"""

    __tablename__ = "label_transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    label_issue_id: Mapped[int] = mapped_column(ForeignKey("label_issues.id"), nullable=False, index=True)
    from_party: Mapped[str] = mapped_column(String(128), nullable=False)
    to_party: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    transferred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    operator: Mapped[str] = mapped_column(String(64), default="品牌管理方")


class InspectionEvent(Base):
    """异常巡查：命中后暂停相关批次新增用标（历史领用保留）。"""

    __tablename__ = "inspection_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_no: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), nullable=True)
    cooperative: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finding: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    resolved_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TransitionRule(Base):
    """品牌方确认的重划界过渡规则（虚构，人工确认）。

    harvest_before（含当日）采收的批次按旧边界结论处理：已使用标签保留；
    已调拨/未使用的存量标签由人工决定召回；待发标签暂停。
    之后采收的批次按新边界：不合格地块待发停发；新授权额度按新区内面积比例折算。
    """

    __tablename__ = "transition_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_no: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    pa_code: Mapped[str] = mapped_column(String(32), nullable=False)
    from_version_id: Mapped[int] = mapped_column(ForeignKey("protected_area_versions.id"), nullable=False)
    to_version_id: Mapped[int] = mapped_column(ForeignKey("protected_area_versions.id"), nullable=False)
    harvest_cutoff: Mapped[date] = mapped_column(Date, nullable=False)
    carry_used: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)       # 已使用标签保留
    quarantine_stock: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # 已调拨未使用：建议召回
    stop_pending: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)      # 待发标签暂停
    area_ratio_quota: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # 新额度按新区内面积比例折算
    confirmed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DispositionDraft(Base):
    """批次级处置【草案】：系统依据过渡规则计算的候选，人工决定。

    status: DRAFT / CONFIRMED / REVOKED。撤销只把草案作废，不回滚已执行的暂停/召回动作。
    """

    __tablename__ = "disposition_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_no: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    rule_id: Mapped[int] = mapped_column(ForeignKey("transition_rules.id"), nullable=False)
    applicable_version_id: Mapped[int] = mapped_column(ForeignKey("protected_area_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    segment: Mapped[str] = mapped_column(String(16), nullable=False)  # LEGACY（旧界采收）/ NEW
    parcel_status: Mapped[str] = mapped_column(String(24), nullable=False)
    inside_ratio_new: Mapped[float] = mapped_column(Float, nullable=False)
    stock_labels: Mapped[int] = mapped_column(Integer, default=0, nullable=False)   # 已发未使用（含在社/在厂）
    used_labels: Mapped[int] = mapped_column(Integer, default=0, nullable=False)    # 已使用，不可召回
    pending_labels: Mapped[int] = mapped_column(Integer, default=0, nullable=False) # 待发（新额度剩余）
    transferred_unused: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    suggested: Mapped[dict] = mapped_column(JSON, nullable=False)  # 建议动作：suspend/recall/new_quota...
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="系统计算")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DispositionAction(Base):
    """人工确认草案后实际执行的动作留痕；撤销草案不删除/回滚这些记录。"""

    __tablename__ = "disposition_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("disposition_drafts.id"), nullable=False, index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)  # SUSPEND_PENDING/RECALL_LABELS/NEW_QUOTA/RESUME
    detail: Mapped[dict] = mapped_column(JSON, nullable=False)
    operator: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
