"""保护区与地块核查路由。"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from shapely.geometry import shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, geometry
from ..db import get_db
from ..models import Parcel, ParcelCheck, ProtectedAreaVersion
from ..schemas import (
    CheckHistoryOut,
    ParcelCreate,
    ParcelOut,
    ProtectedAreaOut,
    ValidateResponse,
)

router = APIRouter(tags=["地块与保护区"])


def active_pa_version(db: Session, on_date: date | None = None) -> ProtectedAreaVersion:
    on_date = on_date or config.CURRENT_DATE
    stmt = select(ProtectedAreaVersion).where(ProtectedAreaVersion.valid_from <= on_date)
    stmt = stmt.where(
        (ProtectedAreaVersion.valid_to.is_(None)) | (ProtectedAreaVersion.valid_to > on_date)
    ).order_by(ProtectedAreaVersion.valid_from.desc())
    pa = db.execute(stmt).scalars().first()
    if pa is None:
        raise HTTPException(500, "没有生效中的保护区版本（虚构数据）")
    return pa


def _run_check(db: Session, payload: ParcelCreate):
    """加载 + 几何检查；任何几何问题以 422 退回修正。"""
    try:
        polygon = geometry.load_polygon(payload.geometry, payload.crs)
    except geometry.MissingCRSError as exc:
        raise HTTPException(status_code=422, detail={"code": geometry.MISSING_CRS, "message": str(exc)})
    except geometry.GeometryError as exc:
        raise HTTPException(status_code=422, detail={"code": geometry.INVALID, "message": str(exc)})

    pa = active_pa_version(db)
    pa_polygon = shape(pa.geometry)
    result = geometry.check_parcel(polygon, pa_polygon, config.TOLERANCE_RATIO, config.TOLERANCE_SQM)
    return polygon, pa, result


@router.get("/protected-areas", response_model=list[ProtectedAreaOut], summary="保护区版本（图层叠加）")
def list_protected_areas(db: Session = Depends(get_db)):
    return db.execute(select(ProtectedAreaVersion).order_by(ProtectedAreaVersion.version)).scalars().all()


@router.post("/parcels/validate", response_model=ValidateResponse, summary="申报预检：不入库")
def validate_parcel(payload: ParcelCreate, db: Session = Depends(get_db)):
    try:
        _, pa, result = _run_check(db, payload)
    except HTTPException as exc:
        return ValidateResponse(valid=False, status=exc.detail["code"], reason=exc.detail["message"])
    result["reason"] = f"[按保护区版本 {pa.version} 预检] " + result["reason"]
    return ValidateResponse(valid=True, status=result["status"], reason=result["reason"], detail=result)


@router.post("/parcels", response_model=ParcelOut, status_code=201, summary="申报地块并执行核查")
def create_parcel(payload: ParcelCreate, db: Session = Depends(get_db)):
    if db.execute(select(Parcel).where(Parcel.code == payload.code)).scalar_one_or_none():
        raise HTTPException(409, f"地块编号 {payload.code} 已存在")
    _, pa, r = _run_check(db, payload)
    parcel = Parcel(
        code=payload.code,
        name=payload.name,
        cooperative=payload.cooperative,
        crs=payload.crs,
        geometry=payload.geometry,
        declared_area_sqm=payload.declared_area_sqm,
        pa_version_id=pa.id,
        status=r["status"],
        qualified=r["qualified"],
        reason=r["reason"],
        total_area_sqm=r["total_area_sqm"],
        inside_area_sqm=r["inside_area_sqm"],
        outside_area_sqm=r["outside_area_sqm"],
        outside_ratio=r["outside_ratio"],
        centroid_inside=r["centroid_inside"],
        shared_edge_m=r["shared_edge_m"],
    )
    db.add(parcel)
    db.flush()
    # 同步落一条核查历史（旧结论可回看）；快照字段即最新核查
    db.add(ParcelCheck(
        parcel_id=parcel.id, pa_version_id=pa.id,
        status=r["status"], qualified=r["qualified"], reason=r["reason"],
        total_area_sqm=r["total_area_sqm"], inside_area_sqm=r["inside_area_sqm"],
        outside_area_sqm=r["outside_area_sqm"], outside_ratio=r["outside_ratio"],
        centroid_inside=r["centroid_inside"], shared_edge_m=r["shared_edge_m"],
    ))
    db.commit()
    db.refresh(parcel)
    return parcel


@router.get("/parcels", response_model=list[ParcelOut], summary="地块清单")
def list_parcels(db: Session = Depends(get_db)):
    return db.execute(select(Parcel).order_by(Parcel.code)).scalars().all()


@router.get("/parcels/{parcel_id}", response_model=ParcelOut, summary="地块核查详情（最新结论）")
def get_parcel(parcel_id: int, db: Session = Depends(get_db)):
    parcel = db.get(Parcel, parcel_id)
    if parcel is None:
        raise HTTPException(404, "地块不存在")
    return parcel


@router.get("/parcels/{parcel_id}/checks", response_model=list[CheckHistoryOut],
            summary="地块核查历史（旧规则结论回看）")
def parcel_checks(parcel_id: int, db: Session = Depends(get_db)):
    if db.get(Parcel, parcel_id) is None:
        raise HTTPException(404, "地块不存在")
    return db.execute(
        select(ParcelCheck).where(ParcelCheck.parcel_id == parcel_id)
        .order_by(ParcelCheck.checked_at.desc(), ParcelCheck.id.desc())
    ).scalars().all()


@router.post("/parcels/{parcel_id}/recheck/{version_id}", response_model=CheckHistoryOut,
             summary="按指定保护区版本重新核查（只新增历史，不覆盖旧结论）")
def recheck_parcel(parcel_id: int, version_id: int, db: Session = Depends(get_db)):
    from ..transitions import recheck_parcel as _recheck

    parcel = db.get(Parcel, parcel_id)
    if parcel is None:
        raise HTTPException(404, "地块不存在")
    version = db.get(ProtectedAreaVersion, version_id)
    if version is None:
        raise HTTPException(404, "保护区版本不存在")
    check = _recheck(db, parcel, version)
    # 现行版本同步快照
    if version.valid_to is None:
        for k in ("status", "qualified", "reason", "total_area_sqm", "inside_area_sqm",
                  "outside_area_sqm", "outside_ratio", "centroid_inside", "shared_edge_m"):
            setattr(parcel, k, getattr(check, k))
        parcel.pa_version_id = version.id
    db.commit()
    db.refresh(check)
    return check
