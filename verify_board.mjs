// Renders TriageBoard's real logic outside the app, with a stub SDK, to prove
// the directive parsing/formatting is correct. Run: node verify_board.mjs
import { readFileSync } from 'node:fs'

const src = readFileSync('./desktop-plugin/suki-triage/plugin.js', 'utf8')

// Pull the components out of the plugin source and eval them with stubs, so we
// test the REAL code rather than a copy of it.
const start = src.indexOf('function verdictTone')
const end = src.indexOf('export default {')
const chunk = src.slice(start, end)
if (start < 0 || end < 0) throw new Error('could not locate components in plugin.js')

const jsx = (t, p) => ({ t, p })
const jsxs = (t, p) => ({ t, p })
const Badge = 'Badge'
const cn = (...a) => a.filter(Boolean).join(' ')

const flatten = (node, depth = 0) => {
  if (node == null || typeof node === 'string' || typeof node === 'number') return node
  if (Array.isArray(node)) return node.map(n => flatten(n, depth + 1))
  const kids = node.p?.children
  let head = `${'  '.repeat(depth)}<${typeof node.t === 'string' ? node.t : node.t}>`
  const parts = []
  if (node.p?.className) parts.push(`class="${node.p.className.slice(0, 60)}"`)
  if (kids !== undefined) parts.push(flatten(kids, depth + 1))
  return head + (parts.length ? ' ' + parts.filter(Boolean).join(' ') : '')
}

const fn = new Function(
  'jsx', 'jsxs', 'Badge', 'cn',
  chunk + '\nreturn { TriageBoard, num, verdictTone };'
)
const { TriageBoard, num, verdictTone } = fn(jsx, jsxs, Badge, cn)

// Real triage_summary numbers, straight out of the verified MCP run.
const rows = [
  'ALB,11,22,47,2.13,stockout-driven',
  'ORT,11,13,38,1.88,stockout-driven',
  'BGC,0,21,53,2.14,complaints-without-stockouts',
  'KAT,8,14,27,2.14,stockout-driven',
  'ERM,14,1,19,2.36,stockout-driven'
].join('|')

console.log('--- RENDER (real numbers) ---')
console.log(flatten(TriageBoard({
  attrs: {
    headline: 'Alabang is losing customers to stockouts',
    rows,
    note: '14 of 17 items can transfer today'
  }
})))

console.log('\n--- EDGE CASES (must not throw) ---')
const cases = [
  ['undefined attrs', {}],
  ['empty rows', { attrs: { rows: '' } }],
  ['short row', { attrs: { rows: 'ALB,11' } }],
  ['non-numeric', { attrs: { rows: 'ALB,x,y,z,q,healthy' } }],
  ['no headline', { attrs: { rows: 'ALB,1,2,3,4.5,healthy' } }],
  ['attrs null', { attrs: null }]
]
let failed = 0
for (const [label, arg] of cases) {
  try {
    TriageBoard(arg)
    console.log(`  ok   ${label}`)
  } catch (e) {
    failed++
    console.log(`  FAIL ${label}: ${e.message}`)
  }
}

console.log('\nnum() guard:', num('abc', -1), num('4.5', -1), num('', -1), num('7', -1))
console.log('verdictTone:', [
  verdictTone('stockout-driven'),
  verdictTone('complaints-without-stockouts'),
  verdictTone('healthy'),
  verdictTone('garbage')
].join(' | '))

// A Tailwind arbitrary-value class carrying a var() fallback would NOT compile;
// assert the crisis tone is a single plain token.
const crisis = verdictTone('stockout-driven')
if (crisis.includes('(') || crisis.includes(',')) {
  console.log('FAIL crisis tone is not a plain token:', crisis)
  failed++
} else {
  console.log('ok   crisis tone is a plain Tailwind token:', crisis)
}

process.exit(failed ? 1 : 0)