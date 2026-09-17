const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')

const source = fs.readFileSync(require.resolve('../src/components/AssetPoolWorkspace.tsx'), 'utf8')

test('asset pool submits catalogue manifest references and preserves selected members across search', () => {
  assert.match(source, /member_type: 'security_catalogue_v1'/)
  assert.match(source, /source_checksum/)
  assert.match(source, /已选证券 \{selected\.length\}\/200/)
  assert.match(source, /const search = \(\) =>/)
  assert.match(source, /setCatalogue\(null\)/)
  assert.match(source, /历史退市：可配置为历史研究名单/)
})

test('asset pool keeps legacy references explicitly compatible without presenting manual creation', () => {
  assert.match(source, /历史手工档案/)
  assert.match(source, /未自动迁移为目录记录/)
  assert.doesNotMatch(source, /当前仍使用旧档案/)
})
