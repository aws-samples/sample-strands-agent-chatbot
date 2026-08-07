import { describe, expect, it } from 'vitest'

import { deriveTurnControl } from '@/lib/turn-control'
import type { AgentStatus } from '@/types/events'

const base = {
  agentStatus: 'idle' as AgentStatus,
  isForegroundRunActive: false,
  isCompacting: false,
  interruptCount: 0,
  hasPendingOAuth: false,
}

describe('deriveTurnControl', () => {
  it.each<AgentStatus>([
    'thinking',
    'responding',
    'researching',
    'swarm',
  ])('makes %s activity interruptible without a foreground flag', agentStatus => {
    expect(deriveTurnControl({ ...base, agentStatus })).toEqual({
      isBusy: true,
      isBlocked: false,
      canInterrupt: true,
    })
  })

  it('treats an empty interrupt envelope as non-blocking', () => {
    expect(deriveTurnControl({
      ...base,
      agentStatus: 'responding',
      interruptCount: 0,
    }).canInterrupt).toBe(true)
  })

  it.each([
    { interruptCount: 1, hasPendingOAuth: false },
    { interruptCount: 0, hasPendingOAuth: true },
  ])('blocks actions for a real approval or OAuth wait', blocked => {
    expect(deriveTurnControl({
      ...base,
      agentStatus: 'responding',
      ...blocked,
    })).toEqual({
      isBusy: true,
      isBlocked: true,
      canInterrupt: false,
    })
  })

  it.each<AgentStatus>(['stopping', 'compacting'])(
    'keeps %s busy without offering another interrupt',
    agentStatus => {
      expect(deriveTurnControl({ ...base, agentStatus })).toEqual({
        isBusy: true,
        isBlocked: false,
        canInterrupt: false,
      })
    },
  )
})
