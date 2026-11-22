<template>
  <section class="page" data-module="label_batch">
    <header class="page-head">
      <div>
        <h2>标签派生批次</h2>
        <p class="page-desc">
          批量登记多张上下游表的直接标签，沿已核实血缘边计算派生标签；
          互斥等级在确认前不成为结果，撤销批次只消除由该批支持的标记。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-batch-create" type="button" @click="openCreate">
          批量登记标签
        </button>
        <button class="btn" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">派生批次</span>
        <strong class="stat-value">{{ batches.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">下游标记</span>
        <strong class="stat-value">{{ labels.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">有效标记</span>
        <strong class="stat-value">{{ effectiveCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待确认冲突</span>
        <strong class="stat-value">{{ conflicts.length }}</strong>
      </article>
    </div>

    <p v-if="error" class="error-text" data-testid="batch-error">{{ error }}</p>

    <h3>下游标签（图谱读入批次后的计算结果）</h3>
    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>目标资产</span>
        <input v-model="assetFilter" data-testid="filter-asset" placeholder="按数据表过滤" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="assetFilter = ''">重置</button>
    </form>

    <table class="data-table" data-testid="derived-table">
      <thead>
        <tr>
          <th>目标资产</th>
          <th>标签维度</th>
          <th>标签值</th>
          <th>候选值</th>
          <th>标记类型</th>
          <th>标记状态</th>
          <th>支持批次</th>
          <th>来源与已纳入计算的边</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in filteredLabels"
          :key="String(row.id)"
          :data-testid="`derived-row-${row.id}`"
          :data-status="row.标记状态"
        >
          <td>{{ row.目标资产 }}</td>
          <td>{{ row.标签维度 }}</td>
          <td>{{ row.标签值 || '—（待确认）' }}</td>
          <td>{{ row.候选值.join(' / ') }}</td>
          <td>{{ row.标记类型 }}</td>
          <td>{{ row.标记状态 }}</td>
          <td>{{ row.支持批次数 }}</td>
          <td>
            <details>
              <summary>{{ row.来源记录.length }} 条来源</summary>
              <ul>
                <li v-for="(src, idx) in row.来源记录" :key="idx">
                  {{ src.批次编号 }} · {{ src.来源数据表 }} · {{ src.标签值 }}
                  <template v-if="src.已纳入计算的边.length">
                    （边：{{ src.已纳入计算的边.map((e) => e.记录编号).join(' → ') }}）
                  </template>
                </li>
              </ul>
            </details>
          </td>
        </tr>
        <tr v-if="!filteredLabels.length">
          <td colspan="8" class="empty-state">暂无下游标签，可先批量登记标签</td>
        </tr>
      </tbody>
    </table>

    <h3>待确认互斥冲突</h3>
    <table class="data-table" data-testid="conflict-table">
      <thead>
        <tr>
          <th>冲突编号</th>
          <th>目标资产</th>
          <th>标签维度</th>
          <th>候选值</th>
          <th>冲突状态</th>
          <th>确认归属</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="conflict in conflicts" :key="String(conflict.id)" :data-testid="`conflict-row-${conflict.id}`">
          <td>{{ conflict.冲突编号 }}</td>
          <td>{{ conflict.目标资产 }}</td>
          <td>{{ conflict.标签维度 }}</td>
          <td>{{ conflict.候选值.join(' / ') }}</td>
          <td>{{ conflict.冲突状态 }}</td>
          <td class="row-actions">
            <button
              v-for="value in conflict.候选值"
              :key="value"
              class="link"
              type="button"
              :data-testid="`resolve-${conflict.id}-${value}`"
              @click="onResolve(conflict.id, value)"
            >
              采用 {{ value }}
            </button>
          </td>
        </tr>
        <tr v-if="!conflicts.length">
          <td colspan="6" class="empty-state">无待确认冲突</td>
        </tr>
      </tbody>
    </table>

    <h3>派生批次</h3>
    <table class="data-table" data-testid="batch-table">
      <thead>
        <tr>
          <th>批次编号</th>
          <th>状态</th>
          <th>直接标签</th>
          <th>提交时间</th>
          <th>撤销时间</th>
          <th>备注</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="batch in batches" :key="String(batch.id)" :data-testid="`batch-row-${batch.id}`">
          <td>{{ batch.批次编号 }}</td>
          <td>{{ batch.status }}</td>
          <td>
            <details>
              <summary>{{ batch.直接标签.length }} 条直接标签</summary>
              <ul>
                <li v-for="(item, idx) in batch.直接标签" :key="idx">
                  {{ item.asset }} · {{ item.dimension }}={{ item.value }}
                </li>
              </ul>
            </details>
          </td>
          <td>{{ formatTime(batch.提交时间) }}</td>
          <td>{{ batch.撤销时间 ? formatTime(batch.撤销时间) : '—' }}</td>
          <td>{{ batch.备注 || '—' }}</td>
          <td class="row-actions">
            <button
              v-if="batch.status === '已提交'"
              class="link"
              type="button"
              :data-testid="`revoke-${batch.批次编号}`"
              @click="onRevoke(batch.批次编号)"
            >
              撤销批次
            </button>
            <span v-else>已撤销</span>
          </td>
        </tr>
        <tr v-if="!batches.length">
          <td colspan="7" class="empty-state">暂无批次</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="batch-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>批量登记标签</h3>
        <p class="page-desc">同一批次的直接标签会一起沿已核实血缘边计算派生标签。</p>
        <div
          v-for="(item, idx) in formLabels"
          :key="idx"
          class="analysis-form"
          :data-testid="`form-row-${idx}`"
        >
          <label class="create-field">
            <span>数据表</span>
            <input v-model="item.asset" required data-testid="input-asset" />
          </label>
          <label class="create-field">
            <span>标签维度</span>
            <input v-model="item.dimension" required data-testid="input-dimension" />
          </label>
          <label class="create-field">
            <span>标签值</span>
            <input v-model="item.value" required data-testid="input-value" />
          </label>
          <label class="create-field">
            <span>来源说明</span>
            <input v-model="item.source" data-testid="input-source" />
          </label>
          <button class="btn ghost" type="button" @click="removeRow(idx)">删除</button>
        </div>
        <div class="page-actions">
          <button class="btn" type="button" @click="addRow">新增一行</button>
          <button class="btn primary" type="submit" data-testid="submit-batch">提交批次</button>
          <button class="btn ghost" type="button" @click="showCreate = false">取消</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  listDerivedLabels,
  listLabelBatches,
  listLabelConflicts,
  registerLabelBatch,
  resolveLabelConflict,
  revokeLabelBatch,
} from '@/api/backend-service'
import type {
  DirectLabelInput,
  DerivedLabel,
  LabelBatch,
  LabelConflict,
} from '@/data/types'

const batches = ref<LabelBatch[]>([])
const labels = ref<DerivedLabel[]>([])
const conflicts = ref<LabelConflict[]>([])
const assetFilter = ref('')
const showCreate = ref(false)
const error = ref('')
type FormRow = { asset: string; dimension: string; value: string; source: string }
const formLabels = ref<FormRow[]>([])

const filteredLabels = computed(() => {
  const keyword = assetFilter.value.trim().toLowerCase()
  if (!keyword) return labels.value
  return labels.value.filter((row) => row.目标资产.toLowerCase().includes(keyword))
})

const effectiveCount = computed(
  () => labels.value.filter((row) => row.标记状态 === '有效').length,
)

function formatTime(value: string): string {
  return value ? value.replace('T', ' ').slice(0, 19) : value
}

async function reload(): Promise<void> {
  error.value = ''
  try {
    const [batchResult, labelResult, conflictResult] = await Promise.all([
      listLabelBatches(),
      listDerivedLabels(),
      listLabelConflicts(),
    ])
    batches.value = batchResult.items
    labels.value = labelResult.items
    conflicts.value = conflictResult.items
  } catch (err) {
    error.value = err instanceof Error ? err.message : '数据加载失败'
  }
}

function openCreate(): void {
  formLabels.value = [{ asset: '', dimension: '', value: '', source: '' }]
  showCreate.value = true
}

function addRow(): void {
  formLabels.value.push({ asset: '', dimension: '', value: '', source: '' })
}function removeRow(index: number): void {
  formLabels.value.splice(index, 1)
}

async function submitCreate(): Promise<void> {
  const payload = formLabels.value
    .map((item) => ({
      asset: item.asset.trim(),
      dimension: item.dimension.trim(),
      value: item.value.trim(),
      source: item.source.trim() || undefined,
    }))
    .filter((item) => item.asset && item.dimension && item.value)
  if (!payload.length) {
    error.value = '至少填写一条完整的直接标签'
    return
  }
  try {
    const result = await registerLabelBatch(payload)
    if (!result.ok) {
      error.value = result.message
      return
    }
    showCreate.value = false
    await reload()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '批次登记失败'
  }
}

async function onResolve(conflictId: number, chosenValue: string): Promise<void> {
  try {
    const result = await resolveLabelConflict(conflictId, chosenValue)
    if (!result.ok) error.value = result.message
    await reload()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '冲突确认失败'
  }
}

async function onRevoke(batchNo: string): Promise<void> {
  try {
    const result = await revokeLabelBatch(batchNo)
    if (!result.ok) error.value = result.message
    await reload()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '批次撤销失败'
  }
}

onMounted(reload)
</script>
