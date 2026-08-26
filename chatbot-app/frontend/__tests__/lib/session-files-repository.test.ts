import { marshall } from '@aws-sdk/util-dynamodb'
import { describe, expect, it, vi } from 'vitest'

import { DynamoSessionFileRepository } from '@/lib/session-files/repository'

const record = {
  sessionKey: 'USER#user-1#SESSION#session-1',
  recordKey: 'FILE#file-1',
  recordType: 'SESSION_FILE',
  userId: 'user-1',
  sessionId: 'session-1',
  fileId: 'file-1',
  filename: 'report.pdf',
  mediaType: 'application/pdf',
  artifactType: 'application',
  role: 'OUTPUT',
  state: 'READY',
  revision: 1,
  producerTool: 'execute_code',
  producerId: 'tool-1',
  createdAt: '2026-08-24T00:00:00Z',
  updatedAt: '2026-08-24T00:00:01Z',
  sizeBytes: 1234,
  blobRef: {
    backend: 's3',
    locator: 'session-files/workspace/outputs/file-1/r000001/report.pdf',
  },
}

describe('DynamoSessionFileRepository', () => {
  it('gets an authenticated session file by opaque file ID', async () => {
    const send = vi.fn().mockResolvedValue({ Item: marshall(record) })
    const repository = new DynamoSessionFileRepository({
      client: { send } as any,
      tableName: 'session-files',
    })

    const result = await repository.get('user-1', 'session-1', 'file-1')

    expect(result).toMatchObject({
      fileId: 'file-1',
      filename: 'report.pdf',
      state: 'READY',
    })
    expect(send.mock.calls[0][0].input.Key).toEqual({
      sessionKey: { S: 'USER#user-1#SESSION#session-1' },
      recordKey: { S: 'FILE#file-1' },
    })
  })

  it('lists non-deleted files without exposing blob locators', async () => {
    const send = vi.fn().mockResolvedValue({
      Items: [
        marshall(record),
        marshall({
          ...record,
          recordKey: 'FILE#file-2',
          fileId: 'file-2',
          state: 'DELETED',
        }),
      ],
    })
    const repository = new DynamoSessionFileRepository({
      client: { send } as any,
      tableName: 'session-files',
    })

    const result = await repository.list('user-1', 'session-1')

    expect(result.files).toEqual([{
      fileId: 'file-1',
      filename: 'report.pdf',
      mediaType: 'application/pdf',
      artifactType: 'application',
      role: 'OUTPUT',
      state: 'READY',
      revision: 1,
      sizeBytes: 1234,
      checksumSha256: undefined,
      updatedAt: '2026-08-24T00:00:01Z',
    }])
    expect(JSON.stringify(result)).not.toContain('session-files/workspace')
  })

  it('rejects identities before querying DynamoDB', async () => {
    const send = vi.fn()
    const repository = new DynamoSessionFileRepository({
      client: { send } as any,
      tableName: 'session-files',
    })

    await expect(repository.get(
      'user-1',
      'session-1',
      '../file',
    )).rejects.toThrow('Invalid fileId')
    expect(send).not.toHaveBeenCalled()
  })
})
