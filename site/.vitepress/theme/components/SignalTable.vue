<template>
  <div class="page-wrap" :class="{ compact: compact }">
    <h1 v-if="!compact" class="tt">信号</h1>
    <div v-else class="eyebrow">最新信号</div>
    <p v-if="!compact" class="sub">全部历史信号 · 按轨筛选 · 标的为同花顺概念指数（885xxx.TI），价格单位为指数点位</p>
    <div v-if="!compact" class="filters">
      <button v-for="f in filters" :key="f" :class="{ on: flt === f }" @click="flt = f">{{ f === 'all' ? '全部' : f }}</button>
    </div>
    <div class="panel table">
      <table>
        <thead><tr>
          <th>日期</th><th>标的</th><th>轨</th><th>动作</th><th class="num">收益</th>
          <th class="num lg">入场</th><th class="num lg">现价 / 离场</th><th class="num lg">持有</th>
        </tr></thead>
        <tbody>
          <tr v-for="(r, i) in rows" :key="i">
            <td class="mono">{{ r.date.slice(5) }}</td>
            <td><b class="mono">{{ r.code }}</b> <i>{{ r.name }}</i></td>
            <td>{{ r.track }}</td>
            <td :class="ACT[r.action]">{{ r.action }}</td>
            <td class="num mono" :class="r.ret >= 0 ? 'up' : 'down'">{{ r.ret == null ? '—' : pct(r.ret) }}</td>
            <td class="num mono lg">{{ r.entry == null ? '—' : r.entry.toFixed(2) }}</td>
            <td class="num mono lg">{{ r.exit == null ? '—' : r.exit.toFixed(2) }}</td>
            <td class="num mono lg">{{ r.hold == null ? '—' : r.hold + '日' }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="8" class="empty">OOS 窗口暂无已平仓或持仓信号</td></tr>
        </tbody>
        <tfoot v-if="compact"><tr><td colspan="8" class="more"><a href="/signals/">查看全部信号 →</a></td></tr></tfoot>
      </table>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
const ACT: Record<string, string> = { 买: 'buy', 卖: 'sell', 换: 'sell', 持: 'hold' }
const { compact } = defineProps<{ compact?: boolean }>()
const data = ref<any>({ rows: [] })
const flt = ref('all')
onMounted(async () => {
  data.value = await (await fetch(import.meta.env.BASE_URL + 'data/signals.json', { cache: 'no-store' })).json()
})
const filters = computed(() => ['all', ...new Set(data.value.rows.map((r: any) => r.track))])
const rows = computed(() => {
  const r = flt.value === 'all' ? data.value.rows : data.value.rows.filter((r: any) => r.track === flt.value)
  return compact ? r.slice(0, 6) : r
})
const pct = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
</script>
<style scoped>
.tt { font-family: var(--font-display); font-size: 18px; letter-spacing: 0.06em; margin: 6px 0 4px; }
.sub { font-size: 12px; color: var(--text-low); margin-bottom: 12px; }
.filters { display: flex; gap: 6px; margin-bottom: 10px; }
.filters button { font-size: 12px; line-height: 22px; padding: 0 10px; border-radius: 5px; background: none; border: 1px solid var(--line); color: var(--text-mid); cursor: pointer; }
.filters button.on { border-color: rgba(232, 192, 107, 0.35); color: #cbb37e; background: transparent; }
.page-wrap.compact { padding: 0 12px 6px; max-width: none; }
.page-wrap.compact td, .page-wrap.compact th { padding: 7px 8px; font-size: 12px; }
.table { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { background: var(--panel-2); color: var(--text-low); font-weight: 500; text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
td { padding: 9px 10px; border-bottom: 1px solid var(--line-soft); white-space: nowrap; }
td b { font-weight: 600; }
td i { font-style: normal; color: #A8BAD1; }
.num { text-align: right; }
.buy { color: var(--up); } .sell { color: var(--down); } .hold { color: #cbb37e; }
.empty { color: var(--text-low); text-align: center; padding: 24px 0; }
.lg { display: none; }
.page-wrap.compact .lg { display: none !important; }
@media (min-width: 900px) { .page-wrap:not(.compact) .lg { display: table-cell; } td, th { padding: 10px 12px; font-size: 13px; } }
</style>
