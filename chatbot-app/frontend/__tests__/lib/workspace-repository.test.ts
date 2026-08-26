import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@aws-sdk/s3-request-presigner', () => ({
  getSignedUrl: vi.fn().mockResolvedValue('https://example.test/preview'),
}))

import {
  getWorkspacePreviewKind,
  normalizeWorkspacePath,
  S3WorkspaceRepository,
  WorkspacePathError,
} from '@/lib/workspace/s3-repository'
import { getSignedUrl } from '@aws-sdk/s3-request-presigner'

describe('S3WorkspaceRepository', () => {
  const send = vi.fn()
  const repository = new S3WorkspaceRepository({ send } as any)

  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubEnv('ARTIFACT_BUCKET', 'workspace-bucket')
  })

  it('returns storage-neutral root namespaces without querying S3', async () => {
    const page = await repository.list('user-1', 'session-1')

    expect(page.entries.map(entry => entry.path)).toEqual([
      'uploads',
      'documents',
      'outputs',
      'code-agent',
    ])
    expect(send).not.toHaveBeenCalled()
  })

  it('lists directories before files and maps S3 keys to logical paths', async () => {
    send.mockResolvedValue({
      CommonPrefixes: [
        { Prefix: 'documents/user-1/session-1/word/' },
      ],
      Contents: [
        {
          Key: 'documents/user-1/session-1/notes.md',
          Size: 42,
          LastModified: new Date('2026-08-09T12:00:00.000Z'),
        },
        {
          Key: 'documents/user-1/session-1/.metadata',
          Size: 3,
          LastModified: new Date('2026-08-09T12:00:00.000Z'),
        },
      ],
      NextContinuationToken: 'next-page',
    })

    const page = await repository.list('user-1', 'session-1', 'documents')

    expect(page.entries).toMatchObject([
      { kind: 'directory', path: 'documents/word', name: 'word' },
      {
        kind: 'file',
        path: 'documents/notes.md',
        name: 'notes.md',
        previewKind: 'markdown',
      },
    ])
    expect(page.nextCursor).toBeTruthy()
    expect(send.mock.calls[0][0].input).toMatchObject({
      Bucket: 'workspace-bucket',
      Prefix: 'documents/user-1/session-1/',
      Delimiter: '/',
      MaxKeys: 200,
    })
  })

  it('reads bounded text previews instead of loading the complete object', async () => {
    send.mockResolvedValue({
      Body: { transformToString: vi.fn().mockResolvedValue('# Report') },
      ContentRange: 'bytes 0-8/2000000',
    })

    const preview = await repository.preview(
      'user-1',
      'session-1',
      'documents/report.md',
    )

    expect(preview).toMatchObject({
      kind: 'markdown',
      content: '# Report',
      truncated: true,
      entry: {
        path: 'documents/report.md',
        size: 2000000,
      },
    })
    expect(send.mock.calls[0][0].input).toMatchObject({
      Bucket: 'workspace-bucket',
      Key: 'documents/user-1/session-1/report.md',
      Range: 'bytes=0-1048575',
    })
  })

  it('generates a short-lived preview for binary files', async () => {
    const preview = await repository.preview(
      'user-1',
      'session-1',
      'documents/chart.png',
    )

    expect(preview).toMatchObject({
      kind: 'image',
      url: 'https://example.test/preview',
    })
    expect(send).not.toHaveBeenCalled()
  })

  it('uploads files into the Code Interpreter inputs prefix', async () => {
    send.mockResolvedValue({})

    const result = await repository.createUpload('user-1', 'session-1', {
      name: 'records.jsonl',
      mimeType: 'application/x-ndjson',
      size: 9,
    })

    expect(result).toMatchObject({
      uploadUrl: 'https://example.test/preview',
      entry: {
      path: 'uploads/records.jsonl',
      parentPath: 'uploads',
      previewKind: 'json',
      },
    })
    expect(vi.mocked(getSignedUrl).mock.calls[0][1].input).toMatchObject({
      Bucket: 'workspace-bucket',
      Key: (
        'code-interpreter-workspace/'
        + 'c75baf0822512599e9fb5404e22693cffa5c19b706f1f6c2/inputs/records.jsonl'
      ),
      ContentType: 'application/x-ndjson',
    })
    expect(send).not.toHaveBeenCalled()
  })

  it('rejects traversal and unknown namespaces', async () => {
    expect(() => normalizeWorkspacePath('documents/../secret')).toThrow(WorkspacePathError)
    await expect(
      repository.preview('user-1', 'session-1', 'unknown/file.txt'),
    ).rejects.toThrow(WorkspacePathError)
  })

  it('normalizes long boundary slash sequences without accepting empty segments', () => {
    const slashes = '/'.repeat(20_000)
    expect(normalizeWorkspacePath(`${slashes}documents/report.md${slashes}`)).toBe(
      'documents/report.md',
    )
    expect(() => normalizeWorkspacePath('documents//report.md')).toThrow(
      WorkspacePathError,
    )
  })

  it('classifies common preview formats', () => {
    expect(getWorkspacePreviewKind('file.csv')).toBe('text')
    expect(getWorkspacePreviewKind('file.json')).toBe('json')
    expect(getWorkspacePreviewKind('file.jsonl')).toBe('json')
    expect(getWorkspacePreviewKind('file.ndjson')).toBe('json')
    expect(getWorkspacePreviewKind('file.pdf')).toBe('pdf')
    expect(getWorkspacePreviewKind('file.xlsx')).toBe('office')
    expect(getWorkspacePreviewKind('file.zip')).toBe('unsupported')
  })
})
