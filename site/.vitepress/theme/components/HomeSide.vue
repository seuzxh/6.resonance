<template>
  <div class="side">
    <div class="panel sc">
      <div class="eyebrow">净值速览 · D3 生产轨</div>
      <div class="st"><span>最新净值</span><b class="mono">{{ nav ? nav.stats.D3.nav.toFixed(3) : '—' }}</b></div>
      <div class="st"><span>{{ (nav && nav.start || '').slice(0, 4) || '2026' }} 收益</span><b class="mono up">{{ nav ? pct(nav.stats.D3.total_ret) : '—' }}</b></div>
      <div class="st"><span>最大回撤</span><b class="mono down">{{ nav ? pct(nav.stats.D3.max_dd) : '—' }}</b></div>
      <a class="lnk" href="/nav/">完整净值曲线与口径 →</a>
    </div>
    <div class="panel sc">
      <div class="eyebrow">链路状态</div>
      <div class="st"><span>数据截至</span><b class="mono">{{ d ? d.as_of : '—' }} <i :class="d && d.health === 'ok' ? 'ok' : 'warn'">{{ d && d.health === 'ok' ? '✓' : '滞后' }}</i></b></div>
      <div class="st"><span>站点更新</span><b class="mono">{{ d ? d.generated_at.slice(5, 16) : '—' }}</b></div>
      <div class="st"><span>发布口径</span><b>T-1（当日走推送）</b></div>
    </div>
    <div class="panel sc">
      <div class="eyebrow">快捷入口</div>
      <a class="lnk" href="/signals/">全部历史信号 →</a><br />
      <a class="lnk" href="/archive/">研究档案 →</a>
    </div>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
const d = ref<any>(null)
const nav = ref<any>(null)
onMounted(async () => {
  d.value = await (await fetch(import.meta.env.BASE_URL + 'data/recent.json', { cache: 'no-store' })).json()
  nav.value = await (await fetch(import.meta.env.BASE_URL + 'data/nav.json', { cache: 'no-store' })).json()
})
const pct = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
</script>
<style scoped>
.sc { padding: 13px 14px; margin-bottom: 12px; }
.st { display: flex; align-items: baseline; justify-content: space-between; padding: 6px 0; border-top: 1px solid var(--line-soft); font-size: 12px; }
.st:first-of-type { border-top: none; }
.st span { color: var(--text-mid); }
.st b { font-size: 15px; font-weight: 600; }
.st .ok { color: var(--down); font-style: normal; } .st .warn { color: var(--amber); font-style: normal; }
.lnk { display: inline-block; margin-top: 6px; font-size: 12px; color: var(--gold); text-decoration: none; }
</style>
