"""虚构演示数据。

运行：python -m app.seed（幂等：已存在则跳过）

保护区划界：
  2015-v1（2015-06-01 ~ 2022-12-31）东界 118.01°E
  2023-v2（2023-01-01 ~ 2026-08-31）东界 118.02°E、西界 117.98°E
  2026-v3（2026-09-01 起）东界不变，西界东移至 117.99°E（重划界）
过渡规则 TR-2026-01：采收分界日 2026-09-01，品牌方确认。
"""
from datetime import date

from shapely.geometry import shape
from sqlalchemy import select

from . import config, geometry
from .db import SessionLocal, init_db
from .models import (
    Authorization,
    Batch,
    BatchQuota,
    InspectionEvent,
    InspectionReport,
    Parcel,
    ParcelCheck,
    ProtectedAreaVersion,
    TransitionRule,
)
from .services import issue_labels


def poly(coords):
    return {"type": "Polygon", "coordinates": [coords + [coords[0]]]}


V1 = poly([(117.9700, 29.9800), (118.0100, 29.9800), (118.0100, 30.0200), (117.9700, 30.0200)])
V2 = poly([(117.9800, 29.9900), (118.0200, 29.9900), (118.0200, 30.0100), (117.9800, 30.0100)])
V3 = poly([(117.9900, 29.9900), (118.0200, 29.9900), (118.0200, 30.0100), (117.9900, 30.0100)])

P1 = poly([(117.9900, 30.0000), (118.0000, 30.0000), (118.0000, 30.0080), (117.9900, 30.0080)])
P2 = poly([
    (118.0180, 29.9950),
    (118.0200, 29.9950),
    (118.0200, 29.9955),
    (118.02001, 29.9955),
    (118.02001, 29.9960),
    (118.0180, 29.9960),
])
P3 = poly([
    (118.0000, 30.0000), (118.0100, 30.0000), (118.0100, 30.0060),
    (118.0220, 30.0060), (118.0220, 30.0100), (118.0000, 30.0100),
])
P4 = poly([
    (118.0120, 29.9940), (118.0280, 29.9940), (118.0280, 29.9950),
    (118.0130, 29.9950), (118.0130, 30.0060), (118.0280, 30.0060),
    (118.0280, 30.0070), (118.0120, 30.0070),
])
P5 = poly([(118.0300, 30.0000), (118.0350, 30.0000), (118.0350, 30.0050), (118.0300, 30.0050)])
# 跨【新西界】：v2 全部在范围内，v3 只剩 98.16%（81㎡ 窄条越界，容差内）→ 额度按比例折算
P6 = poly([(117.989979, 29.9980), (117.99112, 29.9980), (117.99112, 29.99836), (117.989979, 29.99836)])
# 跨【新西界】的大地块：v2 全部在范围内（合法），v3 仅 38.46% 在内 → 新边界下不合格
P7 = poly([(117.9820, 29.9950), (117.9950, 29.9950), (117.9950, 30.0050), (117.9820, 30.0050)])

INVALID_BOWTIE = poly([(118.0000, 30.0000), (118.0100, 30.0090), (118.0100, 30.0000), (118.0000, 30.0090)])

COOP = "雾岭云雾茶叶合作社"
PACKER = "雾岭镇包装厂"
TODAY = date(2026, 9, 12)


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.execute(select(ProtectedAreaVersion)).scalars().first():
            print("seed: 已存在，跳过")
            return

        v1 = ProtectedAreaVersion(code="WL-MJ", name="雾岭毛尖保护范围（虚构）", version="2015-v1",
                                  geometry=V1, valid_from=date(2015, 6, 1), valid_to=date(2023, 1, 1))
        v2 = ProtectedAreaVersion(code="WL-MJ", name="雾岭毛尖保护范围（虚构）", version="2023-v2",
                                  geometry=V2, valid_from=date(2023, 1, 1), valid_to=date(2026, 9, 1))
        v3 = ProtectedAreaVersion(code="WL-MJ", name="雾岭毛尖保护范围（虚构，2026 重划界）",
                                  version="2026-v3", geometry=V3,
                                  valid_from=date(2026, 9, 1), valid_to=None)
        db.add_all([v1, v2, v3])
        db.flush()

        def add_parcel(code, name, geojson):
            """按 v2 申报核查（快照 + 历史行）。"""
            r = geometry.check_parcel(geometry.load_polygon(geojson, "EPSG:4326"), shape(V2),
                                      config.TOLERANCE_RATIO, config.TOLERANCE_SQM)
            p = Parcel(
                code=code, name=name, cooperative=COOP, crs="EPSG:4326", geometry=geojson,
                pa_version_id=v2.id,
                status=r["status"], qualified=r["qualified"], reason=f"[v2 申报] {r['reason']}",
                total_area_sqm=r["total_area_sqm"], inside_area_sqm=r["inside_area_sqm"],
                outside_area_sqm=r["outside_area_sqm"], outside_ratio=r["outside_ratio"],
                centroid_inside=r["centroid_inside"], shared_edge_m=r["shared_edge_m"],
            )
            db.add(p)
            db.flush()
            db.add(ParcelCheck(
                parcel_id=p.id, pa_version_id=v2.id,
                status=r["status"], qualified=r["qualified"], reason=p.reason,
                total_area_sqm=r["total_area_sqm"], inside_area_sqm=r["inside_area_sqm"],
                outside_area_sqm=r["outside_area_sqm"], outside_ratio=r["outside_ratio"],
                centroid_inside=r["centroid_inside"], shared_edge_m=r["shared_edge_m"],
            ))
            print(f"seed parcel {code}(v2): {r['status']} 内 {r['inside_area_sqm']:.0f}㎡ / 外 {r['outside_area_sqm']:.0f}㎡")
            return p

        p1 = add_parcel("P-001", "雾岭东坡 1 号茶园", P1)
        p2 = add_parcel("P-002", "界牌岭贴边 2 号茶园", P2)
        p3 = add_parcel("P-003", "新扩东岭 3 号茶园", P3)
        p4 = add_parcel("P-004", "双岗冲 4 号茶园", P4)
        p5 = add_parcel("P-005", "河东 5 号地块", P5)
        p6 = add_parcel("P-006", "西坡脚 6 号茶园", P6)
        p7 = add_parcel("P-007", "西岭大片 7 号茶园", P7)
        db.flush()

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
            InspectionReport(report_no="RPT-2026-006", parcel_id=p6.id, variety="雾岭毛尖",
                             valid_from=date(2026, 8, 1), valid_until=date(2027, 7, 31),
                             issued_by="虚构雾岭农产品质检中心"),
            InspectionReport(report_no="RPT-2026-007", parcel_id=p7.id, variety="雾岭毛尖",
                             valid_from=date(2026, 8, 1), valid_until=date(2027, 7, 31),
                             issued_by="虚构雾岭农产品质检中心"),
        ]
        db.add_all(reports)

        db.add_all([
            Authorization(auth_no="AUTH-2026-01", cooperative=COOP, brand="雾岭毛尖",
                          variety="雾岭毛尖", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31)),
            Authorization(auth_no="AUTH-2025-01", cooperative=COOP, brand="雾岭毛尖",
                          variety="雾岭毛尖", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31)),
        ])
        db.flush()

        def add_batch(batch_no, parcel, harvest, output, legacy=False):
            b = Batch(batch_no=batch_no, parcel_id=parcel.id, cooperative=COOP, variety="雾岭毛尖",
                      harvest_date=harvest, traceable_output=output, is_legacy=legacy)
            db.add(b)
            db.flush()
            db.add(BatchQuota(batch_id=b.id, basis="original", pa_version_id=v2.id,
                              amount=output, note="登记批次时按可追溯产量设定"))
            return b

        b1 = add_batch("B-2026-041", p1, date(2026, 4, 5), 5000)
        b2 = add_batch("B-2026-042", p2, date(2026, 4, 8), 800)
        b3 = add_batch("B-2025-Q4", p2, date(2025, 10, 20), 600)
        b4 = add_batch("B-2026-099", p1, date(2026, 4, 6), 500)
        # 分界日前一天采收的存量批次：有已调拨包装厂未使用标签（过渡召回候选）
        b_legacy = add_batch("B-2026-088", p7, date(2026, 8, 31), 2000, legacy=True)
        # 分界日后采收的新批次：P6 跨新边界（容差内 → 折算新额度）；P7 新边界不合格
        b_new6 = add_batch("B-2026-101", p6, date(2026, 9, 5), 1000)
        b_new7 = add_batch("B-2026-102", p7, date(2026, 9, 6), 1200)
        db.flush()

        # ── 正常在用批次的已发标签 ────────────────────────────────────
        issue_labels(db, b2.id, 200, "种子数据", today=TODAY)
        db.add(InspectionEvent(
            event_no="XJ-2026-007", batch_id=b2.id, cooperative=COOP,
            finding="例行巡查发现该批次农事记录待补正：暂停新增用标，复核后恢复",
        ))
        issue_labels(db, b1.id, 800, "种子数据", today=TODAY)
        issue_labels(db, b1.id, 400, "种子数据", today=TODAY)
        issue_labels(db, b4.id, 495, "种子数据", today=TODAY)

        # ── 存量批次：在社 300、已调拨包装厂未使用 500、已使用 1000 ──
        from .services import import_legacy_labels, transfer_label, mark_label_used

        recs = import_legacy_labels(db, b_legacy.id, total=1800, used=1000,
                                    transferred_unused=500, operator="种子数据")
        # 500 枚在厂标签拆 200 枚标记已使用（仍有 300 枚已调拨未使用，作召回候选）
        at_packer = next(r for r in recs if r.status == "TRANSFERRED")
        mark_label_used(db, at_packer.id, 200, "种子数据")

        # ── 品牌方确认的过渡规则 ──────────────────────────────────────
        db.add(TransitionRule(
            rule_no="TR-2026-01", pa_code="WL-MJ",
            from_version_id=v2.id, to_version_id=v3.id,
            harvest_cutoff=date(2026, 9, 1),
            carry_used=True, quarantine_stock=True, stop_pending=True, area_ratio_quota=True,
            confirmed_by="品牌管理方-过渡工作组",
            note="西界东移过渡：分界日前采收按旧结论；已使用保留、在厂未用建议召回、待发暂停；之后按新边界。",
        ))

        db.commit()
        print("seed 完成：3 个保护区版本 / 7 个地块 / 6 份报告 / 2 份授权 / 7 个批次 / 过渡规则 TR-2026-01")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
