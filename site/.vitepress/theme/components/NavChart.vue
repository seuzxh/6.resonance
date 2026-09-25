<template>
  <div class="page-wrap" :class="{ compact: compact }">
    <h1 v-if="!compact" class="tt">净值</h1>
    <div v-else class="eyebrow">净值 · D3 整体 × 三锚分净值</div>
    <p v-if="!compact" class="sub">D3 整体净值 × 三锚分净值（{{ nav.start || '2026-01-01' }} 起，样本内+样本外连续，金色竖线=样本外起点）。分净值＝固定某一锚运行的策略净值（起点=1）；深证成指锚即 C1 轨。样本外段：D3/C1 为官方 OOS 口径（空仓起步）；国证/科创锚为连续展示口径（官方 OOS 未含此二轨）。悬停/点按曲线看当日持仓与买卖。</p>
    <div class="stats panel">
      <div><span>最新净值</span><b class="mono">{{ st ? st.nav.toFixed(3) : '—' }}</b></div>
      <div><span>{{ (nav.start || '').slice(0, 4) }} 收益</span><b class="mono up">{{ st ? pct(st.total_ret) : '—' }}</b></div>
      <div><span>最大回撤</span><b class="mono down">{{ st ? pct(st.max_dd) : '—' }}</b></div>
    </div>
    <div v-if="!compact" class="ranges">
      <button v-for="r in RANGES" :key="r.k" :class="{ on: range === r.k }" @click="range = r.k">{{ r.t }}</button>
    </div>
    <div class="panel chartp"><div ref="el" class="chartbox"></div></div>
  </div>
</template>

<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue'

const COLORS: Record<string, string> = { D3: '#E8C06B', C1: '#7FA6D9', G2: '#5FB8C9', K5: '#9A7FD9' }
const RANGES = [
  { k: '1m', t: '近1月' }, { k: '3m', t: '近3月' }, { k: 'all', t: '全部' },
]
const props = defineProps<{ compact?: boolean }>()
const nav = ref<any>({ series: [] })
const range = ref('all')
const el = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
const st = computed(() => nav.value.stats?.D3)
const pct = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'

onMounted(async () => {
  nav.value = await (await fetch(import.meta.env.BASE_URL + 'data/nav.json', { cache: 'no-store' })).json()
  await nextTick()
  if (el.value) {
    chart = echarts.init(el.value)
    render()
    window.addEventListener('resize', onResize)
  }
})
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); chart?.dispose() })
const onResize = () => chart?.resize()

function sliced(s: any) {
  const cut: Record<string, number> = { '1m': 22, '3m': 66 }
  return cut[range.value] ? s.points.slice(-cut[range.value]) : s.points
}

function render() {
  if (!chart || !nav.value.series?.length) return
  const dates = sliced(nav.value.series[0]).map((p: any[]) => p[0])
  const evMap = new Map<string, string[]>()      // "track|date" -> [动作, 代码, 名称]
  const holdMap = new Map<string, string>()      // "track|date" -> 持仓名
  for (const s of nav.value.series) {
    for (const e of s.events ?? []) evMap.set(`${s.track}|${e[0]}`, [e[1], e[2], e[3]])
    for (const h of s.holdings ?? []) holdMap.set(`${s.track}|${h[0]}`, h[2])
  }
  const oosStart: string = nav.value.oos_start || ''
  const showOos = oosStart && dates.includes(oosStart)

  const series = nav.value.series.map((s: any) => ({
    name: s.name,
    type: 'line' as const,
    showSymbol: false,
    data: sliced(s).map((p: any[]) => p[1]),
    lineStyle: { width: s.track === 'D3' ? 3 : 1.6, color: COLORS[s.track] || '#93A7C0' },
    itemStyle: { color: COLORS[s.track] || '#93A7C0' },
    emphasis: { focus: 'series' as const },
    ...(s.track === 'D3' && showOos ? {
      markLine: {
        silent: true, symbol: 'none',
        lineStyle: { color: '#E8C06B', type: 'dashed', width: 1, opacity: 0.7 },
        label: { formatter: '样本外起点', color: '#E8C06B', fontSize: 10, position: 'insideEndTop' },
        data: [{ xAxis: oosStart }],
      },
    } : {}),
  }))

  chart.setOption({
    animation: false,
    backgroundColor: 'transparent',
    grid: { left: 46, right: 14, top: 14, bottom: props.compact ? 8 : 58 },
    legend: props.compact ? { show: false } : {
      bottom: 0, icon: 'rect', itemWidth: 14, itemHeight: 2,
      textStyle: { color: '#93A7C0', fontSize: 11 },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: '#5E7391' } },
      backgroundColor: '#101E31', borderColor: '#1C3149',
      textStyle: { color: '#E9EFF7', fontSize: 12 },
      formatter: (params: any[]) => {
        const date: string = params[0]?.axisValue ?? ''
        if (!date) return ''
        let html = `<b>${date}</b>`
        if (date === oosStart) html += ` <span style="color:#E8C06B">◆ 样本外起点</span>`
        const first = nav.value.series[0].points
        const idxAll = first.findIndex((p: any[]) => p[0] === date)
        for (const p of params) {
          const s = nav.value.series[p.seriesIndex]
          if (!s) continue
          let ret = ''
          if (idxAll > 0) {
            const r = s.points[idxAll][1] / s.points[idxAll - 1][1] - 1
            ret = ` <span style="color:${r >= 0 ? '#FF5449' : '#00B578'}">${pct(r)}</span>`
          }
          html += `<br/><span style="color:${p.color}">━</span> ${p.seriesName} <b>${Number(p.value).toFixed(3)}</b>${ret}`
        }
        for (const s of nav.value.series) {
          const tr = s.track
          const ev = evMap.get(`${tr}|${date}`)
          const h = holdMap.get(`${tr}|${date}`)
          const nm = tr === 'D3' ? 'D3' : s.name.replace('锚', '').replace('三锚动选（生产）', '')
          const lc = COLORS[tr] || '#93A7C0'
          if (ev) html += `<br/><span style="color:${lc}">●</span> <span style="color:#E8C06B">${nm} ${ev[0]}</span> ${ev[1]} ${ev[2]}`
          else if (h) html += `<br/><span style="color:${lc}">●</span> <span style="color:${lc}">${nm} 持有</span> ${h}`
          else html += `<br/><span style="color:${lc};opacity:.45">○</span> <span style="color:#93A7C0">${nm} 空仓</span>`
        }
        return html
      },
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#1C3149' } },
      axisLabel: { color: '#5E7391', fontSize: 10 },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: '#16273D' } },
      axisLabel: { color: '#5E7391', fontSize: 10, formatter: (v: number) => v.toFixed(2) },
    },
    series,
  }, true)
}

watch(range, render)
</script>

<style scoped>
.tt { font-family: var(--font-display); font-size: 18px; letter-spacing: 0.06em; margin: 6px 0 4px; }
.sub { font-size: 12px; color: var(--text-low); margin-bottom: 12px; line-height: 1.6; }
.stats { display: flex; margin-bottom: 12px; }
.stats > div { flex: 1; padding: 10px 12px; }
.stats > div + div { border-left: 1px solid var(--line-soft); }
.stats span { display: block; font-size: 11px; color: var(--text-low); letter-spacing: 0.08em; }
.stats b { font-size: 18px; margin-top: 2px; }
.compact.stats { margin: 0 0 10px; }
.compact .stats b { font-size: 15px; }
.page-wrap.compact { padding: 0 12px 6px; max-width: none; }
.ranges { display: flex; gap: 6px; margin-bottom: 8px; }
.ranges button { font-size: 12px; line-height: 22px; padding: 0 10px; border-radius: 5px; background: none; border: 1px solid var(--line); color: var(--text-mid); cursor: pointer; }
.ranges button.on { border-color: rgba(232, 192, 107, 0.35); color: #cbb37e; background: transparent; }
.chartp { padding: 8px 6px 4px; }
.chartbox { width: 100%; height: 440px; }
.compact .chartbox { height: 258px; }
@media (max-width: 899px) { .chartbox { height: 300px; } }
</style>
