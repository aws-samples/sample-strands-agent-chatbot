import { NextRequest, NextResponse } from 'next/server'
import { extractUserFromRequest } from '@/lib/auth-utils'
import { blobStoreFor } from '@/lib/session-files/blob-store'
import { DynamoSessionFileRepository } from '@/lib/session-files/repository'

export async function POST(
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
    const url = await blobStoreFor(file).createDownload(file, 'attachment')
    return NextResponse.json({
      fileId: file.fileId,
      filename: file.filename,
      url,
    })
  } catch (error) {
    console.error('[SessionFileDownload] Error:', error)
    return NextResponse.json(
      { error: 'Failed to prepare session file download' },
      { status: 500 },
    )
  }
}
