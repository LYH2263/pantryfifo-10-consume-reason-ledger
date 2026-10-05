<template>
  <div>
    <h1>按临期消费</h1>
    <select v-model.number="item_id"><option v-for="i in items" :value="i.id">{{ i.name }}</option></select>
    <input type="number" v-model.number="qty" min="0" step="any" placeholder="数量" />
    <input v-model.trim="reason" list="reason-codes" placeholder="原因码(必填)" />
    <datalist id="reason-codes"><option v-for="r in reasonChoices" :key="r" :value="r" /></datalist>
    <input v-model="note" placeholder="备注(可选)" />
    <div>
      <button @click="preview">预览</button>
      <button @click="confirm" :disabled="!reason">确认扣减</button>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
    <template v-if="previewRows">
      <h2>预览:将扣 {{ previewRows.length }} 批(未落库)</h2>
      <table>
        <tr><th>批号</th><th>到期</th><th>take</th></tr>
        <tr v-for="d in previewRows" :key="d.lot_id">
          <td>#{{ d.lot_id }}</td><td>{{ d.expiry }}</td><td>{{ d.take }}</td>
        </tr>
      </table>
    </template>
    <pre>{{ result }}</pre>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
const items = ref([])
const item_id = ref(1)
const qty = ref(1)
const reason = ref('')
const note = ref('')
const result = ref('')
const error = ref('')
const previewRows = ref(null)
const defaultReason = ref('')
const reasonChoices = computed(() => {
  const base = ['日常消耗', '做菜', '变质丢弃', '送人']
  return defaultReason.value && !base.includes(defaultReason.value) ? [defaultReason.value, ...base] : base
})
onMounted(async () => {
  items.value = await api('/items')
  if (items.value[0]) item_id.value = items.value[0].id
  try {
    const s = await api('/settings')
    defaultReason.value = s.default_reason || ''
    reason.value = defaultReason.value
  } catch { /* 无默认则留空,由用户手填 */ }
})
async function preview() {
  error.value = ''; result.value = ''; previewRows.value = null
  try {
    const r = await api('/consume/preview', { method: 'POST', body: JSON.stringify({ item_id: item_id.value, qty: qty.value }) })
    if (r.ok) previewRows.value = r.deductions
    else error.value = r.reason === 'short' ? `库存不足,还差 ${r.short}` : '数量需为正数'
  } catch (e) { error.value = e.message }
}
async function confirm() {
  error.value = ''; result.value = ''
  if (!reason.value) { error.value = '请填写原因码'; return }
  try {
    const r = await api('/consume', { method: 'POST', body: JSON.stringify({ item_id: item_id.value, qty: qty.value, reason: reason.value, note: note.value }) })
    previewRows.value = null
    result.value = JSON.stringify(r, null, 2)
  } catch (e) { error.value = e.message }
}
</script>
