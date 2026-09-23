import { afterEach, expect, it, vi } from 'vitest'
import { EventEmitter } from 'events'
vi.mock('child_process',()=>({spawn:vi.fn(()=>Object.assign(new EventEmitter(),{stdout:new EventEmitter(),stderr:new EventEmitter(),kill:vi.fn(),pid:4242})),execFileSync:vi.fn()}))
import { spawn } from 'child_process'
import { AutoRemediationEngine } from './dojo.js'
const manager=()=>({on:vi.fn(),off:vi.fn()})
const regression=()=>({cycle:1,pillars:Object.fromEntries(['bow','sword','brush','arts'].map(slug=>[slug,{name:slug,ronin_mode:slug==='bow'?'ronin':'off',live_current:1,live_baseline:10}]))})
afterEach(()=>vi.clearAllMocks())
it('does not launch pillar repair from regression',()=>{const engine=new AutoRemediationEngine(manager() as never);try{engine.check(regression() as never);expect(vi.mocked(spawn).mock.calls.length).toBe(0)}finally{engine.destroy()}})
it('preserves explicit pillar run',()=>{const engine=new AutoRemediationEngine(manager() as never);try{engine.run('bow');expect(spawn).toHaveBeenCalledTimes(1)}finally{engine.destroy()}})
