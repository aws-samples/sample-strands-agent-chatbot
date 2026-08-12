import {
  BatchGetItemCommand,
  DynamoDBClient,
  UpdateItemCommand,
} from '@aws-sdk/client-dynamodb'
import { marshall } from '@aws-sdk/util-dynamodb'
import { afterEach, describe, expect, it, vi } from 'vitest'

describe('session attention state', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('detects only cursors newer than the last seen cursor', async () => {
    const { hasUnseenSessionAttention } = await import('@/lib/session-events')

    expect(hasUnseenSessionAttention({})).toBe(false)
    expect(hasUnseenSessionAttention({
      latestAttentionCursor: 'OUTBOX_V2#2026-08-11T10:00:00Z#event-1',
    })).toBe(true)
    expect(hasUnseenSessionAttention({
      latestAttentionCursor: 'OUTBOX_V2#2026-08-11T10:00:00Z#event-1',
      lastSeenAttentionCursor: 'OUTBOX_V2#2026-08-11T10:00:00Z#event-1',
    })).toBe(false)
  })

  it('batch loads attention state for the requested sessions', async () => {
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'false')
    vi.stubEnv('SESSION_ORCHESTRATION_TABLE', 'orchestration-table')
    const send = vi
      .spyOn(DynamoDBClient.prototype, 'send')
      .mockImplementation(async command => {
        expect(command).toBeInstanceOf(BatchGetItemCommand)
        return {
          Responses: {
            'orchestration-table': [
              marshall({
                sessionKey: 'USER#user-1#SESSION#session-1',
                latestAttentionCursor:
                  'OUTBOX_V2#2026-08-11T10:00:00Z#event-1',
                latestAttentionAt: '2026-08-11T10:00:00Z',
              }),
            ],
          },
        }
      })

    const { getSessionAttentionStates } = await import('@/lib/session-events')
    const states = await getSessionAttentionStates(
      'user-1',
      ['session-1', 'session-2'],
    )

    expect(send).toHaveBeenCalledTimes(1)
    expect(states.get('session-1')).toEqual({
      latestAttentionCursor: 'OUTBOX_V2#2026-08-11T10:00:00Z#event-1',
      latestAttentionAt: '2026-08-11T10:00:00Z',
      lastSeenAttentionCursor: undefined,
      lastSeenAttentionAt: undefined,
    })
    expect(states.get('session-2')).toEqual({})
  })

  it('marks only the represented attention cursor as seen', async () => {
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'false')
    vi.stubEnv('SESSION_ORCHESTRATION_TABLE', 'orchestration-table')
    const send = vi
      .spyOn(DynamoDBClient.prototype, 'send')
      .mockImplementation(async () => ({} as any))

    const { markSessionAttentionSeen } = await import('@/lib/session-events')
    await markSessionAttentionSeen(
      'user-1',
      'session-1',
      'OUTBOX_V2#2026-08-11T10:00:00Z#event-1',
    )

    const command = send.mock.calls[0][0]
    expect(command).toBeInstanceOf(UpdateItemCommand)
    const input = (command as UpdateItemCommand).input
    expect(input.ConditionExpression).toContain(
      'latestAttentionCursor >= :cursor',
    )
    expect(input.ConditionExpression).toContain(
      'lastSeenAttentionCursor < :cursor',
    )
  })
})
