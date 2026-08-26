import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { blobStoreFor } from '@/lib/session-files/blob-store'
import { DynamoSessionFileRepository } from '@/lib/session-files/repository'
import { getWorkspacePreviewKind } from '@/lib/workspace/s3-repository'

const TEXT_PREVIEW_LIMIT = 1024 * 1024

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ fileId: string }> },
) {
  try {
    const sessionId = request.headers.get('X-Session-ID')
    if (!sessionId) {
      return NextResponse.json({ error: 'Session ID required' }, { status: 400 })
    }
    const { fileId } = await context.params
    const user = await extractUserFromRequest(request)
    const repository = new DynamoSessionFileRepository()
    const file = await repository.get(user.userId, sessionId, fileId)
    if (!file || file.state === 'DELETED') {
      return NextResponse.json({ error: 'File not found' }, { status: 404 })
    }
    if (file.state !== 'READY' || !file.blobRef) {
      return NextResponse.json(
        { error: `File is not ready: ${file.state}` },
        { status: 409 },
      )
    }
    const kind = getWorkspacePreviewKind(file.filename)
    const entry = {
      id: Buffer.from(`outputs/${file.fileId}`, 'utf8').toString('base64url'),
      path: `outputs/${file.fileId}`,
      parentPath: 'outputs',
      name: file.filename,
      kind: 'file',
      size: file.sizeBytes,
      modifiedAt: file.updatedAt,
      mimeType: file.mediaType,
      previewKind: kind,
      fileId: file.fileId,
      state: file.state,
    }
    const blobStore = blobStoreFor(file)
    if (kind === 'text' || kind === 'markdown' || kind === 'json') {
      const preview = await blobStore.readText(file, TEXT_PREVIEW_LIMIT)
      return NextResponse.json({ entry, kind, ...preview })
    }
    const url = await blobStore.createDownload(file, 'inline')
    return NextResponse.json({ entry, kind, url })
  } catch (error) {
    console.error('[SessionFilePreview] Error:', error)
    return NextResponse.json(
      { error: 'Failed to prepare session file preview' },
      { status: 500 },
    )
  }
}
