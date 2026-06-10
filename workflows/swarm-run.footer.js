const __args = typeof args === 'string' ? JSON.parse(args) : args
const __out = await runGraph(__args, (p, o) => agent(p, o), log, budget)
return __out
