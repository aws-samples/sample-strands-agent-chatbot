import React, { useState, useCallback, useMemo } from 'react'
import { Download, ChevronDown, ChevronUp, Sparkles, Loader2 } from 'lucide-react'
import { fetchAuthSession } from 'aws-amplify/auth'
import { ToolExecution } from '@/types/chat'
import { getToolDisplayName } from '@/utils/chat'
import { getToolImageSrc, getToolIcon, resolveEffectiveToolId } from '@/config/tool-icons'
import { ChartRenderer } from '@/components/canvas'
import { ChartToolResult } from '@/types/chart'
import { MapRenderer } from '@/components/MapRenderer'
import { MapToolResult } from '@/types/map'
import { JsonDisplay } from '@/components/ui/JsonDisplay'
import { Markdown } from '@/components/ui/Markdown'
import { LazyImage } from '@/components/ui/LazyImage'
import { getApiUrl } from '@/config/environment'
import { isCodeAgentExecution, CodeAgentDetails, CodeAgentResult, CodeAgentDownloadButton } from './CodeAgentUI'

import type { ImageData } from '@/utils/imageExtractor'
import type { ResearchJob } from '@/lib/research-jobs'

// Word document tool names
const WORD_DOCUMENT_TOOLS = ['create_word_document', 'modify_word_document']
// Excel spreadsheet tool names
const EXCEL_SPREADSHEET_TOOLS = ['create_excel_spreadsheet', 'modify_excel_spreadsheet']
// PowerPoint presentation tool names
const POWERPOINT_TOOLS = ['create_presentation', 'update_slide_content', 'add_slide', 'delete_slides', 'move_slide', 'duplicate_slide', 'update_slide_notes']
// Workspace file tools (download button on read/write)
const WORKSPACE_FILE_TOOLS = ['workspace_read', 'workspace_write']
// Code Interpreter tools that save files to workspace
const CI_WORKSPACE_TOOLS = ['execute_code', 'ci_push_to_workspace']

interface ToolExecutionContainerProps {
  toolExecutions: ToolExecution[]
  compact?: boolean
  sessionId?: string
  onOpenResearchArtifact?: (executionId: string) => void  // Open completed research in Canvas
  onOpenWordArtifact?: (filename: string) => void  // Open Word document in Canvas
  onOpenExcelArtifact?: (filename: string) => void  // Open Excel spreadsheet in Canvas
  onOpenPptArtifact?: (filename: string) => void  // Open PowerPoint presentation in Canvas
  onOpenExtractedDataArtifact?: (artifactId: string) => void  // Open extracted data in Canvas
  onOpenExcalidrawArtifact?: (artifactId: string) => void  // Open Excalidraw diagram in Canvas
  researchJobs?: ResearchJob[]
}

// Collapsible Markdown component for tool results
const CollapsibleMarkdown = React.memo<{
  children: string;
  maxLines?: number;
  sessionId?: string;
}>(({ children, maxLines = 8, sessionId }) => {
  const [isExpanded, setIsExpanded] = useState(false)

  const lines = useMemo(() => children.split('\n'), [children])
  const needsTruncation = useMemo(() => lines.length > maxLines, [lines.length, maxLines])

  const displayContent = useMemo(() => {
    return isExpanded || !needsTruncation
      ? children
      : lines.slice(0, maxLines).join('\n') + '\n...'
  }, [isExpanded, needsTruncation, children, lines, maxLines])

  const handleToggleExpand = useCallback(() => {
    setIsExpanded(!isExpanded)
  }, [isExpanded])

  return (
    <div>
      <div className={needsTruncation && !isExpanded ? 'max-h-48 overflow-hidden' : ''}>
        <Markdown size="sm" sessionId={sessionId}>
          {displayContent}
        </Markdown>
      </div>

      {needsTruncation && (
        <button
          onClick={handleToggleExpand}
          className="flex items-center gap-1 text-caption text-primary hover:text-primary/80 transition-colors font-medium mt-1"
        >
          {isExpanded ? (
            <>
              <ChevronUp className="h-3 w-3" />
              Show less
            </>
          ) : (
            <>
              <ChevronDown className="h-3 w-3" />
              Show more ({lines.length - maxLines} more lines)
            </>
          )}
        </button>
      )}
    </div>
  )
}, (prevProps, nextProps) => {
  return prevProps.children === nextProps.children &&
         prevProps.maxLines === nextProps.maxLines &&
         prevProps.sessionId === nextProps.sessionId
})

export const ToolExecutionContainer = React.memo<ToolExecutionContainerProps>(({ toolExecutions, compact = false, sessionId, onOpenResearchArtifact, onOpenWordArtifact, onOpenExcelArtifact, onOpenPptArtifact, onOpenExtractedDataArtifact, onOpenExcalidrawArtifact, researchJobs = [] }) => {
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set())
  const [selectedImage, setSelectedImage] = useState<{ src: string; alt: string } | null>(null)
  const [downloadingFiles, setDownloadingFiles] = useState<Set<string>>(new Set())

  // Extract output filename from Word tool result
  // Prefers metadata.filename if available, falls back to regex extraction
  const extractWordFilename = (toolResult: string, metadata?: any): string | null => {
    if (metadata?.filename) return metadata.filename
    if (!toolResult) return null
    const savedAsMatch = toolResult.match(/\*\*Saved as\*\*:\s*([\w\-. ]+\.docx)/i)
    if (savedAsMatch) return savedAsMatch[1].trim()
    const match = toolResult.match(/([\w\-. ]+\.docx)/i)
    return match ? match[1].trim() : null
  }

  // Extract output filename from Excel tool result
  // Prefers metadata.filename if available, falls back to regex extraction
  const extractExcelFilename = (toolResult: string, metadata?: any): string | null => {
    if (metadata?.filename) return metadata.filename
    if (!toolResult) return null
    const savedAsMatch = toolResult.match(/\*\*Saved as\*\*:\s*([\w\-. ]+\.xlsx)/i)
    if (savedAsMatch) return savedAsMatch[1].trim()
    const match = toolResult.match(/([\w\-. ]+\.xlsx)/i)
    return match ? match[1].trim() : null
  }

  // Extract output filename from PowerPoint tool result
  // Prefers metadata.filename if available, falls back to regex extraction
  const extractPptFilename = (toolResult: string, metadata?: any): string | null => {
    if (metadata?.filename) return metadata.filename
    if (!toolResult) return null
    const updatedMatch = toolResult.match(/\*\*Updated\*\*:\s*([\w\-. ]+\.pptx)/i)
    if (updatedMatch) return updatedMatch[1].trim()
    const filenameMatch = toolResult.match(/\*\*Filename\*\*:\s*([\w\-. ]+\.pptx)/i)
    if (filenameMatch) return filenameMatch[1].trim()
    const match = toolResult.match(/([\w\-. ]+\.pptx)/i)
    return match ? match[1].trim() : null
  }

  // Extract artifact ID from browser_extract tool result
  const extractArtifactId = (toolResult: string): string | null => {
    if (!toolResult) return null
    // Look for "Saved as artifact: artifact-id" pattern
    const match = toolResult.match(/\*\*Saved as artifact\*\*:\s*(extracted-[\w-]+)/)
    return match ? match[1] : null
  }

  // Check if excalidraw tool result was successful (for Canvas button display)
  const hasExcalidrawData = (toolResult: string): boolean => {
    if (!toolResult) return false
    try {
      const parsed = JSON.parse(toolResult)
      return parsed.success === true && !!parsed.excalidraw_data
    } catch {
      return false
    }
  }

  const containsMarkdown = (text: string): boolean => {
    if (typeof text !== 'string') return false
    try {
      JSON.parse(text)
      return false
    } catch {
      // Not valid JSON, check for markdown patterns
    }
    return /\[([^\]]+)\]\(([^)]+)\)|\*\*[^*]+\*\*|_{1,2}[^_]+_{1,2}|^#+\s/.test(text)
  }

  const toggleToolExpansion = (toolId: string) => {
    setExpandedTools(prev => {
      const newSet = new Set(prev)
      if (newSet.has(toolId)) {
        newSet.delete(toolId)
      } else {
        newSet.add(toolId)
      }
      return newSet
    })
  }

  const isToolExpanded = (toolId: string) => {
    return expandedTools.has(toolId)
  }

  const resolvedToolExecutions = useMemo(() => toolExecutions.map(execution => {
    if (execution.toolName !== 'research_agent' || !execution.toolResult) return execution
    try {
      const receipt = JSON.parse(execution.toolResult)
      if (receipt?.status !== 'started' || !receipt.job_id) return execution
      const job = researchJobs.find(item => item.jobId === receipt.job_id)
      const complete = !!job && ['completed', 'delivering', 'delivered'].includes(job.status)
      const failed = job?.status === 'error'
      return {
        ...execution,
        isComplete: complete || failed,
        isCancelled: failed,
        streamingResponse: job?.progress?.content || execution.streamingResponse,
        toolResult: complete && job?.artifact?.content
          ? job.artifact.content
          : failed
            ? `Error: ${job.error || 'Research failed'}`
            : execution.toolResult,
      }
    } catch {
      return execution
    }
  }), [toolExecutions, researchJobs])

  // Memoize parsed chart data (hooks must be called unconditionally)
  const toolExecutionsDeps = useMemo(() => {
    return resolvedToolExecutions.map(t => ({
      id: t.id,
      isComplete: t.isComplete,
      toolResult: t.toolResult,
      toolName: t.toolName
    }))
  }, [resolvedToolExecutions])

  const chartDataCache = useMemo(() => {
    const cache = new Map<string, { parsed: ChartToolResult, resultString: string }>();

    toolExecutionsDeps.forEach((deps) => {
      if ((deps.toolName === 'create_visualization' || deps.toolName === 'show_on_map') &&
          deps.toolResult &&
          deps.isComplete) {
        try {
          let parsed = JSON.parse(deps.toolResult);

          if (parsed.statusCode && parsed.body) {
            try {
              const body = typeof parsed.body === 'string' ? JSON.parse(parsed.body) : parsed.body;
              if (body.content && Array.isArray(body.content)) {
                const textContent = body.content.find((item: any) => item.type === 'text');
                if (textContent?.text) {
                  parsed = JSON.parse(textContent.text);
                }
              }
            } catch (unwrapError) {
              console.warn('Failed to unwrap Lambda response:', unwrapError);
            }
          }

          cache.set(deps.id, {
            parsed,
            resultString: deps.toolResult
          });
        } catch (e) {
          // Invalid JSON, skip
        }
      }
    });

    return cache;
  }, [toolExecutionsDeps]);

  const renderVisualizationResult = useCallback((toolUseId: string) => {
    const cached = chartDataCache.get(toolUseId);
    if (!cached) return null;

    const result = cached.parsed;

    if (result.success && result.map_data) {
      return (
        <div className="my-4">
          <MapRenderer mapData={result.map_data} />
          <p className="text-label text-green-600 mt-2">
            {result.message}
          </p>
        </div>
      );
    }

    if (result.success && result.chart_data) {
      return (
        <div className="my-4">
          <ChartRenderer chartData={result.chart_data} />
          <p className="text-label text-green-600 mt-2">
            {result.message}
          </p>
        </div>
      );
    }

    return (
      <div className="my-4 p-3 bg-red-50 border border-red-200 rounded-sm">
        <p className="text-red-600">{result.message}</p>
      </div>
    );
  }, [chartDataCache]);

  // Early return after all hooks
  if (!toolExecutions || toolExecutions.length === 0) {
    return null
  }

  const handleFilesDownload = async (toolUseId: string, toolName?: string, toolResult?: string) => {
    if (downloadingFiles.has(toolUseId)) return
    setDownloadingFiles(prev => new Set(prev).add(toolUseId))
    try {
      if ((toolName === 'run_python_code' || toolName === 'finalize_document') && sessionId) {
        try {
          const filesListResponse = await fetch(getApiUrl(`files/list?toolUseId=${toolUseId}&sessionId=${sessionId}`));

          if (!filesListResponse.ok) {
            throw new Error(`Failed to get file list: ${filesListResponse.status}`);
          }

          const filesData = await filesListResponse.json();
          const filesList = filesData.files || [];

          if (filesList.length === 0) {
            throw new Error('No files found to download');
          }

          const JSZip = (await import('jszip')).default;
          const zip = new JSZip();

          let filesAdded = 0;

          for (const fileName of filesList) {
            try {
              const fileUrl = getApiUrl(`output/sessions/${sessionId}/${toolUseId}/${fileName}`);
              const response = await fetch(fileUrl);

              if (response.ok) {
                const blob = await response.blob();
                zip.file(fileName, blob);
                filesAdded++;
              }
            } catch (e) {
              console.warn(`Failed to download ${fileName}:`, e);
            }
          }

          if (filesAdded === 0) {
            throw new Error('No files could be downloaded');
          }

          const zipBlob = await zip.generateAsync({ type: 'blob' });
          const objectUrl = URL.createObjectURL(zipBlob);
          const link = document.createElement('a');
          link.href = objectUrl;
          link.download = `python_execution_${toolUseId}.zip`;
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          URL.revokeObjectURL(objectUrl);
          return;

        } catch (error) {
          console.error('Python MCP download failed:', error);
          if (error instanceof Error && error.message.includes('404')) {
            throw new Error('Download session expired. Please run the code again to generate new files.');
          }
          throw error;
        }
      }

      if (toolName === 'bedrock_code_interpreter' && toolResult) {
        try {
          const result = JSON.parse(toolResult);
          if (result.zip_download && result.zip_download.path) {
            const zipUrl = result.zip_download.path;
            const zipResponse = await fetch(zipUrl);
            if (zipResponse.ok) {
              const zipBlob = await zipResponse.blob();
              const objectUrl = URL.createObjectURL(zipBlob);
              const link = document.createElement('a');
              link.href = objectUrl;
              link.download = result.zip_download.name || `code_interpreter_${toolUseId}.zip`;
              link.style.display = 'none';
              document.body.appendChild(link);
              link.click();
              document.body.removeChild(link);
              URL.revokeObjectURL(objectUrl);
              return;
            }
          }
        } catch (e) {
          console.warn('ZIP download info not available or invalid');
        }

        try {
          const zipUrl = sessionId
            ? `/files/download/${sessionId}/${toolUseId}/code_interpreter_${toolUseId}.zip`
            : `/files/download/output/${toolUseId}/code_interpreter_${toolUseId}.zip`;

          const zipResponse = await fetch(zipUrl);
          if (zipResponse.ok) {
            const zipBlob = await zipResponse.blob();
            const objectUrl = URL.createObjectURL(zipBlob);
            const link = document.createElement('a');
            link.href = objectUrl;
            link.download = `code_interpreter_${toolUseId}.zip`;
            link.style.display = 'none';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(objectUrl);
            return;
          }
        } catch (e) {
          console.warn('Pre-made ZIP not available');
        }
      }

      const JSZip = (await import('jszip')).default;
      const zip = new JSZip();

      const params = new URLSearchParams({ toolUseId });
      if (sessionId) {
        params.append('sessionId', sessionId);
      }

      const listResponse = await fetch(getApiUrl(`files/list?${params.toString()}`));

      if (!listResponse.ok) {
        throw new Error(`Failed to get file list: ${listResponse.status}`);
      }

      const { files } = await listResponse.json();

      if (!files || files.length === 0) {
        throw new Error('No files found to download');
      }

      let filesAdded = 0;

      for (const fileName of files) {
        try {
          const fileUrl = sessionId
            ? `/output/sessions/${sessionId}/${toolUseId}/${fileName}`
            : `/output/${toolUseId}/${fileName}`;

          const response = await fetch(fileUrl);

          if (response.ok) {
            if (fileName.endsWith('.py') || fileName.endsWith('.txt') || fileName.endsWith('.csv') || fileName.endsWith('.json')) {
              const content = await response.text();
              zip.file(fileName, content);
            } else {
              const blob = await response.blob();
              zip.file(fileName, blob);
            }
            filesAdded++;
          }
        } catch (e) {
          console.warn(`Failed to download ${fileName}:`, e);
          continue;
        }
      }

      if (filesAdded === 0) {
        throw new Error('No files could be downloaded');
      }

      const zipBlob = await zip.generateAsync({
        type: 'blob',
        compression: 'DEFLATE',
        compressionOptions: { level: 6 }
      });

      const objectUrl = URL.createObjectURL(zipBlob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = `code_interpreter_${toolUseId}.zip`;
      link.style.display = 'none';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(objectUrl);

    } catch (error) {
      console.error('Failed to create ZIP:', error);
      const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
      alert(`Download failed: ${errorMessage}`);
    } finally {
      setDownloadingFiles(prev => {
        const next = new Set(prev)
        next.delete(toolUseId)
        return next
      })
    }
  };

  const handleWorkspaceDownload = async (toolUseId: string, toolResult?: string, toolName?: string) => {
    if (downloadingFiles.has(toolUseId) || !toolResult || !sessionId) return
    setDownloadingFiles(prev => new Set(prev).add(toolUseId))
    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      try {
        const session = await fetchAuthSession()
        const token = session.tokens?.accessToken?.toString()
        if (token) headers['Authorization'] = `Bearer ${token}`
      } catch { /* auth optional */ }
      headers['X-Session-ID'] = sessionId

      let paths: string[] = []

      if (toolName === 'ci_push_to_workspace') {
        const parsed = JSON.parse(toolResult)
        if (parsed.status === 'ok' && parsed.files_saved?.length) {
          paths = parsed.files_saved.map((f: string) => `documents/${f}`)
        }
      } else if (toolName === 'execute_code') {
        const match = toolResult.match(/File saved:\s*(\S+)/)
        if (match) {
          const filename = match[1]
          const docType = /\.(png|jpg|jpeg|gif|webp)$/i.test(filename) ? 'image' : 'code-output'
          paths = [`documents/${docType}/${filename}`]
        }
      } else {
        const parsed = JSON.parse(toolResult)
        if (parsed.status === 'ok' && parsed.path) {
          paths = [parsed.path]
        }
      }

      if (paths.length === 0) throw new Error('No file path in result')

      if (paths.length === 1) {
        const response = await fetch(getApiUrl('workspace/download'), {
          method: 'POST',
          headers,
          body: JSON.stringify({ path: paths[0], sessionId }),
        })
        if (!response.ok) throw new Error(`Download failed: ${response.status}`)
        const { url, filename } = await response.json()

        const link = document.createElement('a')
        link.href = url
        link.download = filename
        link.style.display = 'none'
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
      } else {
        const JSZip = (await import('jszip')).default
        const zip = new JSZip()
        for (const path of paths) {
          const response = await fetch(getApiUrl('workspace/download'), {
            method: 'POST',
            headers,
            body: JSON.stringify({ path, sessionId }),
          })
          if (!response.ok) continue
          const { url, filename } = await response.json()
          const fileResponse = await fetch(url)
          if (fileResponse.ok) {
            zip.file(filename, await fileResponse.blob())
          }
        }
        const zipBlob = await zip.generateAsync({ type: 'blob' })
        const objectUrl = URL.createObjectURL(zipBlob)
        const link = document.createElement('a')
        link.href = objectUrl
        link.download = `workspace_${toolUseId.slice(0, 8)}.zip`
        link.style.display = 'none'
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        URL.revokeObjectURL(objectUrl)
      }
    } catch (error) {
      console.error('Workspace download failed:', error)
      alert(`Download failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
    } finally {
      setDownloadingFiles(prev => {
        const next = new Set(prev)
        next.delete(toolUseId)
        return next
      })
    }
  }

  // Helper: render Canvas / download action buttons for a single tool execution
  const renderActionButtons = (toolExecution: ToolExecution) => (
    <>
      {(toolExecution.toolName === 'bedrock_code_interpreter' ||
        toolExecution.toolName === 'run_python_code' ||
        toolExecution.toolName === 'finalize_document') &&
        toolExecution.isComplete && (
        <button
          onClick={(e) => { e.stopPropagation(); handleFilesDownload(toolExecution.id, toolExecution.toolName, toolExecution.toolResult); }}
          disabled={downloadingFiles.has(toolExecution.id)}
          className="ml-auto p-1 hover:bg-muted rounded-sm transition-colors opacity-0 group-hover:opacity-100 disabled:opacity-50"
          title="Download files"
        >
          {downloadingFiles.has(toolExecution.id)
            ? <Loader2 className="h-3.5 w-3.5 text-muted-foreground animate-spin" />
            : <Download className="h-3.5 w-3.5 text-muted-foreground" />}
        </button>
      )}
      {WORKSPACE_FILE_TOOLS.includes(toolExecution.toolName) &&
        toolExecution.isComplete && !toolExecution.isCancelled && toolExecution.toolResult && (() => {
          try { const p = JSON.parse(toolExecution.toolResult || ''); return p.status === 'ok' && p.path } catch { return false }
        })() && (
        <button
          onClick={(e) => { e.stopPropagation(); handleWorkspaceDownload(toolExecution.id, toolExecution.toolResult, toolExecution.toolName); }}
          disabled={downloadingFiles.has(toolExecution.id)}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-primary border border-primary/40 hover:border-primary hover:bg-primary/10 rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {downloadingFiles.has(toolExecution.id)
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <Download className="h-3.5 w-3.5" />}
          <span>{downloadingFiles.has(toolExecution.id) ? 'Preparing...' : 'Download'}</span>
        </button>
      )}
      {CI_WORKSPACE_TOOLS.includes(toolExecution.toolName) &&
        toolExecution.isComplete && !toolExecution.isCancelled && toolExecution.toolResult && (() => {
          if (toolExecution.toolName === 'ci_push_to_workspace') {
            try { const p = JSON.parse(toolExecution.toolResult || ''); return p.status === 'ok' && p.files_saved?.length > 0 } catch { return false }
          }
          return /File saved:\s*\S+/.test(toolExecution.toolResult || '')
        })() && (
        <button
          onClick={(e) => { e.stopPropagation(); handleWorkspaceDownload(toolExecution.id, toolExecution.toolResult, toolExecution.toolName); }}
          disabled={downloadingFiles.has(toolExecution.id)}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-primary border border-primary/40 hover:border-primary hover:bg-primary/10 rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {downloadingFiles.has(toolExecution.id)
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <Download className="h-3.5 w-3.5" />}
          <span>{downloadingFiles.has(toolExecution.id) ? 'Preparing...' : 'Download'}</span>
        </button>
      )}
      {isCodeAgentExecution(toolExecution) && toolExecution.isComplete && !toolExecution.isCancelled && (
        <CodeAgentDownloadButton sessionId={sessionId} />
      )}
      {toolExecution.toolName === 'research_agent' && toolExecution.isComplete && !toolExecution.isCancelled &&
        toolExecution.toolResult &&
        !['user declined to proceed with research', 'user declined to proceed with browser automation']
          .includes((toolExecution.toolResult || '').toLowerCase()) && onOpenResearchArtifact && (
        <button onClick={(e) => { e.stopPropagation(); onOpenResearchArtifact(toolExecution.id); }}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-primary border border-primary/40 hover:border-primary hover:bg-primary/10 rounded-full transition-colors">
          <Sparkles className="h-3.5 w-3.5" /><span>Canvas</span>
        </button>
      )}
      {WORD_DOCUMENT_TOOLS.includes(toolExecution.toolName) &&
        toolExecution.isComplete && !toolExecution.isCancelled && toolExecution.toolResult &&
        extractWordFilename(toolExecution.toolResult, toolExecution.metadata) && onOpenWordArtifact && (
        <button onClick={(e) => { e.stopPropagation(); const f = extractWordFilename(toolExecution.toolResult || '', toolExecution.metadata); if (f) onOpenWordArtifact(f); }}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-primary border border-primary/40 hover:border-primary hover:bg-primary/10 rounded-full transition-colors">
          <Sparkles className="h-3.5 w-3.5" /><span>Canvas</span>
        </button>
      )}
      {EXCEL_SPREADSHEET_TOOLS.includes(toolExecution.toolName) &&
        toolExecution.isComplete && !toolExecution.isCancelled && toolExecution.toolResult &&
        extractExcelFilename(toolExecution.toolResult, toolExecution.metadata) && onOpenExcelArtifact && (
        <button onClick={(e) => { e.stopPropagation(); const f = extractExcelFilename(toolExecution.toolResult || '', toolExecution.metadata); if (f) onOpenExcelArtifact(f); }}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-primary border border-primary/40 hover:border-primary hover:bg-primary/10 rounded-full transition-colors">
          <Sparkles className="h-3.5 w-3.5" /><span>Canvas</span>
        </button>
      )}
      {POWERPOINT_TOOLS.includes(toolExecution.toolName) &&
        toolExecution.isComplete && !toolExecution.isCancelled && toolExecution.toolResult &&
        extractPptFilename(toolExecution.toolResult, toolExecution.metadata) && onOpenPptArtifact && (
        <button onClick={(e) => { e.stopPropagation(); const f = extractPptFilename(toolExecution.toolResult || '', toolExecution.metadata); if (f) onOpenPptArtifact(f); }}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-primary border border-primary/40 hover:border-primary hover:bg-primary/10 rounded-full transition-colors">
          <Sparkles className="h-3.5 w-3.5" /><span>Canvas</span>
        </button>
      )}
      {toolExecution.toolName === 'browser_extract' && toolExecution.isComplete && !toolExecution.isCancelled &&
        toolExecution.toolResult && extractArtifactId(toolExecution.toolResult) && onOpenExtractedDataArtifact && (
        <button onClick={(e) => { e.stopPropagation(); const a = extractArtifactId(toolExecution.toolResult || ''); if (a) onOpenExtractedDataArtifact(a); }}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-primary border border-primary/40 hover:border-primary hover:bg-primary/10 rounded-full transition-colors">
          <Sparkles className="h-3.5 w-3.5" /><span>Canvas</span>
        </button>
      )}
      {toolExecution.toolName === 'create_excalidraw_diagram' &&
        toolExecution.isComplete && !toolExecution.isCancelled && toolExecution.toolResult &&
        hasExcalidrawData(toolExecution.toolResult) && onOpenExcalidrawArtifact && (
        <button onClick={(e) => { e.stopPropagation(); onOpenExcalidrawArtifact(`excalidraw-${toolExecution.id}`); }}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-primary border border-primary/40 hover:border-primary hover:bg-primary/10 rounded-full transition-colors">
          <Sparkles className="h-3.5 w-3.5" /><span>Canvas</span>
        </button>
      )}
    </>
  )

  // Helper: render expanded detail for a single tool execution
  const renderExpandedDetail = (toolExecution: ToolExecution) => (
    <div className="ml-4 mt-1 mb-2 border-l-2 border-muted pl-3 space-y-2 animate-fade-in">
      {toolExecution.toolInputState === 'streaming' && !toolExecution.toolInputRaw && (
        <div className="flex items-center gap-2 py-1 text-caption text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span>Preparing parameters...</span>
        </div>
      )}
      {toolExecution.toolInputState === 'streaming' && toolExecution.toolInputRaw && (
        <div className="text-label">
          <JsonDisplay data={toolExecution.toolInputRaw} maxLines={4} label="Input" />
        </div>
      )}
      {toolExecution.toolInputState !== 'streaming' &&
        toolExecution.toolInput &&
        Object.keys(toolExecution.toolInput).length > 0 && (
        <div className="text-label">
          <JsonDisplay data={toolExecution.toolInput} maxLines={4} label="Input" />
        </div>
      )}
      {toolExecution.reasoningText && toolExecution.reasoningText.trim() && (
        <div className="text-label text-muted-foreground italic">{toolExecution.reasoningText}</div>
      )}
      {isCodeAgentExecution(toolExecution) && <CodeAgentDetails toolExecution={toolExecution} />}
      {toolExecution.toolResult && (() => {
        if (isCodeAgentExecution(toolExecution)) return <CodeAgentResult toolResult={toolExecution.toolResult} />
        return (
          <div className="text-label">
            {containsMarkdown(toolExecution.toolResult) ? (
              <CollapsibleMarkdown sessionId={sessionId} maxLines={8}>{toolExecution.toolResult}</CollapsibleMarkdown>
            ) : (
              <JsonDisplay data={toolExecution.toolResult} maxLines={6} label="Result" />
            )}
          </div>
        )
      })()}
      {toolExecution.images && toolExecution.images.length > 0 && (
        <div className="mt-2">
          <div className="flex gap-2 overflow-x-auto pb-2">
            {toolExecution.images
              .filter((image) => {
                const isUrlImage = 'type' in image && image.type === 'url'
                return isUrlImage ? (image.thumbnail || image.url) : ('data' in image && image.data)
              })
              .slice(0, 5)
              .map((image: ImageData, idx: number) => {
                const isUrlImage = 'type' in image && image.type === 'url'
                let imageSrc = ''
                if (isUrlImage) { imageSrc = image.url || image.thumbnail || '' }
                else if ('data' in image && 'format' in image) {
                  const d = typeof image.data === 'string' ? image.data : btoa(String.fromCharCode(...new Uint8Array(image.data as ArrayBuffer)))
                  imageSrc = `data:image/${image.format};base64,${d}`
                }
                const imageTitle = (isUrlImage && 'title' in image && typeof image.title === 'string') ? image.title : `Tool generated image ${idx + 1}`
                const imageFormat = isUrlImage ? 'WEB' : ('format' in image && typeof image.format === 'string') ? image.format.toUpperCase() : 'IMG'
                return (
                  <div key={idx} className="relative flex-shrink-0 h-[140px]">
                    <div className="relative h-full rounded-lg overflow-hidden border border-border shadow-xs hover:shadow-lg transition-all cursor-pointer bg-gray-50 dark:bg-gray-900"
                      onClick={() => isUrlImage && 'url' in image && image.url ? window.open(image.url, '_blank', 'noopener,noreferrer') : setSelectedImage({ src: imageSrc, alt: imageTitle })}>
                      <LazyImage src={imageSrc} alt={imageTitle} className="h-full w-auto object-cover" />
                      <div className="absolute top-2 right-2">
                        <div className="text-[10px] font-medium bg-black/70 text-white backdrop-blur-sm px-1.5 py-0.5 rounded-sm">{String(imageFormat)}</div>
                      </div>
                      {isUrlImage && 'title' in image && image.title && (
                        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent p-2 pt-6">
                          <p className="text-[11px] font-medium text-white line-clamp-2 leading-tight max-w-[200px]">{image.title}</p>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
          </div>
        </div>
      )}
    </div>
  )

  // Compact status indicator shared by every tool activity row.
  const StatusIndicator = ({ isComplete }: { isComplete: boolean }) => isComplete ? (
    <svg className="h-3.5 w-3.5 shrink-0 text-primary" viewBox="0 0 16 16" fill="none" aria-label="Complete">
      <circle cx="8" cy="8" r="7" fill="currentColor" />
      <path d="M5 8l2 2 4-4" stroke="hsl(var(--primary-foreground))" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ) : (
    <span className="flex gap-0.5 shrink-0">
      <span className="w-1 h-1 bg-primary rounded-full animate-pulse" />
      <span className="w-1 h-1 bg-primary rounded-full animate-pulse" style={{ animationDelay: '150ms' }} />
      <span className="w-1 h-1 bg-primary rounded-full animate-pulse" style={{ animationDelay: '300ms' }} />
    </span>
  )

  // ── Group tool executions by visual identity (same icon = same group) ──
  type ToolGroup = {
    key: string
    executions: ToolExecution[]
    effectiveToolId: string
    imageSrc: string | null
    IconComp: ReturnType<typeof getToolIcon> | null
    displayName: { running: string; complete: string }
  }
  type RenderItem =
    | { kind: 'group'; group: ToolGroup }
    | { kind: 'viz'; execution: ToolExecution }

  const renderItems: RenderItem[] = []
  const groupMap = new Map<string, ToolGroup>()

  for (const exec of resolvedToolExecutions) {
    // Skip skill_dispatcher — the executor calls show the actual work
    if (exec.toolName === 'skill_dispatcher') continue

    // Visualization/map results render inline (not grouped)
    if (exec.toolResult && exec.isComplete && chartDataCache.has(exec.id)) {
      renderItems.push({ kind: 'viz', execution: exec })
      continue
    }

    // Special standalone tools (code agent, research agent) — never grouped
    const isSpecial = isCodeAgentExecution(exec) || exec.toolName === 'research_agent'

    const effectiveId = resolveEffectiveToolId(exec.toolName, exec.toolInput)
    const imageSrc = getToolImageSrc(effectiveId)
    const groupKey = isSpecial ? `standalone-${exec.id}` : (imageSrc || effectiveId)

    if (!isSpecial && groupMap.has(groupKey)) {
      groupMap.get(groupKey)!.executions.push(exec)
    } else {
      const group: ToolGroup = {
        key: groupKey,
        executions: [exec],
        effectiveToolId: effectiveId,
        imageSrc,
        IconComp: !imageSrc ? getToolIcon(effectiveId) : null,
        displayName: {
          running: getToolDisplayName(exec.toolName, false, exec.toolInput),
          complete: getToolDisplayName(exec.toolName, true, exec.toolInput),
        },
      }
      renderItems.push({ kind: 'group', group })
      if (!isSpecial) groupMap.set(groupKey, group)
    }
  }

  return (
    <>
      <div className="space-y-1.5">
        {renderItems.map((item) => {
          // ── Visualization / map result ──
          if (item.kind === 'viz') {
            return <div key={item.execution.id} className="my-4">{renderVisualizationResult(item.execution.id)}</div>
          }

          // ── Tool group ──
          const { group } = item
          const { executions, imageSrc, IconComp, displayName, key } = group
          const completedCount = executions.filter(e => e.isComplete).length
          const allDone = completedCount === executions.length
          const count = executions.length
          const isExpanded = isToolExpanded(key)
          const liveProgress = count === 1 && !allDone
            ? executions[0].streamingResponse
            : undefined

          // Find the last completed execution for action buttons (Canvas, Download)
          const lastCompleteExec = [...executions].reverse().find(e => e.isComplete)

          return (
            <React.Fragment key={key}>
              <div className="rounded-lg border border-border bg-card overflow-hidden">
                {/* Collapsed row */}
                <div
                  onClick={() => toggleToolExpansion(key)}
                  className="flex min-h-10 items-center gap-2 px-3 py-2 hover:bg-muted/45 transition-colors w-full text-left group cursor-pointer"
                >
                  {/* Tool icon */}
                  {imageSrc ? (
                    <img src={imageSrc} alt="" className="h-4 w-4 object-contain shrink-0" />
                  ) : IconComp ? (
                    <IconComp className="h-4 w-4 text-muted-foreground shrink-0" />
                  ) : null}

                  {/* Display name — user-friendly running/complete form */}
                  <span className="text-label text-foreground">
                    {allDone ? displayName.complete : displayName.running}
                  </span>
                  {liveProgress && (
                    <span className="text-caption text-muted-foreground truncate">
                      {liveProgress}
                    </span>
                  )}

                  {/* Count badge — only when count > 1 */}
                  {count > 1 && (
                    <span className="text-[11px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded-md tabular-nums">
                      {allDone ? `×${count}` : `${completedCount}/${count}`}
                    </span>
                  )}

                  {/* Status indicator */}
                  <StatusIndicator isComplete={allDone} />

                  {/* Action buttons from last completed execution */}
                  {lastCompleteExec && renderActionButtons(lastCompleteExec)}
                </div>

                {/* Expanded: individual call details */}
                {isExpanded && (
                  <div className="border-t border-border bg-muted/20 px-3 py-3 space-y-3 animate-fade-in">
                    {executions.map((exec, i) => (
                      <div key={exec.id}>
                        {count > 1 && (
                          <div className="text-label text-muted-foreground mb-1 font-medium flex items-center gap-1.5">
                            <span className="text-[10px] bg-muted rounded-sm px-1 py-0.5 tabular-nums">{i + 1}</span>
                            {getToolDisplayName(exec.toolName, exec.isComplete, exec.toolInput)}
                          </div>
                        )}
                        {renderExpandedDetail(exec)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </React.Fragment>
          )
        })}
      </div>

      {/* Image Modal */}
      {selectedImage && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-[9999] p-8"
          onClick={() => setSelectedImage(null)}
        >
          <div className="relative max-w-[80vw] max-h-[80vh]">
            <img
              src={selectedImage.src}
              alt={selectedImage.alt}
              className="max-w-full max-h-[80vh] object-contain rounded-lg cursor-zoom-out"
              onClick={(e) => e.stopPropagation()}
            />
            <button
              onClick={() => setSelectedImage(null)}
              className="absolute top-2 right-2 bg-black/60 hover:bg-black/80 text-white rounded-full p-2 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      )}
    </>
  )
}, (prevProps, nextProps) => {
  if (prevProps.toolExecutions.length !== nextProps.toolExecutions.length) {
    return false
  }

  if (prevProps.compact !== nextProps.compact || prevProps.sessionId !== nextProps.sessionId) {
    return false
  }

  if (prevProps.researchJobs !== nextProps.researchJobs) {
    return false
  }

  return prevProps.toolExecutions.every((tool, idx) => {
    const nextTool = nextProps.toolExecutions[idx]
    if (!nextTool) return false

    if (tool.id !== nextTool.id) return false
    if (tool.toolName !== nextTool.toolName) return false
    if (tool.isComplete !== nextTool.isComplete) return false
    if (tool.toolResult !== nextTool.toolResult) return false
    if (tool.toolInputState !== nextTool.toolInputState) return false
    if (tool.toolInputRaw !== nextTool.toolInputRaw) return false

    const prevInput = JSON.stringify(tool.toolInput || {})
    const nextInput = JSON.stringify(nextTool.toolInput || {})
    if (prevInput !== nextInput) return false

    if ((tool.images?.length || 0) !== (nextTool.images?.length || 0)) return false
    if ((tool.codeSteps?.length || 0) !== (nextTool.codeSteps?.length || 0)) return false
    if (tool.codeResultMeta !== nextTool.codeResultMeta) return false
    if (JSON.stringify(tool.codeTodos) !== JSON.stringify(nextTool.codeTodos)) return false

    return true
  })
})
