<template>
  <div class="submit-box">
    <h3>合作社地块申报</h3>
    <div class="row">
      <label>地块编号</label>
      <input v-model="form.code" placeholder="P-006" />
      <label>名称</label>
      <input v-model="form.name" placeholder="新茶园" />
    </div>
    <div class="row">
      <label>合作社</label>
      <input v-model="form.cooperative" placeholder="雾岭云雾茶叶合作社" />
      <label>坐标系 CRS</label>
      <input v-model="form.crs" placeholder="EPSG:4326" :class="{ bad: missingCrsHint }" />
    </div>
    <label class="lbl">GeoJSON Polygon（经纬度 [lon, lat]）</label>
    <textarea v-model="geoText" rows="6" spellcheck="false"></textarea>
    <div class="btns">
      <button class="btn ghost" @click="doValidate" :disabled="busy">预检（不入库）</button>
      <button class="btn primary" @click="doSubmit" :disabled="busy">正式申报核查</button>
      <button class="btn link" @click="useSample('bowtie')">填入自交样例</button>
      <button class="btn link" @click="useSample('ok')">填入跨边界样例</button>
    </div>
    <div v-if="result" class="result" :class="resultClass">
      <div class="r-head">
        <b>{{ resultHead }}</b>
      </div>
      <div>{{ result.reason || result.message }}</div>
      <div v-if="result.detail" class="metric">
        <span>总 {{ result.detail.total_area_sqm?.toLocaleString() }}㎡</span>
        <span class="in">区内 {{ result.detail.inside_area_sqm?.toLocaleString() }}㎡</span>
        <span class="out">区外 {{ result.detail.outside_area_sqm?.toLocaleString() }}㎡（{{ (result.detail.outside_ratio * 100).toFixed(2) }}%）</span>
        <span>质心在内：{{ result.detail.centroid_inside ? '是' : '否' }}（不作为合格依据）</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { api } from '../api'

const emit = defineEmits(['changed'])
const busy = ref(false)
const geoText = ref('')
const form = reactive({ code: 'P-TMP', name: '申报测试地块', cooperative: '雾岭云雾茶叶合作社', crs: 'EPSG:4326' })
const result = ref(null)

const SAMPLES = {
  bowtie: {
    crs: 'EPSG:4326',
    geo: {
      type: 'Polygon',
      coordinates: [[[118.0, 30.0], [118.01, 30.009], [118.01, 30.0], [118.0, 30.009], [118.0, 30.0]]],
    },
  },
  ok: {
    crs: 'EPSG:4326',
    geo: {
      // 东界 118.02 外 1m 左右的窄条，用于体验“边界贴合”
      type: 'Polygon',
      coordinates: [[[118.0185, 29.997], [118.02001, 29.997], [118.02001, 29.9978], [118.0185, 29.9978], [118.0185, 29.997]]],
    },
  },
}

function useSample(kind) {
  form.crs = SAMPLES[kind].crs
  geoText.value = JSON.stringify(SAMPLES[kind].geo)
  result.value = null
}

function parseBody() {
  let geometry
  try {
    geometry = JSON.parse(geoText.value)
  } catch {
    result.value = { ok: false, reason: 'GeoJSON 不是合法 JSON，请修正后重试' }
    return null
  }
  return { ...form, geometry }
}

const resultClass = computed(() => {
  if (!result.value) return ''
  if (result.value.valid === false) return 'reject'
  if (result.value.status === 'CROSS_EXCEEDED' || result.value.status === 'OUTSIDE') return 'warn'
  return 'ok'
})
const resultHead = computed(() => {
  const r = result.value
  if (r.valid === false) return '退回修正'
  if (r.status === 'CROSS_EXCEEDED' || r.status === 'OUTSIDE') return '核查未通过'
  if (r.status === 'CROSS_TOLERATED') return '核查通过（边界贴合）'
  if (r.status === 'INSIDE') return '核查通过'
  return r.code ? `已受理：${r.code}` : '预检完成'
})
const missingCrsHint = computed(() => result.value?.code === 'MISSING_CRS')

async function doValidate() {
  const body = parseBody()
  if (!body) return
  busy.value = true
  try {
    result.value = await api.validateParcel(body)
  } catch (e) {
    result.value = { valid: false, reason: e.message, code: e.code }
  } finally {
    busy.value = false
  }
}

async function doSubmit() {
  const body = parseBody()
  if (!body) return
  busy.value = true
  try {
    const p = await api.createParcel(body)
    result.value = { ...p, reason: p.reason, status: p.status }
    emit('changed')
  } catch (e) {
    result.value = { valid: false, reason: e.message, code: e.code }
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.submit-box { background: #fff; border-radius: 10px; padding: 14px; box-shadow: 0 1px 4px rgba(0,0,0,.1); }
h3 { margin: 0 0 10px; font-size: 15px; }
.row { display: grid; grid-template-columns: 60px 1fr 50px 1fr; gap: 6px; align-items: center; margin-bottom: 8px; font-size: 12px; }
input, textarea { width: 100%; box-sizing: border-box; border: 1px solid #cfd8dc; border-radius: 6px; padding: 6px 8px; font-size: 12px; font-family: inherit; }
textarea { font-family: ui-monospace, monospace; }
input.bad { border-color: #c62828; background: #fdecea; }
.lbl { font-size: 12px; color: #546e7a; }
.btns { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.btn { border-radius: 6px; padding: 6px 12px; font-size: 12px; cursor: pointer; border: 1px solid #b0bec5; background: #fff; }
.btn.primary { background: #1565c0; color: #fff; border-color: #1565c0; }
.btn.ghost { background: #eceff1; }
.btn.link { border: 0; color: #1565c0; background: none; text-decoration: underline; }
.result { margin-top: 10px; border-radius: 8px; padding: 8px 10px; font-size: 12px; line-height: 1.7; }
.result.ok { background: #e8f5e9; border: 1px solid #81c784; }
.result.warn { background: #fff8e1; border: 1px solid #ffb300; }
.result.reject { background: #fdecea; border: 1px solid #e57373; }
.r-head { margin-bottom: 2px; }
.metric { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 4px; color: #37474f; }
.metric .in { color: #2e7d32; font-weight: 700; }
.metric .out { color: #c62828; font-weight: 700; }
</style>
