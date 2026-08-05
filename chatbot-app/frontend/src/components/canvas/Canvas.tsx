"use client"

import React, { useState, useEffect, useRef, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { X, FileText, Image as ImageIcon, Code, FileDown, Sparkles, Printer, Clock, Tag, GripHorizontal, Monitor, Database, Layers } from 'lucide-react'
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription } from '@/components/ui/empty'
import { Artifact } from '@/types/artifact'
import { ResearchArtifact } from './ResearchArtifact'
import { BrowserLiveView } from './BrowserLiveView'
import { OfficeViewer, isOfficeFileUrl, getFilenameFromS3Url } from './OfficeViewer'
import { ExcalidrawRenderer } from './ExcalidrawRenderer'
import { marked } from 'marked'
import { citationPrintCSS } from '@/components/ui/CitationLink'
import { Markdown } from '@/components/ui/Markdown'

interface BrowserState {
  sessionId: string
  browserId: string
  isActive: boolean
  onConnectionError: () => void
  onValidationFailed: () => void
}

interface CanvasProps {
  isOpen: boolean
  onClose: () => void
  artifacts: Artifact[]
  selectedArtifactId: string | null
  onSelectArtifact: (id: string) => void
  onUpdateArtifact?: (artifactId: string, updates: Partial<Artifact>) => void
  researchState?: any // Live research state
  browserState?: BrowserState // Live browser state
  justUpdated?: boolean // Flash effect trigger when artifact is updated
  sessionId?: string
}

const getArtifactIcon = (type: string) => {
  switch (type) {
    case 'markdown':
    case 'research':
    case 'document':
    case 'word_document':
    case 'excel_spreadsheet':
    case 'powerpoint_presentation':
      return <FileText className="h-4 w-4" />
    case 'image':
      return <ImageIcon className="h-4 w-4" />
    case 'excalidraw':
      return <ImageIcon className="h-4 w-4" />
    case 'code':
      return <Code className="h-4 w-4" />
    case 'browser':
      return <Monitor className="h-4 w-4" />
    case 'extracted_data':
      return <Database className="h-4 w-4" />
    default:
      return <Sparkles className="h-4 w-4" />
  }
}

const formatTimestamp = (timestamp: string) => {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  if (isNaN(date.getTime())) return ''
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`
  return date.toLocaleDateString()
}

const getArtifactTypeLabel = (type: string) => {
  switch (type) {
    case 'research': return 'Research'
    case 'markdown': return 'Markdown'
    case 'image': return 'Image'
    case 'code': return 'Code'
    case 'document': return 'Document'
    case 'word_document': return 'Word Document'
    case 'excel_spreadsheet': return 'Excel Spreadsheet'
    case 'powerpoint_presentation': return 'PowerPoint'
    case 'browser': return 'Browser'
    case 'extracted_data': return 'Extracted Data'
    case 'excalidraw': return 'Diagram'
    default: return 'Artifact'
  }
}

// Helper to strip <research> tags and extract clean content
const stripResearchTags = (content: string): string => {
  if (!content) return ''
  // Extract content from <research> tags if present
  const match = content.match(/<research>([\s\S]*?)<\/research>/)
  if (match && match[1]) {
    return match[1].trim()
  }
  // Remove any remaining <research> or </research> tags
  return content.replace(/<\/?research>/g, '')
}

export function Canvas({
  isOpen,
  onClose,
  artifacts,
  selectedArtifactId,
  onSelectArtifact,
  onUpdateArtifact,
  researchState,
  browserState,
  justUpdated = false,
  sessionId,
}: CanvasProps) {
  const selectedArtifactRaw = artifacts.find(a => a.id === selectedArtifactId)

  // A pending research approval outranks whatever artifact happens to be open.
  // The approval UI lives in ResearchArtifact, which only renders when nothing is
  // selected — so once a previous research run had produced an artifact (and
  // auto-selected it), the next run's approval was invisible and the turn could
  // not be answered or stopped.
  const awaitingResearchApproval = !!researchState?.showPlanConfirm
  const selectedArtifact = awaitingResearchApproval ? undefined : selectedArtifactRaw

  const previewContentRef = useRef<HTMLDivElement>(null)

  const displayArtifacts = artifacts

  // Handle close - if plan confirmation is showing, treat as cancel
  const handleClose = useCallback(() => {
    if (researchState?.showPlanConfirm && researchState?.onCancel) {
      // Research plan confirmation is showing, treat close as cancel
      researchState.onCancel()
    } else {
      // Normal close
      onClose()
    }
  }, [researchState, onClose])

  // Download artifact as Markdown
  const handleDownloadMarkdown = () => {
    if (!selectedArtifact || typeof selectedArtifact.content !== 'string') return

    const filename = `${selectedArtifact.title.replace(/[^a-z0-9]/gi, '_').toLowerCase()}.md`
    const blob = new Blob([selectedArtifact.content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  // Export to PDF via print - uses rendered HTML with resolved image URLs
  const handlePrintPDF = () => {
    if (!selectedArtifact || typeof selectedArtifact.content !== 'string') return

    // Use rendered HTML from DOM (images already have presigned URLs)
    const htmlContent = previewContentRef.current?.innerHTML || marked.parse(selectedArtifact.content)

    // Base document styles
    const baseCSS = `
      * { box-sizing: border-box; }
      body { font-family: system-ui, -apple-system, sans-serif; line-height: 1.7; margin: 0; padding: 0; color: #333; }
      .container { max-width: 100%; margin: 0 auto; padding: 30mm 25mm; }
      h1 { font-size: 2.2em; margin-top: 0; margin-bottom: 0.8em; font-weight: 600; line-height: 1.2; }
      h2 { font-size: 1.6em; margin-top: 1.8em; margin-bottom: 0.6em; font-weight: 600; line-height: 1.3; }
      h3 { font-size: 1.3em; margin-top: 1.5em; margin-bottom: 0.5em; font-weight: 600; }
      h4, h5, h6 { margin-top: 1.2em; margin-bottom: 0.5em; font-weight: 600; }
      p { margin: 0.8em 0; text-align: justify; }
      ul, ol { margin: 0.8em 0; padding-left: 2.5em; }
      li { margin: 0.4em 0; }
      code { background: #f5f5f5; padding: 0.2em 0.5em; border-radius: 3px; font-family: monospace; font-size: 0.9em; }
      pre { background: #f8f8f8; padding: 1.2em; border-radius: 5px; overflow-x: auto; margin: 1.2em 0; border: 1px solid #e0e0e0; }
      pre code { background: none; padding: 0; }
      blockquote { border-left: 4px solid #ddd; padding-left: 1.2em; margin: 1.2em 0; color: #666; font-style: italic; }
      table { border-collapse: collapse; width: 100%; margin: 1em 0; }
      th, td { border: 1px solid #ddd; padding: 0.6em; text-align: left; }
      th { background: #f5f5f5; font-weight: 600; }
      img { max-width: 70%; height: auto; margin: 1.5em auto; display: block; }
      @media print {
        body { margin: 0; padding: 0; }
        .container { padding: 20mm 25mm; }
        h1, h2, h3, h4, h5, h6 { page-break-after: avoid; }
        p, li { orphans: 3; widows: 3; }
      }
    `

    const printContent = `
      <!DOCTYPE html>
      <html>
      <head>
        <meta charset="utf-8">
        <title>${selectedArtifact.title}</title>
        <style>
          ${baseCSS}
          ${citationPrintCSS}
          .print-controls { position: fixed; top: 20px; right: 20px; display: flex; gap: 8px; z-index: 1000; }
          .print-controls button { padding: 10px 20px; font-size: 14px; font-weight: 500; border: none; border-radius: 6px; cursor: pointer; }
          .print-btn { background-color: #2563eb; color: white; }
          .print-btn:hover { background-color: #1d4ed8; }
          .close-btn { background-color: #e5e7eb; color: #374151; }
          .close-btn:hover { background-color: #d1d5db; }
          @media print { .print-controls { display: none; } }
        </style>
      </head>
      <body>
        <div class="print-controls">
          <button class="print-btn" onclick="window.print()">Save as PDF</button>
          <button class="close-btn" onclick="window.close()">Close</button>
        </div>
        <div class="container">${htmlContent}</div>
      </body>
      </html>
    `

    const printWindow = window.open('', '_blank')
    if (printWindow) {
      printWindow.document.write(printContent)
      printWindow.document.close()
    }
  }

  // Resizable bottom panel
  const [bottomPanelHeight, setBottomPanelHeight] = useState(130) // Initial height: 130px
  const [isResizing, setIsResizing] = useState(false)
  const resizeStartY = useRef(0)
  const resizeStartHeight = useRef(0)

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setIsResizing(true)
    resizeStartY.current = e.clientY
    resizeStartHeight.current = bottomPanelHeight
  }, [bottomPanelHeight])

  const handleResizeMove = useCallback((e: MouseEvent) => {
    if (!isResizing) return

    const deltaY = resizeStartY.current - e.clientY // Inverted because moving up increases height
    const newHeight = Math.max(100, Math.min(600, resizeStartHeight.current + deltaY)) // Min 100px, Max 600px
    setBottomPanelHeight(newHeight)
  }, [isResizing])

  const handleResizeEnd = useCallback(() => {
    setIsResizing(false)
  }, [])

  // Add/remove global mouse event listeners
  useEffect(() => {
    if (isResizing) {
      document.addEventListener('mousemove', handleResizeMove)
      document.addEventListener('mouseup', handleResizeEnd)
      document.body.style.cursor = 'ns-resize'
      document.body.style.userSelect = 'none'

      return () => {
        document.removeEventListener('mousemove', handleResizeMove)
        document.removeEventListener('mouseup', handleResizeEnd)
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }
  }, [isResizing, handleResizeMove, handleResizeEnd])

  // Keep Canvas mounted (but hidden) when browser session exists to preserve DCV connection
  const shouldStayMounted = browserState !== undefined

  if (!isOpen && !shouldStayMounted) return null

  return (
    <div
      className={`fixed top-0 right-0 h-screen w-full md:w-[950px] md:max-w-[80vw] bg-sidebar-background border-l border-sidebar-border text-sidebar-foreground flex flex-col z-40 shadow-2xl transition-transform duration-300 ${
        isOpen ? 'translate-x-0' : 'translate-x-full'
      }`}
    >
      {/* Header */}
      <div className="flex-shrink-0 px-4 py-3 border-b border-sidebar-border/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-sidebar-foreground" />
            <span className="text-heading font-semibold text-sidebar-foreground">Canvas</span>
            {displayArtifacts.length > 0 && (
              <span className="text-label text-sidebar-foreground/60">({displayArtifacts.length})</span>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleClose}
            className="h-8 w-8 p-0"
            title="Close panel"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="flex-1 min-h-0 flex flex-col">
        {/* Preview Area */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {/* Priority: selectedArtifact first, then live states (research/browser) */}
          {selectedArtifact ? (
            // User selected an artifact - show it
            selectedArtifact.type === 'browser' && browserState ? (
              // Browser artifact selected - show live browser view
              <BrowserLiveView
                sessionId={browserState.sessionId}
                browserId={browserState.browserId}
                isActive={browserState.isActive}
                onConnectionError={browserState.onConnectionError}
                onValidationFailed={browserState.onValidationFailed}
              />
            ) : (
            <>
              {/* Preview Header */}
              <div className="px-4 py-3 border-b border-sidebar-border/50">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-heading text-sidebar-foreground truncate mb-2">
                      {selectedArtifact.title}
                    </h3>
                    <div className="flex items-center gap-4 text-label text-sidebar-foreground/60">
                      {/* Type */}
                      <div className="flex items-center gap-1.5">
                        <Tag className="h-3.5 w-3.5" />
                        <span>{getArtifactTypeLabel(selectedArtifact.type)}</span>
                      </div>
                      {/* Timestamp */}
                      <div className="flex items-center gap-1.5">
                        <Clock className="h-3.5 w-3.5" />
                        <span>{formatTimestamp(selectedArtifact.timestamp)}</span>
                      </div>
                    </div>
                  </div>
                  {/* Action Buttons - hidden for Office files (they have their own buttons in OfficeViewer) */}
                  {(selectedArtifact.type === 'document' || selectedArtifact.type === 'research') &&
                    typeof selectedArtifact.content === 'string' &&
                    !isOfficeFileUrl(selectedArtifact.content) && (
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={handleDownloadMarkdown}
                        className="h-8 px-3"
                        title="Download as Markdown file"
                      >
                        <FileDown className="h-4 w-4 mr-1.5" />
                        Download MD
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={handlePrintPDF}
                        className="h-8 px-3"
                        title="Print to PDF"
                      >
                        <Printer className="h-4 w-4 mr-1.5" />
                        PDF
                      </Button>
                    </div>
                  )}
                </div>
              </div>

              {/* Preview Content */}
              {selectedArtifact.type === 'excalidraw' ? (
                // Excalidraw diagram viewer - full height, interactive
                <div className={`flex-1 min-h-0 flex flex-col transition-all duration-500 ${justUpdated ? 'bg-green-500/10 ring-2 ring-green-500/30 rounded-lg' : ''}`}>
                  <div className="flex-shrink-0 px-3 py-1.5 text-xs text-muted-foreground bg-muted/40 border-b border-border/40 flex items-center gap-1.5">
                    <span>Manual edits are not saved.</span>
                    <span className="text-muted-foreground/60">Ask the agent to modify, or export (⋮) to save locally.</span>
                  </div>
                  <div className="flex-1 min-h-0">
                    <ExcalidrawRenderer
                      data={selectedArtifact.content}
                    />
                  </div>
                </div>
              ) : (selectedArtifact.type === 'word_document' || selectedArtifact.type === 'excel_spreadsheet' || selectedArtifact.type === 'powerpoint_presentation' || (selectedArtifact.type === 'document' && typeof selectedArtifact.content === 'string' && isOfficeFileUrl(selectedArtifact.content))) ? (
                // Office document viewer (Word/Excel/PowerPoint) - full height, no ScrollArea
                <div className={`flex-1 min-h-0 transition-all duration-500 ${justUpdated ? 'bg-green-500/10 ring-2 ring-green-500/30 rounded-lg' : ''}`}>
                  <OfficeViewer
                    s3Url={(selectedArtifact.type === 'word_document' || selectedArtifact.type === 'excel_spreadsheet' || selectedArtifact.type === 'powerpoint_presentation') ? (selectedArtifact.content || selectedArtifact.metadata?.s3_url || '') : selectedArtifact.content}
                    filename={(selectedArtifact.type === 'word_document' || selectedArtifact.type === 'excel_spreadsheet' || selectedArtifact.type === 'powerpoint_presentation') ? selectedArtifact.title : getFilenameFromS3Url(selectedArtifact.content)}
                  />
                </div>
              ) : (
                <ScrollArea className="flex-1">
                  <div className={`p-4 transition-all duration-500 ${justUpdated ? 'bg-green-500/10 ring-2 ring-green-500/30 rounded-lg' : ''}`}>
                    {(selectedArtifact.type === 'markdown' || selectedArtifact.type === 'research' || selectedArtifact.type === 'document') && typeof selectedArtifact.content === 'string' ? (
                      <div ref={previewContentRef}>
                        <Markdown sessionId={sessionId}>
                          {stripResearchTags(selectedArtifact.content)}
                        </Markdown>
                      </div>
                    ) : selectedArtifact.type === 'image' ? (
                      <div className="flex items-center justify-center">
                        <img
                          src={selectedArtifact.content}
                          alt={selectedArtifact.title}
                          className="max-w-full h-auto rounded-lg shadow-lg"
                        />
                      </div>
                    ) : selectedArtifact.type === 'extracted_data' ? (
                      <div className="bg-muted rounded-lg p-4 overflow-auto border border-border">
                        <pre className="text-sm text-foreground whitespace-pre-wrap font-mono">
                          {typeof selectedArtifact.content === 'string'
                            ? selectedArtifact.content
                            : JSON.stringify(selectedArtifact.content, null, 2)}
                        </pre>
                      </div>
                    ) : (
                      <div className="text-label text-sidebar-foreground/60">
                        Preview not available for this artifact type
                      </div>
                    )}
                  </div>
                </ScrollArea>
              )}
            </>
            )
          ) : researchState ? (
            // No artifact selected, show live research state
            <ResearchArtifact {...researchState} />
          ) : browserState ? (
            // No artifact selected, show live browser view
            <BrowserLiveView
              sessionId={browserState.sessionId}
              browserId={browserState.browserId}
              isActive={browserState.isActive}
              onConnectionError={browserState.onConnectionError}
              onValidationFailed={browserState.onValidationFailed}
            />
          ) : (
            <Empty className="text-sidebar-foreground/60">
              <EmptyHeader>
                <EmptyMedia variant="icon" className="bg-sidebar-accent text-sidebar-foreground/50">
                  <Layers className="h-6 w-6" />
                </EmptyMedia>
                <EmptyTitle className="text-sidebar-foreground/80">No Content Selected</EmptyTitle>
                <EmptyDescription className="text-sidebar-foreground/50">
                  Select an item from the library below to preview
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
        </div>

        {/* Bottom Artifact List - Horizontal Scroll (Resizable) */}
        <div
          className="flex-shrink-0 border-t border-sidebar-border/50 bg-sidebar-background/50 flex flex-col"
          style={{ height: `${bottomPanelHeight}px` }}
        >
          {/* Resize Handle */}
          <div
            className="w-full h-2 cursor-ns-resize hover:bg-primary/10 active:bg-primary/20 transition-colors flex items-center justify-center group"
            onMouseDown={handleResizeStart}
          >
            <GripHorizontal className="h-3 w-3 text-sidebar-foreground/30 group-hover:text-sidebar-foreground/60 transition-colors" />
          </div>

          <div className="px-4 py-3 flex-1 flex flex-col min-h-0">
            <div className="text-caption font-medium text-sidebar-foreground/60 uppercase tracking-wide mb-3">
              Canvas Library ({displayArtifacts.length})
            </div>
            <div className="overflow-x-auto overflow-y-hidden flex-1">
              <div className="flex gap-4 pb-2 min-w-min h-full">
                {displayArtifacts.length === 0 ? (
                  <div className="px-4 py-8 text-center text-label text-sidebar-foreground/50 w-full">
                    No artifacts yet
                  </div>
                ) : (
                  displayArtifacts.map((artifact) => {
                    return (
                      <button
                        key={artifact.id}
                        onClick={() => onSelectArtifact(artifact.id)}
                        className={`flex-shrink-0 text-left p-3 rounded-xl border-2 transition-all ${
                          selectedArtifactId === artifact.id
                            ? 'bg-primary/5 border-primary shadow-md ring-1 ring-primary/20'
                            : 'bg-sidebar-background border-sidebar-border hover:border-primary/50 hover:bg-sidebar-accent/30 hover:shadow-xs'
                        }`}
                      >
                        <div className="flex items-center gap-3">
                          <div className="flex-shrink-0 p-2 rounded-lg bg-primary/10">
                            {getArtifactIcon(artifact.type)}
                          </div>
                          <div className="min-w-0">
                            <div className="font-semibold text-label truncate text-sidebar-foreground whitespace-nowrap">
                              {artifact.title}
                            </div>
                            <div className="flex items-center gap-1.5 text-caption text-sidebar-foreground/60 whitespace-nowrap">
                              <span>{getArtifactTypeLabel(artifact.type)}</span>
                              <span>•</span>
                              <span>{formatTimestamp(artifact.timestamp)}</span>
                            </div>
                          </div>
                        </div>
                      </button>
                    )
                  })
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
