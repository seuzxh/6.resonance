<template>
  <div class="page-wrap feed-area">
    <div class="feed-col">
      <div class="feed-head">
        <span class="tt">近五日</span>
        <span class="sub">收盘后随 runner 发布 · 点卡片看当日明细</span>
      </div>
      <div class="cards"><article v-for="(day, i) in days" :key="day.date" class="card panel" :class="{ open: !collapsed.has(i) }">
        <button class="head" :aria-expanded="!collapsed.has(i)" @click="toggle(i)">
          <span class="date"><b class="mono">{{ day.date.slice(5) }}</b><i class="wd">{{ day.wd }}</i></span>
          <span class="chips">
            <span v-for="t in day.tracks.filter(t => t.track.startsWith('D3'))" :key="t.track"
                  class="chip mono" :class="t.ret >= 0 ? 'up' : 'down'">{{ t.track }} {{ pct(t.ret) }}</span>
          </span>
          <i class="caret" />
        </button>
        <div v-if="!collapsed.has(i)" class="body">
          <div class="row"><span class="k">信号</span>
            <span class="v">
              <span v-for="s in day.signals" :key="s.code + s.meta" class="sig">
                <b class="mono">{{ s.code }}</b> {{ s.name }} · {{ s.meta }}
              </span>
              <span v-if="!day.signals.length" class="none">当日无信号（闸门未触发或空仓等待）</span>
            </span>
          </div>
          <div class="row"><span class="k">净值</span>
            <span class="v">
              <span v-for="t in day.tracks" :key="t.track" class="track">
                <i>{{ t.track }}</i><em class="mono">{{ t.nav.toFixed(3) }} <b :class="t.ret >= 0 ? 'up' : 'down'">{{ pct(t.ret) }}</b></em>
              </span>
            </span>
          </div>
          <div v-if="day.note" class="note">{{ day.note }}</div>
        </div>
      </article></div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
// 卡片头 chip 只显 D3（轨名以 D3 开头匹配）；明细净值行显示全部轨
const d = ref<any>(null)
const collapsed = ref(new Set<number>())   // 默认全部展开，点击卡片头可收起
function toggle(i: number) {
  const n = new Set(collapsed.value)
  if (n.has(i)) n.delete(i); else n.add(i)
  collapsed.value = n
}
const days = computed(() => (d.value ? d.value.days : []))
onMounted(async () => {
  d.value = await (await fetch(import.meta.env.BASE_URL + 'data/recent.json', { cache: 'no-store' })).json()
})
const pct = (v: number | null) => v == null ? '—' : (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
</script>
<style scoped>
.tt { font-family: var(--font-display); font-weight: 700; font-size: 18px; letter-spacing: 0.06em; }
.sub { font-size: 12px; color: var(--text-low); }
.feed-head { display: flex; align-items: baseline; justify-content: space-between; margin: 14px 0 8px; }
.card { margin-bottom: 10px; overflow: hidden; }
.head { display: flex; align-items: center; gap: 10px; width: 100%; padding: 11px 12px; background: none; border: none; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.head:hover { background: rgba(255, 255, 255, 0.02); }
.date { display: flex; align-items: baseline; gap: 6px; }
.date b { font-size: 16px; }
.wd { font-style: normal; font-size: 12px; color: var(--text-low); }
.chips { margin-left: auto; display: flex; gap: 6px; }
.chip { font-size: 11px; line-height: 18px; height: 20px; padding: 0 7px; border-radius: 4px; background: var(--panel-2); border: 1px solid var(--line); color: var(--text-mid); white-space: nowrap; }
.chip.up { color: var(--up); } .chip.down { color: var(--down); }
.caret { width: 7px; height: 7px; flex: none; border-right: 1.5px solid var(--text-low); border-bottom: 1.5px solid var(--text-low); transform: rotate(-45deg); transition: transform 0.16s; }
.card.open .caret { transform: rotate(45deg); }
.body { border-top: 1px solid var(--line-soft); padding: 4px 12px 8px; }
.row { display: flex; gap: 12px; padding: 7px 0; font-size: 13px; border-top: 1px solid var(--line-soft); }
.row:first-child { border-top: none; }
.k { width: 34px; flex: none; color: var(--text-low); font-size: 12px; padding-top: 1px; }
.v { flex: 1; min-width: 0; }
.sig { display: block; margin-bottom: 4px; color: var(--text-mid); }
.sig b { color: var(--text-hi); }
.sig .mono { font-weight: 600; }
.none { color: var(--text-low); }
.track { display: flex; align-items: baseline; gap: 8px; padding: 2px 0; }
.track i { width: 40px; flex: none; font-style: normal; color: var(--text-mid); font-size: 12px; }
.track em { margin-left: auto; font-style: normal; }
.note { margin: 4px 0 8px; padding: 7px 9px; border-radius: 6px; background: var(--amber-dim); border: 1px solid rgba(233, 162, 59, 0.3); color: var(--amber); font-size: 12px; }

@media (min-width: 900px) {
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
  .card { margin-bottom: 0; }
}
</style>
