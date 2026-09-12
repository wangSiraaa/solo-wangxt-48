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
    batches: Mapped[list["Batch"]] = relationship(back_populates="parcel")


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
    """生产批次：绑定地块/品种/采收日期，可追溯产量即标签可发上限。"""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    parcel_id: Mapped[int] = mapped_column(ForeignKey("parcels.id"), nullable=False)
    cooperative: Mapped[str] = mapped_column(String(128), nullable=False)
    variety: Mapped[str] = mapped_column(String(64), nullable=False)
    harvest_date: Mapped[date] = mapped_column(Date, nullable=False)
    traceable_output: Mapped[int] = mapped_column(Integer, nullable=False)  # 枚，标签上限
    labels_issued: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    parcel: Mapped[Parcel] = relationship(back_populates="batches")
    labels: Mapped[list["LabelIssue"]] = relationship(back_populates="batch")


class LabelIssue(Base):
    """标签领用流水（已发记录在巡查暂停期间保留，不删除）。"""

    __tablename__ = "label_issues"
    __table_args__ = (UniqueConstraint("label_code", name="uq_label_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    label_code: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    auth_id: Mapped[int] = mapped_column(ForeignKey("authorizations.id"), nullable=False)
    report_id: Mapped[int] = mapped_column(ForeignKey("inspection_reports.id"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    operator: Mapped[str] = mapped_column(String(64), default="品牌管理方")

    batch: Mapped[Batch] = relationship(back_populates="labels")


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
