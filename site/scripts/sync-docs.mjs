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
await cp(join(docsSrc, 'diagrams'), diagDst, { recursive: true })

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
GitHub Pages 双通道同源）。

${items}
`
await writeFile(join(docsDst, 'index.md'), index, 'utf8')
console.log(`[sync-docs] ${files.length} md + diagrams → site/docs, site/public/diagrams`)
