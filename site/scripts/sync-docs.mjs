// docs/ → site/ 构建期同步（EdgeOne 与 GitHub Pages 双通道共用，
// 由 package.json 的 docs:dev / docs:build 前置调用；产物不入库）。
//
// 职责：
//   1. docs/*.md            → site/docs/*.md（链接改写 + frontmatter 注入：
//      目录标题、outline [2,3] 右侧目录；长文导航）
//   2. docs/diagrams/*.html → site/public/diagrams/（public 直通；src/ 规格不公开）
//   3. site/.vitepress/docs-manifest.json（config.mts 读取 → /docs/ 侧边栏）
//   4. site/docs/index.md（分组卡片首页，样式 custom.css .doc-*）
//   5. site/docs/diagrams.md（图库：iframe 相对路径——Pages 子路径 base 下
//      绝对路径 /diagrams/ 会 404）
//
// 「docs 下内容都可以展示」兜底：未登记进 CATALOG 的 md 自动进入「其他」
// 分组，标题取正文首个 `# ` 行。
import { cp, mkdir, readdir, readFile, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const site = dirname(dirname(fileURLToPath(import.meta.url)))
const root = dirname(site)
const docsSrc = join(root, 'docs')
const docsDst = join(site, 'docs')
const diagDst = join(site, 'public', 'diagrams')
const manifestPath = join(site, '.vitepress', 'docs-manifest.json')

// ---- 分组目录（单一事实源；badge 可省略） ----
const CATALOG = [
  {
    group: '生产与验证', desc: '现役口径与样本外跟踪',
    docs: [
      { file: 'v43-best-plan.md', title: 'V4.3 生产方案（三锚动选）', badge: '现行',
        desc: '冻结参数、组件来源表、领先分布与 2022-24 时间外推警示' },
      { file: 'oos-validation-design.md', title: 'OOS 验证设计', badge: '预注册',
        desc: '四轨纸面验证（D3 主轨 / A9 / B13 / C1 对照）、判据与运行纪律' },
    ],
  },
  {
    group: '方法论', desc: '怎么做实验，以及为什么',
    docs: [
      { file: 'experiment-playbook.md', title: '实验手册', badge: '核心',
        desc: '方法论协议、七条核心定律（L1-L7）、负结论登记表与新实验检查清单' },
      { file: 'gpt-session-summary.md', title: '起源纪要（GPT 会话）',
        desc: '策略规则的逐条确认记录与项目迁移上下文' },
    ],
  },
  {
    group: '探索记录', desc: '完整实验闭环存档',
    docs: [
      { file: 'moneyflow-gate-plan.md', title: '资金流闸门探索', badge: '收官',
        desc: 'high_frequency 资金指标 × 共振：预注册 → 四轮实验 → 全线终局' },
    ],
  },
  {
    group: '历史存档', desc: '已被后续口径取代的设计文档',
    docs: [
      { file: 'minute-resonance-design.md', title: '分钟共振设计（dyn5 时代）', badge: '历史',
        desc: 'Top10 池内分钟二次排序的三版演化，V4.1+ 口径取代' },
    ],
  },
]

// 交互图登记（图库页；文件名 → [标题, 说明]）
const DIAGRAMS = {
  'architecture.html': ['系统架构', '指数锚共振策略的整体架构：数据层 → 信号层 → 执行层 → 发布层'],
  'oos-daily-workflow.html': ['OOS 每日运行流程', '无人值守 runner 的三道数据闸门与 15:05 盘中硬闸'],
  'signal-lifecycle-sequence.html': ['信号生命周期时序', 'T 日信号计算 → T+1 收盘成交 → 持仓检查的完整时序'],
  'data-pipeline-dataflow.html': ['数据流', 'iFinD/qlib 双链路采集、降级与修复路径'],
  'holding-lifecycle.html': ['持仓状态机', '建仓/续持/换仓/止损/退出现金的状态迁移'],
}

const firstHeading = text => (text.match(/^#\s+(.+)$/m) || [null, '未命名文档'])[1].trim()

await rm(docsDst, { recursive: true, force: true })
await mkdir(docsDst, { recursive: true })
if (existsSync(diagDst)) await rm(diagDst, { recursive: true, force: true })
await mkdir(diagDst, { recursive: true })

const srcFiles = (await readdir(docsSrc)).filter(f => f.endsWith('.md'))
const registered = new Set(CATALOG.flatMap(g => g.docs.map(d => d.file)))
// 未登记文档兜底组（保证 docs 下任何 md 都有入口）
const unreg = srcFiles.filter(f => !registered.has(f))
if (unreg.length) {
  CATALOG.push({
    group: '其他', desc: '未登记进目录的文档（自动纳入）',
    docs: unreg.map(f => ({ file: f, title: '', desc: '' })),
  })
}

// ---- 1. 复制 md（链接改写 + frontmatter 注入） ----
const titleByFile = {}
for (const g of CATALOG) for (const d of g.docs) titleByFile[d.file] = d.title
for (const f of srcFiles) {
  let text = await readFile(join(docsSrc, f), 'utf8')
  // 交互图链接 → public 直通路径（markdown 链接由 VitePress 处理 base）
  text = text.replaceAll('](diagrams/', '](/diagrams/')
  // 指向未发布路径的链接 → 纯文字注记
  text = text.replace(/\[([^\]]+)\]\((\.\.\/(?:outputs|work)\/[^)]+)\)/g,
    '$1（仓库内路径：$2）')
  const title = titleByFile[f] || firstHeading(text)
  const fm = text.startsWith('---') ? '' :
    `---\ntitle: ${title}\noutline: [2, 3]\n---\n\n`
  await writeFile(join(docsDst, f), fm + text, 'utf8')
}
// 补齐目录标题/摘要（兜底组从首标题回填）
for (const g of CATALOG) for (const d of g.docs) {
  if (!d.title) d.title = titleByFile[d.file]
}

// ---- 2. 交互 HTML 本体（src/ 的 JSON 规格不入公开产物） ----
for (const f of (await readdir(join(docsSrc, 'diagrams')))) {
  if (f.endsWith('.html')) await cp(join(docsSrc, 'diagrams', f), join(diagDst, f))
}

// ---- 3. 侧边栏 manifest（config.mts 构建期读取） ----
const overview = {
  group: '总览',
  items: [
    { text: '文档首页', link: '/docs/' },
    { text: '交互图表', link: '/docs/diagrams/' },
  ],
}
const manifest = [
  overview,
  ...CATALOG.map(g => ({
    group: g.group,
    items: g.docs.map(d => ({
      text: d.title,
      link: `/docs/${d.file.replace(/\.md$/, '')}/`,
    })),
  })),
]
await writeFile(manifestPath, JSON.stringify(manifest, null, 2), 'utf8')

// ---- 4. 分组卡片首页 ----
const badge = b => b ? `<span class="doc-badge">${b}</span>` : ''
const cardHtml = (title, href, desc, b) => `  <a class="doc-card" href="${href}">
    <div class="doc-card-head">${title}${badge(b)}</div>
    <div class="doc-card-desc">${desc}</div>
  </a>`
const groupsHtml = [
  `## 总览

<div class="doc-grid">
${cardHtml('交互图表（架构 / 流程 / 时序 / 数据流 / 状态机）', './diagrams/',
  '五张交互式 HTML 的内嵌图库，archify 生成', '图库')}
</div>`,
  ...CATALOG.map(g => `## ${g.group}

<div class="doc-grid">
${g.docs.map(d => cardHtml(d.title, `./${d.file.replace(/\.md$/, '')}/`, d.desc, d.badge)).join('\n')}
</div>`),
].join('\n\n')
const index = `---
title: 文档
aside: false
outline: false
---

# 项目文档

方法论文档与实验记录，构建期自动同步自仓库 \`docs/\` 目录（EdgeOne 与
GitHub Pages 双通道同源）。

${groupsHtml}
`
await writeFile(join(docsDst, 'index.md'), index, 'utf8')

// ---- 5. 图库页（iframe 用构建期 base 拼根绝对路径：相对路径在无尾斜杠 URL
//         下会解析错位（/docs/diagrams → iframe 打到 /docs/diagrams/x.html）） ----
const BASE = (() => {
  const b = process.env.VITEPRESS_BASE ?? '/'
  return b.endsWith('/') ? b : b + '/'
})()
const gallery = `---
title: 图表
outline: false
---

# 交互式图表

架构图、流程图、时序图与状态机（archify 生成，构建期同步自
\`docs/diagrams/\`）。以下为内嵌预览，也可新窗口全屏查看。

${Object.entries(DIAGRAMS).map(([f, [title, desc]]) => `## ${title}

${desc}

<iframe src="${BASE}diagrams/${f}" loading="lazy" title="${title}"
  style="width:100%;height:640px;border:1px solid rgba(128,128,128,.35);border-radius:8px;background:#fff">
</iframe>

[新窗口打开 ↗](/diagrams/${f})
`).join('\n')}

> 图表的 JSON 规格源文件在仓库 \`docs/diagrams/src/\`，不入公开产物。
`
await writeFile(join(docsDst, 'diagrams.md'), gallery, 'utf8')

console.log(`[sync-docs] ${srcFiles.length} md（${CATALOG.length} 组）+ ${Object.keys(DIAGRAMS).length} diagrams + manifest`)
