"""数据库引擎与会话。

- SQLite：几何/JSON 以 JSON 文本落库，几何运算在应用层用 Shapely 完成；
  开启 WAL、busy_timeout，并把 pysqlite 改造成 BEGIN IMMEDIATE，
  保证“并发领用最后额度”场景下写入串行化。
- PostgreSQL：同样以 JSONB 存 GeoJSON（Shapely 负责权威判定，跨库行为一致）；
  可后续用 GeoAlchemy2 增加 GEOMETRY 列与 GiST 索引（见文件末尾说明）。
"""
from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        eng = create_engine(
            url,
            connect_args={"timeout": 30, "check_same_thread": False},
            future=True,
        )

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
            # 关闭 pysqlite 自动 BEGIN，改为显式 IMMEDIATE（拿写锁后再读额度）
            dbapi_conn.isolation_level = None

        @event.listens_for(eng, "begin")
        def _begin_immediate(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")

        return eng

    # PostgreSQL/PostGIS：JSONB + 应用层 Shapely。pool_pre_ping 防止长连接失效。
    return create_engine(url, pool_pre_ping=True, future=True)


engine = _make_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(engine)
    if not config.IS_SQLITE:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── PostGIS 升级说明 ─────────────────────────────────────────────────────
# 切换到 PostgreSQL 后，如需数据库侧空间能力，可增加：
#
#   from geoalchemy2 import Geometry
#   protected_area_geom = Column(Geometry("POLYGON", 4326))
#   CREATE INDEX ix_parcels_geom ON parcels USING GIST (geom);
#
# 判定仍以 Shapely 结果为准（容差、投影面积口径在应用层统一），
# PostGIS 只承担空间过滤（&& / ST_Intersects）与 GIS 工具展示。
