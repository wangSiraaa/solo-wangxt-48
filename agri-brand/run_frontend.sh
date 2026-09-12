#!/usr/bin/env bash
# 启动前端（vite dev，自动代理 /api -> http://localhost:8000）
set -e
cd "$(dirname "$0")/web"
exec npm run dev
