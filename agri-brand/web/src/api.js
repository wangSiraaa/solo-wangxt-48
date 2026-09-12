// 后端访问：开发期经 vite 代理 /api -> http://localhost:8000
const BASE = import.meta.env.VITE_API_BASE || '/api'

async function req(path, options = {}) {
  const r = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) {
    const msg = data?.detail?.message || data?.detail || `请求失败 ${r.status}`
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    err.code = data?.detail?.code
    err.status = r.status
    throw err
  }
  return data
}

export const api = {
  protectedAreas: () => req('/protected-areas'),
  parcels: () => req('/parcels'),
  validateParcel: (body) => req('/parcels/validate', { method: 'POST', body }),
  createParcel: (body) => req('/parcels', { method: 'POST', body }),
  parcelChecks: (id) => req(`/parcels/${id}/checks`),
  recheck: (parcelId, versionId) => req(`/parcels/${parcelId}/recheck/${versionId}`, { method: 'POST' }),
  batches: () => req('/batches'),
  eligibility: (id) => req(`/batches/${id}/eligibility`),
  issueLabels: (id, quantity, operator) =>
    req(`/batches/${id}/issue-labels`, { method: 'POST', body: { quantity, operator } }),
  labels: (batchId) => req('/labels' + (batchId ? `?batch_id=${batchId}` : '')),
  segments: (batchId) => req('/label-segments' + (batchId ? `?batch_id=${batchId}` : '')),
  transferLabel: (id, body) => req(`/labels/${id}/transfer`, { method: 'POST', body }),
  useLabel: (id, used, operator) => req(`/labels/${id}/use`, { method: 'POST', body: { used, operator } }),
  recallLabels: (labelIds, operator, note) =>
    req('/labels/recall', { method: 'POST', body: { label_ids: labelIds, operator, note } }),
  splitBatch: (body) => req('/batches/split', { method: 'POST', body }),
  mergeBatches: (body) => req('/batches/merge', { method: 'POST', body }),
  inspections: (activeOnly = false) => req('/inspections' + (activeOnly ? '?active_only=true' : '')),
  createInspection: (body) => req('/inspections', { method: 'POST', body }),
  resolveInspection: (id, note) =>
    req(`/inspections/${id}/resolve`, { method: 'POST', body: { note } }),
  transitionRules: () => req('/transition-rules'),
  candidates: (ruleId) => req(`/rules/${ruleId}/candidates?only_affected=false`),
  makeDraft: (ruleId, batchId) => req(`/rules/${ruleId}/drafts/${batchId}`, { method: 'POST' }),
  drafts: () => req('/dispositions'),
  confirmDraft: (id, actions, note, operator) =>
    req(`/dispositions/${id}/confirm`, { method: 'POST', body: { actions, note, operator } }),
  revokeDraft: (id, note, operator) =>
    req(`/dispositions/${id}/revoke`, { method: 'POST', body: { note, operator } }),
  draftActions: (id) => req(`/dispositions/${id}/actions`),
}

export const STATUS_TEXT = {
  INSIDE: '全部在范围内',
  OUTSIDE: '全部在范围外',
  CROSS_TOLERATED: '跨边界 · 容差内（边界贴合）',
  CROSS_EXCEEDED: '跨边界 · 超容差（不合格）',
  INVALID: '几何无效',
  MISSING_CRS: '缺少坐标系',
}

export const STATUS_COLOR = {
  INSIDE: '#2e7d32',
  OUTSIDE: '#9e9e9e',
  CROSS_TOLERATED: '#1565c0',
  CROSS_EXCEEDED: '#c62828',
}
