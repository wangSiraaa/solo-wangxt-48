"""虚构演示数据。

运行：python -m app.seed
幂等：已存在则跳过。
保护区：虚构“雾岭毛尖地理标志保护范围”，东界约在 118.0200°E（≈94860m）。
"""
from datetime import date

from sqlalchemy import select

from . import config
from .db import SessionLocal, init_db
from .models import (
    Authorization,
    Batch,
    InspectionEvent,
    InspectionReport,
    Parcel,
    ProtectedAreaVersion,
)
from .routers.parcels import active_pa_version
from .services import issue_labels
from shapely.geometry import shape


def poly(coords: list[tuple[float, float]]) -> dict:
    return {"type": "Polygon", "coordinates": [coords + [coords[0]]]}


PA_COORDS = [
    (117.9800, 29.9900),
    (118.0200, 29.9900),
    (118.0200, 30.0100),
    (117.9800, 30.0100),
]

# P1 完全在内部
P1 = poly([(117.9900, 30.0000), (118.0000, 30.0000), (118.0000, 30.0080), (117.9900, 30.0080)])
# P2 贴东界的“台阶”形长条：大部分在区内，东北角有一小块越界（≈53㎡，约 0.25%），
# 落在 2%/200㎡ 容差内 → 边界贴合 CROSS_TOLERATED；且有一段边与界线重合。
P2 = poly([
    (118.0180, 29.9950),
    (118.0200, 29.9950),
    (118.0200, 29.9955),
    (118.02001, 29.9955),
    (118.02001, 29.9960),
    (118.0180, 29.9960),
])
# P3 向东伸出的 L 形：区外约 5%（约 8.5 万㎡），但质心在内部 → 质心法会误判，整体不合格
P3 = poly(
    [
        (118.0000, 30.0000),
        (118.0100, 30.0000),
        (118.0100, 30.0060),
        (118.0220, 30.0060),
        (118.0220, 30.0100),
        (118.0000, 30.0100),
    ]
)
# P4 U 形：两条伸出区外的长臂，质心仍在内部，整体不合格
P4 = poly(
    [
        (118.0120, 29.9940),
        (118.0280, 29.9940),
        (118.0280, 29.9950),
        (118.0130, 29.9950),
        (118.0130, 30.0060),
        (118.0280, 30.0060),
        (118.0280, 30.0070),
        (118.0120, 30.0070),
    ]
)
# P5 完全在区外
P5 = poly([(118.0300, 30.0000), (118.0350, 30.0000), (118.0350, 30.0050), (118.0300, 30.0050)])

# 前端“退回修正”演示用，不入库
INVALID_BOWTIE = poly([(118.0000, 30.0000), (118.0100, 30.0090), (118.0100, 30.0000), (118.0000, 30.0090)])

COOP = "雾岭云雾茶叶合作社"
TODAY = date(2026, 9, 12)


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.execute(select(ProtectedAreaVersion)).scalars().first():
            print("seed: 已存在，跳过")
            return

        pa_v1 = ProtectedAreaVersion(
            code="WL-MJ",
            name="雾岭毛尖地理标志保护范围（虚构）",
            version="2015-v1",
            geometry=poly([(117.9700, 29.9800), (118.0100, 29.9800), (118.0100, 30.0200), (117.9700, 30.0200)]),
            valid_from=date(2015, 6, 1),
            valid_to=date(2023, 1, 1),
        )
        pa_v2 = ProtectedAreaVersion(
            code="WL-MJ",
            name="雾岭毛尖地理标志保护范围（虚构）",
            version="2023-v2",
            geometry={"type": "Polygon", "coordinates": [PA_COORDS + [PA_COORDS[0]]]},
            valid_from=date(2023, 1, 1),
            valid_to=None,
        )
        db.add_all([pa_v1, pa_v2])
        db.flush()

        # ── 地块（走与申报接口相同的几何检查口径）──────────────────────
        from . import geometry

        pa_poly = shape(pa_v2.geometry)

        def add_parcel(code, name, geojson):
            r = geometry.check_parcel(geometry.load_polygon(geojson, "EPSG:4326"), pa_poly,
                                      config.TOLERANCE_RATIO, config.TOLERANCE_SQM)
            p = Parcel(
                code=code, name=name, cooperative=COOP, crs="EPSG:4326", geometry=geojson,
                pa_version_id=pa_v2.id,
                status=r["status"], qualified=r["qualified"], reason=r["reason"],
                total_area_sqm=r["total_area_sqm"], inside_area_sqm=r["inside_area_sqm"],
                outside_area_sqm=r["outside_area_sqm"], outside_ratio=r["outside_ratio"],
                centroid_inside=r["centroid_inside"], shared_edge_m=r["shared_edge_m"],
            )
            db.add(p)
            db.flush()
            print(f"seed parcel {code}: {r['status']} 内 {r['inside_area_sqm']:.0f}㎡ / 外 {r['outside_area_sqm']:.0f}㎡ ({r['outside_ratio']*100:.1f}%)")
            return p

        p1 = add_parcel("P-001", "雾岭东坡 1 号茶园", P1)
        p2 = add_parcel("P-002", "界牌岭贴边 2 号茶园", P2)
        p3 = add_parcel("P-003", "新扩东岭 3 号茶园", P3)
        p4 = add_parcel("P-004", "双岗冲 4 号茶园", P4)
        p5 = add_parcel("P-005", "河东 5 号地块", P5)

        # ── 检测报告 ─────────────────────────────────────────────────
        reports = [
            InspectionReport(report_no="RPT-2026-001", parcel_id=p1.id, variety="雾岭毛尖",
                             valid_from=date(2026, 1, 1), valid_until=date(2026, 12, 31),
                             issued_by="虚构雾岭农产品质检中心"),
            InspectionReport(report_no="RPT-2026-002", parcel_id=p2.id, variety="雾岭毛尖",
                             valid_from=date(2026, 1, 1), valid_until=date(2026, 12, 31),
                             issued_by="虚构雾岭农产品质检中心"),
            InspectionReport(report_no="RPT-2025-OLD", parcel_id=p2.id, variety="雾岭毛尖",
                             valid_from=date(2025, 1, 1), valid_until=date(2026, 3, 31),
                             issued_by="虚构雾岭农产品质检中心", conclusion="合格（历史报告，已过期）"),
            InspectionReport(report_no="RPT-2026-005", parcel_id=p5.id, variety="雾岭毛尖",
                             valid_from=date(2026, 1, 1), valid_until=date(2026, 12, 31),
                             issued_by="虚构雾岭农产品质检中心"),
        ]
        db.add_all(reports)

        # ── 授权 ─────────────────────────────────────────────────────
        auths = [
            Authorization(auth_no="AUTH-2026-01", cooperative=COOP, brand="雾岭毛尖",
                          variety="雾岭毛尖", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)),
            Authorization(auth_no="AUTH-2025-01", cooperative=COOP, brand="雾岭毛尖",
                          variety="雾岭毛尖", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)),
        ]
        db.add_all(auths)
        db.flush()

        # ── 批次 ─────────────────────────────────────────────────────
        b1 = Batch(batch_no="B-2026-041", parcel_id=p1.id, cooperative=COOP, variety="雾岭毛尖",
                   harvest_date=date(2026, 4, 5), traceable_output=5000)
        # 边界贴合样例：资格齐全
        b2 = Batch(batch_no="B-2026-042", parcel_id=p2.id, cooperative=COOP, variety="雾岭毛尖",
                   harvest_date=date(2026, 4, 8), traceable_output=800)
        # 报告过期样例：采收日在 2026，只被过期报告 RPT-2025-OLD 覆盖
        b3 = Batch(batch_no="B-2025-Q4", parcel_id=p2.id, cooperative=COOP, variety="雾岭毛尖",
                   harvest_date=date(2025, 10, 20), traceable_output=600)
        # 并发最后额度样例：剩余恰好 5 枚
        b4 = Batch(batch_no="B-2026-099", parcel_id=p1.id, cooperative=COOP, variety="雾岭毛尖",
                   harvest_date=date(2026, 4, 6), traceable_output=500)
        db.add_all([b1, b2, b3, b4])
        db.flush()

        # ── 巡查：暂停 B-2026-042（已发标签保留）────────────────────
        # 先给 b2 发一批标签，再登记巡查，演示“暂停新增、保留已发”
        issue_labels(db, b2.id, 200, "种子数据", today=TODAY)
        db.add(InspectionEvent(
            event_no="XJ-2026-007", batch_id=b2.id, cooperative=COOP,
            finding="例行巡查发现该批次农事记录待补正：暂停新增用标，复核后恢复",
        ))

        # 普通在用批次已发 1200
        issue_labels(db, b1.id, 800, "种子数据", today=TODAY)
        issue_labels(db, b1.id, 400, "种子数据", today=TODAY)
        # 并发样例：预置已发 495，剩 5
        issue_labels(db, b4.id, 495, "种子数据", today=TODAY)

        db.commit()
        print("seed 完成：2 个保护区版本 / 5 个地块 / 4 份报告 / 2 份授权 / 4 个批次 / 1 起在查巡查")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
