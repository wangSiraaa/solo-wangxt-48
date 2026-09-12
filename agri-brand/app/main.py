"""FastAPI 入口。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routers import batches, certificates, inspections, parcels


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="农产品地理标志用标核对服务（虚构样例）",
    description="地块保护区几何核查（Shapely）+ 用标资格/额度/巡查控制。数据均为虚构，不连接政府审批。",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(parcels.router)
app.include_router(certificates.router)
app.include_router(batches.router)
app.include_router(inspections.router)


@app.get("/health", tags=["系统"], summary="健康检查")
def health():
    return {"status": "ok"}
