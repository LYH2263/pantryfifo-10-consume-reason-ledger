<template>
  <div>
    <h1>按临期消费</h1>
    <p class="muted">FEFO 先到期先出 · 消费必须填写原因，确认后逐批落下履历行</p>

    <label>品项</label>
    <select v-model.number="item_id"><option v-for="i in items" :value="i.id">{{ i.name }}</option></select>

    <label>数量</label>
    <input type="number" v-model.number="qty" />

    <label>消费原因 <span style="color:var(--alert)">*</span></label>
    <select v-model="reason_code">
      <option value="" disabled>请选择原因</option>
      <option v-for="r in reasons" :key="r.code" :value="r.code">{{ r.code }} · {{ r.label }}</option>
    </select>

    <label>备注（可选）</label>
    <input v-model="note" placeholder="备注" />

    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button style="background:#fff;color:var(--teal);border:1px solid var(--teal)" @click="preview">预览将扣批次</button>
      <button @click="confirm">确认扣减并落履历</button>
    </div>

    <p v-if="error" style="color:var(--alert)">{{ error }}</p>

    <div v-if="plan" style="margin-top:12px">
      <h3 style="margin:8px 0">预览（未落库、余量未改）</h3>
      <p v-if="!plan.ok" style="color:var(--alert)">库存不足，还差 {{ plan.short }}，无法确认。</p>
      <table v-else border="1" cellpadding="6" style="border-collapse:collapse;background:#fff">
        <thead><tr><th>批号</th><th>到期</th><th>将扣 take</th></tr></thead>
        <tbody>
          <tr v-for="d in plan.deductions" :key="d.lot_id">
            <td>#{{ d.lot_id }}</td><td>{{ d.expiry }}</td><td>{{ d.take }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="confirmed" style="margin-top:12px">
      <h3 style="margin:8px 0">已确认 #{{ confirmed.order_id }} · 原因 {{ confirmed.reason_code }}</h3>
      <p class="muted">回包 {{ confirmed.deductions.length }} 笔 take：
        {{ confirmed.deductions.map(d => 'lot#' + d.lot_id + ' -' + d.take).join('，') }}
      </p>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const items = ref([])
const reasons = ref([])
const item_id = ref(1)
const qty = ref(1)
const reason_code = ref('')
const note = ref('')
const plan = ref(null)
const confirmed = ref(null)
const error = ref('')

onMounted(async () => {
  items.value = await api('/items')
  reasons.value = await api('/reasons')
  const s = await api('/settings')
  // 默认码只用于预填；确认时仍显式上送，缺原因整单失败
  if (items.value[0]) item_id.value = items.value[0].id
  if (s.default_reason) reason_code.value = s.default_reason
})

async function preview() {
  error.value = ''; confirmed.value = null
  if (!reason_code.value) { error.value = '请先选择消费原因（预览不写库，但原因必填）'; return }
  try {
    plan.value = await api('/consume/preview?item_id=' + item_id.value + '&qty=' + qty.value)
  } catch (e) { error.value = e.message }
}

async function confirm() {
  error.value = ''
  if (!reason_code.value) { error.value = '消费原因必填，未填写原因整单不会扣减'; return }
  try {
    confirmed.value = await api('/consume', {
      method: 'POST',
      body: JSON.stringify({ item_id: item_id.value, qty: qty.value, reason_code: reason_code.value, note: note.value }),
    })
    plan.value = null
  } catch (e) { error.value = '确认失败（余量与履历均未改动）：' + e.message }
}
</script>
