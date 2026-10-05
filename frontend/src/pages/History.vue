<template>
  <div>
    <h1>消费原因履历</h1>
    <p class="muted">按批号把履历 take 加总，与全层该批少掉的量（入库−余量）对账</p>

    <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
      <div>
        <label>按批号过滤（可空）</label><br />
        <input v-model="lotFilter" placeholder="批号 lot_id" style="margin:0" />
      </div>
      <div>
        <label>按层过滤</label><br />
        <select v-model="layer" style="margin:0">
          <option value="">全层</option><option value="upper">上层</option>
          <option value="mid">中层</option><option value="lower">下层</option>
        </select>
      </div>
      <button @click="load">刷新对账</button>
    </div>

    <h2 style="margin-top:18px">按批号对账</h2>
    <p :style="rec.balanced ? 'color:var(--teal)' : 'color:var(--alert)'">
      总对账：履历 {{ rec.total_lines }} 行 · 履历 take 合计 {{ rec.total_history_take }}
      · 全层减少合计 {{ rec.total_layer_decrease }}
      —— {{ rec.balanced ? '✓ 三方对平' : '✗ 对不平，存在差异' }}
    </p>
    <table border="1" cellpadding="6" style="border-collapse:collapse;background:#fff">
      <thead>
        <tr>
          <th>批号</th><th>品项</th><th>层</th><th>到期</th><th>入库</th><th>余量</th>
          <th>全层减少</th><th>履历 take 合计</th><th>履历行数</th><th>对账</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in shownLots" :key="r.lot_id">
          <td>#{{ r.lot_id }}</td><td>{{ r.item_name }}</td><td>{{ r.layer }}</td>
          <td>{{ r.expiry }}</td><td>{{ r.qty_in }}</td><td>{{ r.qty_remain }}</td>
          <td>{{ r.layer_decrease }}</td><td>{{ r.history_take }}</td>
          <td>{{ r.line_count }}</td>
          <td :style="r.balanced ? 'color:var(--teal)' : 'color:var(--alert)'">
            {{ r.balanced ? '✓' : '✗' }}
          </td>
        </tr>
      </tbody>
    </table>

    <h2 style="margin-top:18px">履历明细</h2>
    <table border="1" cellpadding="6" style="border-collapse:collapse;background:#fff">
      <thead>
        <tr><th>行</th><th>单号</th><th>批号</th><th>品项</th><th>take</th>
        <th>扣前</th><th>扣后</th><th>原因</th><th>时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="h in history" :key="h.id">
          <td>{{ h.id }}</td><td>{{ h.order_id }}</td><td>#{{ h.lot_id }}</td>
          <td>{{ h.item_name }}</td><td>{{ h.take }}</td>
          <td>{{ h.qty_before }}</td><td>{{ h.qty_after }}</td>
          <td>{{ h.reason_code }}</td><td>{{ h.created_at }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="!history.length" class="muted">暂无履历行。</p>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
const history = ref([])
const rec = ref({ lots: [], balanced: true, total_lines: 0, total_history_take: 0, total_layer_decrease: 0 })
const lotFilter = ref('')
const layer = ref('')

const shownLots = computed(() => {
  if (!lotFilter.value) return rec.value.lots
  const id = Number(lotFilter.value)
  return rec.value.lots.filter(r => r.lot_id === id)
})

async function load() {
  const q = layer.value ? ('?layer=' + layer.value) : ''
  rec.value = await api('/consume/reconcile' + q)
  const params = new URLSearchParams()
  if (lotFilter.value) params.set('lot_id', lotFilter.value)
  history.value = await api('/consume/history' + (params.toString() ? '?' + params.toString() : ''))
}
onMounted(load)
</script>
