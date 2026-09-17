export type PaperAccountForm = { name: string; base_currency: string; initial_cash: string; margin_mode: 'cash' | 'cross' | 'isolated' }
export const paperFieldIds: Record<string, string> = { name: 'paper-name', base_currency: 'paper-currency', initial_cash: 'paper-cash', margin_mode: 'paper-margin' }
export const paperMessages: Record<string, string> = {
  name: '请输入 1–120 个字符的账户名称。',
  base_currency: '请输入 3–12 位大写币种代码，以字母开头，仅含字母和数字。',
  initial_cash: '请输入大于 0 的有效十进制初始现金；金额不能留空。',
  margin_mode: '请选择现金、全仓或逐仓保证金模式。',
}
export function validatePaperAccount(form: PaperAccountForm): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!form.name.trim() || form.name.trim().length > 120) errors.name = paperMessages.name
  if (!/^[A-Z][A-Z0-9]{2,11}$/.test(form.base_currency.trim())) errors.base_currency = paperMessages.base_currency
  const cash = form.initial_cash.trim()
  const positiveDecimal = /^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(cash) && /[1-9]/.test(cash.split(/[eE]/)[0])
  if (!positiveDecimal) errors.initial_cash = paperMessages.initial_cash
  if (!['cash', 'cross', 'isolated'].includes(form.margin_mode)) errors.margin_mode = paperMessages.margin_mode
  return errors
}
export function paperServerError(message: string): Record<string, string> {
  const key = Object.keys(paperFieldIds).find(field => new RegExp(`\\b${field}\\b`).test(message))
  return key ? { [key]: paperMessages[key] } : {}
}
