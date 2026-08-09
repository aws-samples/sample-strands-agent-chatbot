export type WorkspaceEntryKind = 'file' | 'directory'

export type WorkspacePreviewKind =
  | 'text'
  | 'markdown'
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
}
