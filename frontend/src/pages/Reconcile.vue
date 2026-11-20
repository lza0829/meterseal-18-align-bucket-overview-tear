<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 批状态, 命运, 差额原因, 合拢方式, 容差口径, 百分比, 标 } from '../labels'
const periodId = ref('2026-10')
const batch = ref(null)
const vars = ref([])
const audit = ref([])
const msg = ref('')
const err = ref('')
async function loadBatch(id) {
  batch.value = (await api.batches(periodId.value)).find((b) => b.batch_id === id) || null
  if (!batch.value) { vars.value = []; audit.value = []; return }
  vars.value = await api.variances({ batch_id: batch.value.batch_id })
  audit.value = await api.matches(batch.value.batch_id)
}
async function align() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.align(periodId.value)
    await loadBatch(r.batch_id)
    msg.value = `对齐完成：自动合拢 ${r.matched_count}，差额 ${r.variance_count}（${标(容差口径, r.ruler.tol_mode)} ≤ ${百分比(r.ruler.rel_tol)}）`
  } catch (e) { err.value = e.message }
}
async function fate(id, f) {
  err.value = ''
  try {
    await api.setFate(id, f)
    vars.value = await api.variances({ batch_id: batch.value.batch_id })
  } catch (e) { err.value = e.message }
}
onMounted(async () => {
  const bs = await api.batches(periodId.value)
  if (bs[0]) await loadBatch(bs[0].batch_id)
})

const snap = ref(null)
onMounted(async () => {
  try { snap.value = await api.get('/api/snapshot-pending') } catch (_) {}
})
</script>
<template>
  <div>
    <h1>对齐差额</h1>
    <p class="sub">
      全局唯一尺子：{{ batch ? 标(容差口径, batch.tol_mode) : '相对误差率' }}；
      |实抄−估抄| ÷ |估抄| ≤ 容差即自动合拢并留审计，超出进差额待人工处置。
    </p>
    <div class="row">
      <label>账期<input v-model="periodId" /></label>
      <button @click="align">跑对齐</button>
    </div>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
    <div class="panel" v-if="batch">
      <p>
        批号 {{ batch.batch_id }} · 指纹 {{ batch.fingerprint }} · 尺子
        {{ 标(容差口径, batch.tol_mode) }} {{ 百分比(batch.rel_tol) }} ·
        <span class="tag" :class="batch.status">{{ 标(批状态, batch.status) }}</span>
      </p>
      <p class="sub" v-if="batch.status === 'stale'">该批基于旧容差，不可封印，请重新对齐。</p>

      <h3>差额（人工处置）</h3>
      <table>
        <thead><tr><th>差额编号</th><th>表号</th><th>估抄</th><th>实抄</th><th>差值</th><th>相对误差</th><th>原因</th><th>命运</th><th>处置</th></tr></thead>
        <tbody>
          <tr v-for="v in vars" :key="v.variance_id">
            <td>{{ v.variance_id }}</td>
            <td>{{ v.meter_id }}</td>
            <td>{{ v.estimate_qty ?? '—' }}</td>
            <td>{{ v.actual_qty ?? '—' }}</td>
            <td>{{ v.delta }}</td>
            <td>{{ 百分比(v.ratio) }}</td>
            <td>{{ 标(差额原因, v.reason) }}</td>
            <td>{{ 标(命运, v.fate) }}</td>
            <td>
              <button class="secondary" :disabled="v.delta > 0" @click="fate(v.variance_id, 'accept_short')">认短</button>
              <button class="secondary" :disabled="v.delta < 0" @click="fate(v.variance_id, 'accept_over')">认溢</button>
              <button class="warn" @click="fate(v.variance_id, 'quarantine')">隔离</button>
            </td>
          </tr>
          <tr v-if="!vars.length"><td colspan="9">本批无差额，全部在容差内自动合拢。</td></tr>
        </tbody>
      </table>

      <h3>自动合拢审计</h3>
      <table>
        <thead><tr><th>表号</th><th>估抄</th><th>实抄</th><th>差值</th><th>相对误差</th><th>合拢方式</th><th>尺子</th></tr></thead>
        <tbody>
          <tr v-for="a in audit" :key="a.audit_id">
            <td>{{ a.meter_id }}</td>
            <td>{{ a.estimate_qty }}</td>
            <td>{{ a.actual_qty }}</td>
            <td>{{ a.delta }}</td>
            <td>{{ 百分比(a.ratio) }}</td>
            <td>{{ 标(合拢方式, a.outcome) }}</td>
            <td>{{ 标(容差口径, a.tol_mode) }} {{ 百分比(a.rel_tol) }}</td>
          </tr>
          <tr v-if="!audit.length"><td colspan="7">本批无自动合拢记录。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
