import { defineConfig } from 'vitepress'

export default defineConfig({
  // 子路径部署开关：GitHub Pages 项目页传 VITEPRESS_BASE=/6.resonance/；
  // 缺省 '/'（EdgeOne 自有域名根路径，行为不变）
  base: process.env.VITEPRESS_BASE ?? '/',
  title: '共振 · 信号站',
  description: '指数锚共振信号站 · D3 生产轨 / C1 深证单锚对照',
  appearance: false,          // 仪器盘固定暗色，不提供切换
  cleanUrls: true,
  head: [['meta', { name: 'viewport', content: 'width=device-width, initial-scale=1' }]],
  themeConfig: {
    nav: [
      { text: '首页', link: '/' },
      { text: '净值', link: '/nav/' },
      { text: '信号', link: '/signals/' },
      {
        text: '文档',
        items: [
          { text: 'V4.3 生产方案', link: '/docs/v43-best-plan/' },
          { text: 'OOS 验证设计', link: '/docs/oos-validation-design/' },
          { text: '实验手册', link: '/docs/experiment-playbook/' },
          { text: '资金流闸门探索', link: '/docs/moneyflow-gate-plan/' },
          { text: '起源纪要', link: '/docs/gpt-session-summary/' },
          { text: '全部文档', link: '/docs/' },
        ],
      },
      { text: '档案', link: '/archive/' },
    ],
    outline: false,
    lastUpdated: false,
    returnToTopLabel: '返回顶部',
    docFooter: { prev: false, next: false },
  },
})
