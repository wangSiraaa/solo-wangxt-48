"""请求/响应模型。"""
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


# ── 地块 ──────────────────────────────────────────────────────────────────
class ParcelCreate(BaseModel):
    code: str = Field(..., examples=["P-007"])
    name: str
    cooperative: str
    crs: str | None = Field(None, description="坐标系声明，如 EPSG:4326；缺失将被退回修正")
    geometry: dict[str, Any] = Field(..., description="GeoJSON Polygon")
    declared_area_sqm: float | None = None


class ParcelOut(BaseModel):
    id: int
    code: str
    name: str
    cooperative: str
    crs: str
    geometry: dict[str, Any]
    declared_area_sqm: float | None
    pa_version_id: int
    status: str
    qualified: bool
    reason: str
    total_area_sqm: float
    inside_area_sqm: float
    outside_area_sqm: float
    outside_ratio: float
    centroid_inside: bool
    shared_edge_m: float
    checked_at: datetime

    class Config:
        from_attributes = True


class ValidateResponse(BaseModel):
    """申报预检：无效时返回修正建议，不创建地块。"""

    valid: bool
    status: str | None = None
    reason: str
    detail: dict[str, Any] | None = None


# ── 保护区 ────────────────────────────────────────────────────────────────
class ProtectedAreaOut(BaseModel):
    id: int
    code: str
    name: str
    version: str
    geometry: dict[str, Any]
    valid_from: date
    valid_to: date | None

    class Config:
        from_attributes = True


# ── 检测报告 ──────────────────────────────────────────────────────────────
class ReportCreate(BaseModel):
    report_no: str
    parcel_id: int
    variety: str
    valid_from: date
    valid_until: date
    issued_by: str = "虚构检测机构"
    conclusion: str = "合格"


class ReportOut(ReportCreate):
    id: int

    class Config:
        from_attributes = True


# ── 授权 ──────────────────────────────────────────────────────────────────
class AuthCreate(BaseModel):
    auth_no: str
    cooperative: str
    brand: str
    variety: str
    start_date: date
    end_date: date


class AuthOut(AuthCreate):
    id: int
    revoked: bool

    class Config:
        from_attributes = True


# ── 批次 ──────────────────────────────────────────────────────────────────
class BatchCreate(BaseModel):
    batch_no: str
    parcel_id: int
    cooperative: str
    variety: str
    harvest_date: date
    traceable_output: int = Field(..., gt=0)


class BatchOut(BaseModel):
    id: int
    batch_no: str
    parcel_id: int
    cooperative: str
    variety: str
    harvest_date: date
    traceable_output: int
    labels_issued: int
    suspended: bool

    class Config:
        from_attributes = True


class EligibilityOut(BaseModel):
    batch_no: str
    variety: str
    cooperative: str
    harvest_date: date
    eligible: bool
    suspended: bool
    parcel: dict[str, Any]
    report: dict[str, Any] | None
    authorization: dict[str, Any] | None
    quota: dict[str, Any]
    blockers: list[str]


# ── 标签 ──────────────────────────────────────────────────────────────────
class LabelIssueRequest(BaseModel):
    quantity: int = Field(..., gt=0)
    operator: str = "品牌管理方"


class LabelIssueOut(BaseModel):
    id: int
    label_code: str
    batch_id: int
    batch_no: str
    quantity: int
    auth_id: int
    report_id: int
    issued_at: datetime
    operator: str


class LabelListOut(LabelIssueOut):
    cooperative: str
    variety: str


# ── 巡查 ──────────────────────────────────────────────────────────────────
class InspectionCreate(BaseModel):
    event_no: str
    batch_id: int | None = None
    cooperative: str | None = None
    finding: str


class InspectionResolve(BaseModel):
    note: str = "整改复核通过"


class InspectionOut(BaseModel):
    id: int
    event_no: str
    batch_id: int | None
    cooperative: str | None
    finding: str
    active: bool
    resolved_note: str | None
    created_at: datetime
    resolved_at: datetime | None

    class Config:
        from_attributes = True
