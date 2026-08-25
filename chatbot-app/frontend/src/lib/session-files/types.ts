export type SessionFileState =
  | 'RESERVED'
  | 'UPLOADING'
  | 'READY'
  | 'FAILED'
  | 'DELETED'

export type SessionFileRole = 'INPUT' | 'OUTPUT'

export interface BlobRef {
  backend: string
  locator: string
  version?: string
}

export interface SessionFileRecord {
  userId: string
  sessionId: string
  fileId: string
  filename: string
  mediaType: string
  artifactType: string
  role: SessionFileRole
  state: SessionFileState
  revision: number
  producerTool: string
  producerId: string
  createdAt: string
  updatedAt: string
  blobRef?: BlobRef
  sizeBytes?: number
  checksumSha256?: string
  failureCode?: string
  failureMessage?: string
}

export interface SessionFileRef {
  fileId: string
  filename: string
  mediaType: string
  artifactType: string
  role: SessionFileRole
  state: SessionFileState
  revision: number
  sizeBytes?: number
  checksumSha256?: string
  updatedAt?: string
}

export interface SessionFilePage {
  files: SessionFileRef[]
  nextCursor?: string
}
