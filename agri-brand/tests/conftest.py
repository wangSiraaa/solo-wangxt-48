"""测试夹具：每个测试用独立临时 SQLite + TestClient + 固定业务日期。

并发领用测试必须真正走 HTTP（各请求在服务端各自取 SessionLocal），
不能在同一 Session 里调用 service 函数。
"""
import os
import tempfile

import pytest

# 必须在导入应用前固定业务日期与数据库路径
_TMP = tempfile.mkdtemp(prefix="agri-test-")
_DB_PATH = os.path.join(_TMP, "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["CURRENT_DATE"] = "2026-09-12"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import COOP, INVALID_BOWTIE, P1, P2, P3, TODAY, seed  # noqa: E402


@pytest.fixture(scope="session")
def client():
    init_db()
    seed()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def bbox_polygon(west, south, east, north):
    return {
        "type": "Polygon",
        "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
    }
