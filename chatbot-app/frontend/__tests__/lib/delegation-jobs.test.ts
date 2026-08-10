import {
  DynamoDBClient,
  GetItemCommand,
  UpdateItemCommand,
} from '@aws-sdk/client-dynamodb'
import { afterEach, describe, expect, it, vi } from 'vitest'

describe('delegation job lifecycle', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('terminalizes an active job when cancellation is requested', async () => {
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'false')
    vi.stubEnv('SESSION_ORCHESTRATION_TABLE', 'orchestration-table')
    const send = vi
      .spyOn(DynamoDBClient.prototype, 'send')
      .mockImplementation(async command => {
        if (command instanceof GetItemCommand) {
          return {
            Item: {
              recordType: { S: 'DELEGATION_JOB' },
              jobId: { S: 'job-1' },
              sessionId: { S: 'session-1' },
              userId: { S: 'user-1' },
              profile: { S: 'analyst' },
              executionStatus: { S: 'running' },
              workStatus: { S: 'running' },
              deliveryStatus: { S: 'none' },
              request: {
                M: {
                  goal: { S: 'Analyze data' },
                  deliverable: { S: 'Report' },
                },
              },
              createdAt: { S: '2026-08-10T00:00:00Z' },
              updatedAt: { S: '2026-08-10T00:00:00Z' },
            },
          }
        }
        if (command instanceof UpdateItemCommand) {
          return {
            Attributes: {
              recordType: { S: 'DELEGATION_JOB' },
              jobId: { S: 'job-1' },
              sessionId: { S: 'session-1' },
              userId: { S: 'user-1' },
              profile: { S: 'analyst' },
              executionStatus: { S: 'cancelled' },
              workStatus: { S: 'terminal' },
              deliveryStatus: { S: 'none' },
              desiredState: { S: 'cancelled' },
              request: {
                M: {
                  goal: { S: 'Analyze data' },
                  deliverable: { S: 'Report' },
                },
              },
              createdAt: { S: '2026-08-10T00:00:00Z' },
              updatedAt: { S: '2026-08-10T00:00:01Z' },
              completedAt: { S: '2026-08-10T00:00:01Z' },
            },
          }
        }
        throw new Error(`Unexpected command: ${command.constructor.name}`)
      })

    const { cancelDelegationJob } = await import('@/lib/delegation-jobs')
    const result = await cancelDelegationJob(
      'user-1',
      'session-1',
      'job-1',
    )

    expect(result).toMatchObject({
      desiredState: 'cancelled',
      executionStatus: 'cancelled',
      workStatus: 'terminal',
    })
    const update = send.mock.calls[1][0] as UpdateItemCommand
    expect(update.input.UpdateExpression).toContain(
      'executionStatus = :cancelled',
    )
    expect(update.input.UpdateExpression).toContain('workStatus = :terminal')
    expect(update.input.UpdateExpression).toContain('completedAt = :updatedAt')
    expect(update.input.UpdateExpression).toContain('#ttl = :ttl')
  })

  it('rejects an unsafe local job ID before writing a file', async () => {
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'true')
    const { cancelDelegationJob } = await import('@/lib/delegation-jobs')

    const result = await cancelDelegationJob(
      'user-1',
      'session-1',
      '../outside',
    )

    expect(result).toBeNull()
  })
})
