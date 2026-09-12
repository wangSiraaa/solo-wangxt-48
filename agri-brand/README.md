# 农产品地理标志：保护范围核地 + 用标追踪（虚构可运行样例）

品牌管理方核对合作社申报地块是否真在保护范围内，并追踪标签发到了哪些批次。
**所有保护区、规则与数据均为虚构，不连接政府审批。**

技术栈：FastAPI + Shapely + SQLAlchemy，SQLite 零依赖跑样例/测试，
设置 `DATABASE_URL=postgresql+psycopg://...` 即切换 PostgreSQL（`app/db.py` 内含 PostGIS 升级说明，
几何以 JSONB 存 GeoJSON，判定口径统一在应用层 Shapely，PostGIS 承担空间索引/过滤）。
前端 Vue 3 + MapLibre GL（地图叠加、内外面积展示、用标清单）。

## 快速开始

```bash
# 后端（首次会自动建虚拟环境与依赖，或用已有的 .venv）
./run_backend.sh                 # http://localhost:8000/docs

# 前端（另开终端）
cd web && npm install && cd ..
./run_frontend.sh                # http://localhost:5173
```

接口测试（21 个用例）：

```bash
.venv/bin/python -m pytest tests/ -q
```

## 业务口径

### 地块几何核查（`app/geometry.py`，Shapely）

1. **坐标系缺失 → 退回修正**：申报必须声明 CRS（样例只接受 EPSG:4326，其他投影要求先转换）；
2. **多边形自交/无效 → 退回修正**：`is_valid` + `explain_validity` 给出原因，不入库、不判定；
3. 面积用**以保护区内点为原点的局部等距投影**计算平方米，分别给出**区内/区外面积**（跨边界必须展示两者）；
4. **中心点在范围内 ≠ 整块合格**：`centroid_inside` 只是展示字段；种子里 P-003（L 形）、P-004（U 形）质心在内部但区外超容差，仍判不合格；
5. 容差（虚构规则，环境变量可调）：区外面积占比 ≤ `TOLERANCE_RATIO`(默认 2%) 且绝对值 ≤ `TOLERANCE_SQM`(默认 200㎡) → `CROSS_TOLERATED`（边界贴合，合格）；否则 `CROSS_EXCEEDED`；
6. 核查快照**绑定保护区版本**（2015-v1 已失效 / 2023-v2 现行），界线调整后旧结论可追溯。

状态：`INSIDE` / `CROSS_TOLERATED`（合格）｜`CROSS_EXCEEDED` / `OUTSIDE`（不合格）｜预检返回 `MISSING_CRS` / `INVALID`。

### 可用标范围（`app/services.py`，以批次采收日为判定时点）

三件事同时满足才可领用标签：

- 地块核查合格（绑版本）；
- 检测报告**品种一致**、覆盖采收日、且**当前未过期**（`REPORT_GRACE_DAYS` 可配宽限）；
- 用标授权**主体 + 品种一致**、采收日落在授权期间内、未撤销；
- 用标数量 **≤ 批次可追溯产量 − 已发数量**；
- **异常巡查命中（批次级或合作社级）→ 暂停新增用标；已发记录保留不删**，整改复核 (`POST /inspections/{id}/resolve`) 后恢复。

### 并发领用最后额度

`POST /batches/{id}/issue-labels` 在单写事务内执行「锁批次行 → 复核资格 → 校验额度 → 写流水」：

- SQLite：`BEGIN IMMEDIATE` + WAL + busy_timeout，写事务全串行；
- PostgreSQL：`SELECT ... FOR UPDATE` 行锁；
- 标签号段唯一约束兜底。

种子批次 **B-2026-099** 剩余恰好 5 枚，10 个并发请求各领 1 枚时恰有 5 个成功、5 个 `409 quota_exceeded`，已发总数绝不超过 500。

## 虚构种子数据（业务日期 2026-09-12，可用 `CURRENT_DATE` 覆盖）

| 对象 | 用途 |
|---|---|
| 保护区 2015-v1 / 2023-v2 | 版本叠加，东界由 118.01°E 调整到 118.02°E |
| P-001 完全在区内 | 正常用标批次 B-2026-041（已发 1200/5000） |
| P-002 台阶形贴边，区外 53.5㎡（0.25%） | **边界贴合**：B-2026-042 本可用标，被巡查 XJ-2026-007 暂停（已发 200 保留） |
| P-003 L 形 / P-004 U 形 | **质心在内部但不合格**的反例 |
| P-005 完全在区外 | 有合格报告也不能用标 |
| B-2025-Q4 + RPT-2025-OLD | **报告过期**样例（2026-03-31 到期） |
| AUTH-2025-01 | 授权期间不覆盖 2026 年采收日的反例 |
| B-2026-099 | **并发最后额度**：495/500，剩 5 |

## 主要接口

| 方法/路径 | 说明 |
|---|---|
| `POST /parcels/validate` | 申报预检（几何问题不入库，返回修正原因与内外面积） |
| `POST /parcels` | 正式申报核查（422 = 退回修正：`MISSING_CRS`/`INVALID`） |
| `GET /protected-areas`、`GET /parcels` | 地图图层叠加数据 |
| `POST /reports`、`POST /authorizations`、`POST /authorizations/{id}/revoke` | 报告与授权 |
| `POST /batches`、`GET /batches/{id}/eligibility` | 批次与可用标范围（含阻断原因、内外面积、额度） |
| `POST /batches/{id}/issue-labels` | 领用标签（409 码：`suspended`/`not_eligible`/`quota_exceeded`/`conflict`） |
| `GET /labels?batch_id=` | 用标清单：标签号段发到了哪个批次/主体/品种 |
| `POST /inspections`、`POST /inspections/{id}/resolve` | 异常巡查暂停 / 整改复核解除 |

## 目录

```
app/            FastAPI 后端
  geometry.py     Shapely 几何校验与内外面积/容差判定
  services.py     用标资格 + 事务化领用
  routers/        parcels / certificates / batches / inspections
  seed.py         虚构数据
web/            Vue 3 + MapLibre（MapView 地图、申报、批次领用、清单、巡查）
tests/          pytest + TestClient 接口测试（含真实 HTTP 并发测试）
```
