<template>
  <div class="tv">
    <div class="rule-bar">
      <label>过渡规则</label>
      <select v-model="ruleId" @change="loadCandidates">
        <option v-for="r in rules" :key="r.id" :value="r.id">
          {{ r.rule_no }}（分界日 {{ r.harvest_cutoff }}，{{ r.confirmed_by }}）
        </option>
      </select>
      <button class="btn ghost" @click="reload">刷新候选</button>
      <span class="muted">候选只计算不执行；暂停/召回/降额须人工确认草案</span>
    </div>

    <div class="cols">
      <!-- 左：受影响批次（标签段级影响） -->
      <div class="cand-list">
        <h4>批次影响（按采收日适用版本）</h4>
        <div
          v-for="c in candidates" :key="c.batch_id"
          class="cand" :class="{ sel: selected?.batch_id === c.batch_id }"
          @click="selected = c"
        >
          <div class="cand-top">
            <b>{{ c.batch_no }}</b>
            <span class="seg" :class="c.segment.toLowerCase()">{{ c.segment === 'LEGACY' ? '分界日前·旧界' : '分界日后·新界' }}</span>
            <span class="tag" :style="c.qualified ? 'background:#2e7d32' : 'background:#c62828'">
              {{ c.parcel_status }}
            </span>
          </div>
          <div class="muted">
            采收 {{ c.harvest_date }} · 适用 {{ c.applicable_version }} · 新区内比例 {{ (c.inside_ratio_new*100).toFixed(2) }}%
          </div>
          <div class="three">
            <span class="used">已使用 <b>{{ c.labels.used_total }}</b></span>
            <span class="stock">未使用存量 <b>{{ c.labels.stock_unused }}</b>（在厂 {{ c.labels.transferred_unused }}）</span>
            <span class="pending">待发 <b>{{ c.quota.pending }}</b></span>
          </div>
          <div class="acts">
            <span v-for="s in c.suggested" :key="s.action" class="chip" :class="actClass(s.action)">
              {{ actText(s) }}
            </span>
          </div>
        </div>
      </div>

      <!-- 右：详情 / 标签段 / 草案 -->
      <div class="detail" v-if="selected">
        <h4>{{ selected.batch_no }} · 处置工作台</h4>
        <div class="muted" v-if="selected.parcel.sources">
          合批来源：
          <span v-for="(s, i) in selected.parcel.sources" :key="i">
            {{ s.code }}（{{ s.status }}，份额 {{ (s.share*100).toFixed(1) }}%）{{ i < selected.parcel.sources.length - 1 ? '、' : '' }}
          </span>
        </div>
        <div class="muted" v-else>
          {{ selected.parcel.parcel_code }} · {{ selected.parcel.pa_version }} ·
          区外 {{ selected.parcel.outside_area_sqm?.toLocaleString() }}㎡ — {{ selected.parcel.reason }}
        </div>

        <table class="segs">
          <thead><tr><th>标签段</th><th>状态/持有人</th><th>数量</th><th>已使用</th><th>未使用</th><th></th></tr></thead>
          <tbody>
            <tr v-for="s in selected.labels.segments" :key="s.id">
              <td class="mono">{{ s.label_code }}</td>
              <td>{{ segText(s.status) }} / {{ s.holder || '—' }}</td>
              <td>{{ s.quantity }}</td>
              <td class="used">{{ s.used_count }}</td>
              <td><b>{{ s.unused }}</b></td>
              <td>
                <button v-if="s.status === 'ISSUED'" class="mini" @click="doTransfer(s)">调拨包装厂</button>
                <button v-if="s.unused > 0" class="mini warn" @click="doRecall([s.id])">召回未用</button>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="quota-line">
          当前额度 <b>{{ selected.quota.current }}</b> · 已发 <b>{{ selected.quota.issued }}</b> ·
          待发 <b>{{ selected.quota.pending }}</b>
          <template v-if="selected.quota.proposed_new != null">
            ｜ 建议替代新额度 <b class="new-q">{{ selected.quota.proposed_new }}</b>
            <span class="muted">（剩余 {{ selected.quota.proposed_new - selected.quota.issued }}，不叠加旧额度）</span>
          </template>
        </div>

        <div class="draft-box">
          <button class="btn primary" @click="makeDraft">生成处置草案</button>
          <span class="muted">系统建议动作（人工可勾选）：</span>
          <label v-for="s in selected.suggested.filter(x => x.action !== 'NONE')" :key="s.action" class="pick">
            <input type="checkbox" :value="s.action" v-model="picked" />
            {{ actText(s) }}
          </label>
        </div>

        <h4 style="margin-top:14px">该批次草案记录</h4>
        <div v-for="d in draftsFor" :key="d.id" class="draft" :class="d.status.toLowerCase()">
          <div class="cand-top">
            <b class="mono">{{ d.draft_no }}</b>
            <span class="tag" :style="draftStyle(d.status)">{{ draftText(d.status) }}</span>
          </div>
          <div class="muted">
            存量未用 {{ d.stock_labels }}（在厂 {{ d.transferred_unused }}）· 已使用 {{ d.used_labels }} · 待发 {{ d.pending_labels }}
          </div>
          <div class="acts">
            <span v-for="(s, i) in d.suggested" :key="i" class="chip" :class="actClass(s.action)">{{ actText(s) }}</span>
          </div>
          <div class="draft-ops" v-if="d.status === 'DRAFT'">
            <button class="btn ok" @click="confirm(d)">按勾选项确认执行</button>
            <button class="btn danger" @click="revoke(d)">撤销草案（不影响已执行暂停）</button>
          </div>
          <div class="muted" v-if="d.decision_note">{{ d.reviewed_by }}：{{ d.decision_note }}</div>
          <div class="acts" v-if="actionMap[d.id]?.length">
            <span class="chip done" v-for="a in actionMap[d.id]" :key="a.id">✓ 已执行 {{ actText(a.detail) }}（{{ a.operator }}）</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const emit = defineEmits(['changed'])
const rules = ref([])
const ruleId = ref(null)
const candidates = ref([])
const selected = ref(null)
const allDrafts = ref([])
const actionMap = ref({})
const picked = ref([])

const SEG_TEXT = { ISSUED: '在合作社', TRANSFERRED: '已调拨包装厂', USED: '已使用', RECALLED: '已召回' }
const segText = (s) => SEG_TEXT[s] || s

function actText(s) {
  switch (s.action) {
    case 'KEEP_USED': return `保留已使用 ${s.labels ?? ''}`
    case 'RECALL_LABELS': return `建议召回未用 ${s.labels ?? ''}（在厂 ${s.transferred_unused ?? ''}）`
    case 'SUSPEND_PENDING': return `暂停待发 ${s.labels ?? ''}`
    case 'NEW_QUOTA': return `新额度替代 ${s.old_quota}→${s.new_quota}`
    default: return s.action
  }
}
const actClass = (a) => ({
  KEEP_USED: 'keep', RECALL_LABELS: 'recall', SUSPEND_PENDING: 'susp', NEW_QUOTA: 'quota',
}[a] || '')
const draftText = (s) => ({ DRAFT: '待确认', CONFIRMED: '已确认执行', REVOKED: '已撤销' }[s] || s)
const draftStyle = (s) => ({
  DRAFT: 'background:#ef6c00', CONFIRMED: 'background:#2e7d32', REVOKED: 'background:#90a4ae',
}[s])

const draftsFor = computed(() => allDrafts.value.filter((d) => d.batch_id === selected.value?.batch_id))

async function loadRules() {
  rules.value = await api.transitionRules()
  if (rules.value.length) ruleId.value = rules.value[0].id
  await loadCandidates()
  await loadDrafts()
}

async function loadCandidates() {
  if (!ruleId.value) return
  candidates.value = await api.candidates(ruleId.value)
  if (selected.value) selected.value = candidates.value.find((c) => c.batch_id === selected.value.batch_id) || null
}

async function loadDrafts() {
  allDrafts.value = await api.drafts()
  await Promise.all(allDrafts.value.map(async (d) => {
    actionMap.value[d.id] = await api.draftActions(d.id)
  }))
}

async function reload() {
  await loadCandidates()
  await loadDrafts()
}

async function makeDraft() {
  try {
    await api.makeDraft(ruleId.value, selected.value.batch_id)
    picked.value = selected.value.suggested.filter((s) => s.action !== 'NONE').map((s) => s.action)
    await reload()
  } catch (e) { alert(e.message) }
}

async function confirm(d) {
  try {
    await api.confirmDraft(d.id, picked.value, '人工确认过渡处置', '王复核')
    await reload()
    emit('changed')
  } catch (e) { alert(e.message) }
}

async function revoke(d) {
  try {
    await api.revokeDraft(d.id, '撤销处置草案，已执行动作保留', '王复核')
    await reload()
  } catch (e) { alert(e.message) }
}

async function doTransfer(s) {
  const party = prompt('调拨给哪家包装厂？', '雾岭镇包装厂')
  if (!party) return
  await api.transferLabel(s.id, { to_party: party })
  await reload()
}

async function doRecall(ids) {
  if (!confirm('确认人工召回这些标签段的未使用部分？已使用标签保留。')) return
  const r = await api.recallLabels(ids, '王复核', '过渡处置：召回未使用标签')
  alert(`已召回 ${r.recalled.reduce((n, x) => n + x.unused, 0)} 枚`)
  await reload()
}

defineExpose({ reload })
onMounted(loadRules)
</script>

<style scoped>
.tv { display: flex; flex-direction: column; height: 100%; }
.rule-bar { display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: #fff; border-bottom: 1px solid #e0e0e0; font-size: 12px; }
select { padding: 5px 8px; border: 1px solid #cfd8dc; border-radius: 6px; font-size: 12px; }
.cols { flex: 1; display: flex; gap: 10px; min-height: 0; padding: 10px; }
.cand-list { width: 46%; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.detail { flex: 1; overflow-y: auto; background: #fff; border-radius: 10px; padding: 12px; }
h4 { margin: 0 0 8px; font-size: 13px; }
.cand { background: #fff; border-radius: 10px; padding: 10px 12px; cursor: pointer; border: 2px solid transparent; box-shadow: 0 1px 3px rgba(0,0,0,.07); }
.cand.sel { border-color: #1565c0; }
.cand-top { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.seg { font-size: 11px; padding: 2px 8px; border-radius: 10px; }
.seg.legacy { background: #eceff1; color: #455a64; }
.seg.new { background: #e3f2fd; color: #0d47a1; }
.tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; color: #fff; margin-left: auto; }
.three { display: flex; gap: 14px; font-size: 12px; margin: 6px 0; }
.three .used { color: #2e7d32; }
.three .stock { color: #ef6c00; }
.three .pending { color: #c62828; }
.acts { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #f5f5f5; border: 1px solid #e0e0e0; }
.chip.keep { background: #e8f5e9; border-color: #81c784; }
.chip.recall { background: #fff3e0; border-color: #ffb74d; }
.chip.susp { background: #fdecea; border-color: #e57373; }
.chip.quota { background: #e3f2fd; border-color: #64b5f6; }
.chip.done { background: #ede7f6; border-color: #9575cd; }
.muted { color: #78909c; font-size: 11px; }
.segs { width: 100%; border-collapse: collapse; font-size: 12px; margin: 8px 0; }
.segs th, .segs td { padding: 5px 6px; border-bottom: 1px solid #eceff1; text-align: left; }
.mono { font-family: ui-monospace, monospace; }
.used { color: #2e7d32; }
.mini { font-size: 11px; padding: 2px 8px; border-radius: 6px; border: 1px solid #b0bec5; background: #fff; cursor: pointer; margin-right: 4px; }
.mini.warn { border-color: #ef6c00; color: #e65100; }
.quota-line { font-size: 12px; background: #f4f7fa; padding: 8px; border-radius: 8px; }
.new-q { color: #1565c0; font-size: 14px; }
.draft-box { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 10px 0; font-size: 12px; }
.pick { display: inline-flex; align-items: center; gap: 4px; background: #fafafa; padding: 3px 8px; border-radius: 8px; }
.draft { border: 1px solid #e0e0e0; border-radius: 10px; padding: 8px 10px; margin-bottom: 8px; }
.draft.confirmed { background: #f6fbf6; }
.draft.revoked { opacity: .65; }
.draft-ops { display: flex; gap: 8px; margin: 6px 0; }
.btn { border-radius: 6px; padding: 6px 12px; font-size: 12px; cursor: pointer; border: 1px solid #b0bec5; background: #fff; }
.btn.primary { background: #1565c0; color: #fff; border-color: #1565c0; }
.btn.ok { background: #2e7d32; color: #fff; border-color: #2e7d32; }
.btn.danger { background: #fff; color: #c62828; border-color: #c62828; }
.btn.ghost { background: #eceff1; }
</style>
