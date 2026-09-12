"""几何检查（Shapely）。

业务口径（虚构规则，与政府审批无关）：
1. 申报多边形必须声明 CRS（本样例只接受 EPSG:4326 经纬度），否则退回修正；
2. 多边形必须有效（不能自交等），否则退回修正，不入库、不判定；
3. 用局部等距投影计算面积（平方米），分别给出保护区内/外面积；
4. “中心点在范围内”只作为展示字段，绝不等同于整块合格；
5. 跨边界时，区外面积占比 ≤ TOLERANCE_RATIO 且绝对面积 ≤ TOLERANCE_SQM，
   判定为“边界贴合”（CROSS_TOLERATED，合格）；否则不合格。
"""
import math
from typing import Any

from shapely.geometry import Point, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import snap, transform
from shapely.validation import explain_validity

EARTH_R = 6_371_000.0  # 米


class GeometryError(ValueError):
    """几何数据问题，需退回合作社修正。"""


class MissingCRSError(GeometryError):
    pass


class InvalidGeometryError(GeometryError):
    pass


# 状态码（英文枚举，前端映射中文展示）
INSIDE = "INSIDE"                          # 全部在保护范围内
OUTSIDE = "OUTSIDE"                        # 全部在范围外
CROSS_TOLERATED = "CROSS_TOLERATED"        # 跨边界但在容差内（边界贴合）
CROSS_EXCEEDED = "CROSS_EXCEEDED"          # 跨边界且超出容差

# 提交校验阶段（不入库）
INVALID = "INVALID"
MISSING_CRS = "MISSING_CRS"

_AREA_EPS = 1e-6  # 平方米，面积视为 0 的数值阈值


def load_polygon(geojson: dict[str, Any], crs: str | None) -> Polygon:
    """校验并加载多边形。坐标系缺失或几何无效时抛 GeometryError（先退回修正）。"""
    if not crs:
        raise MissingCRSError("缺少坐标系声明：申报地块必须声明 CRS（示例接受 EPSG:4326）")
    if crs.upper().replace(" ", "") not in {"EPSG:4326", "EPSG4326", "WGS84", "CRS84"}:
        raise InvalidGeometryError(
            f"暂不支持坐标系 {crs}：请统一转换为 EPSG:4326 后重新申报"
        )

    if not isinstance(geojson, dict) or geojson.get("type") != "Polygon":
        raise InvalidGeometryError("geometry 必须是 GeoJSON Polygon 对象")

    try:
        geom: BaseGeometry = shape(geojson)
    except Exception as exc:  # malformed coordinates
        raise InvalidGeometryError(f"GeoJSON 无法解析：{exc}") from exc

    if geom.is_empty:
        raise InvalidGeometryError("多边形为空")
    if not isinstance(geom, Polygon):
        raise InvalidGeometryError("只接受 Polygon（多地块请拆分申报）")

    if not geom.is_valid:
        reason = explain_validity(geom)
        raise InvalidGeometryError(f"多边形自交或无效，请修正后重新申报：{reason}")

    # 业务字段级校验
    lons = [x for ring in (geom.exterior, *geom.interiors) for x, _ in ring.coords]
    lats = [y for ring in (geom.exterior, *geom.interiors) for _, y in ring.coords]
    if not all(-180 <= x <= 180 for x in lons) or not all(-90 <= y <= 90 for y in lats):
        raise InvalidGeometryError("经纬度超出合法范围（确认是否经纬度顺序填反）")

    return geom


def _aeqd_projector(lon0: float, lat0: float):
    """以 (lon0, lat0) 为原点的球面等距投影，返回经纬度→米平面坐标函数。

    乡县级尺度内面积误差远小于容差判定量级；所有相关几何用同一原点，口径一致。
    """
    phi0, lam0 = math.radians(lat0), math.radians(lon0)

    def project(lon: float, lat: float) -> tuple[float, float]:
        phi, lam = math.radians(lat), math.radians(lon)
        cos_c = math.sin(phi0) * math.sin(phi) + math.cos(phi0) * math.cos(phi) * math.cos(lam - lam0)
        cos_c = max(-1.0, min(1.0, cos_c))
        c = math.acos(cos_c)
        if c < 1e-12:
            return 0.0, 0.0
        k = c / math.sin(c)
        x = EARTH_R * k * math.cos(phi) * math.sin(lam - lam0)
        y = EARTH_R * k * (math.cos(phi0) * math.sin(phi) - math.sin(phi0) * math.cos(phi) * math.cos(lam - lam0))
        return x, y

    return project


def _transform_coords(geom: BaseGeometry, fn) -> BaseGeometry:
    """轻量坐标变换（避免强依赖 pyproj）。"""
    def _fn(x, y, z=None):
        pts = [fn(lon, lat) for lon, lat in zip(x, y)]
        return [p[0] for p in pts], [p[1] for p in pts]

    return transform(_fn, geom)


def _geodetic_length(geom: BaseGeometry) -> float:
    """按各折线的等距投影长度求和，返回米。递归展开 Multi*/GeometryCollection。"""
    if geom.is_empty:
        return 0.0
    if geom.geom_type == "LineString":
        if len(geom.coords) < 2:
            return 0.0
        lon0, lat0 = geom.coords[0]
        project = _aeqd_projector(lon0, lat0)
        return _transform_coords(geom, project).length
    if "Collection" in geom.geom_type or geom.geom_type.startswith("Multi"):
        return sum(_geodetic_length(g) for g in geom.geoms)
    return 0.0


def check_parcel(
    parcel: Polygon,
    protected_area: Polygon,
    tolerance_ratio: float,
    tolerance_sqm: float,
) -> dict[str, Any]:
    """执行内外面积检查，返回判定详情。调用前两个几何必须已通过有效性校验。"""
    # 1mm 吸附：消除“申报边界与界线坐标一致、浮点投影后不再共线”的噪声，
    # 仅影响贴合证据计算，不改变容差判定结果（1mm 面积差远小于容差）。
    snapped = snap(parcel, protected_area, tolerance=1e-9)

    origin = protected_area.representative_point()
    project = _aeqd_projector(origin.x, origin.y)

    p_area = _transform_coords(snapped, project)
    a_area = _transform_coords(protected_area, project)

    total = p_area.area
    inter = p_area.intersection(a_area)
    inside = inter.area
    outside = max(0.0, total - inside)
    ratio = outside / total if total > 0 else 0.0

    # 共用边界长度（贴合判定的辅助证据），按大地投影折线计算
    shared_geom = snapped.boundary.intersection(protected_area.boundary)
    shared_edge = _geodetic_length(shared_geom)

    centroid_inside = parcel.centroid.within(protected_area)
    rep_inside = parcel.representative_point().within(protected_area)

    # 数值噪声清理
    inside = min(total, inside)
    if outside < _AREA_EPS:
        outside = 0.0
        ratio = 0.0

    if inside <= _AREA_EPS:
        status = OUTSIDE
        qualified = False
        reason = "地块全部位于保护范围外"
    elif outside == 0.0:
        status = INSIDE
        qualified = True
        reason = "地块全部位于保护范围内"
    else:
        fits_ratio = ratio <= tolerance_ratio + 1e-12
        fits_abs = outside <= tolerance_sqm + _AREA_EPS
        if fits_ratio and fits_abs:
            status = CROSS_TOLERATED
            qualified = True
            reason = (
                f"跨边界：区外 {outside:.1f}㎡（{ratio * 100:.2f}%），"
                f"在容差（占比≤{tolerance_ratio * 100:.0f}% 且≤{tolerance_sqm:.0f}㎡）内，按边界贴合处理"
            )
        else:
            status = CROSS_EXCEEDED
            qualified = False
            reason = (
                f"跨边界：区外 {outside:.1f}㎡（{ratio * 100:.2f}%），"
                f"超出容差（占比≤{tolerance_ratio * 100:.0f}% 且≤{tolerance_sqm:.0f}㎡），不合格"
            )

    return {
        "status": status,
        "qualified": qualified,
        "reason": reason,
        "total_area_sqm": round(total, 2),
        "inside_area_sqm": round(inside, 2),
        "outside_area_sqm": round(outside, 2),
        "outside_ratio": round(ratio, 6),
        "centroid_inside": centroid_inside,
        "representative_point_inside": rep_inside,
        "shared_edge_m": round(shared_edge, 2),
    }


def representative_point(geom: BaseGeometry) -> Point:
    return geom.representative_point()
