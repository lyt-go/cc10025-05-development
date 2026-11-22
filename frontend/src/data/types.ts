/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type DirectLabelInput = {
  asset: string
  dimension: string
  value: string
  source?: string
}

export type LabelSourceRecord = {
  批次编号: string
  来源数据表: string
  标签值: string
  是否直接登记: boolean
  已纳入计算的边: { 记录编号: string; from: string; to: string }[]
  登记时间: string
}

export type DerivedLabel = EntryRow & {
  标记编号: string
  目标资产: string
  标签维度: string
  标签值: string
  候选值: string[]
  标记状态: string
  标记类型: string
  支持批次数: number
  来源记录: LabelSourceRecord[]
}

export type LabelConflict = EntryRow & {
  冲突编号: string
  目标资产: string
  标签维度: string
  候选值: string[]
  支持批次: string[]
  冲突状态: string
  确认值: string
}

export type LabelBatch = EntryRow & {
  批次编号: string
  提交时间: string
  撤销时间: string
  备注: string
  直接标签: DirectLabelInput[]
}
