<template>
  <div class="page-wrap board">
    <header class="head">
      <div>
        <h1>生产并行总览</h1>
        <p>D3（三锚动选）与 D2（双锚动选）并行运行。这里是纸面生产账户，没有实盘下单接口。</p>
      </div>
      <div class="as-of panel">
        <span>信号日</span><b>{{ asOf }}</b>
        <span>执行时点</span><b>T+1（下一交易日）开盘</b>
      </div>
    </header>

    <section class="audit panel" :class="auditStatus">
      <div>
        <span>凌晨审计智能体</span>
        <b>{{ audit ? auditLabel : '待运行' }}</b>
      </div>
      <div>
        <span>审计时间</span><b>{{ audit ? audit.run_date : '—' }}</b>
      </div>
      <div>
        <span>优化队列</span><b>{{ auditProposals.length }} 项</b>
      </div>
    </section>

    <div class="grid">
      <article v-for="t in tracks" :key="t.track" class="panel card">
        <div class="card-head">
          <span class="track">{{ t.track }}</span>
          <b>{{ name(t.track) }}</b>
        </div>

        <div class="key-metrics">
          <div><span>当前持仓</span><b>{{ t.holding ? `${t.holding_name} (${t.holding})` : '空仓' }}</b></div>
          <div><span>当前仓位</span><b>{{ (t.position_weight * 100).toFixed(0) }}%</b></div>
          <div><span>滞后指令</span><b>{{ pendingText(t) }}</b></div>
          <div><span>当日锚</span><b>{{ t.leader ? `${leaderName(t, t.leader)} (${t.leader})` : '无' }}</b></div>
          <div><span>闸门</span><b :class="t.gate_pass ? 'up' : 'down'">{{ t.gate_pass ? '通过' : '未通过' }}</b></div>
          <div><span>半衰期档位</span><b>{{ t.half_life }} 日</b></div>
        </div>

        <section>
          <h2>锚筛选</h2>
          <table>
            <thead><tr><th>指数</th><th>10日收益</th><th>3日收益</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="a in t.anchors" :key="a.code" :class="{ selected: a.selected }">
                <td>{{ a.name }}<br /><code>{{ a.code }}</code></td>
                <td class="mono" :class="a.return_10d >= 0 ? 'up' : 'down'">{{ pct(a.return_10d) }}</td>
                <td class="mono" :class="a.return_3d >= 0 ? 'up' : 'down'">{{ pct(a.return_3d) }}</td>
                <td>{{ a.selected ? '当日锚' : '候选' }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section>
          <h2>最终候选榜</h2>
          <table>
            <thead><tr><th>#</th><th>概念指数</th><th>日线分</th><th>分钟分</th></tr></thead>
            <tbody>
              <tr v-for="(c, i) in t.candidates" :key="c.code" :class="{ held: c.code === t.holding }">
                <td class="mono">{{ c.final_rank ?? i + 1 }}</td>
                <td>{{ c.name }}<br /><code>{{ c.code }}</code></td>
                <td class="mono">{{ c.daily_score.toFixed(4) }}</td>
                <td class="mono">{{ c.minute_score === null ? '降级' : c.minute_score.toFixed(4) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="!t.candidates.length" class="empty">闸门未通过或无合格概念指数。</p>
        </section>
      </article>
    </div>

    <section class="proposals panel">
      <h2>审计优化队列</h2>
      <p>以下事项只进入人工复核，不自动修改参数。</p>
      <article v-for="p in auditProposals" :key="p.code + p.action" :class="priorityClass(p.priority)">
        <b>{{ p.code }} · {{ p.priority }}</b>
        <span>{{ p.action }}</span>
        <small>{{ p.guard }}</small>
      </article>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

const nav = ref<any>({ screening: {} })
const audit = ref<any>(null)
const tracks = computed<any[]>(() => Object.values(nav.value.screening || {}))
const auditProposals = computed(() => audit.value?.proposals || [])
const auditStatus = computed(() => audit.value?.status || 'pending')
const auditLabel = computed(() => ({ ok: '通过', warning: '有告警', critical: '有硬告警' } as Record<string, string>)[audit.value?.status] || audit.value?.status)
const asOf = computed(() => tracks.value[0]?.signal_date || nav.value.as_of || '—')
const NAMES: Record<string, string> = {
  D3: '三锚动选',
  D2: '双锚动选',
}
const LEADER_NAMES: Record<string, string> = {
  '399001.SZ': '深证成指',
  '399303.SZ': '国证2000',
  '000688.SH': '科创50',
  '000852.SH': '中证1000',
}
const names = ref<Record<string, string>>({})
onMounted(async () => {
  nav.value = await (await fetch(import.meta.env.BASE_URL + 'data/nav.json', { cache: 'no-store' })).json()
  try { audit.value = await (await fetch(import.meta.env.BASE_URL + 'data/audit.json', { cache: 'no-store' })).json() } catch {}
  const n: Record<string, string> = {}
  for (const t of Object.values(nav.value.screening || {})) {
    for (const a of (t as any).anchors) n[a.code] = a.name
    for (const c of (t as any).candidates) n[c.code] = c.name
  }
  names.value = n
})
const name = (track: string) => NAMES[track] || track
const leaderName = (t: any, code: string) => t.anchors.find((a: any) => a.code === code)?.name || LEADER_NAMES[code] || code
const pct = (v: number) => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(2) + '%'
function pendingText(t: any): string {
  const p = t.pending
  if (!p) return '无'
  const target = p.code || p.to
  const targetName = target ? names.value[target] || target : ''
  if (p.type === 'buy') return `下一交易日开盘买 ${targetName}`
  if (p.type === 'switch') return `下一交易日开盘换入 ${targetName}`
  if (p.type === 'sell') return `下一交易日开盘卖出 ${p.from || ''}`
  return '见交易文件'
}
const priorityClass = (priority: string) => ({ '最高': 'p0', '高': 'p1' } as Record<string, string>)[priority] || 'p2'
</script>

<style scoped>
.board { padding-top: 18px; }
.head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; margin-bottom: 14px; }
h1 { font-family: var(--font-display); font-size: 22px; margin: 0 0 6px; }
h2 { font-size: 13px; color: var(--text-mid); margin: 18px 0 8px; }
.head p, .empty { font-size: 12px; color: var(--text-low); line-height: 1.6; margin: 0; }
.as-of { display: grid; grid-template-columns: auto auto; gap: 2px 12px; padding: 10px 12px; font-size: 12px; }
.as-of span { color: var(--text-low); }
.audit { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 14px; }
.audit > div { padding: 10px 12px; border-left: 1px solid var(--line-soft); }
.audit > div:first-child { border-left: none; }
.audit span { display: block; font-size: 11px; color: var(--text-low); margin-bottom: 4px; }
.audit b { font-size: 14px; }
.audit.ok b { color: var(--down); }
.audit.warning, .audit.critical { border-color: rgba(233, 162, 59, 0.55); }
.audit.warning b, .audit.critical b { color: var(--amber); }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.card { padding: 14px; }
.card-head { display: flex; align-items: center; gap: 8px; padding-bottom: 10px; border-bottom: 1px solid var(--line-soft); }
.card-head b { font-size: 16px; }
.track { font-family: var(--font-mono); color: var(--gold); font-size: 13px; }
.proposals { margin-top: 14px; padding: 14px; }
.proposals h2 { margin-top: 0; }
.proposals p { margin: 0 0 10px; font-size: 12px; color: var(--text-low); }
.proposals article { padding: 10px 12px; border: 1px solid var(--line-soft); border-radius: 7px; margin-bottom: 8px; }
.proposals article.p0 { border-color: rgba(255, 84, 73, 0.45); }
.proposals article.p1 { border-color: rgba(233, 162, 59, 0.45); }
.proposals b { display: block; font-size: 13px; margin-bottom: 4px; }
.proposals span { display: block; font-size: 13px; color: var(--text-mid); line-height: 1.5; }
.proposals small { display: block; margin-top: 4px; font-size: 11px; color: var(--text-low); }
.key-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }
.key-metrics > div { min-height: 52px; padding: 8px 10px; background: var(--panel-2); border: 1px solid var(--line-soft); border-radius: 7px; }
.key-metrics span { display: block; font-size: 11px; color: var(--text-low); margin-bottom: 4px; }
.key-metrics b { font-size: 13px; line-height: 1.35; font-weight: 600; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; color: var(--text-low); font-weight: 500; border-bottom: 1px solid var(--line-soft); padding: 0 6px 6px; }
td { padding: 7px 6px; border-bottom: 1px solid var(--line-soft); vertical-align: top; }
td:nth-child(2), td:nth-child(3), td:nth-child(4), th:nth-child(2), th:nth-child(3), th:nth-child(4) { text-align: right; }
code { font-family: var(--font-mono); font-size: 11px; color: var(--text-low); }
tr.selected td { background: var(--gold-dim); }
tr.held td { background: rgba(232, 192, 107, 0.08); }
@media (max-width: 899px) { .head, .grid { display: block; } .as-of { margin-top: 10px; } .audit { grid-template-columns: 1fr; } .audit > div + div { border-top: 1px solid var(--line-soft); border-left: none; } .card + .card { margin-top: 12px; } }
</style>
