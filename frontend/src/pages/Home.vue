<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 容差口径, 命运, 批状态, 百分比, 标 } from '../labels'
const data = ref(null)
const err = ref('')
onMounted(async () => {
  try { data.value = await api.overview() } catch (e) { err.value = e.message }
})
</script>
<template>
  <div>
    <h1>总览</h1>
    <p class="sub">当前账期估抄、实抄、对齐与封印状态</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <div class="grid" v-if="data">
      <div class="stat">当前账期<b>{{ data.current_period }}</b></div>
      <div class="stat">开放账期<b>{{ data.open_periods }}</b></div>
      <div class="stat">已封印账期<b>{{ data.sealed_periods }}</b></div>
      <div class="stat">表具数<b>{{ data.meter_count }}</b></div>
      <div class="stat">未决差额<b>{{ data.unresolved_variances }}</b></div>
      <div class="stat">已决差额<b>{{ data.resolved }}</b></div>
      <div class="stat">自动合拢<b>{{ data.auto_matched }}</b></div>
      <div class="stat">封印残留<b>{{ data.sealed_unresolved }}</b></div>
    </div>
    <div class="panel" v-if="data">
      <h3>容差尺子</h3>
      <p>
        {{ 标(容差口径, data.ruler.tol_mode) }} · 容差 {{ 百分比(data.ruler.rel_tol) }}
      </p>
      <h3>命运分桶</h3>
      <p>
        <span v-for="(count, f) in data.fate_buckets" :key="f" class="tag">
          {{ 标(命运, f) }} {{ count }}
        </span>
        <span v-if="!Object.keys(data.fate_buckets).length" class="sub">暂无差额</span>
      </p>
    </div>
    <div class="panel" v-if="data">
      <h3>最近对账批</h3>
      <table>
        <thead><tr><th>批号</th><th>账期</th><th>自动合拢</th><th>差额</th><th>尺子</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in data.recent_batches" :key="b.batch_id">
            <td>{{ b.batch_id }}</td>
            <td>{{ b.period_id }}</td>
            <td>{{ b.matched_count }}</td>
            <td>{{ b.variance_count }}</td>
            <td>{{ 标(容差口径, b.tol_mode) }} {{ 百分比(b.rel_tol) }}</td>
            <td><span class="tag" :class="b.status">{{ 标(批状态, b.status) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
