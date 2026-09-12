"""几何核查接口测试：退回修正、边界贴合、质心≠合格、跨边界面积。"""
from conftest import P3, bbox_polygon  # type: ignore


def _find(client, code: str) -> dict:
    rows = client.get("/parcels").json()
    return next(p for p in rows if p["code"] == code)


def test_protected_area_versions_listed(client):
    pas = client.get("/protected-areas").json()
    versions = {p["version"] for p in pas}
    assert {"2015-v1", "2023-v2"} <= versions


def test_missing_crs_is_rejected(client):
    body = {
        "code": "P-T1", "name": "无坐标系统", "cooperative": "x社",
        "crs": None, "geometry": bbox_polygon(117.99, 30.0, 118.0, 30.005),
    }
    r = client.post("/parcels", json=body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "MISSING_CRS"


def test_self_intersecting_polygon_is_rejected(client):
    from app.seed import INVALID_BOWTIE

    body = {
        "code": "P-T2", "name": "蝴蝶结自交", "cooperative": "x社",
        "crs": "EPSG:4326", "geometry": INVALID_BOWTIE,
    }
    r = client.post("/parcels", json=body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "INVALID"
    # 不入库：清单里不应出现
    assert all(p["code"] != "P-T2" for p in client.get("/parcels").json())


def test_unsupported_crs_rejected(client):
    body = {
        "code": "P-T3", "name": "投影坐标误填", "cooperative": "x社",
        "crs": "EPSG:32650", "geometry": bbox_polygon(500000, 3320000, 500500, 3320500),
    }
    r = client.post("/parcels", json=body)
    assert r.status_code == 422


def test_inside_parcel_qualified(client):
    p = _find(client, "P-001")
    assert p["status"] == "INSIDE"
    assert p["qualified"] is True
    assert p["outside_area_sqm"] == 0


def test_boundary_fitting_cross_tolerated(client):
    """贴边长条：有区外面积，但在 2%/200㎡ 容差内 → 合格，且内外面积都展示。"""
    p = _find(client, "P-002")
    assert p["status"] == "CROSS_TOLERATED"
    assert p["qualified"] is True
    assert p["inside_area_sqm"] > 0 and p["outside_area_sqm"] > 0
    assert p["outside_ratio"] <= 0.02
    assert p["outside_area_sqm"] <= 200
    assert p["shared_edge_m"] > 0


def test_centroid_inside_does_not_make_qualified(client):
    """P-003/P-004：质心在保护区内，但区外面积超容差 → 不合格。"""
    for code in ("P-003", "P-004"):
        p = _find(client, code)
        assert p["centroid_inside"] is True
        assert p["qualified"] is False
        assert p["status"] == "CROSS_EXCEEDED"
        assert p["outside_area_sqm"] > 200
        assert p["outside_ratio"] > 0.02


def test_fully_outside_parcel(client):
    p = _find(client, "P-005")
    assert p["status"] == "OUTSIDE"
    assert p["qualified"] is False
    assert p["inside_area_sqm"] == 0


def test_validate_endpoint_does_not_persist(client):
    body = {
        "code": "P-T9", "name": "预检样例", "cooperative": "x社",
        "crs": "EPSG:4326", "geometry": P3,
    }
    r = client.post("/parcels/validate", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True
    assert data["status"] == "CROSS_EXCEEDED"
    assert data["detail"]["centroid_inside"] is True
    assert all(p["code"] != "P-T9" for p in client.get("/parcels").json())
