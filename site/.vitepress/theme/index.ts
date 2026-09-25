import DefaultTheme from 'vitepress/theme'
import { defineComponent, h, onMounted, watch } from 'vue'
import { useRoute } from 'vitepress'
import { useData } from 'vitepress'
import type { Theme } from 'vitepress'
import TabBar from './components/TabBar.vue'
import PhaseStrip from './components/PhaseStrip.vue'
import RecentFeed from './components/RecentFeed.vue'
import NavChart from './components/NavChart.vue'
import SignalTable from './components/SignalTable.vue'
import ArchiveList from './components/ArchiveList.vue'
import HomeSide from './components/HomeSide.vue'
import './custom.css'

// 文档页作用域开关：/docs/** 时给 body 挂 docs-page，custom.css 依据它
// 恢复文档排版（全局规则为仪表盘全宽服务）；route.path 不含 base 前缀。
const DocsBodyClass = defineComponent({
  setup() {
    const route = useRoute()
    onMounted(() => {
      watch(() => route.path, p => {
        document.body.classList.toggle('docs-page', p.startsWith('/docs/'))
      }, { immediate: true })
    })
    return () => null
  },
})

export default {
  extends: DefaultTheme,
  Layout: () => h(DefaultTheme.Layout, null, {
    'layout-bottom': () => h(TabBar),
    'layout-top': () => h(DocsBodyClass),
  }),
  enhanceApp({ app }) {
    app.component('PhaseStrip', PhaseStrip)
    app.component('RecentFeed', RecentFeed)
    app.component('NavChart', NavChart)
    app.component('SignalTable', SignalTable)
    app.component('ArchiveList', ArchiveList)
    app.component('HomeSide', HomeSide)
  },
} satisfies Theme
