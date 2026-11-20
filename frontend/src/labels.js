/** 界面文案映射：库内码 → 中文（页面不直接露英文码） */
export const 表类型 = { water: '水表', electric: '电表', 水表: '水表', 电表: '电表' }
export const 表状态 = { active: '在用', inactive: '停用', 在用: '在用', 停用: '停用' }
export const 账期状态 = { open: '开放', sealed: '已封印', expired: '已作废', 开放: '开放', 已封印: '已封印' }
export const 批状态 = {
  open: '开放',
  sealed: '已封印',
  expired: '已作废',
  stale: '尺子已变·待重对齐',
}
export const 命运 = {
  pending: '未决',
  accept_short: '认短',
  accept_over: '认溢',
  quarantine: '隔离',
}
export const 容差口径 = { rel: '相对误差率', abs: '绝对差值' }
export const 合拢方式 = { exact: '完全一致', auto_matched: '容差内自动合拢' }
export const 差额原因 = {
  over_tolerance: '超出容差',
  missing_estimate: '缺估抄',
  missing_actual: '缺实抄',
  multi_doc: '单据重复',
}

export function 标(map, key) {
  if (key == null || key === '') return '—'
  return map[key] || String(key)
}

export function 百分比(x, digits = 2) {
  if (x == null) return '—'
  return (Number(x) * 100).toFixed(digits) + '%'
}
