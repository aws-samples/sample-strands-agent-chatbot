import { NextRequest, NextResponse } from 'next/server'
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3'
import { SSMClient, GetParameterCommand } from '@aws-sdk/client-ssm'
import { getSignedUrl } from '@aws-sdk/s3-request-presigner'
import { extractUserFromRequest, getSessionId } from '@/lib/auth-utils'

const region = process.env.AWS_REGION || 'us-west-2'

const NAMESPACE_MAP: [string, string][] = [
  ['code-agent', 'code-agent-workspace/{userId}/{sessionId}/'],
  ['code-interpreter', 'code-interpreter-workspace/{userId}/{sessionId}/'],
  ['documents', 'documents/{userId}/{sessionId}/'],
]

function toS3Key(userId: string, sessionId: string, path: string): string {
  const cleanPath = path.replace(/^\//, '')
  for (const [prefix, template] of NAMESPACE_MAP) {
    if (cleanPath.startsWith(prefix)) {
      const suffix = cleanPath.slice(prefix.length).replace(/^\//, '')
      const base = template
        .replace('{userId}', userId)
        .replace('{sessionId}', sessionId)
      return base + suffix
    }
  }
  return `documents/${userId}/${sessionId}/${cleanPath}`
}

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
    const { path, sessionId } = await request.json()

    if (!path || !sessionId) {
      return NextResponse.json(
        { error: 'Missing required fields: path, sessionId' },
        { status: 400 }
      )
    }

    const user = await extractUserFromRequest(request)
    const userId = user.userId

    let bucket = process.env.ARTIFACT_BUCKET
    if (!bucket) {
      const ssmClient = new SSMClient({ region })
      const projectName = process.env.PROJECT_NAME || 'strands-agent-chatbot'
      const environment = process.env.ENVIRONMENT || 'dev'
      const paramName = `/${projectName}/${environment}/agentcore/artifact-bucket`
      const paramResponse = await ssmClient.send(
        new GetParameterCommand({ Name: paramName })
      )
      bucket = paramResponse.Parameter?.Value
      if (!bucket) {
        return NextResponse.json(
          { error: 'Artifact bucket not configured' },
          { status: 500 }
        )
      }
    }

    const s3Key = toS3Key(userId, sessionId, path)
    const filename = path.split('/').pop() || 'download'

    const s3Client = new S3Client({ region })
    const command = new GetObjectCommand({
      Bucket: bucket,
      Key: s3Key,
      ResponseContentDisposition: `attachment; filename="${filename}"`,
    })

    const url = await getSignedUrl(s3Client, command, { expiresIn: 3600 })

    return NextResponse.json({ url, filename })
  } catch (error) {
    console.error('[WorkspaceDownload] Error:', error)
    return NextResponse.json(
      { error: 'Failed to generate download URL' },
      { status: 500 }
    )
  }
}
