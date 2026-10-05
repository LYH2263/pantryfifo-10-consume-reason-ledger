<template>
  <div>
    <h1>设置</h1>
    <label class="muted">默认原因码(新确认预填,不改写已落库履历)</label>
    <input v-model.trim="defaultReason" placeholder="如:日常消耗" />
    <label class="muted">临期预警天数</label>
    <input type="number" v-model.number="warnDays" min="0" />
    <button @click="save">保存</button>
    <p v-if="msg" class="muted">{{ msg }}</p>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const defaultReason = ref('')
const warnDays = ref(3)
const msg = ref('')
onMounted(async () => {
  const s = await api('/settings')
  defaultReason.value = s.default_reason || ''
  warnDays.value = Number(s.warn_days ?? 3)
})
async function save() {
  try {
    await api('/settings', { method: 'PUT', body: JSON.stringify({ key: 'default_reason', value: defaultReason.value }) })
    await api('/settings', { method: 'PUT', body: JSON.stringify({ key: 'warn_days', value: String(warnDays.value) }) })
    msg.value = '已保存;已落库的履历保持原文'
  } catch (e) { msg.value = e.message }
}
</script>
