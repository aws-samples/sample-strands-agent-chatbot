import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import {
  mkdir,
  mkdtemp,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

describe('mounted Code Interpreter workspace', () => {
  let mountPath: string
  let sessionRoot: string

  beforeEach(async () => {
    mountPath = await mkdtemp(join(tmpdir(), 'workspace-mount-'))
    sessionRoot = join(mountPath, 'user-1', 'session-1')
    await mkdir(sessionRoot, { recursive: true })
    vi.stubEnv('S3_FILES_MOUNT_PATH', mountPath)
    vi.stubEnv('ARTIFACT_BUCKET', 'workspace-bucket')
    vi.resetModules()
  })

  afterEach(async () => {
    vi.unstubAllEnvs()
    vi.resetModules()
    await rm(mountPath, { recursive: true, force: true })
  })

  it('lists and previews files directly from the mounted session root', async () => {
    await writeFile(join(sessionRoot, 'report.md'), '# Mounted report')
    const {
      S3WorkspaceRepository,
    } = await import('@/lib/workspace/s3-repository')
    const repository = new S3WorkspaceRepository({ send: vi.fn() } as any)

    const page = await repository.list(
      'user-1',
      'session-1',
      'code-interpreter',
    )
    expect(page.entries).toMatchObject([
      {
        name: 'report.md',
        path: 'code-interpreter/report.md',
        kind: 'file',
      },
    ])

    const preview = await repository.preview(
      'user-1',
      'session-1',
      'code-interpreter/report.md',
    )
    expect(preview).toMatchObject({
      kind: 'markdown',
      content: '# Mounted report',
    })
  })

  it('rejects symlinks that escape the authenticated session root', async () => {
    const outside = join(mountPath, 'outside.txt')
    await writeFile(outside, 'secret')
    await symlink(outside, join(sessionRoot, 'escape.txt'))
    const {
      openMountedWorkspaceFile,
      WorkspacePathError,
    } = await import('@/lib/workspace/s3-repository')

    await expect(openMountedWorkspaceFile(
      'user-1',
      'session-1',
      'code-interpreter/escape.txt',
    )).rejects.toThrow(WorkspacePathError)
  })
})
