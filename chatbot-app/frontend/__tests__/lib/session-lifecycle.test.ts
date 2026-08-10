import {
  DynamoDBClient,
  QueryCommand,
  UpdateItemCommand,
} from '@aws-sdk/client-dynamodb'
import { afterEach, describe, expect, it, vi } from 'vitest'

describe('session orchestration lifecycle', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('uses DynamoDB for anonymous sessions in AWS mode', async () => {
    vi.stubEnv('NEXT_PUBLIC_AGENTCORE_LOCAL', 'false')
    vi.stubEnv('SESSION_ORCHESTRATION_TABLE', 'orchestration-table')
    const send = vi
      .spyOn(DynamoDBClient.prototype, 'send')
      .mockImplementation(async command => {
        if (command instanceof UpdateItemCommand) return {}
        if (command instanceof QueryCommand) return { Items: [] }
        throw new Error(`Unexpected command: ${command.constructor.name}`)
      })

    const { tombstoneSessionOrchestration } = await import(
      '@/lib/session-lifecycle'
    )
    await tombstoneSessionOrchestration('anonymous', 'session-1')

    expect(send).toHaveBeenCalledTimes(5)
    expect(send.mock.calls[0][0]).toBeInstanceOf(UpdateItemCommand)
    expect(
      send.mock.calls.slice(1).every(([command]) =>
        command instanceof QueryCommand
      ),
    ).toBe(true)
  })
})
