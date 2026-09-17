const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
const moduleValue = { exports: {} }
const code = ts.transpileModule(fs.readFileSync(require.resolve('../src/idempotency.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
vm.runInNewContext(code, { exports: moduleValue.exports, Uint8Array, globalThis: {} })
const { idempotencyKey } = moduleValue.exports
test('secure contexts use native UUID with its receiver', () => {
  const provider = { randomUUID() { assert.equal(this, provider); return 'native-id' } }
  assert.equal(idempotencyKey('backtest', provider), 'backtest-native-id')
})
test('HTTP context without randomUUID uses cryptographic UUID v4', () => {
  const provider = { getRandomValues(bytes) { assert.equal(this, provider); return bytes.fill(255) } }
  assert.equal(idempotencyKey('backtest', provider), 'backtest-ffffffff-ffff-4fff-bfff-ffffffffffff')
})
test('HTTP keys are distinct using getRandomValues', () => {
  const crypto = require('node:crypto').webcrypto
  const provider = { getRandomValues: crypto.getRandomValues.bind(crypto) }
  const keys = new Set(Array.from({ length: 1000 }, () => idempotencyKey('factor', provider)))
  assert.equal(keys.size, 1000)
})
test('missing secure randomness fails with actionable message', () => {
  assert.throws(() => idempotencyKey('backtest', {}), /浏览器无法生成安全提交标识/)
})
