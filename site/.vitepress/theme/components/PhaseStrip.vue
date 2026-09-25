<template>
  <div class="page-wrap">
    <div class="phase panel">
      <div class="eyebrow-row">
        <span class="eyebrow" role="button" tabindex="0" @click="showHint = !showHint">共振相位 ⓘ</span>
        <span v-if="d" class="align mono" :class="{ res: isRes }">{{ d.align.aligned }} / {{ d.align.total }} 对齐</span>
      </div>
      <p class="hint" :class="{ show: showHint }">相位＝指数锚 10 日动量方向（多/空）。对齐 ≥ 2 时闸门放行、当日允许产生信号，即「共振」。三锚为 D3 轨锚池；C1 轨固定深证成指。</p>
      <div v-if="d" class="rows" :class="{ res: isRes }">
        <div v-for="a in d.days[0].anchors" :key="a.code" class="row" :class="{ 'is-res': a.state === '多' }">
          <span class="name">{{ a.name }}</span>
          <svg class="spark" viewBox="0 0 100 22" preserveAspectRatio="none" aria-hidden="true">
            <polyline :points="sparkPts(a.spark)" />
          </svg>
          <span class="chg mono" :class="a.chg >= 0 ? 'up' : 'down'">{{ pct(a.chg) }}</span>
          <span class="state" :class="a.state === '多' ? 'long' : 'flat'">{{ a.state }}</span>
        </div>
      </div>
      <div class="formula mono">共振分 = 同步率 × √捕获率（10 日窗 · EW 权重 0.5^(t/h)）｜闸门：锚 3 日动量 &gt; 0<span role="button" tabindex="0" @click="showHint = !showHint">指标与权重 ⓘ</span></div>
      <div class="hint detail" :class="{ show: showHint }">
        <b>共振判定链</b>（T 日收盘计算 · 冻结参数，OOS 期间不改）<br/>
        ① <b>锚选择</b>：锚池内近 <b>10 日</b>复合收益最大者为当日锚（D3 三锚动选）<br/>
        ② <b>闸门</b>：锚近 <b>3 日</b>复合收益 ≤ 0 → 停新仓 / 持仓退出至现金<br/>
        ③ <b>候选资格</b>：概念指数近 3 日复合收益 &gt; 0<br/>
        ④ <b>上涨共振分</b>（共振窗 10 日，指数衰减权重 w = 0.5^(t/h)，越近权重越高）：<br/>
        <span class="mono f">同步率 = Σw·1[锚涨且概念涨] / Σw·1[锚涨]</span>（仅锚上涨日计入）<br/>
        <span class="mono f">捕获率 = Σw·max(概念日收益, 0) / Σw·max(锚日收益, 0)</span>（全窗正部）<br/>
        <span class="mono f">共振分 = 同步率 × √clip(捕获率, 0, 2)</span><br/>
        ⑤ <b>半衰期 h</b>（全A 近 10 日最大回撤分档）：≤2% → 5 日｜≤4% → 3 日｜&gt;4% → 2 日<br/>
        ⑥ <b>执行</b>：日线共振 Top5 → 24 根 5min K 纯分钟共振重排 → 前 2 持仓；最短持有 3 日，跌出 Top3 换仓，止损 5%（5min 盘中），单边成本 10bp
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
const d = ref<any>(null)
const showHint = ref(false)
const isRes = computed(() => d.value && d.value.align.aligned >= 2)
onMounted(async () => {
  d.value = await (await fetch(import.meta.env.BASE_URL + 'data/recent.json', { cache: 'no-store' })).json()
})
const pct = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
function sparkPts(arr: number[]): string {
  if (!arr || arr.length < 2) return ''
  const mn = Math.min(...arr), mx = Math.max(...arr), span = mx - mn || 1
  return arr.map((v, i) => `${((i / (arr.length - 1)) * 100).toFixed(1)},${(20 - ((v - mn) / span) * 18 + 1).toFixed(1)}`).join(' ')
}
</script>
<style scoped>
.phase { padding: 12px 12px 8px; }
.eyebrow-row { display: flex; justify-content: space-between; align-items: baseline; padding: 0 4px 8px; }
.align { color: var(--text-mid); }
.align.res { color: var(--gold); }
.hint { display: none; margin: 0 4px 10px; font-size: 12px; line-height: 1.7; color: var(--text-mid); background: var(--panel-2); border: 1px solid var(--line-soft); border-radius: 6px; padding: 9px 11px; }
.hint.show { display: block; }
.eyebrow-row .eyebrow { cursor: help; }
.row { display: flex; align-items: center; gap: 10px; height: 36px; padding: 0 4px 0 16px; border-top: 1px solid var(--line-soft); position: relative; }
.row.is-res { background: linear-gradient(90deg, var(--gold-dim), transparent 55%); box-shadow: inset 3px 0 0 var(--gold); }
/* 常显公式摘要行：指标与权重一目了然；ⓘ 展开完整判定链 */
.formula { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-top: 10px; padding: 6px 10px; font-size: 11px; color: var(--text-mid); background: var(--panel-2); border: 1px solid var(--line-soft); border-radius: 6px; white-space: nowrap; overflow-x: auto; }
.formula span { flex: none; color: var(--gold); cursor: help; }
.hint.detail { font-size: 12px; }
.hint.detail b { color: var(--text-hi); font-weight: 600; }
.hint.detail .f { display: inline-block; margin: 2px 0 2px 1em; color: #cbb37e; }
.name { width: 74px; flex: none; font-size: 13px; }
.spark { flex: 1; height: 22px; }
.spark polyline { fill: none; stroke: #7e93af; stroke-width: 1.4; }
.chg { width: 52px; flex: none; text-align: right; font-size: 12px; }
.state { width: 30px; flex: none; text-align: center; font-size: 12px; line-height: 20px; height: 20px; border-radius: 4px; border: 1px solid; }
.state.long { color: var(--up); border-color: rgba(255, 84, 73, 0.45); background: rgba(255, 84, 73, 0.08); }
.state.flat { color: var(--down); border-color: rgba(0, 181, 120, 0.45); background: rgba(0, 181, 120, 0.08); }
@media (min-width: 900px) {
  .rows { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; padding-top: 8px; }
  .rows.res::before { content: ''; position: absolute; left: 10px; right: 10px; top: 0; height: 2px; background: var(--gold); }
  .rows { position: relative; }
  .row { flex-direction: unset; display: grid; height: auto; padding: 13px 12px 12px 16px; gap: 6px 8px; grid-template-columns: 1fr auto; grid-template-areas: 'name state' 'spark spark' 'chg chg'; border: 1px solid var(--line-soft); border-radius: 8px; }
  .row.is-res { box-shadow: inset 0 2px 0 0 var(--gold), inset 3px 0 0 var(--gold); background: linear-gradient(180deg, var(--gold-dim), transparent 60%); }
  .name { grid-area: name; width: auto; font-size: 14px; }
  .state { grid-area: state; justify-self: end; }
  .spark { grid-area: spark; height: 38px; width: 100%; flex: none; }
  .chg { grid-area: chg; width: auto; justify-self: end; font-size: 13px; }
}
</style>
