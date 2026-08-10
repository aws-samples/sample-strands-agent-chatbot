import { describe, expect, it } from 'vitest'
import {
  getGlobalTurnActivity,
  getTurnActivityOwner,
} from '@/lib/turn-activity'

describe('turn activity presentation', () => {
  it('renders only unowned waits in the global activity row', () => {
    expect(getGlobalTurnActivity('waiting_for_model')).toEqual({
      label: 'Thinking...',
      ariaLabel: 'Waiting for the model',
    })
    expect(getGlobalTurnActivity('processing_tool_result')?.label)
      .toBe('Reviewing tool results...')
  })

  it('assigns tool phases to the tool execution surface', () => {
    expect(getTurnActivityOwner('preparing_tool')).toBe('tool')
    expect(getTurnActivityOwner('running_tool')).toBe('tool')
    expect(getGlobalTurnActivity('preparing_tool')).toBeNull()
    expect(getGlobalTurnActivity('running_tool')).toBeNull()
  })

  it('does not duplicate UI owned by content, approval, or connection surfaces', () => {
    expect(getTurnActivityOwner('streaming_response')).toBe('response')
    expect(getTurnActivityOwner('waiting_for_user')).toBe('approval')
    expect(getTurnActivityOwner('reconnecting')).toBe('connection')
    expect(getGlobalTurnActivity('streaming_response')).toBeNull()
    expect(getGlobalTurnActivity('waiting_for_user')).toBeNull()
    expect(getGlobalTurnActivity('reconnecting')).toBeNull()
  })
})
