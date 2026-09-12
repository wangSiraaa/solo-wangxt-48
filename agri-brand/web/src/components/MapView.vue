<template>
  <div class="map-wrap">
    <div ref="el" class="map"></div>
    <div class="map-legend">
      <div class="lg-title">图例（底图 © OpenStreetMap）</div>
      <div><span class="sw" :style="{ background: 'rgba(21,101,192,.12)', border: '2px dashed #1565c0' }"></span>现行保护区 2026-v3</div>
      <div><span class="sw" :style="{ background: 'rgba(239,108,0,.07)', border: '2px dashed #ef6c00' }"></span>历史版本 2023-v2</div>
      <div><span class="sw" :style="{ background: 'rgba(142,36,170,.07)', border: '2px dashed #8e24aa' }"></span>历史版本 2015-v1</div>
      <div><span class="sw" :style="{ background: '#2e7d32' }"></span>全部在范围内</div>
      <div><span class="sw" :style="{ background: '#1565c0' }"></span>边界贴合（容差内）</div>
      <div><span class="sw" :style="{ background: '#c62828' }"></span>跨边界超容差</div>
      <div><span class="sw" :style="{ background: '#9e9e9e' }"></span>全部在范围外</div>
      <button class="link" @click="fitAll">缩放至全部</button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import maplibregl from 'maplibre-gl'
import { STATUS_COLOR, STATUS_TEXT } from '../api'

const props = defineProps({
  areas: { type: Array, default: () => [] },
  parcels: { type: Array, default: () => [] },
  selectedCode: { type: String, default: null },
})
const emit = defineEmits(['select'])

const el = ref(null)
let map = null
let popup = null

const blankStyle = {
  version: 8,
  sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#eef2f0' } }],
}
const osmStyle = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
}

const fc = (features) => ({ type: 'FeatureCollection', features })
const feat = (g, props) => ({ type: 'Feature', properties: props || {}, geometry: g })

function dataVersion() {
  return {
    areas: props.areas.map((a) => a.id).join(','),
    parcels: props.parcels.map((p) => p.id).join(','),
  }
}
let lastVersion = ''

function renderData() {
  if (!map) return
  const v = JSON.stringify(dataVersion()) + '|' + props.selectedCode
  if (v === lastVersion) return
  lastVersion = v

  for (const id of ['pa-old-fill', 'pa-old-line', 'pa-fill', 'pa-line', 'parcel-fill', 'parcel-line']) {
    if (map.getLayer(id)) map.removeLayer(id)
    if (map.getSource(id)) map.removeSource(id)
  }

  const oldAreas = props.areas.filter((a) => a.valid_to).sort((a, b) => a.version.localeCompare(b.version))
  const curAreas = props.areas.filter((a) => !a.valid_to)

  map.addSource('pa-old-fill', { type: 'geojson', data: fc(oldAreas.map((a) => feat(a.geometry, { v: a.version }))) })
  map.addLayer({ id: 'pa-old-fill', type: 'fill', source: 'pa-old-fill',
    paint: { 'fill-color': ['match', ['get', 'v'], '2023-v2', '#ef6c00', '#8e24aa'], 'fill-opacity': 0.06 } })
  map.addSource('pa-old-line', { type: 'geojson', data: fc(oldAreas.map((a) => feat(a.geometry, { v: a.version }))) })
  map.addLayer({ id: 'pa-old-line', type: 'line', source: 'pa-old-line',
    paint: { 'line-color': ['match', ['get', 'v'], '2023-v2', '#ef6c00', '#8e24aa'],
             'line-width': 2, 'line-dasharray': [3, 2] } })

  map.addSource('pa-fill', { type: 'geojson', data: fc(curAreas.map((a) => feat(a.geometry))) })
  map.addLayer({ id: 'pa-fill', type: 'fill', source: 'pa-fill',
    paint: { 'fill-color': '#1565c0', 'fill-opacity': 0.1 } })
  map.addSource('pa-line', { type: 'geojson', data: fc(curAreas.map((a) => feat(a.geometry))) })
  map.addLayer({ id: 'pa-line', type: 'line', source: 'pa-line',
    paint: { 'line-color': '#1565c0', 'line-width': 2.5, 'line-dasharray': [4, 2] } })

  map.addSource('parcel-fill', {
    type: 'geojson',
    data: fc(props.parcels.map((p) => feat(p.geometry, { code: p.code, status: p.status }))),
  })
  map.addLayer({
    id: 'parcel-fill', type: 'fill', source: 'parcel-fill',
    paint: {
      'fill-color': ['coalesce', ['get', 'color'], ['literal', '#999']],
      'fill-opacity': ['case', ['==', ['get', 'code'], props.selectedCode || ''], 0.55, 0.28],
    },
  })
  // 用 feature-state 之外的简单方式上色：直接写进 properties
  map.getSource('parcel-fill').setData(
    fc(props.parcels.map((p) => feat(p.geometry, { code: p.code, status: p.status, color: STATUS_COLOR[p.status] || '#999' }))),
  )
  map.addSource('parcel-line', {
    type: 'geojson',
    data: fc(props.parcels.map((p) => feat(p.geometry, { code: p.code }))),
  })
  map.addLayer({
    id: 'parcel-line', type: 'line', source: 'parcel-line',
    paint: {
      'line-color': '#263238',
      'line-width': ['case', ['==', ['get', 'code'], props.selectedCode || ''], 3, 1],
    },
  })
}

function fitAll() {
  const bounds = new maplibregl.LngLatBounds()
  let any = false
  for (const a of [...props.areas, ...props.parcels]) {
    for (const ring of a.geometry.coordinates)
      for (const [x, y] of ring) { bounds.extend([x, y]); any = true }
  }
  if (any) map.fitBounds(bounds, { padding: 40 })
}

onMounted(() => {
  map = new maplibregl.Map({
    container: el.value,
    style: blankStyle,
    center: [118.0, 30.0],
    zoom: 12,
    attributionControl: true,
  })
  map.addControl(new maplibregl.NavigationControl(), 'top-right')
  map.on('load', () => {
    // 尝试加载 OSM 栅格底图；离线失败时保留无底图样式
    try {
      map.addSource('osm', {
        type: 'raster',
        tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
      })
      map.addLayer({ id: 'osm', type: 'raster', source: 'osm' }, 'pa-old-fill')
    } catch (e) { /* 离线环境忽略 */ }
    renderData()
    fitAll()
  })

  map.on('click', 'parcel-fill', (e) => {
    const f = e.features[0]
    const p = props.parcels.find((x) => x.code === f.properties.code)
    if (!p) return
    emit('select', p.code)
    if (popup) popup.remove()
    popup = new maplibregl.Popup({ closeOnClick: true, maxWidth: '300px' })
      .setLngLat(e.lngLat)
      .setHTML(
        `<div class="pop">
          <b>${p.code} ${p.name}</b><br/>
          核查结论：<span style="color:${STATUS_COLOR[p.status]}">${STATUS_TEXT[p.status] || p.status}</span><br/>
          总面积：${p.total_area_sqm.toLocaleString()} ㎡<br/>
          <b>区内：${p.inside_area_sqm.toLocaleString()} ㎡</b> ／
          <b style="color:#c62828">区外：${p.outside_area_sqm.toLocaleString()} ㎡</b>
          （${(p.outside_ratio * 100).toFixed(2)}%）<br/>
          质心在范围内：${p.centroid_inside ? '是' : '否'}（仅参考，不作合格依据）<br/>
          贴合边界长度：${p.shared_edge_m} m　保护区版本：#${p.pa_version_id}
        </div>`,
      )
      .addTo(map)
  })
  map.on('mouseenter', 'parcel-fill', () => (map.getCanvas().style.cursor = 'pointer'))
  map.on('mouseleave', 'parcel-fill', () => (map.getCanvas().style.cursor = ''))

  // 离线时 OSM 源报错不影响矢量图层
  map.on('error', () => {})
})

watch(() => [props.areas, props.parcels, props.selectedCode], renderData, { deep: false })
</script>

<style scoped>
.map-wrap { position: relative; height: 100%; width: 100%; }
.map { height: 100%; width: 100%; }
.map-legend {
  position: absolute; left: 10px; bottom: 10px; z-index: 5;
  background: rgba(255,255,255,.94); border-radius: 8px; padding: 8px 10px;
  font-size: 12px; line-height: 1.9; box-shadow: 0 1px 6px rgba(0,0,0,.18);
}
.lg-title { font-weight: 700; margin-bottom: 2px; }
.sw { display: inline-block; width: 14px; height: 10px; margin-right: 6px; border-radius: 2px; vertical-align: middle; }
.link { margin-top: 4px; font-size: 12px; color: #1565c0; background: none; border: 0; cursor: pointer; padding: 0; }
:deep(.pop) { font-size: 12px; line-height: 1.7; }
</style>
