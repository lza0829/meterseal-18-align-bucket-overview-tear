<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 账期状态, 批状态, 百分比, 标 } from '../labels'
const periods = ref([])
const batches = ref([])
const periodId = ref('2026-10')
const batchId = ref('')
const msg = ref('')
const err = ref('')
async function refresh() {
  periods.value = await api.periods()
  batches.value = await api.batches(periodId.value)
  // 只有基于当前容差尺子的开放批才可封印
  batchId.value = batches.value.find(b => b.status === 'open')?.batch_id || ''
}
async function seal() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.seal({ period_id: periodId.value, batch_id: batchId.value || null })
    msg.value = `已封印 ${r.period_id}，绑定批 ${r.batch_id}，剩余未决 ${r.pending_left}`
    await refresh()
  } catch (e) { err.value = e.message }
}
onMounted(refresh)
</script>
<template>
  <div>
    <h1>账期封印</h1>
    <p class="sub">封印后该期估抄、实抄禁止再写</p>
    <div class="row">
      <label>账期<input v-model="periodId" @change="refresh" /></label>
      <label>对账批（仅当前尺子的开放批）<input v-model="batchId" placeholder="可空取最新开放批" /></label>
      <button class="warn" @click="seal">封印账期</button>
      <button class="secondary" @click="refresh">刷新</button>
    </div>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
    <div class="panel">
      <h3>本账期对账批</h3>
      <table>
        <thead><tr><th>批号</th><th>尺子</th><th>自动合拢</th><th>差额</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in batches" :key="b.batch_id">
            <td>{{ b.batch_id }}</td>
            <td>{{ 百分比(b.rel_tol) }}</td>
            <td>{{ b.matched_count }}</td>
            <td>{{ b.variance_count }}</td>
            <td><span class="tag" :class="b.status">{{ 标(批状态, b.status) }}</span></td>
          </tr>
          <tr v-if="!batches.length"><td colspan="5">尚未对齐</td></tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <table>
        <thead><tr><th>账期</th><th>状态</th><th>封印时刻</th><th>绑定批</th></tr></thead>
        <tbody>
          <tr v-for="p in periods" :key="p.period_id">
            <td>{{ p.period_id }}</td>
            <td><span class="tag" :class="p.status">{{ 标(账期状态, p.status) }}</span></td>
            <td>{{ p.sealed_at || '—' }}</td>
            <td>{{ p.batch_no || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
