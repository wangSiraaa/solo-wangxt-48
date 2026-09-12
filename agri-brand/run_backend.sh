#!/usr/bin/env bash
# 一键：建库 → 虚构种子数据 → 启动 FastAPI（http://localhost:8000/docs）
set -e
cd "$(dirname "$0")"
./.venv/bin/python -m app.seed
exec ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
