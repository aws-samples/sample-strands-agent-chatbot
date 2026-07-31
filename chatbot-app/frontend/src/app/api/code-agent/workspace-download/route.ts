import { NextRequest, NextResponse } from 'next/server'
import { S3Client, ListObjectsV2Command, GetObjectCommand } from '@aws-sdk/client-s3'
import { getSignedUrl } from '@aws-sdk/s3-request-presigner'
import { SSMClient, GetParameterCommand } from '@aws-sdk/client-ssm'
import { extractUserFromRequest } from '@/lib/auth-utils'

const region = process.env.AWS_REGION || 'us-west-2'
const s3Client = new S3Client({ region })

let _bucketCache: string | null = null

async function getDocumentBucket(): Promise<string> {
  if (process.env.ARTIFACT_BUCKET) return process.env.ARTIFACT_BUCKET
  if (_bucketCache) return _bucketCache

  const ssmClient = new SSMClient({ region })
  const projectName = process.env.PROJECT_NAME || 'strands-agent-chatbot'
  const environment = process.env.ENVIRONMENT || 'dev'
  const response = await ssmClient.send(
    new GetParameterCommand({ Name: `/${projectName}/${environment}/agentcore/artifact-bucket` })
  )
  const bucket = response.Parameter?.Value
  if (!bucket) throw new Error('Document bucket not configured')
  _bucketCache = bucket
  return bucket
}

/**
 * GET /api/code-agent/workspace-download?sessionId=xxx
 *
 * Lists all files in the code-agent workspace for this user+session
 * and returns presigned S3 download URLs (valid 1 hour).
 *
 * The workspace prefix in S3 is:
 *   code-agent-workspace/{userId}/{sessionId}/
 *
 * Response:
 *   { files: [{ relativePath, presignedUrl, size }] }
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url)
    const sessionId = searchParams.get('sessionId')

    if (!sessionId) {
      return NextResponse.json({ error: 'sessionId is required' }, { status: 400 })
    }

    const user = await extractUserFromRequest(request)
    const userId = user.userId

    const bucket = await getDocumentBucket()
    const prefix = `code-agent-workspace/${userId}/${sessionId}/`

    // List all objects under the workspace prefix
    const listResponse = await s3Client.send(
      new ListObjectsV2Command({ Bucket: bucket, Prefix: prefix })
    )

    const objects = (listResponse.Contents || []).filter(
      (obj) => obj.Key && !obj.Key.endsWith('/')
    )

    if (objects.length === 0) {
      return NextResponse.json({ files: [] })
    }

    // Generate a presigned GET URL for each file
    const files = await Promise.all(
      objects.map(async (obj) => {
        const key = obj.Key!
        const relativePath = key.slice(prefix.length)
        const presignedUrl = await getSignedUrl(
          s3Client,
          new GetObjectCommand({ Bucket: bucket, Key: key }),
          { expiresIn: 3600 }
        )
        return { relativePath, presignedUrl, size: obj.Size ?? 0 }
      })
    )

    return NextResponse.json({ files })
  } catch (error) {
    console.error('[CodeAgent] workspace-download error:', error)
    return NextResponse.json(
      { error: 'Failed to list workspace files' },
      { status: 500 }
    )
  }
}
