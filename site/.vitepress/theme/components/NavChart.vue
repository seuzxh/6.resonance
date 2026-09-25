<template>
  <div class="page-wrap">
    <h1 class="tt">净值</h1>
    <p class="sub">D3 整体净值 × 三锚分净值。分净值＝固定某一锚运行的策略净值（起点=1）；当期锚由近 10 日动量选定，深证成指锚即 C1 轨。</p>
    <div class="stats panel">
      <div><span>最新净值</span><b class="mono">{{ st ? st.nav.toFixed(3) : '—' }}</b></div>
      <div><span>区间收益</span><b class="mono up">{{ st ? pct(st.total_ret) : '—' }}</b></div>
      <div><span>最大回撤</span><b class="mono down">{{ st ? pct(st.max_dd) : '—' }}</b></div>
    </div>
    <div class="ranges">
      <button v-for="r in RANGES" :key="r.k" :class="{ on: range === r.k }" @click="range = r.k">{{ r.t }}</button>
    </div>
    <div class="panel chart">
      <p v-if="!layers.length" class="wait">OOS 数据累积中……</p>
      <svg v-else viewBox="0 0 720 280" preserveAspectRatio="xMidYMid meet">
        <g stroke="#16273D" stroke-width="1">
          <line v-for="g in grid" :key="g.y" x1="40" :y1="g.y" x2="710" :y2="g.y" />
        </g>
        <g fill="#5E7391" font-size="10" font-family="monospace">
          <text v-for="g in grid" :key="'t' + g.y" x="4" :y="g.y + 3">{{ g.v.toFixed(2) }}</text>
          <text v-for="x in xlabels" :key="x.x" :x="x.x" y="272" text-anchor="middle">{{ x.d }}</text>
        </g>
        <polyline v-for="s in layers" :key="s.track" fill="none"
          :stroke="COLORS[s.track] || '#93A7C0'" :stroke-width="s.track === 'D3' ? 2.5 : 1.6"
          stroke-linejoin="round" stroke-linecap="round" :points="s.path" />
      </svg>
      <div class="legend">
        <span v-for="s in nav.series" :key="s.track"><i :style="{ background: COLORS[s.track] || '#93A7C0' }" />{{ s.name }}</span>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
const COLORS: Record<string, string> = { D3: '#E8C06B', C1: '#7FA6D9', G2: '#5FB8C9', K5: '#9A7FD9' }
const RANGES = [
  { k: '1m', t: '近1月' }, { k: '3m', t: '近3月' }, { k: 'ytd', t: '今年' }, { k: 'all', t: '全部' },
]
const nav = ref<any>({ series: [] })
const range = ref('all')
onMounted(async () => {
  nav.value = await (await fetch(import.meta.env.BASE_URL + 'data/nav.json', { cache: 'no-store' })).json()
})
const st = computed(() => nav.value.stats?.D3)
const visible = computed(() => {
  const cut: Record<string, number> = { '1m': 22, '3m': 66 }
  const ytd = `${new Date().getFullYear()}-01-01`
  return (nav.value.series ?? []).map((s: any) => {
    let pts = s.points
    if (range.value === 'ytd') pts = pts.filter((p: any[]) => p[0] >= ytd)
    else if (cut[range.value]) pts = pts.slice(-cut[range.value])
    return { track: s.track, pts }
  }).filter((s: any) => s.pts.length >= 2)
})
const scale = computed(() => {
  const all = visible.value.flatMap((s: any) => s.pts.map((p: any[]) => p[1]))
  if (!all.length) return { mn: 0, mx: 1 }
  const mn = Math.min(...all), mx = Math.max(...all)
  const pad = (mx - mn || 0.01) * 0.08
  return { mn: mn - pad, mx: mx + pad }
})
const grid = computed(() => {
  const Y0 = 16, Y1 = 244, N = 5
  return Array.from({ length: N }, (_, i) => {
    const t = i / (N - 1)
    return { y: Y0 + t * (Y1 - Y0), v: scale.value.mx - t * (scale.value.mx - scale.value.mn) }
  })
})
const xlabels = computed(() => {
  const pts = visible.value[0]?.pts ?? []
  if (!pts.length) return []
  const Y = 40, X1 = 710
  return [0, 1 / 3, 2 / 3, 1].map((t) => {
    const p = pts[Math.round(t * (pts.length - 1))]
    return { x: Y + t * (X1 - Y), d: String(p[0]).slice(2, 7) }
  })
})
const Y0 = 16, Y1 = 244, X0 = 40, X1 = 710
const layers = computed(() => {
  const toPath = (pts: any[]) =>
    pts.map((p, i, a) => {
      const x = X0 + (i / (a.length - 1)) * (X1 - X0)
      const y = Y1 - ((p[1] - scale.value.mn) / (scale.value.mx - scale.value.mn)) * (Y1 - Y0)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    }).join(' ')
  return visible.value.map((s: any) => ({ ...s, path: toPath(s.pts) }))
})
const pct = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
</script>
<style scoped>
.tt { font-family: var(--font-display); font-size: 18px; letter-spacing: 0.06em; margin: 6px 0 4px; }
.sub { font-size: 12px; color: var(--text-low); margin-bottom: 12px; line-height: 1.6; }
.stats { display: flex; margin-bottom: 12px; }
.stats > div { flex: 1; padding: 10px 12px; }
.stats > div + div { border-left: 1px solid var(--line-soft); }
.stats span { display: block; font-size: 11px; color: var(--text-low); letter-spacing: 0.08em; }
.stats b { font-size: 18px; margin-top: 2px; }
.ranges { display: flex; gap: 6px; margin-bottom: 8px; }
.ranges button { font-size: 12px; line-height: 22px; padding: 0 10px; border-radius: 5px; background: none; border: 1px solid var(--line); color: var(--text-mid); cursor: pointer; }
.ranges button.on { border-color: var(--gold); color: var(--gold); background: var(--gold-dim); }
.chart { padding: 14px 12px 8px; }
.chart svg { width: 100%; height: auto; max-height: 460px; }
.wait { color: var(--text-low); text-align: center; padding: 40px 0; font-size: 13px; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; justify-content: center; padding: 8px 0 6px; font-size: 11px; color: var(--text-mid); }
.legend span { display: flex; align-items: center; gap: 5px; }
.legend i { width: 12px; height: 2px; }
</style>
