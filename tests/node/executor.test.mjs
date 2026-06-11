import { test } from 'node:test'
import assert from 'node:assert/strict'
import { runGraph, validateGraph, buildPrompt, effectiveModel, RESERVE_TOKENS } from '../../workflows/run_graph.mjs'

const T = (id, deps = [], extra = {}) => ({
  id, title: id, type: 'research', prompt: `do ${id}`, deps,
  agent_type: 'swarm-reader', packet_path: `/run/packets/${id}.md`,
  schema: { type: 'object', properties: { summary: { type: 'string', maxLength: 2000 } } },
  max_retries: 1, ...extra,
})
const ARGS = (tasks, completed = {}, extra = {}) => ({
  run_dir: '/run', graph_hash: 'H', results_dir: '/run/results',
  agent_ceiling: null, tasks, completed, ...extra,
})
const okAgent = (result = (t) => ({ summary: `ok` })) => {
  const calls = []
  let inFlight = 0, maxInFlight = 0
  const fn = async (prompt, opts) => {
    calls.push({ prompt, opts })
    inFlight++; maxInFlight = Math.max(maxInFlight, inFlight)
    await new Promise(r => setTimeout(r, 5))
    inFlight--
    return { summary: `done ${opts.label}` }
  }
  return { fn, calls, max: () => maxInFlight }
}

test('independent tasks all launch concurrently', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('a'), T('b'), T('c'), T('d')]), a.fn, null, null)
  assert.equal(Object.keys(out.completed).length, 4)
  assert.equal(a.max(), 4)
})

test('dependency order respected and summaries flow', async () => {
  const order = []
  const fn = async (prompt, opts) => { order.push(opts.label); return { summary: `S-${opts.label}` } }
  const out = await runGraph(ARGS([T('a'), T('b', ['a'])]), fn, null, null)
  assert.deepEqual(order, ['research:a', 'research:b'])
  assert.equal(out.completed.b.summary, 'S-research:b')
})

test('dep summaries and result paths injected into prompt', async () => {
  let bPrompt = ''
  const fn = async (prompt, opts) => {
    if (opts.label === 'research:b') bPrompt = prompt
    return { summary: `S-${opts.label}` }
  }
  await runGraph(ARGS([T('a'), T('b', ['a'])]), fn, null, null)
  assert.ok(bPrompt.includes('S-research:a'))
  assert.ok(bPrompt.includes('/run/results/a.json'))
  assert.ok(bPrompt.startsWith('SWARM-TASK run=/run task=b hash=H'))
})

test('no double launch in diamond', async () => {
  const a = okAgent()
  await runGraph(ARGS([T('a'), T('b', ['a']), T('c', ['a']), T('d', ['b', 'c'])]), a.fn, null, null)
  const ids = a.calls.map(c => c.opts.label).sort()
  assert.deepEqual(ids, ['research:a', 'research:b', 'research:c', 'research:d'])
})

test('failure isolates: transitive skip, independents complete, partial state returned', async () => {
  const fn = async (p, o) => o.label === 'research:bad' ? null : { summary: 'ok' }
  const tasks = [T('bad', [], { max_retries: 0 }), T('child', ['bad']), T('grand', ['child']), T('solo')]
  const out = await runGraph(ARGS(tasks), fn, null, null)
  assert.deepEqual(out.failed, ['bad'])
  assert.deepEqual(out.skipped.sort(), ['child', 'grand'])
  assert.ok('solo' in out.completed)
})

test('retry once then success', async () => {
  let calls = 0
  const fn = async () => (++calls === 1 ? null : { summary: 'ok' })
  const out = await runGraph(ARGS([T('a')]), fn, null, null)
  assert.equal(calls, 2)
  assert.ok('a' in out.completed)
})

test('null under exhausted budget pauses instead of failing', async () => {
  const budget = { total: 100000, remaining: () => RESERVE_TOKENS - 1 }
  const fn = async () => null
  const out = await runGraph(ARGS([T('a')]), fn, null, budget)
  assert.equal(out.paused, 'paused_for_budget')
  assert.deepEqual(out.failed, [])
  assert.deepEqual(out.pending, ['a'])
})

test('budget reservation limits launches', async () => {
  // affords exactly: remaining > RESERVE*(inflight+1)+FLOOR -> with 100k: 1 inflight ok, 2nd not
  const budget = { total: 1, remaining: () => RESERVE_TOKENS * 2 + 10000 }
  const a = okAgent()
  const out = await runGraph(ARGS([T('a'), T('b'), T('c')]), a.fn, null, budget)
  assert.equal(out.paused, 'paused_for_budget')
  assert.ok(a.calls.length < 3)
})

test('agent ceiling pauses with pending work', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('a'), T('b'), T('c')], {}, { agent_ceiling: 2 }), a.fn, null, null)
  assert.equal(out.paused, 'agent_ceiling')
  assert.equal(a.calls.length, 2)
  assert.equal(out.pending.length, 1)
})

test('resume short-circuits completed tasks', async () => {
  const a = okAgent()
  const out = await runGraph(
    ARGS([T('a'), T('b', ['a'])], { a: { summary: 'precomputed' } }), a.fn, null, null)
  assert.deepEqual(a.calls.map(c => c.opts.label), ['research:b'])
  assert.ok(a.calls[0].prompt.includes('precomputed'))
  assert.ok('a' in out.completed && 'b' in out.completed)
})

test('fatal validation refuses to launch anything', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('a', ['ghost'])]), a.fn, null, null)
  assert.ok(out.fatal.length > 0)
  assert.equal(a.calls.length, 0)
  const out2 = await runGraph(ARGS([T('a')], { ghost: { summary: 'x' } }), a.fn, null, null)
  assert.ok(out2.fatal.length > 0)
})

test('cycle detected at runtime', async () => {
  const out = await runGraph(ARGS([T('a', ['b']), T('b', ['a'])]), okAgent().fn, null, null)
  assert.ok(out.fatal.some(e => e.includes('cycle')))
})

test('validateGraph flags fan-in over 8', () => {
  const deps = Array.from({ length: 9 }, (_, i) => `d${i}`)
  const tasks = [...deps.map(d => T(d)), T('big', deps)]
  assert.ok(validateGraph(tasks, {}).some(e => e.includes('fan-in')))
})

test('plain object with __budget_null property is treated as normal success, not budget pause', async () => {
  const fn = async () => ({ __budget_null: true, summary: 'real result' })
  const out = await runGraph(ARGS([T('a')]), fn, null, null)
  assert.ok('a' in out.completed)
  assert.equal(out.paused, null)
})

test('agent_ceiling 0 causes immediate pause with zero agentFn calls', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('a'), T('b')], {}, { agent_ceiling: 0 }), a.fn, null, null)
  assert.equal(a.calls.length, 0)
  assert.equal(out.paused, 'agent_ceiling')
  assert.equal(out.pending.length, 2)
})

test('agentFn throw is contained: throwing task fails, dependents skipped, independents complete, resolves', async () => {
  const fn = async (p, opts) => {
    if (opts.label === 'research:bad') throw new Error('agent exploded')
    return { summary: 'ok' }
  }
  const tasks = [T('bad', [], { max_retries: 0 }), T('child', ['bad']), T('solo')]
  const out = await runGraph(ARGS(tasks), fn, null, null)
  assert.ok('bad' in out.failed || out.failed.includes('bad'))
  assert.ok(out.skipped.includes('child'))
  assert.ok('solo' in out.completed)
})

test('invalid task id is fatal, no agentFn calls', async () => {
  const a = okAgent()
  const out = await runGraph(ARGS([T('my task')]), a.fn, null, null)
  assert.ok(out.fatal.length > 0)
  assert.equal(a.calls.length, 0)
})

test('agent opts pass through agentType and isolation', async () => {
  const a = okAgent()
  await runGraph(ARGS([T('a', [], { agent_type: 'swarm-implementer', isolation: 'worktree', type: 'implement' })]), a.fn, null, null)
  assert.equal(a.calls[0].opts.agentType, 'swarm-implementer')
  assert.equal(a.calls[0].opts.isolation, 'worktree')
  assert.equal(a.calls[0].opts.phase, 'implement')
})

test('schema missing summary cap is fatal', async () => {
  const a = okAgent()
  // task with schema: {} (no summary property) → fatal validation error
  const badTask = { ...T('a'), schema: {} }
  const out = await runGraph(ARGS([badTask]), a.fn, null, null)
  assert.ok(out.fatal.length > 0)
  assert.ok(out.fatal.some(e => e.includes('schema must cap summary at 2000')))
  assert.equal(a.calls.length, 0)
})

test('effectiveModel: type defaults, explicit override, synthesize inherits', () => {
  assert.equal(effectiveModel(T('a'), null), 'sonnet')                       // research
  assert.equal(effectiveModel(T('a', [], { type: 'implement' }), null), 'opus')
  assert.equal(effectiveModel(T('a', [], { type: 'integrate' }), null), 'opus')
  assert.equal(effectiveModel(T('a', [], { type: 'synthesize' }), null), null)
  assert.equal(effectiveModel(T('a', [], { model: 'haiku' }), null), 'haiku') // explicit wins
})

test('effectiveModel: session tier caps defaults but never explicit values', () => {
  assert.equal(effectiveModel(T('a', [], { type: 'implement' }), 'sonnet'), 'sonnet') // opus capped
  assert.equal(effectiveModel(T('a'), 'opus'), 'sonnet')                              // below cap: kept
  assert.equal(effectiveModel(T('a', [], { model: 'fable' }), 'sonnet'), 'fable')     // explicit exceeds
  assert.equal(effectiveModel(T('a', [], { type: 'synthesize' }), 'opus'), null)      // inherit stays inherit
})

test('runGraph passes the effective model to agent opts', async () => {
  const a = okAgent()
  await runGraph(ARGS([T('r'), T('i', [], { type: 'implement' }), T('s', [], { type: 'synthesize' })]), a.fn, null, null)
  const byLabel = Object.fromEntries(a.calls.map(c => [c.opts.label, c.opts]))
  assert.equal(byLabel['research:r'].model, 'sonnet')
  assert.equal(byLabel['implement:i'].model, 'opus')
  assert.equal('model' in byLabel['synthesize:s'], false)  // inherit = option absent
})

test('runGraph respects session_model cap from args', async () => {
  const a = okAgent()
  await runGraph(ARGS([T('i', [], { type: 'implement' })], {}, { session_model: 'sonnet' }), a.fn, null, null)
  assert.equal(a.calls[0].opts.model, 'sonnet')
})

test('validateGraph rejects unknown model values', () => {
  const errs = validateGraph([T('a', [], { model: 'gpt5' })], {})
  assert.ok(errs.some(e => e.includes('unknown model')))
  assert.deepEqual(validateGraph([T('a', [], { model: 'haiku' })], {}), [])
})
