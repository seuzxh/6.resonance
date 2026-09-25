import DefaultTheme from 'vitepress/theme'
import { h } from 'vue'
import type { Theme } from 'vitepress'
import TabBar from './components/TabBar.vue'
import PhaseStrip from './components/PhaseStrip.vue'
import RecentFeed from './components/RecentFeed.vue'
import NavChart from './components/NavChart.vue'
import SignalTable from './components/SignalTable.vue'
import ArchiveList from './components/ArchiveList.vue'
import HomeSide from './components/HomeSide.vue'
import './custom.css'

export default {
  extends: DefaultTheme,
  Layout: () => h(DefaultTheme.Layout, null, {
    'layout-bottom': () => h(TabBar),
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
