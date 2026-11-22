import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  DirectLabelInput,
  EntryRow,
  LabelBatch,
  LabelConflict,
  DerivedLabel,
  OverviewResult,
  PageResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---------- 标签派生批次 ----------

export async function registerLabelBatch(
  directLabels: DirectLabelInput[],
  remark = '',
): Promise<ActionResult & { batch?: LabelBatch }> {
  const response = await request('/api/label-batches', {
    method: 'POST',
    body: JSON.stringify({ direct_labels: directLabels, remark }),
  })
  if (!response.ok) throw new Error('标签批次登记失败')
  return await response.json()
}

export async function listLabelBatches(): Promise<{ items: LabelBatch[]; total: number }> {
  const response = await request('/api/label-batches')
  if (!response.ok) throw new Error('标签批次读取失败')
  return await response.json()
}

export async function listDerivedLabels(
  asset?: string,
): Promise<{ items: DerivedLabel[]; total: number }> {
  const query = asset ? `?asset=${encodeURIComponent(asset)}` : ''
  const response = await request(`/api/label-batches/downstream-labels${query}`)
  if (!response.ok) throw new Error('下游标签读取失败')
  return await response.json()
}

export async function listLabelConflicts(
  onlyOpen = true,
): Promise<{ items: LabelConflict[]; total: number }> {
  const response = await request(`/api/label-batches/conflicts?only_open=${onlyOpen}`)
  if (!response.ok) throw new Error('标签冲突读取失败')
  return await response.json()
}

export async function resolveLabelConflict(
  conflictId: number,
  chosenValue: string,
): Promise<ActionResult> {
  const response = await request(
    `/api/label-batches/conflicts/${conflictId}/resolve`,
    { method: 'POST', body: JSON.stringify({ chosen_value: chosenValue }) },
  )
  if (!response.ok) throw new Error('冲突确认失败')
  return await response.json()
}

export async function revokeLabelBatch(batchNo: string): Promise<ActionResult> {
  const response = await request(`/api/label-batches/${batchNo}/revoke`, {
    method: 'POST',
  })
  if (!response.ok) throw new Error('批次撤销失败')
  return await response.json()
}

export async function correctLineageEdge(
  recordId: string,
  payload: { new_upstream?: string; new_downstream?: string; invalidate?: boolean },
): Promise<ActionResult> {
  const response = await request(
    `/api/label-batches/lineage/${encodeURIComponent(recordId)}/correction`,
    { method: 'POST', body: JSON.stringify(payload) },
  )
  if (!response.ok) throw new Error('血缘修正失败')
  return await response.json()
}

export type { EntryRow }
