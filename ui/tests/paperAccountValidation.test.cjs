const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
const exportsValue = {}
vm.runInNewContext(ts.transpileModule(fs.readFileSync(require.resolve('../src/paperAccountValidation.ts'), 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{exports:exportsValue})
const {validatePaperAccount,paperServerError}=exportsValue
const valid={name:'验收',base_currency:'USD',initial_cash:'1000',margin_mode:'cash'}
test('valid cash is preserved as exact decimal text',()=>{assert.equal(Object.keys(validatePaperAccount({...valid,initial_cash:'10000000000000000.01'})).length,0)})
test('empty names and invalid currency have field errors',()=>{const e=validatePaperAccount({...valid,name:' ',base_currency:'U$'});assert(e.name);assert(e.base_currency)})
test('blank, zero, negative and nondecimal cash fail closed',()=>{for(const s of ['', ' ', '0', '0.00e20', '-1', 'NaN', 'Infinity', '0x10','1_000'])assert(validatePaperAccount({...valid,initial_cash:s}).initial_cash,s)})
test('decimal exponent does not lose small positive values to JS underflow',()=>{assert.equal(Object.keys(validatePaperAccount({...valid,initial_cash:'1e-1000'})).length,0)})
test('unknown margin modes rejected',()=>{assert(validatePaperAccount({...valid,margin_mode:'real'}).margin_mode)})
test('backend known field errors map to field-level messages',()=>{assert(paperServerError('initial_cash must be a finite positive number').initial_cash);assert.equal(Object.keys(paperServerError('Failed to fetch')).length,0)})
