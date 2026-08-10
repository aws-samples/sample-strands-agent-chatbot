import {
  DeleteObjectCommand,
  GetObjectCommand,
  S3Client,
} from '@aws-sdk/client-s3'
import { S3FilesClient, DeleteAccessPointCommand } from '@aws-sdk/client-s3files'

const region = process.env.AWS_REGION || 'us-west-2'
const SAFE_ID = /^[A-Za-z0-9_-]+$/

interface AccessPointRegistry {
  accessPointId: string
  accessPointArn: string
}

export async function deleteSessionWorkspaceAccessPoint(
  userId: string,
  sessionId: string,
): Promise<void> {
  if (!process.env.S3_FILES_FILE_SYSTEM_ID) return
  if (!SAFE_ID.test(userId) || !SAFE_ID.test(sessionId)) {
    throw new Error('Invalid workspace identity')
  }

  const bucket = process.env.ARTIFACT_BUCKET
  if (!bucket) throw new Error('Artifact bucket is not configured')

  const key = `.workspace-access-points/${userId}/${sessionId}.json`
  const s3 = new S3Client({ region })
  let registry: AccessPointRegistry
  try {
    const response = await s3.send(new GetObjectCommand({
      Bucket: bucket,
      Key: key,
    }))
    registry = JSON.parse(await response.Body!.transformToString())
  } catch (error) {
    const name = error instanceof Error ? error.name : ''
    if (name === 'NoSuchKey' || name === 'NotFound') return
    throw error
  }

  if (!registry.accessPointId) return
  await new S3FilesClient({ region }).send(new DeleteAccessPointCommand({
    accessPointId: registry.accessPointId,
  }))
  await s3.send(new DeleteObjectCommand({ Bucket: bucket, Key: key }))
}
