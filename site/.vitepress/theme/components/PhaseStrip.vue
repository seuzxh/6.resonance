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
.row.is-res { background: linear-gradient(90deg, var(--gold-dim), transparent 55%); }
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
  .row.is-res { box-shadow: inset 0 2px 0 0 var(--gold); background: linear-gradient(180deg, var(--gold-dim), transparent 60%); }
  .name { grid-area: name; width: auto; font-size: 14px; }
  .state { grid-area: state; justify-self: end; }
  .spark { grid-area: spark; height: 38px; width: 100%; flex: none; }
  .chg { grid-area: chg; width: auto; justify-self: end; font-size: 13px; }
}
</style>
