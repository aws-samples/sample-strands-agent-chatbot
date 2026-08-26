export type WorkspaceEntryKind = 'file' | 'directory'

export type WorkspacePreviewKind =
  | 'text'
  | 'markdown'
  | 'json'
  | 'image'
  | 'pdf'
  | 'office'
  | 'unsupported'

export interface WorkspaceEntry {
  id: string
  path: string
  parentPath: string
  name: string
  kind: WorkspaceEntryKind
  size?: number
  modifiedAt?: string
  mimeType?: string
  previewKind?: WorkspacePreviewKind
  fileId?: string
  state?: string
}

export interface WorkspacePage {
  entries: WorkspaceEntry[]
  nextCursor?: string
}

export interface WorkspacePreview {
  entry: WorkspaceEntry
  kind: WorkspacePreviewKind
  content?: string
  url?: string
  truncated?: boolean
}

export interface WorkspaceRepository {
  list(
    userId: string,
    sessionId: string,
    path?: string,
    cursor?: string,
  ): Promise<WorkspacePage>
  preview(
    userId: string,
    sessionId: string,
    path: string,
  ): Promise<WorkspacePreview>
  createUpload(
    userId: string,
    sessionId: string,
    file: {
      name: string
      mimeType: string
      size: number
    },
  ): Promise<{ entry: WorkspaceEntry; uploadUrl: string }>
}
