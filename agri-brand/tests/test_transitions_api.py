"""重划界过渡、标签调拨/拆合批、草案处置的接口测试。

覆盖：
- 跨新边界地块（原合法 → 部分在新范围）的版本化核查与旧结论回看；
- 同日分批采收（拆批）；
- 已调拨包装厂但未使用标签的召回候选；
- 新授权额度替代旧额度，不重复计入；
- 撤销处置草案不影响已执行的暂停；
- 合批不能洗掉不合格来源。
"""
from conftest import COOP  # type: ignore


def _batches(client):
    return {b["batch_no"]: b for b in client.get("/batches").json()}


def _parcels(client):
    return {p["code"]: p for p in client.get("/parcels").json()}


def _rule_id(client):
    return client.get("/transition-rules").json()[0]["id"]


# ── 1. 跨新边界地块：v2 全在，v3 部分在；旧结论可回看 ─────────────────────
def test_parcel_crossing_new_boundary_has_versioned_checks(client):
    parcels = _parcels(client)
    p6, p7 = parcels["P-006"], parcels["P-007"]
    versions = {v["version"]: v["id"] for v in client.get("/protected-areas").json()}

    # 申报快照是 v2：两地块原本都合法
    assert p6["status"] == "INSIDE" and p7["status"] == "INSIDE"

    # 按 v3 重查：P6 容差内跨边界、P7 大面积越界
    r6 = client.post(f"/parcels/{p6['id']}/recheck/{versions['2026-v3']}")
    r7 = client.post(f"/parcels/{p7['id']}/recheck/{versions['2026-v3']}")
    assert r6.status_code == 200 and r6.json()["status"] == "CROSS_TOLERATED"
    assert 0 < r6.json()["outside_area_sqm"] <= 200
    assert r7.json()["status"] == "CROSS_EXCEEDED" and r7.json()["qualified"] is False

    # 核查历史：v2 旧结论仍在（旧规则结论可回看），v3 结论并存
    checks = client.get(f"/parcels/{p7['id']}/checks").json()
    by_version = {c["pa_version_id"]: c for c in checks}
    assert by_version[versions["2023-v2"]]["status"] == "INSIDE"
    assert by_version[versions["2026-v3"]]["status"] == "CROSS_EXCEEDED"

    # 重查 v3（非现行快照…此处 v3 是现行版本，快照随之更新）后回看 v2 行不变
    assert by_version[versions["2023-v2"]]["qualified"] is True


# ── 2. 分界日前后批次适用不同版本；候选按三段标签给出 ─────────────────────
def test_candidate_segments_for_legacy_and_new(client):
    rid = _rule_id(client)
    b = _batches(client)

    # 分界日前一天采收的存量批次：已用/在厂未用/待发
    c88 = client.get(f"/rules/{rid}/candidates/{b['B-2026-088']['id']}").json()
    assert c88["segment"] == "LEGACY"
    assert c88["applicable_version"] == "2023-v2"
    lab = c88["labels"]
    assert lab["used_total"] == 1200
    assert lab["transferred_unused"] == 300      # 已调拨包装厂未使用
    assert lab["stock_unused"] == 600            # 在社300 + 在厂300
    assert c88["quota"]["pending"] == 200
    actions = {s["action"] for s in c88["suggested"]}
    assert {"KEEP_USED", "RECALL_LABELS", "SUSPEND_PENDING"} <= actions

    # 按具体标签段展示影响：存在 TRANSFERRED 段
    seg_status = [(s["status"], s["unused"]) for s in lab["segments"]]
    assert ("TRANSFERRED", 300) in seg_status
    assert any(st == "USED" and u == 0 for st, u in seg_status)


# ── 3. 跨新边界合格地块：新授权额度按比例折算并替代旧额度，不叠加 ─────────
def test_new_quota_replaces_does_not_stack(client):
    rid = _rule_id(client)
    b = _batches(client)
    bid = b["B-2026-101"]["id"]

    # 先按新边界正常签发 300 枚（新边界采收批次，v3 容差内合格）
    r = client.post(f"/batches/{bid}/issue-labels", json={"quantity": 300})
    assert r.status_code == 200, r.text

    c = client.get(f"/rules/{rid}/candidates/{bid}").json()
    nq = next(s for s in c["suggested"] if s["action"] == "NEW_QUOTA")
    # 新额度 = 1000 × 98.16% ≈ 981；已发 300 要从新额度扣，而不是 981+旧剩余
    assert nq["new_quota"] == 981
    assert nq["issued"] == 300
    assert nq["pending_after"] == 681

    # 确认草案（只执行 NEW_QUOTA，不暂停）
    draft = client.post(f"/rules/{rid}/drafts/{bid}").json()
    ok = client.post(f"/dispositions/{draft['id']}/confirm",
                     json={"operator": "王复核", "actions": ["NEW_QUOTA"], "note": "按折算额度替代"})
    assert ok.status_code == 200

    elig = client.get(f"/batches/{bid}/eligibility").json()
    assert elig["quota"]["current_quota"] == 981          # 替代而非叠加
    assert elig["quota"]["labels_issued"] == 300
    assert elig["quota"]["remaining"] == 681

    # 再发 681 枚正好到顶；第 682 枚必须 409（证明旧额度的 700 剩余没有被重复计入）
    assert client.post(f"/batches/{bid}/issue-labels", json={"quantity": 681}).status_code == 200
    over = client.post(f"/batches/{bid}/issue-labels", json={"quantity": 1})
    assert over.status_code == 409 and over.json()["detail"]["code"] == "quota_exceeded"


# ── 4. 已调拨未使用标签：人工召回，已使用保留 ─────────────────────────────
def test_transferred_unused_labels_recall_keeps_used(client):
    rid = _rule_id(client)
    b = _batches(client)
    bid = b["B-2026-088"]["id"]  # 分界日前存量批：种子导入在厂500，已使用200
    c = client.get(f"/rules/{rid}/candidates/{bid}").json()
    # 已调拨包装厂但未使用 300 枚（500 中 200 已用）
    assert c["labels"]["transferred_unused"] == 300
    at_packer = [s for s in c["labels"]["segments"] if s["status"] == "TRANSFERRED"]
    assert sum(s["unused"] for s in at_packer) == 300

    # 确认草案（含召回建议）并人工召回在厂未使用段；已使用的 1200 枚保留
    draft = client.post(f"/rules/{rid}/drafts/{bid}").json()
    ok = client.post(f"/dispositions/{draft['id']}/confirm",
                     json={"operator": "李复核",
                           "actions": ["RECALL_LABELS", "SUSPEND_PENDING", "KEEP_USED"]})
    assert ok.status_code == 200
    res = client.post("/labels/recall",
                      json={"label_ids": [s["id"] for s in at_packer], "operator": "李复核",
                            "note": "召回在厂未用标签，已使用保留"})
    assert sum(x["unused"] for x in res.json()["recalled"]) == 300

    # 已使用标签总量不变，仍可在用标清单查到
    c2 = client.get(f"/rules/{rid}/candidates/{bid}").json()
    assert c2["labels"]["used_total"] == 1200
    assert len(client.get(f"/labels?batch_id={bid}").json()) >= 2

    # 待发暂停生效（历史导入后剩余额度不能新发）
    blocked = client.post(f"/batches/{bid}/issue-labels", json={"quantity": 1})
    assert blocked.status_code == 409


# ── 4b. 新边界不合格的新采收批次：候选提示；确认 SUSPEND_PENDING 后停发 ──────
def test_new_boundary_unqualified_batch_stops_after_confirmation(client):
    rid = _rule_id(client)
    b = _batches(client)
    bid = b["B-2026-102"]["id"]  # P7：v3 仅 38.46% 在内
    versions = {v["version"]: v["id"] for v in client.get("/protected-areas").json()}
    parcels = _parcels(client)
    client.post(f"/parcels/{parcels['P-007']['id']}/recheck/{versions['2026-v3']}")

    c = client.get(f"/rules/{rid}/candidates/{bid}").json()
    assert c["qualified"] is False and c["applicable_version"] == "2026-v3"
    assert any(s["action"] == "SUSPEND_PENDING" and s["labels"] == 1200 for s in c["suggested"])

    # 重查本身不阻断签发（处置须人工确认）
    assert client.post(f"/batches/{bid}/issue-labels", json={"quantity": 10}).status_code == 200

    draft = client.post(f"/rules/{rid}/drafts/{bid}").json()
    client.post(f"/dispositions/{draft['id']}/confirm",
                json={"operator": "王复核", "actions": ["SUSPEND_PENDING"]})
    blocked = client.post(f"/batches/{bid}/issue-labels", json={"quantity": 1})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "suspended"

    # 撤销另一个待确认草案不影响已执行的暂停
    d2 = client.post(f"/rules/{rid}/drafts/{bid}").json()
    rv = client.post(f"/dispositions/{d2['id']}/revoke", json={"operator": "王复核"})
    assert rv.status_code == 200
    still = client.post(f"/batches/{bid}/issue-labels", json={"quantity": 1})
    assert still.status_code == 409


# ── 5. 撤销处置草案不影响已经执行的暂停 ───────────────────────────────────
def test_revoke_draft_keeps_executed_suspension(client):
    rid = _rule_id(client)
    # 用全新合格批次：先由异常巡查暂停（已执行动作），再生成处置草案并撤销——暂停必须保留。
    p1 = _parcels(client)["P-001"]
    nb = client.post("/batches", json={
        "batch_no": "B-KEEP-01", "parcel_id": p1["id"], "cooperative": COOP,
        "variety": "雾岭毛尖", "harvest_date": "2026-04-15", "traceable_output": 100}).json()
    bid = nb["id"]
    ev = client.post("/inspections", json={
        "event_no": "XJ-KEEP-01", "batch_id": bid, "cooperative": COOP,
        "finding": "用标前农残抽检异常：暂停新增"}).json()
    assert client.post(f"/batches/{bid}/issue-labels", json={"quantity": 1}).status_code == 409

    draft = client.post(f"/rules/{rid}/drafts/{bid}").json()
    rv = client.post(f"/dispositions/{draft['id']}/revoke",
                     json={"operator": "王复核", "note": "草案计算口径有误，作废重算"})
    assert rv.status_code == 200 and rv.json()["status"] == "REVOKED"

    # 草案撤销不回滚巡查暂停：仍不能发标签
    again = client.post(f"/batches/{bid}/issue-labels", json={"quantity": 1})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "suspended"

    # 撤销后允许为同批次重新生成草案（旧的已作废）
    again_draft = client.post(f"/rules/{rid}/drafts/{bid}")
    assert again_draft.status_code == 201

    # 已确认草案不可再撤销
    d2 = again_draft.json()
    client.post(f"/dispositions/{d2['id']}/confirm", json={"actions": ["SUSPEND_PENDING"]})
    second_revoke = client.post(f"/dispositions/{d2['id']}/revoke", json={})
    assert second_revoke.status_code == 409
    # 动作留痕仍可查
    acts = client.get(f"/dispositions/{d2['id']}/actions").json()
    assert any(a["action"] == "SUSPEND_PENDING" for a in acts)
    # 清理：解除巡查，避免污染其它用例对该批次的依赖
    client.post(f"/inspections/{ev['id']}/resolve", json={"note": "测试清理"})


# ── 6. 同日分批采收：拆批保留来源份额，原批次关闭 ─────────────────────────
def test_same_day_split_harvest(client):
    b = _batches(client)
    src = b["B-2026-041"]
    before_remain = src["traceable_output"] - src["labels_issued"]

    # 上午/下午同日分采：拆 500 枚到新批
    r = client.post("/batches/split", json={
        "source_batch_id": src["id"], "new_batch_no": "B-2026-041-PM",
        "quantity": 500, "operator": "采收班长"})
    assert r.status_code == 200, r.text
    child = r.json()
    assert child["harvest_date"] == src["harvest_date"]
    assert child["traceable_output"] == 500

    # 原批次关闭，不能再签发
    closed = client.post(f"/batches/{src['id']}/issue-labels", json={"quantity": 1})
    assert closed.status_code == 409 and "关闭" in closed.json()["detail"]["message"]

    # 来源谱系可回溯 100%
    elig = client.get(f"/batches/{child['id']}/eligibility").json()
    assert elig["sources"][0]["share"] == 1.0
    assert elig["sources"][0]["parcel_code"] == "P-001"

    # 拆出的批次可以发标签；父批 traceable_output 被切走 500（总盘子不增加）
    assert client.post(f"/batches/{child['id']}/issue-labels", json={"quantity": 50}).status_code == 200
    parent_after = next(x for x in client.get("/batches").json() if x["id"] == src["id"])
    assert parent_after["traceable_output"] == src["traceable_output"] - 500
    assert child["traceable_output"] == 500
    assert before_remain >= 0  # 父批拆分前剩余（值随其它用例领用情况变化）


# ── 7. 合批：不合格来源不能被洗掉；合格合批按份额归因 ─────────────────────
def test_merge_blocks_dirty_source_and_attributes_shares(client):
    b = _batches(client)
    # B-2026-101（P6 跨边界但容差内合格）与 B-2026-102（P7 新边界不合格，已被暂停）
    good, bad = b["B-2026-101"], b["B-2026-102"]

    r = client.post("/batches/merge", json={
        "source_batch_ids": [good["id"], bad["id"]], "new_batch_no": "B-MERGE-DIRTY"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "dirty_source"

    # 两个合格批次合批成功（均新建，避免受其它用例领用影响）
    p1 = _parcels(client)["P-001"]
    nb1 = client.post("/batches", json={
        "batch_no": "B-FOR-MERGE-1", "parcel_id": p1["id"], "cooperative": COOP,
        "variety": "雾岭毛尖", "harvest_date": "2026-04-10", "traceable_output": 300}).json()
    nb2 = client.post("/batches", json={
        "batch_no": "B-FOR-MERGE-2", "parcel_id": p1["id"], "cooperative": COOP,
        "variety": "雾岭毛尖", "harvest_date": "2026-04-10", "traceable_output": 100}).json()
    ok = client.post("/batches/merge", json={
        "source_batch_ids": [nb1["id"], nb2["id"]], "new_batch_no": "B-MERGE-OK"})
    assert ok.status_code == 200, ok.text
    merged = ok.json()
    assert merged["traceable_output"] == 400
    # 来源批次关闭
    assert all(x["closed"] for x in client.get("/batches").json()
               if x["batch_no"] in ("B-FOR-MERGE-1", "B-FOR-MERGE-2"))
    # 合批签发后按来源份额归因到两个来源批次/地块
    issued = client.post(f"/batches/{merged['id']}/issue-labels", json={"quantity": 100})
    assert issued.status_code == 200, issued.text
    elig = client.get(f"/batches/{merged['id']}/eligibility").json()
    shares = sorted((round(s["share"], 4), s["parcel_code"]) for s in elig["sources"])
    assert shares == sorted([(0.75, "P-001"), (0.25, "P-001")])
    # 标签段带 source_batch_id，可回溯来源批次
    seg = client.get(f"/label-segments?batch_id={merged['id']}").json()[-1]
    assert seg["source_batch_id"] in (nb1["id"], nb2["id"])


# ── 8. 调拨不改变批次归属；标签段清单完整反映生命周期 ─────────────────────
def test_transfer_keeps_batch_and_lifecycle(client):
    b = _batches(client)
    bid = b["B-2026-041"]["id"]
    # 041 已被拆批关闭，换一个仍开放的合格批次：新建
    p1 = _parcels(client)["P-001"]
    nb = client.post("/batches", json={
        "batch_no": "B-LIFE-01", "parcel_id": p1["id"], "cooperative": COOP,
        "variety": "雾岭毛尖", "harvest_date": "2026-04-12", "traceable_output": 100}).json()
    client.post(f"/batches/{nb['id']}/issue-labels", json={"quantity": 100})
    seg = client.get(f"/label-segments?batch_id={nb['id']}").json()[-1]
    assert seg["status"] == "ISSUED" and seg["used_count"] == 0

    # 全部调拨包装厂
    tr = client.post(f"/labels/{seg['id']}/transfer",
                     json={"to_party": "雾岭镇包装厂"}).json()
    assert tr["holder"] == "雾岭镇包装厂" and tr["batch_id"] == nb["id"]
    assert tr["quantity"] == 100 and tr["status"] == "TRANSFERRED"

    # 用 60 枚：段仍在原批次，used_count=60
    used = client.post(f"/labels/{tr['id']}/use", json={"used": 60}).json()
    assert used["used_count"] == 60 and used["batch_id"] == nb["id"]
    # 已使用数量不能超未使用
    bad_use = client.post(f"/labels/{tr['id']}/use", json={"used": 41})
    assert bad_use.status_code == 422
    # 再用 40 枚后整段转 USED
    last = client.post(f"/labels/{tr['id']}/use", json={"used": 40}).json()
    assert last["status"] == "USED"
