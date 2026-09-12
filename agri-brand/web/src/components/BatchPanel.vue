<template>
  <div class="panel">
    <h3>批次与标签领用</h3>
    <div v-if="!selected" class="muted">请选择一个批次查看可用标范围</div>
    <template v-else>
      <div class="head">
        <div><b>{{ selected.batch_no }}</b> · {{ selected.variety }} · 采收 {{ selected.harvest_date }}</div>
        <div class="muted">{{ selected.cooperative }}</div>
      </div>

      <div class="verdict" :class="elig?.eligible ? 'ok' : 'no'">
        {{ elig ? (elig.suspended ? '巡查暂停新增用标' : elig.eligible ? '当前可以用标' : '当前不可用标') : '加载中…' }}
      </div>

      <table v-if="elig" class="kv">
        <tr>
          <td>地块核查</td>
          <td>
            <span :style="{ color: elig.parcel.qualified ? '#2e7d32' : '#c62828', fontWeight: 700 }">
              {{ STATUS_TEXT[elig.parcel.status] }}
            </span>
            <div class="sub">区内 {{ elig.parcel.inside_area_sqm.toLocaleString() }}㎡ ·
              区外 {{ elig.parcel.outside_area_sqm.toLocaleString() }}㎡（{{ (elig.parcel.outside_ratio*100).toFixed(2) }}%）·
              质心在内：{{ elig.parcel.centroid_inside ? '是' : '否' }}（仅参考）</div>
          </td>
        </tr>
        <tr>
          <td>检测报告</td>
          <td v-if="elig.report" :class="{ expired: elig.report.expired }">
            {{ elig.report.report_no }}（{{ elig.report.variety }}）<br/>
            <span class="sub">{{ elig.report.valid_from }} ~ {{ elig.report.valid_until }}</span>
          </td>
          <td v-else class="no-text">无在有效期内且品种一致的报告（已过期或品种不符）</td>
        </tr>
        <tr>
          <td>用标授权</td>
          <td v-if="elig.authorization">
            {{ elig.authorization.auth_no }}<br/>
            <span class="sub">{{ elig.authorization.start_date }} ~ {{ elig.authorization.end_date }}</span>
          </td>
          <td v-else class="no-text">无覆盖主体/品种/采收日期的有效授权</td>
        </tr>
        <tr>
          <td>用标额度</td>
          <td>
            可追溯产量 <b>{{ elig.quota.traceable_output }}</b> 枚 ·
            已发 <b>{{ elig.quota.labels_issued }}</b> 枚 ·
            剩余 <b :class="{ near: elig.quota.remaining <= 10 }">{{ elig.quota.remaining }}</b> 枚
            <div class="bar">
              <div class="bar-fill" :style="{ width: usedPct + '%' }"></div>
            </div>
          </td>
        </tr>
      </table>

      <ul v-if="elig?.blockers?.length" class="blockers">
        <li v-for="(b, i) in elig.blockers" :key="i">⛔ {{ b }}</li>
      </ul>

      <div class="issue-row">
        本次领用
        <input v-model.number="qty" type="number" min="1" style="width:80px" /> 枚
        <input v-model="operator" placeholder="经办人" style="width:110px" />
        <button class="btn primary" @click="issue" :disabled="issuing || !elig?.eligible">领用</button>
      </div>
      <div v-if="issueMsg" class="issue-msg" :class="issueOk ? 'ok' : 'no'">{{ issueMsg }}</div>

      <details class="labels" open>
        <summary>本批次已发标签（{{ batchLabels.length }} 条，巡查暂停也保留）</summary>
        <table>
          <tbody>
            <tr v-for="l in batchLabels" :key="l.id">
              <td class="mono">{{ l.label_code }}</td><td>{{ l.quantity }} 枚</td><td class="muted">{{ l.issued_at?.slice(0,19).replace('T',' ') }}</td>
            </tr>
            <tr v-if="!batchLabels.length"><td class="muted">暂无记录</td></tr>
          </tbody>
        </table>
      </details>

      <div class="inspect-actions">
        <button class="btn warn" @click="pause">登记异常巡查（暂停新增）</button>
        <button v-if="activeInspection" class="btn ghost" @click="resume">整改复核通过（解除暂停 #{{ activeInspection.event_no }}）</button>
      </div>
    </template>
</div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { api, STATUS_TEXT } from '../api'

const props = defineProps({ selected: { type: Object, default: null } })
const emit = defineEmits(['changed', 'inspection'])

const elig = ref(null)
const batchLabels = ref([])
const qty = ref(1)
const operator = ref('品牌管理方')
const issuing = ref(false)
const issueMsg = ref('')
const issueOk = ref(false)

const usedPct = computed(() => {
  if (!elig.value) return 0
  const q = elig.value.quota
  return q.traceable_output ? Math.min(100, (q.labels_issued / q.traceable_output) * 100) : 0
})

const activeInspection = computed(() => props.activeInspection)

async function load() {
  if (!props.selected) { elig.value = null; return }
  elig.value = await api.eligibility(props.selected.id)
  batchLabels.value = await api.labels(props.selected.id)
  issueMsg.value = ''
}

watch(() => props.selected?.id, load, { immediate: true })
defineExpose({ reload: load })

async function issue() {
  issuing.value = true
  issueMsg.value = ''
  try {
    const r = await api.issueLabels(props.selected.id, qty.value, operator.value)
    issueOk.value = true
    issueMsg.value = `领用成功：${r.label_code}，共 ${r.quantity} 枚`
    await load()
    emit('changed')
  } catch (e) {
    issueOk.value = false
    issueMsg.value = `${e.code ? '[' + e.code + '] ' : ''}${e.message}`
  } finally {
    issuing.value = false
  }
}

async function pause() {
  const no = 'XJ-' + Date.now().toString().slice(-8)
  await api.createInspection({
    event_no: no, batch_id: props.selected.id,
    cooperative: props.selected.cooperative, finding: '管理端登记异常巡查：暂停新增用标，已发标签保留',
  })
  emit('inspection')
  await load()
}

async function resume() {
  await api.resolveInspection(activeInspection.value.id, '整改复核通过')
  emit('inspection')
  await load()
}
</script>

<style scoped>
.panel { background: #fff; border-radius: 10px; padding: 14px; box-shadow: 0 1px 4px rgba(0,0,0,.1); }
h3 { margin: 0 0 10px; font-size: 15px; }
.head { font-size: 13px; }
.muted, .sub { color: #78909c; font-size: 11px; }
.verdict { margin: 10px 0; padding: 8px 12px; border-radius: 8px; font-weight: 700; text-align: center; }
.verdict.ok { background: #e8f5e9; color: #2e7d32; }
.verdict.no { background: #fdecea; color: #c62828; }
table.kv { width: 100%; border-collapse: collapse; font-size: 12px; }
table.kv td { border-top: 1px solid #eceff1; padding: 7px 4px; vertical-align: top; }
table.kv td:first-child { width: 70px; color: #546e7a; white-space: nowrap; }
.no-text { color: #c62828; }
.expired { color: #ef6c00; }
.blockers { margin: 8px 0; padding-left: 18px; font-size: 12px; color: #b71c1c; line-height: 1.8; }
.bar { margin-top: 4px; height: 6px; background: #eceff1; border-radius: 3px; overflow: hidden; }
.bar-fill { height: 100%; background: linear-gradient(90deg,#42a5f5,#1565c0); }
.near { color: #c62828; }
.issue-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 12px; flex-wrap: wrap; }
input { border: 1px solid #cfd8dc; border-radius: 6px; padding: 6px 8px; font-size: 12px; }
.btn { border-radius: 6px; padding: 6px 12px; font-size: 12px; cursor: pointer; border: 1px solid #b0bec5; background: #fff; }
.btn.primary { background: #1565c0; color: #fff; border-color: #1565c0; }
.btn.warn { background: #fff3e0; border-color: #ef6c00; color: #e65100; }
.btn.ghost { background: #eceff1; }
.issue-msg { margin-top: 8px; font-size: 12px; border-radius: 6px; padding: 6px 8px; }
.issue-msg.ok { background: #e8f5e9; color: #2e7d32; }
.issue-msg.no { background: #fdecea; color: #c62828; }
.labels { margin-top: 12px; font-size: 12px; }
.labels summary { cursor: pointer; color: #37474f; margin-bottom: 4px; }
.labels table { width: 100%; }
.labels td { padding: 2px 4px; font-size: 11px; }
.mono { font-family: ui-monospace, monospace; }
.inspect-actions { margin-top: 10px; display: flex; gap: 8px; }
</style>
