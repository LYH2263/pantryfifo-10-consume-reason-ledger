<template>
  <div>
    <h1>设置</h1>

    <label>默认消费原因</label>
    <p class="muted">仅用于消费页预填；修改不会改写已落库的履历字，新的一次确认才用新默认。</p>
    <select v-model="defaultReason">
      <option v-for="r in reasons" :key="r.code" :value="r.code">{{ r.code }} · {{ r.label }}</option>
    </select>
    <button @click="save">保存默认原因</button>
    <p v-if="msg" :style="ok ? 'color:var(--teal)' : 'color:var(--alert)'">{{ msg }}</p>

    <h2 style="margin-top:18px">全部设置</h2>
    <pre>{{ s }}</pre>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const s = ref('')
const reasons = ref([])
const defaultReason = ref('')
const msg = ref('')
const ok = ref(true)
onMounted(async () => {
  s.value = JSON.stringify(await api('/settings'), null, 2)
  reasons.value = await api('/reasons')
  const cur = await api('/settings')
  defaultReason.value = cur.default_reason || (reasons.value[0] && reasons.value[0].code) || ''
})
async function save() {
  msg.value = ''
  try {
    const r = await api('/settings/default-reason', {
      method: 'PUT', body: JSON.stringify({ code: defaultReason.value }),
    })
    ok.value = true; msg.value = '已保存默认原因：' + r.default_reason
    s.value = JSON.stringify(await api('/settings'), null, 2)
  } catch (e) { ok.value = false; msg.value = e.message }
}
</script>
