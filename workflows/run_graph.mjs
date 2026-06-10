// Pure DAG scheduler for swarm. No workflow-runtime dependencies:
// agentFn/logFn/budget are injected, so this file runs under plain Node for tests
// and is embedded into ~/.claude/workflows/swarm-run.js by the installer.
export const RESERVE_TOKENS = 30000
export const FLOOR_TOKENS = 20000

export function validateGraph(tasks, completed) {
  const errors = []
  const ids = new Set()
  for (const t of tasks) {
    if (ids.has(t.id)) errors.push(`duplicate id ${t.id}`)
    ids.add(t.id)
  }
  for (const t of tasks) {
    for (const d of t.deps) if (!ids.has(d)) errors.push(`${t.id}: dangling dep ${d}`)
    if (t.deps.length > 8) errors.push(`${t.id}: fan-in ${t.deps.length} > 8`)
  }
  for (const c of Object.keys(completed)) if (!ids.has(c)) errors.push(`completed id ${c} not in graph`)
  const indeg = new Map(tasks.map(t => [t.id, t.deps.length]))
  const children = new Map(tasks.map(t => [t.id, []]))
  for (const t of tasks) for (const d of t.deps) if (children.has(d)) children.get(d).push(t.id)
  const queue = tasks.filter(t => t.deps.length === 0).map(t => t.id)
  let seen = 0
  while (queue.length) {
    const n = queue.pop()
    seen++
    for (const c of children.get(n)) {
      indeg.set(c, indeg.get(c) - 1)
      if (indeg.get(c) === 0) queue.push(c)
    }
  }
  if (seen !== tasks.length && errors.length === 0) errors.push('dependency cycle detected')
  return errors
}

export function buildPrompt(argsObj, t, completed) {
  const lines = [
    `SWARM-TASK run=${argsObj.run_dir} task=${t.id} hash=${argsObj.graph_hash}`,
    'You are one worker in a swarm run. Work ONLY on this task. Your final output MUST match',
    'the required schema; keep summary under 2000 chars of dense, factual content.',
    `First, Read your context packet at: ${t.packet_path}`,
    '',
    t.prompt,
  ]
  if (t.deps.length) {
    lines.push('', '## Results from tasks you depend on')
    for (const d of t.deps) {
      const r = completed[d] || {}
      lines.push(`### ${d}`, String(r.summary || '').slice(0, 2200),
        `(full result on disk: ${argsObj.results_dir}/${d}.json)`)
    }
  }
  return lines.join('\n')
}

export async function runGraph(argsObj, agentFn, logFn, budget) {
  const tasks = argsObj.tasks
  const fatal = validateGraph(tasks, argsObj.completed || {})
  if (fatal.length) return { fatal, completed: {}, failed: [], skipped: [], pending: tasks.map(t => t.id) }
  const completed = { ...(argsObj.completed || {}) }
  const failedSet = new Set()
  const skippedSet = new Set()
  const launched = new Set(Object.keys(completed))
  const running = new Map()
  let agentsUsed = 0
  let paused = null

  const canAfford = () => !budget || !budget.total ||
    budget.remaining() > RESERVE_TOKENS * (running.size + 1) + FLOOR_TOKENS

  const attempt = async (t) => {
    let tries = 0
    while (tries <= (t.max_retries ?? 1)) {
      const res = await agentFn(buildPrompt(argsObj, t, completed), {
        label: `${t.type}:${t.id}`,
        phase: t.type,
        schema: t.schema,
        ...(t.agent_type ? { agentType: t.agent_type } : {}),
        ...(t.isolation ? { isolation: t.isolation } : {}),
      })
      if (res !== null && res !== undefined) return res
      if (budget && budget.total && budget.remaining() < RESERVE_TOKENS) return { __budget_null: true }
      tries++
    }
    return null
  }

  while (true) {
    let changed = true
    while (changed) {
      changed = false
      for (const t of tasks) {
        if (launched.has(t.id) || skippedSet.has(t.id)) continue
        if (t.deps.some(d => failedSet.has(d) || skippedSet.has(d))) {
          skippedSet.add(t.id)
          changed = true
        }
      }
    }
    if (!paused) {
      for (const t of tasks) {
        if (launched.has(t.id) || skippedSet.has(t.id)) continue
        if (!t.deps.every(d => d in completed)) continue
        if (argsObj.agent_ceiling && agentsUsed >= argsObj.agent_ceiling) { paused = 'agent_ceiling'; break }
        if (!canAfford()) { paused = 'paused_for_budget'; break }
        launched.add(t.id)
        agentsUsed++
        if (logFn) logFn(`swarm: launch ${t.type}:${t.id} (${running.size + 1} in flight)`)
        running.set(t.id, attempt(t).then(result => ({ id: t.id, result })))
      }
    }
    if (running.size === 0) break
    const { id, result } = await Promise.race(running.values())
    running.delete(id)
    if (result && result.__budget_null) {
      launched.delete(id) // stays pending; resumable
      paused = 'paused_for_budget'
    } else if (result !== null && result !== undefined) {
      completed[id] = result
      if (logFn) logFn(`swarm: ${id} done (${Object.keys(completed).length}/${tasks.length})`)
    } else {
      failedSet.add(id)
      if (logFn) logFn(`swarm: ${id} FAILED after retries`)
    }
  }
  return {
    fatal: [],
    completed,
    failed: [...failedSet],
    skipped: [...skippedSet],
    paused,
    agentsUsed,
    pending: tasks.filter(t => !(t.id in completed) && !failedSet.has(t.id) && !skippedSet.has(t.id)).map(t => t.id),
  }
}
