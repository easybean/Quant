/** HTTP origins may expose getRandomValues but not randomUUID. Never use Math.random. */
export function idempotencyKey(prefix: string, provider: Crypto = globalThis.crypto): string {
  if (typeof provider?.randomUUID === 'function') return `${prefix}-${provider.randomUUID()}`
  if (typeof provider?.getRandomValues !== 'function') throw new Error('浏览器无法生成安全提交标识，请更换浏览器后重试。')
  const bytes = provider.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
  return `${prefix}-${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
