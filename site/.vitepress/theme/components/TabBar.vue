<template>
  <nav class="TabBar" role="tablist">
    <a v-for="t in tabs" :key="t.link" :href="t.link" class="tb" :class="{ on: active(t.link) }">
      {{ t.text }}
    </a>
  </nav>
</template>
<script setup lang="ts">
import { useRoute } from 'vitepress'
const route = useRoute()
const tabs = [
  { text: '首页', link: '/' },
  { text: '净值', link: '/nav/' },
  { text: '信号', link: '/signals/' },
  { text: '档案', link: '/archive/' },
]
const active = (link: string) =>
  link === '/' ? route.path === '/' || route.path === '/index.html' : route.path.startsWith(link)
</script>
<style scoped>
.TabBar {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 40;
  background: rgba(11, 21, 35, 0.94); backdrop-filter: blur(8px);
  border-top: 1px solid var(--line);
  max-width: 100%;
}
.tb {
  flex: 1; text-align: center; padding: 11px 0 13px;
  color: var(--text-low); font-size: 13px; letter-spacing: 0.08em;
  border-top: 2px solid transparent; text-decoration: none;
}
.tb.on { color: var(--text-hi); border-top-color: var(--gold); }
</style>
