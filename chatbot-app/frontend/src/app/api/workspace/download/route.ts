import { NextRequest, NextResponse } from 'next/server'
import {
  S3Client,
  GetObjectCommand,
  HeadObjectCommand,
  NotFound,
} from '@aws-sdk/client-s3'
import { getSignedUrl } from '@aws-sdk/s3-request-presigner'
import { extractUserFromRequest } from '@/lib/auth-utils'
import {
  resolveWorkspaceS3Location,
  WorkspacePathError,
} from '@/lib/workspace/s3-repository'

const region = process.env.AWS_REGION || 'us-west-2'

/**
 * POST /api/workspace/download
 *
 * Generates a presigned URL for downloading a workspace file.
 *
 * Request body:
 * - path: string (logical workspace path, e.g. 'code-agent/output.csv')
 * - sessionId: string (chat session ID)
 *
 * Returns:
 * - url: string (presigned download URL)
 * - filename: string (extracted filename)
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const path = body.path
    const sessionId = request.headers.get('X-Session-ID') || body.sessionId

    if (!path || !sessionId) {
      return NextResponse.json(
        { error: 'Missing required fields: path, sessionId' },
        { status: 400 }
      )
    }

    const user = await extractUserFromRequest(request)
    const userId = user.userId
    const filename = path.split('/').pop() || 'download'

    const { bucket, key: s3Key } = await resolveWorkspaceS3Location(
      userId,
      sessionId,
      path,
    )

    const s3Client = new S3Client({ region })
    try {
      await s3Client.send(new HeadObjectCommand({
        Bucket: bucket,
        Key: s3Key,
      }))
    } catch (error) {
      if (
        error instanceof NotFound
        || (error as { name?: string }).name === 'NotFound'
        || (error as { name?: string }).name === 'NoSuchKey'
      ) {
        return NextResponse.json(
          { error: 'Workspace file is still synchronizing. Please retry shortly.' },
          { status: 409 },
        )
      }
      throw error
    }
    const command = new GetObjectCommand({
      Bucket: bucket,
      Key: s3Key,
      ResponseContentDisposition: `attachment; filename="${filename}"`,
    })

    const url = await getSignedUrl(s3Client, command, { expiresIn: 3600 })

    return NextResponse.json({ url, filename })
  } catch (error) {
    if (error instanceof WorkspacePathError) {
      return NextResponse.json({ error: error.message }, { status: 400 })
    }
    console.error('[WorkspaceDownload] Error:', error)
    return NextResponse.json(
      { error: 'Failed to generate download URL' },
      { status: 500 }
    )
  }
}
