import { defineConfig } from 'vitepress'

export default defineConfig({
  title: '共振 · 六号机',
  description: '指数锚共振信号站 · D3 生产轨 / C1 深证单锚对照',
  appearance: false,          // 仪器盘固定暗色，不提供切换
  cleanUrls: true,
  head: [['meta', { name: 'viewport', content: 'width=device-width, initial-scale=1' }]],
  themeConfig: {
    nav: [
      { text: '首页', link: '/' },
      { text: '净值', link: '/nav/' },
      { text: '信号', link: '/signals/' },
      { text: '档案', link: '/archive/' },
    ],
    outline: false,
    lastUpdated: false,
    returnToTopLabel: '返回顶部',
    docFooter: { prev: false, next: false },
  },
})
