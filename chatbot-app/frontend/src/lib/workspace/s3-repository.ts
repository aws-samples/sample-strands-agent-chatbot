import {
  GetObjectCommand,
  ListObjectsV2Command,
  S3Client,
} from '@aws-sdk/client-s3'
import { GetParameterCommand, SSMClient } from '@aws-sdk/client-ssm'
import { getSignedUrl } from '@aws-sdk/s3-request-presigner'
import type {
  WorkspaceEntry,
  WorkspacePage,
  WorkspacePreview,
  WorkspacePreviewKind,
  WorkspaceRepository,
} from './types'

const region = process.env.AWS_REGION || 'us-west-2'
const TEXT_PREVIEW_LIMIT = 1024 * 1024

interface Namespace {
  logicalPath: string
  label: string
  prefix: (userId: string, sessionId: string) => string
}

const NAMESPACES: Namespace[] = [
  {
    logicalPath: 'documents',
    label: 'Documents',
    prefix: (userId, sessionId) => `documents/${userId}/${sessionId}/`,
  },
  {
    logicalPath: 'code-interpreter',
    label: 'Code Interpreter',
    prefix: (userId, sessionId) => `code-interpreter-workspace/${userId}/${sessionId}/`,
  },
  {
    logicalPath: 'code-agent',
    label: 'Code Agent',
    prefix: (userId, sessionId) => `code-agent-workspace/${userId}/${sessionId}/`,
  },
]

const MIME_TYPES: Record<string, string> = {
  csv: 'text/csv',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  gif: 'image/gif',
  html: 'text/html',
  jpeg: 'image/jpeg',
  jpg: 'image/jpeg',
  js: 'text/javascript',
  json: 'application/json',
  md: 'text/markdown',
  pdf: 'application/pdf',
  png: 'image/png',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  py: 'text/x-python',
  svg: 'image/svg+xml',
  ts: 'text/typescript',
  tsx: 'text/tsx',
  txt: 'text/plain',
  webp: 'image/webp',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  xml: 'application/xml',
  yaml: 'application/yaml',
  yml: 'application/yaml',
}

let bucketPromise: Promise<string> | undefined

export class WorkspacePathError extends Error {}

export function normalizeWorkspacePath(path = ''): string {
  if (path.includes('\0') || path.includes('\\')) {
    throw new WorkspacePathError('Invalid workspace path')
  }

  let start = 0
  let end = path.length
  while (start < end && path.charCodeAt(start) === 47) start += 1
  while (end > start && path.charCodeAt(end - 1) === 47) end -= 1

  const clean = path.slice(start, end)
  if (!clean) return ''

  const segments = clean.split('/')
  if (segments.some(segment => !segment || segment === '.' || segment === '..')) {
    throw new WorkspacePathError('Invalid workspace path')
  }
  return segments.join('/')
}

export function getWorkspaceMimeType(path: string): string {
  const extension = path.split('.').pop()?.toLowerCase() || ''
  return MIME_TYPES[extension] || 'application/octet-stream'
}

export function getWorkspacePreviewKind(path: string): WorkspacePreviewKind {
  const mimeType = getWorkspaceMimeType(path)
  if (mimeType === 'text/markdown') return 'markdown'
  if (
    mimeType.startsWith('text/')
    || mimeType === 'application/json'
    || mimeType === 'application/xml'
    || mimeType === 'application/yaml'
  ) return 'text'
  if (mimeType.startsWith('image/')) return 'image'
  if (mimeType === 'application/pdf') return 'pdf'
  if (
    mimeType.includes('officedocument')
    || mimeType === 'application/msword'
    || mimeType === 'application/vnd.ms-excel'
    || mimeType === 'application/vnd.ms-powerpoint'
  ) return 'office'
  return 'unsupported'
}

function encodeCursor(path: string, token: string): string {
  return Buffer.from(JSON.stringify({ path, token }), 'utf8').toString('base64url')
}

function decodeCursor(path: string, cursor?: string): string | undefined {
  if (!cursor) return undefined
  try {
    const decoded = JSON.parse(Buffer.from(cursor, 'base64url').toString('utf8'))
    if (decoded.path !== path || typeof decoded.token !== 'string') {
      throw new Error('Cursor does not match path')
    }
    return decoded.token
  } catch {
    throw new WorkspacePathError('Invalid workspace cursor')
  }
}

async function getWorkspaceBucket(): Promise<string> {
  const configuredBucket = process.env.ARTIFACT_BUCKET
  if (configuredBucket) return configuredBucket

  if (!bucketPromise) {
    bucketPromise = (async () => {
      const projectName = process.env.PROJECT_NAME || 'strands-agent-chatbot'
      const environment = process.env.ENVIRONMENT || 'dev'
      const parameterName = `/${projectName}/${environment}/agentcore/artifact-bucket`
      const response = await new SSMClient({ region }).send(
        new GetParameterCommand({ Name: parameterName }),
      )
      const bucket = response.Parameter?.Value
      if (!bucket) throw new Error('Artifact bucket not configured')
      return bucket
    })().catch(error => {
      bucketPromise = undefined
      throw error
    })
  }
  return bucketPromise
}

function namespaceForPath(path: string): {
  namespace: Namespace
  relativePath: string
} {
  const [root, ...rest] = path.split('/')
  const namespace = NAMESPACES.find(candidate => candidate.logicalPath === root)
  if (!namespace) throw new WorkspacePathError('Unknown workspace namespace')
  return { namespace, relativePath: rest.join('/') }
}

function entryId(path: string): string {
  return Buffer.from(path, 'utf8').toString('base64url')
}

function directoryEntry(path: string, name: string): WorkspaceEntry {
  const parentPath = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : ''
  return {
    id: entryId(path),
    path,
    parentPath,
    name,
    kind: 'directory',
  }
}

export async function resolveWorkspaceS3Location(
  userId: string,
  sessionId: string,
  logicalPath: string,
): Promise<{ bucket: string; key: string }> {
  const path = normalizeWorkspacePath(logicalPath)
  const { namespace, relativePath } = namespaceForPath(path)
  if (!relativePath) throw new WorkspacePathError('A file path is required')
  return {
    bucket: await getWorkspaceBucket(),
    key: `${namespace.prefix(userId, sessionId)}${relativePath}`,
  }
}

async function bodyToString(body: unknown): Promise<string> {
  if (!body) return ''
  if (
    typeof body === 'object'
    && body !== null
    && 'transformToString' in body
    && typeof body.transformToString === 'function'
  ) {
    return body.transformToString()
  }
  if (body instanceof Uint8Array) return new TextDecoder().decode(body)
  throw new Error('Unsupported S3 response body')
}

export class S3WorkspaceRepository implements WorkspaceRepository {
  private readonly s3: S3Client

  constructor(s3 = new S3Client({ region })) {
    this.s3 = s3
  }

  async list(
    userId: string,
    sessionId: string,
    rawPath = '',
    cursor?: string,
  ): Promise<WorkspacePage> {
    const path = normalizeWorkspacePath(rawPath)

    if (!path) {
      return {
        entries: NAMESPACES.map(namespace => (
          directoryEntry(namespace.logicalPath, namespace.label)
        )),
      }
    }

    const { namespace, relativePath } = namespaceForPath(path)
    const bucket = await getWorkspaceBucket()
    const basePrefix = namespace.prefix(userId, sessionId)
    const prefix = relativePath ? `${basePrefix}${relativePath}/` : basePrefix
    const continuationToken = decodeCursor(path, cursor)
    const response = await this.s3.send(new ListObjectsV2Command({
      Bucket: bucket,
      Prefix: prefix,
      Delimiter: '/',
      ContinuationToken: continuationToken,
      MaxKeys: 200,
    }))

    const entries: WorkspaceEntry[] = []
    for (const commonPrefix of response.CommonPrefixes || []) {
      if (!commonPrefix.Prefix) continue
      const relative = commonPrefix.Prefix.slice(basePrefix.length).replace(/\/$/, '')
      const name = relative.split('/').pop()
      if (!name || name.startsWith('.')) continue
      const logicalPath = `${namespace.logicalPath}/${relative}`
      entries.push(directoryEntry(logicalPath, name))
    }

    for (const object of response.Contents || []) {
      if (!object.Key || object.Key === prefix) continue
      const relative = object.Key.slice(basePrefix.length)
      const name = relative.split('/').pop()
      if (!name || name.startsWith('.')) continue
      const logicalPath = `${namespace.logicalPath}/${relative}`
      entries.push({
        id: entryId(logicalPath),
        path: logicalPath,
        parentPath: path,
        name,
        kind: 'file',
        size: object.Size,
        modifiedAt: object.LastModified?.toISOString(),
        mimeType: getWorkspaceMimeType(logicalPath),
        previewKind: getWorkspacePreviewKind(logicalPath),
      })
    }

    entries.sort((left, right) => {
      if (left.kind !== right.kind) return left.kind === 'directory' ? -1 : 1
      return left.name.localeCompare(right.name, undefined, { sensitivity: 'base' })
    })

    return {
      entries,
      nextCursor: response.NextContinuationToken
        ? encodeCursor(path, response.NextContinuationToken)
        : undefined,
    }
  }

  async preview(
    userId: string,
    sessionId: string,
    rawPath: string,
  ): Promise<WorkspacePreview> {
    const path = normalizeWorkspacePath(rawPath)
    const { bucket, key } = await resolveWorkspaceS3Location(userId, sessionId, path)
    const name = path.split('/').pop() || path
    const parentPath = path.slice(0, Math.max(0, path.lastIndexOf('/')))
    const kind = getWorkspacePreviewKind(path)
    const mimeType = getWorkspaceMimeType(path)
    const entry: WorkspaceEntry = {
      id: entryId(path),
      path,
      parentPath,
      name,
      kind: 'file',
      mimeType,
      previewKind: kind,
    }

    if (kind === 'text' || kind === 'markdown') {
      const response = await this.s3.send(new GetObjectCommand({
        Bucket: bucket,
        Key: key,
        Range: `bytes=0-${TEXT_PREVIEW_LIMIT - 1}`,
      }))
      const content = await bodyToString(response.Body)
      const totalSize = response.ContentRange
        ? Number(response.ContentRange.split('/').pop())
        : response.ContentLength
      return {
        entry: { ...entry, size: totalSize },
        kind,
        content,
        truncated: typeof totalSize === 'number' && totalSize > TEXT_PREVIEW_LIMIT,
      }
    }

    const command = new GetObjectCommand({
      Bucket: bucket,
      Key: key,
      ResponseContentDisposition: `inline; filename="${name.replace(/"/g, '')}"`,
      ResponseContentType: mimeType,
    })
    const url = await getSignedUrl(this.s3, command, { expiresIn: 900 })

    return {
      entry,
      kind,
      url,
    }
  }
}
