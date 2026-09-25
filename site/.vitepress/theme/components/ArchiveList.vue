<template>
  <div class="page-wrap">
    <h1 class="tt">档案</h1>
    <p class="sub">研究结论与口径变更记录（原文在 GitHub 仓库 docs/）</p>
    <div v-if="items.length" class="docs">
      <a v-for="d in items" :key="d.url" class="doc" :href="d.url" target="_blank" rel="noopener">
        <div class="top"><span class="date mono">{{ d.date }}</span><span class="title">{{ d.title }}</span></div>
      </a>
    </div>
    <p v-else class="sub">暂无档案条目。</p>
  </div>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
const items = ref<any[]>([])
onMounted(async () => {
  const r = await fetch(import.meta.env.BASE_URL + 'data/archive.json', { cache: 'no-store' })
  items.value = (await r.json()).items ?? []
})
</script>
<style scoped>
.tt { font-family: var(--font-display); font-size: 18px; letter-spacing: 0.06em; margin: 6px 0 4px; }
.sub { font-size: 12px; color: var(--text-low); margin-bottom: 14px; }
.docs { display: grid; grid-template-columns: 1fr; gap: 10px; }
.doc { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 13px 16px; text-decoration: none; color: inherit; }
.doc:hover { border-color: var(--text-low); }
.top { display: flex; align-items: baseline; gap: 10px; }
.date { font-size: 12px; color: var(--text-low); }
.title { font-size: 15px; font-weight: 600; }
@media (min-width: 900px) { .docs { grid-template-columns: 1fr 1fr; } }
</style>
