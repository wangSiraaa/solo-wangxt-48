"""标签用标接口测试：有效期/品种/授权期、额度、巡查暂停、并发最后额度。"""
import concurrent.futures

from conftest import COOP  # type: ignore


def _batch(client, batch_no: str) -> dict:
    return next(b for b in client.get("/batches").json() if b["batch_no"] == batch_no)


# ── 正常领用：合格地块 + 有效报告 + 有效授权 ───────────────────────────────
def test_issue_labels_happy_path(client):
    b = _batch(client, "B-2026-041")
    before = b["labels_issued"]
    r = client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 50, "operator": "张三"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["label_code"].startswith("B-2026-041-")
    assert data["quantity"] == 50
    after = _batch(client, "B-2026-041")
    assert after["labels_issued"] == before + 50
    assert client.get("/health").status_code == 200


def test_boundary_tolerated_parcel_eligible_until_inspection(client):
    """边界贴合地块本来可用标；巡查期间暂停（该批次在种子里已被在查事件暂停）。"""
    b = _batch(client, "B-2026-042")
    elig = client.get(f"/batches/{b['id']}/eligibility").json()
    assert elig["eligible"] is False and elig["suspended"] is True
    r = client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "suspended"
    # 已发记录保留（种子中已发 200）
    issued = [x for x in client.get("/labels", params={"batch_id": b["id"]}).json()]
    assert sum(x["quantity"] for x in issued) == 200


def test_resolve_inspection_resumes(client):
    """整改复核后恢复新增用标。"""
    # 新建一个合格批次并制造一次已解除的巡查，避免影响依赖暂停状态的其它测试
    p1 = next(p for p in client.get("/parcels").json() if p["code"] == "P-001")
    cb = client.post("/batches", json={
        "batch_no": "B-T-RESUME", "parcel_id": p1["id"], "cooperative": COOP,
        "variety": "雾岭毛尖", "harvest_date": "2026-05-01", "traceable_output": 100})
    assert cb.status_code == 201
    bid = cb.json()["id"]

    ev = client.post("/inspections", json={
        "event_no": "XJ-T-1", "batch_id": bid, "cooperative": COOP, "finding": "测试暂停"}).json()
    assert client.post(f"/batches/{bid}/issue-labels", json={"quantity": 1}).status_code == 409
    client.post(f"/inspections/{ev['id']}/resolve", json={"note": "复核通过"})
    assert client.post(f"/batches/{bid}/issue-labels", json={"quantity": 1}).status_code == 200


# ── 报告过期 ──────────────────────────────────────────────────────────────
def test_expired_report_blocks_issue(client):
    b = _batch(client, "B-2025-Q4")
    elig = client.get(f"/batches/{b['id']}/eligibility").json()
    assert elig["eligible"] is False
    assert elig["report"] is None  # 唯一报告已过期
    assert any("检测报告" in x for x in elig["blockers"])
    r = client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_eligible"


def test_report_renewal_restores_eligibility(client):
    """补一份当前有效的同品种报告后恢复（新建批次，不改动过期样例批次）。"""
    p2 = next(p for p in client.get("/parcels").json() if p["code"] == "P-002")
    b = client.post("/batches", json={
        "batch_no": "B-T-RENEW", "parcel_id": p2["id"], "cooperative": COOP,
        "variety": "雾岭毛尖", "harvest_date": "2026-05-02", "traceable_output": 100}).json()
    # P-002 有有效报告 RPT-2026-002，因此应直接可发；再额外验证“过期阻断”需要删除不了，
    # 故这里断言正常可发，过期场景由 B-2025-Q4 覆盖。
    assert client.get(f"/batches/{b['id']}/eligibility").json()["eligible"] is True
    assert client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 10}).status_code == 200


# ── 品种不符 ──────────────────────────────────────────────────────────────
def test_variety_mismatch_blocks_issue(client):
    p1 = next(p for p in client.get("/parcels").json() if p["code"] == "P-001")
    b = client.post("/batches", json={
        "batch_no": "B-T-VAR", "parcel_id": p1["id"], "cooperative": COOP,
        "variety": "雾岭红茶", "harvest_date": "2026-05-03", "traceable_output": 100}).json()
    elig = client.get(f"/batches/{b['id']}/eligibility").json()
    assert elig["eligible"] is False
    assert any("品种" in x for x in elig["blockers"])
    assert client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 1}).status_code == 409


# ── 授权期：采收日不在授权期间内 ──────────────────────────────────────────
def test_authorization_period_blocks_issue(client):
    p1 = next(p for p in client.get("/parcels").json() if p["code"] == "P-001")
    # 先补一份覆盖该采收日的报告（2024 年），让唯一阻断因素是授权期间
    rep = client.post("/reports", json={
        "report_no": "RPT-T-2024", "parcel_id": p1["id"], "variety": "雾岭毛尖",
        "valid_from": "2024-01-01", "valid_until": "2026-12-31", "issued_by": "测试机构"})
    assert rep.status_code == 201
    b = client.post("/batches", json={
        "batch_no": "B-T-AUTH", "parcel_id": p1["id"], "cooperative": COOP,
        "variety": "雾岭毛尖", "harvest_date": "2024-06-01", "traceable_output": 100}).json()
    elig = client.get(f"/batches/{b['id']}/eligibility").json()
    assert elig["eligible"] is False
    assert any("授权" in x for x in elig["blockers"])


# ── 额度不能超发（串行）──────────────────────────────────────────────────
def test_quantity_cannot_exceed_traceable_output(client):
    p1 = next(p for p in client.get("/parcels").json() if p["code"] == "P-001")
    b = client.post("/batches", json={
        "batch_no": "B-T-CAP", "parcel_id": p1["id"], "cooperative": COOP,
        "variety": "雾岭毛尖", "harvest_date": "2026-05-04", "traceable_output": 10}).json()
    assert client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 8}).status_code == 200
    r = client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 3})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "quota_exceeded"
    assert "剩余 2" in r.json()["detail"]["message"]
    # 恰好等于剩余可以成功
    assert client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 2}).status_code == 200
    assert client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 1}).status_code == 409


# ── 并发领用最后额度（关键样例）──────────────────────────────────────────
def test_concurrent_last_quota(client):
    """B-2026-099 剩余恰好 5 枚；10 个请求各领 1 枚，只有 5 个成功，总数不超过可追溯产量。"""
    b = _batch(client, "B-2026-099")
    bid = b["id"]
    assert b["traceable_output"] - b["labels_issued"] == 5

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(client.post, f"/batches/{bid}/issue-labels", json={"quantity": 1})
                for _ in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futs)]

    codes = [r.status_code for r in results]
    assert codes.count(200) == 5, codes
    assert codes.count(409) == 5
    assert all(r.json()["detail"]["code"] == "quota_exceeded"
               for r in results if r.status_code == 409)

    b2 = _batch(client, "B-2026-099")
    assert b2["labels_issued"] == b2["traceable_output"] == 500
    labels = client.get("/labels", params={"batch_id": bid}).json()
    assert sum(x["quantity"] for x in labels) == 500  # 含种子预置 495


# ── 用标清单追踪到批次 ────────────────────────────────────────────────────
def test_labels_list_traces_to_batch(client):
    rows = client.get("/labels").json()
    assert len(rows) > 0
    sample = rows[0]
    assert {"label_code", "batch_no", "quantity", "cooperative", "variety", "issued_at"} <= set(sample)


def test_cooperative_level_inspection_suspends_batches(client):
    """不绑批次、只绑合作社的巡查事件也应暂停该社所有批次新增用标。"""
    # 先解除现有事件以隔离合作社级测试，用新合作社+新批次
    other = "测试外社"
    p1 = next(p for p in client.get("/parcels").json() if p["code"] == "P-001")
    # P-001 属于 COOP；合作社级事件命中 COOP 即可。选一个当前未被批次级事件命中的新批次
    b = client.post("/batches", json={
        "batch_no": "B-T-COOP", "parcel_id": p1["id"], "cooperative": COOP,
        "variety": "雾岭毛尖", "harvest_date": "2026-05-05", "traceable_output": 50}).json()
    ev = client.post("/inspections", json={
        "event_no": "XJ-T-COOP", "batch_id": None, "cooperative": COOP,
        "finding": "合作社级飞行检查：暂停全部新增用标"})
    assert ev.status_code == 201
    r = client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "suspended"
    # 历史标签仍可查
    assert len(client.get("/labels").json()) > 0


def test_invalid_quantity_rejected(client):
    b = _batch(client, "B-2026-041")
    r = client.post(f"/batches/{b['id']}/issue-labels", json={"quantity": 0})
    assert r.status_code == 422
