import { beforeEach, describe, expect, it, vi } from 'vitest'

const s3Send = vi.fn()
const s3FilesSend = vi.fn()

vi.mock('@aws-sdk/client-s3', () => ({
  DeleteObjectCommand: class DeleteObjectCommand {
    input: unknown
    constructor(input: unknown) {
      this.input = input
    }
  },
  GetObjectCommand: class GetObjectCommand {
    input: unknown
    constructor(input: unknown) {
      this.input = input
    }
  },
  S3Client: class S3Client {
    send = s3Send
  },
}))

vi.mock('@aws-sdk/client-s3files', () => ({
  DeleteAccessPointCommand: class DeleteAccessPointCommand {
    input: unknown
    constructor(input: unknown) {
      this.input = input
    }
  },
  S3FilesClient: class S3FilesClient {
    send = s3FilesSend
  },
}))

describe('session workspace access point lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubEnv('AWS_REGION', 'us-west-2')
    vi.stubEnv('ARTIFACT_BUCKET', 'workspace-bucket')
    vi.stubEnv('S3_FILES_FILE_SYSTEM_ID', 'fs-123')
  })

  it('deletes the access point before removing its registry record', async () => {
    s3Send
      .mockResolvedValueOnce({
        Body: {
          transformToString: vi.fn().mockResolvedValue(JSON.stringify({
            accessPointId: 'ap-123',
            accessPointArn: 'arn:aws:s3files:us-west-2:123:access-point/ap-123',
          })),
        },
      })
      .mockResolvedValueOnce({})
    s3FilesSend.mockResolvedValue({})

    const {
      deleteSessionWorkspaceAccessPoint,
    } = await import('@/lib/workspace/access-point-lifecycle')

    await deleteSessionWorkspaceAccessPoint('user-1', 'session-1')

    expect(s3FilesSend).toHaveBeenCalledTimes(1)
    expect(s3FilesSend.mock.calls[0][0].input).toEqual({
      accessPointId: 'ap-123',
    })
    expect(s3Send.mock.calls[1][0].input).toEqual({
      Bucket: 'workspace-bucket',
      Key: '.workspace-access-points/user-1/session-1.json',
    })
  })

  it('is a no-op when the registry record does not exist', async () => {
    const notFound = new Error('missing')
    notFound.name = 'NoSuchKey'
    s3Send.mockRejectedValue(notFound)

    const {
      deleteSessionWorkspaceAccessPoint,
    } = await import('@/lib/workspace/access-point-lifecycle')

    await expect(
      deleteSessionWorkspaceAccessPoint('user-1', 'session-1'),
    ).resolves.toBeUndefined()
    expect(s3FilesSend).not.toHaveBeenCalled()
  })
})
