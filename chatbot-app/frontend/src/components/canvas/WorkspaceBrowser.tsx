"use client"

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Download,
  File,
  FileCode2,
  FileImage,
  FileText,
  Folder,
  FolderOpen,
  Loader2,
  RefreshCw,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Markdown } from '@/components/ui/Markdown'
import { apiFetch } from '@/lib/api-client'
import type {
  WorkspaceEntry,
  WorkspacePage,
  WorkspacePreview,
} from '@/lib/workspace/types'
import { OfficeViewer } from './OfficeViewer'

interface WorkspaceBrowserProps {
  sessionId?: string
}

interface DirectoryState {
  entries: WorkspaceEntry[]
  nextCursor?: string
  loading?: boolean
  error?: string
}

function formatSize(size?: number): string {
  if (size === undefined) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function FileIcon({ entry }: { entry: WorkspaceEntry }) {
  if (entry.kind === 'directory') return <Folder className="h-4 w-4" />
  if (entry.previewKind === 'image') return <FileImage className="h-4 w-4" />
  if (entry.previewKind === 'text' || entry.previewKind === 'markdown') {
    return <FileCode2 className="h-4 w-4" />
  }
  if (entry.previewKind === 'pdf' || entry.previewKind === 'office') {
    return <FileText className="h-4 w-4" />
  }
  return <File className="h-4 w-4" />
}

export function WorkspaceBrowser({ sessionId }: WorkspaceBrowserProps) {
  const [directories, setDirectories] = useState<Record<string, DirectoryState>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<WorkspacePreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const workspaceGeneration = useRef(0)
  const previewRequestId = useRef(0)

  const loadDirectory = useCallback(async (
    path: string,
    cursor?: string,
    append = false,
  ) => {
    if (!sessionId) return
    const generation = workspaceGeneration.current
    setDirectories(current => ({
      ...current,
      [path]: {
        ...(current[path] || { entries: [] }),
        loading: true,
        error: undefined,
      },
    }))

    try {
      const query = new URLSearchParams()
      if (path) query.set('path', path)
      if (cursor) query.set('cursor', cursor)
      const queryString = query.toString()
      const response = await apiFetch(
        `workspace/entries${queryString ? `?${queryString}` : ''}`,
        { headers: { 'X-Session-ID': sessionId } },
      )
      if (!response.ok) throw new Error('Unable to load workspace')
      const page = await response.json() as WorkspacePage
      if (generation !== workspaceGeneration.current) return
      setDirectories(current => ({
        ...current,
        [path]: {
          entries: append
            ? [...(current[path]?.entries || []), ...page.entries]
            : page.entries,
          nextCursor: page.nextCursor,
          loading: false,
        },
      }))
    } catch (error) {
      if (generation !== workspaceGeneration.current) return
      setDirectories(current => ({
        ...current,
        [path]: {
          ...(current[path] || { entries: [] }),
          loading: false,
          error: error instanceof Error ? error.message : 'Unable to load workspace',
        },
      }))
    }
  }, [sessionId])

  useEffect(() => {
    workspaceGeneration.current += 1
    previewRequestId.current += 1
    setDirectories({})
    setExpanded(new Set())
    setSelected(null)
    setPreviewLoading(false)
    setPreviewError(null)
    setDownloading(false)
    if (sessionId) void loadDirectory('')
  }, [loadDirectory, sessionId])

  const toggleDirectory = useCallback((entry: WorkspaceEntry) => {
    const shouldLoad = !expanded.has(entry.path) && !directories[entry.path]
    setExpanded(current => {
      const next = new Set(current)
      if (next.has(entry.path)) {
        next.delete(entry.path)
      } else {
        next.add(entry.path)
      }
      return next
    })
    if (shouldLoad) void loadDirectory(entry.path)
  }, [directories, expanded, loadDirectory])

  const openFile = useCallback(async (entry: WorkspaceEntry) => {
    if (!sessionId) return
    const requestId = ++previewRequestId.current
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const response = await apiFetch(
        `workspace/preview?path=${encodeURIComponent(entry.path)}`,
        { headers: { 'X-Session-ID': sessionId } },
      )
      if (!response.ok) throw new Error('Preview is not available')
      const preview = await response.json() as WorkspacePreview
      if (requestId !== previewRequestId.current) return
      setSelected(preview)
    } catch (error) {
      if (requestId !== previewRequestId.current) return
      setPreviewError(error instanceof Error ? error.message : 'Preview is not available')
    } finally {
      if (requestId === previewRequestId.current) setPreviewLoading(false)
    }
  }, [sessionId])

  const downloadSelected = useCallback(async () => {
    if (!selected || !sessionId) return
    setDownloading(true)
    try {
      const response = await apiFetch('workspace/download', {
        method: 'POST',
        headers: { 'X-Session-ID': sessionId },
        body: JSON.stringify({ path: selected.entry.path, sessionId }),
      })
      if (!response.ok) throw new Error('Download is not available')
      const { url, filename } = await response.json()
      const link = document.createElement('a')
      link.href = url
      link.download = filename || selected.entry.name
      link.rel = 'noopener noreferrer'
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : 'Download is not available')
    } finally {
      setDownloading(false)
    }
  }, [selected, sessionId])

  const refresh = useCallback(() => {
    workspaceGeneration.current += 1
    previewRequestId.current += 1
    setDirectories({})
    setExpanded(new Set())
    setSelected(null)
    setPreviewLoading(false)
    setPreviewError(null)
    setDownloading(false)
    if (sessionId) void loadDirectory('')
  }, [loadDirectory, sessionId])

  const renderDirectory = useCallback((path: string, depth = 0): React.ReactNode => {
    const directory = directories[path]
    if (!directory) return null

    return (
      <>
        {directory.entries.map(entry => {
          const isExpanded = expanded.has(entry.path)
          return (
            <React.Fragment key={entry.id}>
              <button
                type="button"
                className="flex h-8 w-full items-center gap-2 rounded-sm pr-2 text-left text-sm text-sidebar-foreground hover:bg-sidebar-accent/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                style={{ paddingLeft: `${8 + depth * 16}px` }}
                onClick={() => (
                  entry.kind === 'directory' ? toggleDirectory(entry) : void openFile(entry)
                )}
                aria-expanded={entry.kind === 'directory' ? isExpanded : undefined}
              >
                {entry.kind === 'directory' ? (
                  <>
                    {isExpanded
                      ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                    {isExpanded
                      ? <FolderOpen className="h-4 w-4 shrink-0 text-primary" />
                      : <Folder className="h-4 w-4 shrink-0 text-primary" />}
                  </>
                ) : (
                  <>
                    <span className="w-3.5 shrink-0" />
                    <span className="shrink-0 text-muted-foreground">
                      <FileIcon entry={entry} />
                    </span>
                  </>
                )}
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                {entry.kind === 'file' && entry.size !== undefined && (
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatSize(entry.size)}
                  </span>
                )}
              </button>
              {entry.kind === 'directory' && isExpanded && (
                <>
                  {directories[entry.path]?.loading && (
                    <div
                      className="flex h-8 items-center gap-2 text-xs text-muted-foreground"
                      style={{ paddingLeft: `${40 + depth * 16}px` }}
                    >
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      Loading
                    </div>
                  )}
                  {directories[entry.path]?.error && (
                    <div
                      className="py-2 pr-2 text-xs text-destructive"
                      style={{ paddingLeft: `${40 + depth * 16}px` }}
                    >
                      {directories[entry.path]?.error}
                    </div>
                  )}
                  {!directories[entry.path]?.loading
                    && !directories[entry.path]?.error
                    && directories[entry.path]?.entries.length === 0 && (
                    <div
                      className="h-8 py-1 text-xs text-muted-foreground"
                      style={{ paddingLeft: `${40 + depth * 16}px` }}
                    >
                      Empty folder
                    </div>
                  )}
                  {renderDirectory(entry.path, depth + 1)}
                </>
              )}
            </React.Fragment>
          )
        })}
        {directory.nextCursor && (
          <button
            type="button"
            className="h-8 text-xs text-primary hover:underline"
            style={{ marginLeft: `${32 + depth * 16}px` }}
            disabled={directory.loading}
            onClick={() => void loadDirectory(path, directory.nextCursor, true)}
          >
            Load more
          </button>
        )}
      </>
    )
  }, [directories, expanded, loadDirectory, openFile, toggleDirectory])

  const previewContent = useMemo(() => {
    if (!selected) return null
    if (selected.kind === 'markdown') {
      return (
        <div className="p-4">
          <Markdown sessionId={sessionId}>{selected.content || ''}</Markdown>
        </div>
      )
    }
    if (selected.kind === 'text') {
      return (
        <pre className="min-h-full overflow-auto p-4 font-mono text-xs leading-5 text-sidebar-foreground whitespace-pre-wrap">
          {selected.content || ''}
        </pre>
      )
    }
    if (selected.kind === 'image' && selected.url) {
      return (
        <div className="flex min-h-full items-start justify-center p-4">
          <img
            src={selected.url}
            alt={selected.entry.name}
            className="max-h-full max-w-full object-contain"
          />
        </div>
      )
    }
    if (selected.kind === 'pdf' && selected.url) {
      return (
        <iframe
          src={selected.url}
          title={`Preview: ${selected.entry.name}`}
          className="h-full w-full border-0"
        />
      )
    }
    if (selected.kind === 'office' && selected.url) {
      return <OfficeViewer previewUrl={selected.url} filename={selected.entry.name} />
    }
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-muted-foreground">
        <File className="h-8 w-8" />
        <p className="text-sm">Preview is not available for this file.</p>
      </div>
    )
  }, [selected, sessionId])

  if (!sessionId) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
        Start a conversation to create a session workspace.
      </div>
    )
  }

  if (selected) {
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-sidebar-border px-2">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => {
              setSelected(null)
              setPreviewError(null)
            }}
            aria-label="Back to workspace files"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{selected.entry.name}</div>
            <div className="truncate text-xs text-muted-foreground">
              {selected.entry.path}
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => void downloadSelected()}
            disabled={downloading}
            aria-label={`Download ${selected.entry.name}`}
          >
            {downloading
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Download className="h-4 w-4" />}
          </Button>
        </div>
        {selected.truncated && (
          <div className="border-b border-sidebar-border bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
            Showing the first 1 MB. Download the file to view the rest.
          </div>
        )}
        {previewError && (
          <div className="flex items-center gap-2 border-b border-destructive/20 bg-destructive/5 px-4 py-2 text-xs text-destructive">
            <AlertCircle className="h-3.5 w-3.5" />
            {previewError}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-auto">{previewContent}</div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-sidebar-border/60 px-3">
        <div>
          <div className="text-sm font-medium">Session files</div>
          <div className="text-xs text-muted-foreground">Persistent workspace</div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={refresh}
          aria-label="Refresh workspace files"
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>
      {previewLoading && (
        <div className="flex items-center gap-2 border-b border-sidebar-border/60 px-4 py-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Opening file
        </div>
      )}
      {previewError && (
        <div className="flex items-center gap-2 border-b border-destructive/20 bg-destructive/5 px-4 py-2 text-xs text-destructive">
          <AlertCircle className="h-3.5 w-3.5" />
          {previewError}
        </div>
      )}
      <ScrollArea className="min-h-0 flex-1">
        <div className="p-2">
          {directories['']?.loading && (
            <div className="flex h-20 items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading workspace
            </div>
          )}
          {directories['']?.error && (
            <div className="p-4 text-sm text-destructive">{directories[''].error}</div>
          )}
          {renderDirectory('')}
        </div>
      </ScrollArea>
    </div>
  )
}
