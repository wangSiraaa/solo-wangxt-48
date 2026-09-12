"""检测报告与授权路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Authorization, InspectionReport, Parcel
from ..schemas import AuthCreate, AuthOut, ReportCreate, ReportOut

router = APIRouter(tags=["报告与授权"])


@router.post("/reports", response_model=ReportOut, status_code=201, summary="录入检测报告")
def create_report(payload: ReportCreate, db: Session = Depends(get_db)):
    if db.get(Parcel, payload.parcel_id) is None:
        raise HTTPException(404, f"地块 {payload.parcel_id} 不存在")
    if payload.valid_from > payload.valid_until:
        raise HTTPException(422, "报告有效期开始日不能晚于到期日")
    if db.execute(select(InspectionReport).where(InspectionReport.report_no == payload.report_no)).scalar_one_or_none():
        raise HTTPException(409, f"报告编号 {payload.report_no} 已存在")
    report = InspectionReport(**payload.model_dump())
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.get("/reports", response_model=list[ReportOut], summary="检测报告清单")
def list_reports(db: Session = Depends(get_db)):
    return db.execute(select(InspectionReport).order_by(InspectionReport.id)).scalars().all()


@router.post("/authorizations", response_model=AuthOut, status_code=201, summary="登记用标授权")
def create_authorization(payload: AuthCreate, db: Session = Depends(get_db)):
    if payload.start_date > payload.end_date:
        raise HTTPException(422, "授权开始日不能晚于结束日")
    if db.execute(select(Authorization).where(Authorization.auth_no == payload.auth_no)).scalar_one_or_none():
        raise HTTPException(409, f"授权号 {payload.auth_no} 已存在")
    auth = Authorization(**payload.model_dump())
    db.add(auth)
    db.commit()
    db.refresh(auth)
    return auth


@router.get("/authorizations", response_model=list[AuthOut], summary="授权清单")
def list_authorizations(db: Session = Depends(get_db)):
    return db.execute(select(Authorization).order_by(Authorization.id)).scalars().all()


@router.post("/authorizations/{auth_id}/revoke", response_model=AuthOut, summary="撤销授权")
def revoke_authorization(auth_id: int, db: Session = Depends(get_db)):
    auth = db.get(Authorization, auth_id)
    if auth is None:
        raise HTTPException(404, "授权不存在")
    auth.revoked = True
    db.commit()
    db.refresh(auth)
    return auth
