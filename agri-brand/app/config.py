"""应用配置。

默认使用 SQLite（可零依赖运行样例与接口测试）；
设置 DATABASE_URL 指向 postgresql+psycopg://... 即切换到 PostgreSQL/PostGIS。
几何运算统一由 Shapely 完成；PostGIS 模式下另提供空间索引/交集下推，见 db.py。
"""
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agri_brand.db"),
)

# 跨边界容差：保护区外（或缝隙）面积占总面积比例不超过该值且绝对面积不超过限值时，
# 按“边界贴合”放行。单位：比例 / 平方米
TOLERANCE_RATIO = float(os.getenv("TOLERANCE_RATIO", "0.02"))
TOLERANCE_SQM = float(os.getenv("TOLERANCE_SQM", "200"))

# 检测报告到期宽限天数（仅样例演示用的简单规则）
REPORT_GRACE_DAYS = int(os.getenv("REPORT_GRACE_DAYS", "0"))

# 业务“今天”，默认系统日期；可用 CURRENT_DATE=YYYY-MM-DD 固定（演示/测试可复现）
_current = os.getenv("CURRENT_DATE")
if _current:
    from datetime import date as _date

    CURRENT_DATE = _date.fromisoformat(_current)
else:
    from datetime import date as _date

    CURRENT_DATE = _date.today()

IS_SQLITE = DATABASE_URL.startswith("sqlite")
