<template>
  <div class="app">
    <header class="topbar">
      <div class="brand">🌱 雾岭毛尖 · 地理标志用标核对台</div>
      <div class="badge">虚构保护区与规则 · 不连接政府审批 · 业务日期 {{ today }}</div>
      <nav class="tabs">
        <button v-for="t in tabs" :key="t.key" :class="{ active: tab === t.key }" @click="tab = t.key">{{ t.label }}</button>
      </nav>
    </header>

    <main class="main" :class="{ wide: tab === 'transition' }">
      <section class="map-pane">
        <MapView :areas="areas" :parcels="parcels" :selected-code="selectedParcelCode" @select="onMapSelect" />
      </section>

      <aside class="side" :class="{ full: tab === 'transition' }">
        <!-- 地块 -->
        <div v-if="tab === 'parcels'" class="tab-body">
          <div class="list">
            <div
              v-for="p in parcels" :key="p.id"
              class="card" :class="{ sel: p.code === selectedParcelCode }"
              @click="selectParcel(p)"
            >
              <div class="card-top">
                <b>{{ p.code }} {{ p.name }}</b>
                <span class="tag" :style="tagStyle(p.status)">{{ STATUS_TEXT[p.status] }}</span>
              </div>
              <div class="areas">
                总 {{ p.total_area_sqm.toLocaleString() }}㎡ ·
                <span class="in">内 {{ p.inside_area_sqm.toLocaleString() }}㎡</span> ·
                <span class="out">外 {{ p.outside_area_sqm.toLocaleString() }}㎡ ({{ (p.outside_ratio*100).toFixed(2) }}%)</span>
              </div>
              <div class="muted">{{ p.reason }}</div>
              <div class="muted">质心在范围内：{{ p.centroid_inside ? '是（仅参考）' : '否' }} ｜ 贴边 {{ p.shared_edge_m }}m ｜ 保护区版本 #{{ p.pa_version_id }}</div>
            </div>
          </div>
          <ParcelSubmit @changed="loadAll" />
        </div>

        <!-- 批次 / 用标 -->
        <div v-else-if="tab === 'batches'" class="tab-body">
          <div class="list compact">
            <div
              v-for="b in batches" :key="b.id"
              class="card" :class="{ sel: selectedBatch?.id === b.id }"
              @click="selectBatch(b)"
            >
              <div class="card-top">
                <b>{{ b.batch_no }}</b>
                <span v-if="b.closed" class="tag" style="background:#607d8b">已关闭</span>
                <span v-else-if="b.is_legacy" class="tag" style="background:#8d6e63">存量批次</span>
                <span v-else-if="batchState(b).suspended" class="tag suspended">巡查暂停</span>
                <span v-else-if="batchState(b).eligible" class="tag ok">可用标</span>
                <span v-else class="tag bad">不可用标</span>
              </div>
              <div class="muted">
                {{ b.variety }} · {{ b.harvest_date }} ·
                已发 {{ b.labels_issued }}/{{ b.traceable_output }}（剩 {{ b.traceable_output - b.labels_issued }}）
              </div>
            </div>
          </div>
          <BatchPanel
            v-if="selectedBatch"
            ref="batchPanel"
            :selected="selectedBatch"
            :active-inspection="activeInspectionFor(selectedBatch)"
            @changed="loadAll"
            @inspection="loadInspections"
          />
        </div>

        <!-- 划界处置 -->
        <div v-else-if="tab === 'transition'" class="tab-body transition-body">
          <TransitionView ref="transitionView" @changed="loadAll" />
        </div>

        <!-- 用标清单 -->
        <div v-else-if="tab === 'labels'" class="tab-body">
          <h3>标签发到了哪些批次（含调拨/使用/召回状态）</h3>
          <table class="grid">
            <thead><tr><th>标签号段</th><th>批次</th><th>合作社</th><th>品种</th><th>数量</th><th>已用/未用</th><th>状态</th><th>持有人</th><th>来源批</th></tr></thead>
            <tbody>
              <tr v-for="l in segments" :key="l.id">
                <td class="mono">{{ l.label_code }}</td>
                <td><a href="#" @click.prevent="jumpBatch(l.batch_id)">{{ l.batch_no }}</a></td>
                <td>{{ batchOf(l.batch_id)?.cooperative }}</td>
                <td>{{ batchOf(l.batch_id)?.variety }}</td>
                <td>{{ l.quantity }}</td>
                <td>{{ l.used_count }} / {{ l.quantity - l.used_count }}</td>
                <td><span class="lstat" :class="'ls-' + l.status.toLowerCase()">{{ segText(l.status) }}</span></td>
                <td class="muted">{{ l.holder || '—' }}</td>
                <td class="muted">{{ l.source_batch_id ? batchNoOf(l.source_batch_id) : '—' }}</td>
              </tr>
            </tbody>
            <tfoot><tr><td colspan="4">合计段数 {{ segments.length }}，标签 {{ totalSegLabels }}</td><td colspan="5"></td></tr></tfoot>
          </table>
        </div>

        <!-- 巡查 -->
        <div v-else-if="tab === 'inspections'" class="tab-body">
          <h3>异常巡查事件</h3>
          <div v-for="e in inspections" :key="e.id" class="card">
            <div class="card-top">
              <b>{{ e.event_no }}</b>
              <span class="tag" :class="e.active ? 'suspended' : 'ok'">{{ e.active ? '处理中（已暂停新增用标）' : '已解除（' + (e.resolved_at||'').slice(0,10) + '）' }}</span>
            </div>
            <div class="muted">批次 #{{ e.batch_id ?? '—' }} · {{ e.cooperative || '' }}</div>
            <div>{{ e.finding }}</div>
            <div class="muted">登记 {{ e.created_at?.slice(0,19).replace('T',' ') }}<span v-if="e.resolved_note"> · 复核意见：{{ e.resolved_note }}</span></div>
          </div>
        </div>
      </aside>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import MapView from './components/MapView.vue'
import ParcelSubmit from './components/ParcelSubmit.vue'
import BatchPanel from './components/BatchPanel.vue'
import TransitionView from './components/TransitionView.vue'
import { api, STATUS_COLOR, STATUS_TEXT } from './api'

const tabs = [
  { key: 'parcels', label: '地块核查' },
  { key: 'batches', label: '批次与用标' },
  { key: 'transition', label: '划界处置' },
  { key: 'labels', label: '用标清单' },
  { key: 'inspections', label: '异常巡查' },
]
const tab = ref('parcels')
const today = '2026-09-12'

const areas = ref([])
const parcels = ref([])
const batches = ref([])
const labels = ref([])
const segments = ref([])
const inspections = ref([])
const eligibilityCache = ref({})
const selectedParcelCode = ref(null)
const selectedBatch = ref(null)
const batchPanel = ref(null)
const transitionView = ref(null)

const totalLabels = computed(() => labels.value.reduce((s, l) => s + l.quantity, 0))
const totalSegLabels = computed(() => segments.value.reduce((s, l) => s + l.quantity, 0))

const SEG_TEXT = { ISSUED: '在合作社', TRANSFERRED: '已调拨包装厂', USED: '已使用', RECALLED: '已召回' }
const segText = (s) => SEG_TEXT[s] || s
const batchOf = (id) => batches.value.find((b) => b.id === id)
const batchNoOf = (id) => batchOf(id)?.batch_no || ('#' + id)

function tagStyle(status) {
  return { background: STATUS_COLOR[status] || '#757575', color: '#fff' }
}

async function loadAll() {
  const [a, p, b, l, sg, e] = await Promise.all([
    api.protectedAreas(), api.parcels(), api.batches(), api.labels(), api.segments(), api.inspections(),
  ])
  areas.value = a
  parcels.value = p
  batches.value = b
  labels.value = l
  segments.value = sg
  inspections.value = e
  // 预取各批次资格（供列表角标）
  const entries = await Promise.all(b.map(async (x) => [x.id, await api.eligibility(x.id)]))
  eligibilityCache.value = Object.fromEntries(entries)
}

async function loadInspections() {
  inspections.value = await api.inspections()
}

function batchState(b) {
  const ev = eligibilityCache.value[b.id]
  return { eligible: ev?.eligible ?? false, suspended: ev?.suspended ?? false }
}

function activeInspectionFor(b) {
  return inspections.value.find(
    (e) => e.active && (e.batch_id === b.id || (e.batch_id == null && e.cooperative === b.cooperative)),
  )
}

function selectParcel(p) {
  selectedParcelCode.value = p.code
}

function onMapSelect(code) {
  selectedParcelCode.value = code
  const p = parcels.value.find((x) => x.code === code)
  if (p) {
    const b = batches.value.find((x) => x.parcel_id === p.id)
    if (b) {
      tab.value = 'batches'
      selectBatch(b)
    }
  }
}

function selectBatch(b) {
  selectedBatch.value = b
  const p = parcels.value.find((x) => x.id === b.parcel_id)
  if (p) selectedParcelCode.value = p.code
}

function jumpBatch(batchId) {
  const b = batches.value.find((x) => x.id === batchId)
  if (b) { tab.value = 'batches'; selectBatch(b) }
}

onMounted(loadAll)
</script>

<style>
* { box-sizing: border-box; }
body, html, #app { margin: 0; height: 100%; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; color: #263238; }
</style>

<style scoped>
.app { display: flex; flex-direction: column; height: 100%; }
.topbar { display: flex; align-items: center; gap: 16px; padding: 8px 16px; background: #0d47a1; color: #fff; flex-wrap: wrap; }
.brand { font-weight: 700; font-size: 16px; }
.badge { font-size: 11px; background: rgba(255,255,255,.15); padding: 3px 8px; border-radius: 10px; }
.tabs { margin-left: auto; display: flex; gap: 4px; }
.tabs button { background: rgba(255,255,255,.1); color: #e3f2fd; border: 0; border-radius: 6px 6px 0 0; padding: 7px 14px; cursor: pointer; font-size: 13px; }
.tabs button.active { background: #f4f7fa; color: #0d47a1; font-weight: 700; }
.main { flex: 1; display: flex; min-height: 0; }
.map-pane { flex: 1.6; min-width: 0; }
.side { flex: 1; min-width: 380px; max-width: 560px; background: #f4f7fa; overflow-y: auto; padding: 12px; }
.side.full { flex: 2; max-width: none; }
.transition-body { height: 100%; }
.transition-body > * { flex: 1; min-height: 0; }
.tab-body { display: flex; flex-direction: column; gap: 12px; }
h3 { margin: 0; font-size: 15px; }
.list { display: flex; flex-direction: column; gap: 8px; max-height: 46vh; overflow-y: auto; }
.list.compact { max-height: 38vh; }
.card { background: #fff; border-radius: 10px; padding: 10px 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); cursor: pointer; border: 2px solid transparent; }
.card.sel { border-color: #1565c0; }
.card-top { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 13px; }
.tag { font-size: 11px; padding: 2px 8px; border-radius: 10px; white-space: nowrap; }
.tag.ok { background: #2e7d32; color: #fff; }
.tag.bad { background: #c62828; color: #fff; }
.tag.suspended { background: #ef6c00; color: #fff; }
.areas { font-size: 12px; margin: 4px 0; }
.in { color: #2e7d32; font-weight: 700; }
.out { color: #c62828; font-weight: 700; }
.muted { color: #78909c; font-size: 11px; line-height: 1.6; }
.grid { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; font-size: 12px; }
.grid th, .grid td { padding: 7px 8px; border-bottom: 1px solid #eceff1; text-align: left; }
.grid th { background: #eceff1; }
.mono { font-family: ui-monospace, monospace; }
tfoot td { background: #fafafa; }
a { color: #1565c0; }
.lstat { font-size: 11px; padding: 2px 8px; border-radius: 10px; color: #fff; white-space: nowrap; }
.ls-issued { background: #607d8b; }
.ls-transferred { background: #ef6c00; }
.ls-used { background: #2e7d32; }
.ls-recalled { background: #c62828; }
</style>
