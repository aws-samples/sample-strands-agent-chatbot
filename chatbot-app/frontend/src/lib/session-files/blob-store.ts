import {
  GetObjectCommand,
  S3Client,
} from '@aws-sdk/client-s3'
import { getSignedUrl } from '@aws-sdk/s3-request-presigner'
import type { BlobRef, SessionFileRecord } from './types'

const region = process.env.AWS_REGION || 'us-west-2'

export interface SessionFileBlobStore {
  createDownload(
    file: SessionFileRecord,
    disposition: 'attachment' | 'inline',
  ): Promise<string>
  readText(
    file: SessionFileRecord,
    maxBytes: number,
  ): Promise<{ content: string; truncated: boolean }>
}

export class S3SessionFileBlobStore implements SessionFileBlobStore {
  private readonly client: S3Client
  private readonly bucket: string

  constructor(options?: { client?: S3Client; bucket?: string }) {
    this.client = options?.client || new S3Client({ region })
    this.bucket = options?.bucket || process.env.ARTIFACT_BUCKET || ''
    if (!this.bucket) throw new Error('ARTIFACT_BUCKET is required')
  }

  async createDownload(
    file: SessionFileRecord,
    disposition: 'attachment' | 'inline',
  ): Promise<string> {
    const blobRef = this.requireBlob(file.blobRef)
    const safeName = file.filename.replace(/["\r\n]/g, '')
    const command = new GetObjectCommand({
      Bucket: this.bucket,
      Key: blobRef.locator,
      VersionId: blobRef.version,
      ResponseContentType: file.mediaType,
      ResponseContentDisposition: `${disposition}; filename="${safeName}"`,
    })
    return getSignedUrl(this.client, command, { expiresIn: 900 })
  }

  async readText(
    file: SessionFileRecord,
    maxBytes: number,
  ): Promise<{ content: string; truncated: boolean }> {
    const blobRef = this.requireBlob(file.blobRef)
    const response = await this.client.send(new GetObjectCommand({
      Bucket: this.bucket,
      Key: blobRef.locator,
      VersionId: blobRef.version,
      Range: `bytes=0-${maxBytes}`,
    }))
    const body = response.Body
    if (!body || typeof body.transformToString !== 'function') {
      throw new Error('Unsupported session file body')
    }
    const content = await body.transformToString()
    const totalSize = response.ContentRange
      ? Number(response.ContentRange.split('/').pop())
      : response.ContentLength
    return {
      content: Buffer.from(content).subarray(0, maxBytes).toString('utf8'),
      truncated: typeof totalSize === 'number' && totalSize > maxBytes,
    }
  }

  private requireBlob(blobRef?: BlobRef): BlobRef {
    if (!blobRef || blobRef.backend !== 's3') {
      throw new Error('Unsupported session file storage backend')
    }
    return blobRef
  }
}

export function blobStoreFor(file: SessionFileRecord): SessionFileBlobStore {
  if (file.blobRef?.backend === 's3') return new S3SessionFileBlobStore()
  throw new Error('Unsupported session file storage backend')
}
