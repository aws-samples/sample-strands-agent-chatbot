import { marshall, unmarshall } from '@aws-sdk/util-dynamodb'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const send = vi.hoisted(() => vi.fn())

vi.mock('@aws-sdk/client-dynamodb', () => ({
  DynamoDBClient: class {
    send = send
  },
  QueryCommand: class {
    input: Record<string, unknown>

    constructor(input: Record<string, unknown>) {
      this.input = input
    }
  },
  UpdateItemCommand: class {
    input: Record<string, any>

    constructor(input: Record<string, any>) {
      this.input = input
    }
  },
}))

describe('message metadata records', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.stubEnv('SESSION_ORCHESTRATION_TABLE', 'orchestration-table')
    send.mockReset()
  })

  it('stores allowed fields under the logical message identity', async () => {
    send.mockResolvedValue({})
    const { updateMessageMetadataRecord } = await import('@/lib/message-metadata')

    await updateMessageMetadataRecord(
      'user-1',
      'session-1',
      'mailbox:event-1:1',
      {
        feedback: { type: 'positive' },
        unsupported: 'ignored',
      },
    )

    const command = send.mock.calls[0][0]
    expect(command.input.TableName).toBe('orchestration-table')
    expect(unmarshall(command.input.Key)).toEqual({
      sessionKey: 'USER#user-1#SESSION#session-1',
      recordKey: 'MESSAGE_META#mailbox:event-1:1',
    })
    expect(command.input.ExpressionAttributeNames).toMatchObject({
      '#field0': 'feedback',
    })
    expect(Object.values(command.input.ExpressionAttributeNames)).not.toContain(
      'unsupported',
    )
  })

  it('reads paginated records keyed by logical message id', async () => {
    send
      .mockResolvedValueOnce({
        Items: [
          marshall({
            logicalMessageId: 'message-1',
            feedback: { type: 'positive' },
          }),
        ],
        LastEvaluatedKey: marshall({
          sessionKey: 'USER#user-1#SESSION#session-1',
          recordKey: 'MESSAGE_META#message-1',
        }),
      })
      .mockResolvedValueOnce({
        Items: [
          marshall({
            logicalMessageId: 'message-2',
            documents: [{ id: 'doc-1' }],
          }),
        ],
      })
    const { listMessageMetadataRecords } = await import('@/lib/message-metadata')

    const records = await listMessageMetadataRecords('user-1', 'session-1')

    expect(Object.keys(records)).toEqual(['message-1', 'message-2'])
    expect(records['message-2'].documents).toEqual([{ id: 'doc-1' }])
    expect(send).toHaveBeenCalledTimes(2)
  })
})
