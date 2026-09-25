// docs/ → site/ 构建期同步（EdgeOne 与 GitHub Pages 双通道共用，
// 由 package.json 的 docs:dev / docs:build 前置调用；产物不入库）。
// 规则：
//   docs/*.md            → site/docs/（文档页，VitePress 原生路由）
//   docs/diagrams/*.html → site/public/diagrams/（public 直通，无需构建）
//   链接改写：diagrams/x.html → /diagrams/x.html；
//             ../outputs/…（未发布路径）→ 纯文字注记（仓库内路径）
import { cp, mkdir, readdir, readFile, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const site = dirname(dirname(fileURLToPath(import.meta.url)))
const root = dirname(site)
const docsSrc = join(root, 'docs')
const docsDst = join(site, 'docs')
const diagDst = join(site, 'public', 'diagrams')

const META = {
  'v43-best-plan.md': ['V4.3 生产方案（三锚动选）', '冻结参数、组件来源表、OOS 跟踪与 2022-24 时间外推警示'],
  'oos-validation-design.md': ['OOS 验证设计', '四轨纸面验证（D3 主轨/A9/B13/C1 对照）与预注册判据'],
  'experiment-playbook.md': ['实验手册', '方法论协议、七条核心定律、负结论登记表与新实验检查清单'],
  'moneyflow-gate-plan.md': ['资金流闸门探索', 'high_frequency 资金指标 × 共振：预注册、四轮实验与全线终局'],
  'minute-resonance-design.md': ['分钟共振设计（历史）', 'dyn5 时代分钟层设计文档（已被 V4.1+ 口径取代，存档'],
  'gpt-session-summary.md': ['起源纪要（GPT 会话）', '策略规则的逐条确认记录与项目迁移上下文'],
}

// 交互图登记（图库页生成用；文件名 → [标题, 说明]）
const DIAGRAMS = {
  'architecture.html': ['系统架构', '指数锚共振策略的整体架构：数据层 → 信号层 → 执行层 → 发布层'],
  'oos-daily-workflow.html': ['OOS 每日运行流程', '无人值守 runner 的三道数据闸门与 15:05 盘中硬闸'],
  'signal-lifecycle-sequence.html': ['信号生命周期时序', 'T 日信号计算 → T+1 收盘成交 → 持仓检查的完整时序'],
  'data-pipeline-dataflow.html': ['数据流', 'iFinD/qlib 双链路采集、降级与修复路径'],
  'holding-lifecycle.html': ['持仓状态机', '建仓/续持/换仓/止损/退出现金的状态迁移'],
}

await rm(docsDst, { recursive: true, force: true })
await mkdir(docsDst, { recursive: true })
if (existsSync(diagDst)) await rm(diagDst, { recursive: true, force: true })
await mkdir(diagDst, { recursive: true })

const files = (await readdir(docsSrc)).filter(f => f.endsWith('.md') && f !== 'index.md')
for (const f of files) {
  let text = await readFile(join(docsSrc, f), 'utf8')
  // 交互图链接 → public 直通路径
  text = text.replaceAll('](diagrams/', '](/diagrams/')
  // 指向未发布路径的链接 → 纯文字注记
  text = text.replace(/\[([^\]]+)\]\((\.\.\/(?:outputs|work)\/[^)]+)\)/g,
    '$1（仓库内路径：$2）')
  await writeFile(join(docsDst, f), text, 'utf8')
}
// 只发布交互 HTML 本体（src/ 的 JSON 规格源文件不入公开产物）
for (const f of (await readdir(join(docsSrc, 'diagrams')))) {
  if (f.endsWith('.html')) await cp(join(docsSrc, 'diagrams', f), join(diagDst, f))
}

// 文档索引页（列表由 META 驱动，未登记文档以文件名兜底）
const items = files
  .map(f => {
    const [title, desc] = META[f] || [f.replace(/\.md$/, ''), '']
    return `## [${title}](${f})\n\n${desc}\n`
  })
  .join('\n')
const index = `---
title: 文档
---

# 项目文档

方法论文档与实验记录，构建期自动同步自仓库 \`docs/\` 目录（EdgeOne 与
GitHub Pages 双通道同源）。交互式架构/流程图见[图表页](diagrams.md)。

## [交互图表（架构 / 流程 / 时序 / 数据流 / 状态机）](diagrams.md)

docs/diagrams/ 下五张交互式 HTML 的内嵌图库。

${items}
`
await writeFile(join(docsDst, 'index.md'), index, 'utf8')

// 图库页：iframe 内嵌交互图（lazy）+ 新窗口打开兜底
const gallery = `---
title: 图表
---

# 交互式图表

架构图、流程图、时序图与状态机（archify 生成，构建期同步自
\`docs/diagrams/\`）。以下为内嵌预览，也可新窗口全屏查看。

${Object.entries(DIAGRAMS)
  .map(([f, [title, desc]]) => `## ${title}

${desc}

<iframe src="/diagrams/${f}" loading="lazy" title="${title}"
  style="width:100%;height:640px;border:1px solid rgba(128,128,128,.35);border-radius:8px;background:#fff">
</iframe>

[新窗口打开 ↗](/diagrams/${f})
`)
  .join('\n')}
`
await writeFile(join(docsDst, 'diagrams.md'), gallery, 'utf8')
console.log(`[sync-docs] ${files.length} md + ${Object.keys(DIAGRAMS).length} diagrams → site/docs, site/public/diagrams`)
