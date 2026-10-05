<template>
  <div>
    <h1>消费履历</h1>
    <input type="number" v-model.number="lotFilter" placeholder="按批号过滤(留空看全部)" />
    <h2>按批号对账</h2>
    <p class="muted">履历 take 加总 应等于 该批全层减少量(入库 − 剩余)</p>
    <p v-if="!reconcileRows.length" class="muted">暂无消费记录</p>
    <table v-else>
      <tr><th>批号</th><th>物品</th><th>层</th><th>入库</th><th>剩余</th><th>全层减少</th><th>履历take合计</th><th>对账</th></tr>
      <tr v-for="r in reconcileRows" :key="r.lot_id" :class="{ bad: !r.balanced }">
        <td>#{{ r.lot_id }}</td><td>{{ r.item_name }}</td><td>{{ layerLabel[r.layer] || r.layer }}</td>
        <td>{{ r.qty_in }}</td><td>{{ r.qty_remain }}</td>
        <td>{{ r.reduced }}</td><td>{{ r.take_sum }}</td>
        <td>{{ r.balanced ? '✓ 平' : '✗ 差 ' + r.diff }}</td>
      </tr>
    </table>
    <h2>履历明细</h2>
    <p v-if="!lines.length" class="muted">暂无履历行</p>
    <table v-else>
      <tr><th>时间</th><th>单号</th><th>批号</th><th>物品</th><th>take</th><th>原因码</th></tr>
      <tr v-for="h in lines" :key="h.id">
        <td>{{ h.created_at.slice(0, 19).replace('T', ' ') }}</td>
        <td>#{{ h.consumption_id }}</td><td>#{{ h.lot_id }}</td>
        <td>{{ h.item_name }}</td><td>{{ h.take }} {{ h.unit }}</td><td>{{ h.reason }}</td>
      </tr>
    </table>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
const allLines = ref([])
const allReconcile = ref([])
const lotFilter = ref(null)
const layerLabel = { upper: '上层', mid: '中层', lower: '下层' }
const lines = computed(() => lotFilter.value ? allLines.value.filter(h => h.lot_id === lotFilter.value) : allLines.value)
const reconcileRows = computed(() => allReconcile.value.filter(r =>
  (lotFilter.value ? r.lot_id === lotFilter.value : true) && (r.take_sum > 0 || r.reduced !== 0)))
onMounted(async () => {
  allLines.value = await api('/consumptions')
  allReconcile.value = await api('/consumptions/reconcile')
})
</script>
