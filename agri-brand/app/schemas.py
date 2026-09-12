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
    parcel_id: int | None
    cooperative: str
    variety: str
    harvest_date: date
    traceable_output: int
    labels_issued: int
    suspended: bool
    closed: bool
    is_legacy: bool
    parent_batch_id: int | None = None

    class Config:
        from_attributes = True


class EligibilityOut(BaseModel):
    batch_no: str
    variety: str
    cooperative: str
    harvest_date: date
    eligible: bool
    suspended: bool
    closed: bool = False
    legacy: bool = False
    parcel: dict[str, Any]
    sources: list[dict[str, Any]] | None = None
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
    auth_id: int | None = None
    report_id: int | None = None
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


# ── 重划界过渡 ─────────────────────────────────────────────────────────────
class CheckHistoryOut(BaseModel):
    id: int
    parcel_id: int
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


class TransitionRuleCreate(BaseModel):
    rule_no: str
    pa_code: str
    from_version_id: int
    to_version_id: int
    harvest_cutoff: date
    carry_used: bool = True
    quarantine_stock: bool = True
    stop_pending: bool = True
    area_ratio_quota: bool = True
    confirmed_by: str
    note: str = ""


class TransitionRuleOut(TransitionRuleCreate):
    id: int

    class Config:
        from_attributes = True


class DraftConfirm(BaseModel):
    operator: str = "品牌管理方"
    actions: list[str] | None = None  # None=执行全部建议；可选 SUSPEND_PENDING/RECALL_LABELS/NEW_QUOTA/KEEP_USED
    note: str | None = None


class DraftRevoke(BaseModel):
    operator: str = "品牌管理方"
    note: str | None = None


class DraftOut(BaseModel):
    id: int
    draft_no: str
    batch_id: int
    rule_id: int
    applicable_version_id: int
    status: str
    segment: str
    parcel_status: str
    inside_ratio_new: float
    stock_labels: int
    used_labels: int
    pending_labels: int
    transferred_unused: int
    suggested: list[dict[str, Any]]
    decision_note: str | None
    created_by: str
    reviewed_by: str | None
    created_at: datetime
    decided_at: datetime | None

    class Config:
        from_attributes = True


class ActionOut(BaseModel):
    id: int
    draft_id: int
    batch_id: int
    action: str
    detail: dict[str, Any]
    operator: str
    created_at: datetime

    class Config:
        from_attributes = True


# ── 标签生命周期 / 拆合批 ──────────────────────────────────────────────────
class TransferRequest(BaseModel):
    to_party: str
    quantity: int | None = None
    operator: str = "品牌管理方"


class UseRequest(BaseModel):
    used: int
    operator: str = "品牌管理方"


class RecallRequest(BaseModel):
    label_ids: list[int]
    operator: str = "品牌管理方"
    note: str = ""


class SplitRequest(BaseModel):
    source_batch_id: int
    new_batch_no: str
    quantity: int
    operator: str = "品牌管理方"


class MergeRequest(BaseModel):
    source_batch_ids: list[int]
    new_batch_no: str
    operator: str = "品牌管理方"


class LegacyImportRequest(BaseModel):
    total: int
    used: int = 0
    transferred_unused: int = 0
    operator: str = "历史导入"


class LabelIssueExtended(LabelIssueOut):
    used_count: int
    status: str
    holder: str
    source_batch_id: int | None = None
