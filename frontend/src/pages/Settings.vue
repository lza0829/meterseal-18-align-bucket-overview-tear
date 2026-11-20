<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 容差口径, 百分比, 标 } from '../labels'
const relTol = ref('')
const relEps = ref('')
const ruler = ref(null)
const msg = ref('')
const err = ref('')
async function load() {
  err.value = ''
  try {
    const s = await api.settings()
    ruler.value = s.ruler
    relTol.value = String(s.settings.rel_tol ?? '')
    relEps.value = String(s.settings.rel_eps ?? '')
  } catch (e) { err.value = e.message }
}
async function saveTolerance() {
  err.value = ''; msg.value = ''
  try {
    const r = await api.putSetting('rel_tol', String(relTol.value))
    const e = await api.putSetting('rel_eps', String(relEps.value))
    const stale = Math.max(r.stale_batches || 0, e.stale_batches || 0)
    msg.value = `容差已更新，${stale} 个开放对账批基于旧尺子被作废，请重新对齐。`
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div>
    <h1>设置</h1>
    <p class="sub">容差为全局唯一口径，不按水表/电表分设</p>
    <div class="panel" v-if="ruler">
      <p>
        当前尺子：<b>{{ 标(容差口径, ruler.tol_mode) }}</b> ·
        容差 {{ 百分比(ruler.rel_tol) }} · 分母下限 {{ ruler.rel_eps }}
      </p>
      <p class="sub">
        判定：|实抄−估抄| ÷ max(|估抄|, 分母下限) ≤ 容差 → 自动合拢并写审计；否则进未决差额。
      </p>
      <div class="row">
        <label>相对容差（0~1 小数）<input v-model="relTol" /></label>
        <label>分母下限<input v-model="relEps" /></label>
        <button @click="saveTolerance">保存容差</button>
        <button class="secondary" @click="load">刷新</button>
      </div>
      <p class="msg ok" v-if="msg">{{ msg }}</p>
      <p class="msg err" v-if="err">{{ err }}</p>
      <p class="sub">修改容差会使所有开放对账批作废，必须按新尺子重新对齐；已封印账期不受影响。</p>
    </div>
  </div>
</template>
