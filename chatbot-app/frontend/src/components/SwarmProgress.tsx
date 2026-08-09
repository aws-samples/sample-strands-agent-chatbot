'use client';

import React, { useState, useEffect } from 'react';
import { ChevronDown, Wrench, Sparkles, ArrowRight, FileText, FileSpreadsheet, Presentation, Download, BarChart2, Image } from 'lucide-react';
import { SwarmProgress as SwarmProgressType, SWARM_AGENT_DISPLAY_NAMES, SwarmAgentStep } from '@/types/events';
import { Markdown } from '@/components/ui/Markdown';
import { cn } from '@/lib/utils';
import { fetchAuthSession } from 'aws-amplify/auth';

interface SwarmProgressProps {
  progress?: SwarmProgressType;
  className?: string;
  // History mode: show simplified view with agent names and shared context
  historyMode?: boolean;
  historyAgents?: string[];
  historySharedContext?: Record<string, any>;
  sessionId?: string;
}

// Document info structure from shared_context
interface DocumentInfo {
  filename: string;
  tool_type: string;
}

// Chart info structure from shared_context (legacy)
interface ChartInfo {
  title: string;
  description?: string;
}

// Image info structure from shared_context
interface ImageInfo {
  filename: string;
  description?: string;
}

// Get file icon and color based on tool_type or extension
function getFileIcon(doc: DocumentInfo) {
  const toolType = doc.tool_type?.toLowerCase();
  const ext = doc.filename?.toLowerCase().split('.').pop();

  if (toolType === 'excel' || ext === 'xlsx' || ext === 'xls') {
    return { Icon: FileSpreadsheet, color: 'text-green-600 dark:text-green-400' };
  }
  if (toolType === 'powerpoint' || ext === 'pptx' || ext === 'ppt') {
    return { Icon: Presentation, color: 'text-orange-600 dark:text-orange-400' };
  }
  // Default: word document
  return { Icon: FileText, color: 'text-blue-600 dark:text-blue-400' };
}

// Handle document download (same pattern as AssistantTurn)
async function handleDocumentDownload(filename: string, toolType: string, sessionId?: string) {
  if (!sessionId) {
    console.error('[SwarmProgress] No session ID available for document download');
    return;
  }

  try {
    // Get auth token for BFF to extract userId
    const authHeaders: Record<string, string> = { 'Content-Type': 'application/json' };
    try {
      const session = await fetchAuthSession();
      const token = session.tokens?.accessToken?.toString();
      if (token) {
        authHeaders['Authorization'] = `Bearer ${token}`;
      }
    } catch (error) {
      console.log('[SwarmProgress] No auth session available');
    }

    // Step 1: Get S3 key from documents/download API
    const s3KeyResponse = await fetch('/api/documents/download', {
      method: 'POST',
      headers: authHeaders,
      body: JSON.stringify({
        sessionId,
        filename,
        toolType
      })
    });

    if (!s3KeyResponse.ok) {
      throw new Error(`Failed to get S3 key: ${s3KeyResponse.status}`);
    }

    const { s3Key } = await s3KeyResponse.json();

    // Step 2: Get presigned URL
    const presignedResponse = await fetch('/api/s3/presigned-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ s3Key })
    });

    if (!presignedResponse.ok) {
      throw new Error(`Failed to get presigned URL: ${presignedResponse.status}`);
    }

    const { url } = await presignedResponse.json();

    // Step 3: Trigger download or open in new tab
    const link = document.createElement('a');
    link.href = url;

    // Images open in new tab, documents download
    if (filename.toLowerCase().endsWith('.png') || filename.toLowerCase().endsWith('.jpg') || filename.toLowerCase().endsWith('.jpeg')) {
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    } else {
      link.download = filename;
    }

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    console.log('[SwarmProgress] Download/open triggered:', filename);
  } catch (err) {
    console.error('[SwarmProgress] Failed to download document:', err);
  }
}

/**
 * Renders shared_context items (documents, images, charts) from agent handoffs
 */
function SharedContextRenderer({ context, sessionId }: { context: Record<string, any>; sessionId?: string }) {
  // Extract arrays from context
  const documents: DocumentInfo[] = context?.documents || [];
  const images: ImageInfo[] = context?.images || [];
  const charts: ChartInfo[] = context?.charts || [];  // legacy

  // If no renderable items, show raw JSON as fallback
  if (documents.length === 0 && images.length === 0 && charts.length === 0) {
    return (
      <div className="mt-1 p-2 bg-muted/30 rounded-sm text-caption font-mono overflow-x-auto">
        <pre className="whitespace-pre-wrap break-words text-muted-foreground">
          {typeof context === 'string' ? context : JSON.stringify(context, null, 2)}
        </pre>
      </div>
    );
  }

  return (
    <div className="mt-2 space-y-2">
      {/* Documents */}
      {documents.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {documents.map((doc, idx) => {
            const { Icon, color } = getFileIcon(doc);
            return (
              <div
                key={`doc-${idx}`}
                className="group relative flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg transition-all duration-200 cursor-pointer border border-gray-200/50 dark:border-gray-700/50 hover:border-gray-300 dark:hover:border-gray-600"
                onClick={() => handleDocumentDownload(doc.filename, doc.tool_type, sessionId)}
              >
                <div className="flex items-center justify-center w-6 h-6 bg-gray-50 dark:bg-gray-800 rounded-sm shadow-xs">
                  <Icon className={`h-3 w-3 ${color}`} />
                </div>
                <span className="text-caption font-medium text-gray-700 dark:text-gray-200 whitespace-nowrap">
                  {doc.filename}
                </span>
                <Download className="h-3 w-3 text-gray-400 dark:text-gray-500 opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
            );
          })}
        </div>
      )}

      {/* Images (from data_analyst) */}
      {images.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {images.map((img, idx) => (
            <div
              key={`img-${idx}`}
              className="group relative flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg transition-all duration-200 cursor-pointer border border-gray-200/50 dark:border-gray-700/50 hover:border-gray-300 dark:hover:border-gray-600"
              onClick={() => handleDocumentDownload(img.filename, 'image', sessionId)}
            >
              <div className="flex items-center justify-center w-6 h-6 bg-purple-50 dark:bg-purple-900/30 rounded-sm shadow-xs">
                <Image className="h-3 w-3 text-purple-600 dark:text-purple-400" />
              </div>
              <span className="text-caption font-medium text-gray-700 dark:text-gray-200 whitespace-nowrap">
                {img.filename}
              </span>
              {img.description && (
                <span className="text-caption text-gray-500 dark:text-gray-400 max-w-[200px] truncate">
                  - {img.description}
                </span>
              )}
              <Download className="h-3 w-3 text-gray-400 dark:text-gray-500 opacity-0 group-hover:opacity-100 transition-opacity" />
            </div>
          ))}
        </div>
      )}

      {/* Charts (legacy) */}
      {charts.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {charts.map((chart, idx) => (
            <div
              key={`chart-${idx}`}
              className="flex items-center gap-2 px-3 py-1.5 bg-purple-50 dark:bg-purple-900/20 rounded-lg border border-purple-200/50 dark:border-purple-700/50"
            >
              <BarChart2 className="h-3 w-3 text-purple-600 dark:text-purple-400" />
              <span className="text-caption font-medium text-purple-700 dark:text-purple-300">
                {chart.title}
              </span>
              {chart.description && (
                <span className="text-caption text-purple-500 dark:text-purple-400">
                  - {chart.description}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Swarm progress indicator - integrated into message flow
 * - Running: shows collapsible agent progress + streaming response
 * - Completed: collapsible "Show progress" + final response
 * - History mode: simplified view showing just agent names (no avatar)
 */
export function SwarmProgress({ progress, className, historyMode, historyAgents, historySharedContext, sessionId }: SwarmProgressProps) {
  const [isExpanded, setIsExpanded] = useState(!historyMode); // Start collapsed in history mode

  // Extract status for useEffect dependency (handle both modes)
  const status = progress?.status;

  // Auto-collapse when completed (real-time mode only)
  useEffect(() => {
    if (!historyMode && (status === 'completed' || status === 'failed')) {
      setIsExpanded(false);
    }
  }, [status, historyMode]);

  // History mode - simplified view with shared context
  if (historyMode && historyAgents && historyAgents.length > 0) {
    return (
      <div className={cn("mb-2", className)}>
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors py-1"
        >
          <Sparkles className="h-4 w-4 text-purple-500" />
          <span className="text-label font-medium">Show Progress</span>
          <ChevronDown
            className={cn(
              "h-4 w-4 transition-transform duration-200",
              isExpanded && "rotate-180"
            )}
          />
        </button>

        {isExpanded && (
          <div className="mt-2 border-l-2 border-purple-500/30 pl-4 space-y-3 animate-fade-in">
            {historyAgents.map((agentId, index) => {
              const displayName = SWARM_AGENT_DISPLAY_NAMES[agentId] || agentId;
              const agentContext = historySharedContext?.[agentId];

              return (
                <div key={`${agentId}-${index}`} className="space-y-1.5">
                  {/* Agent header */}
                  <div className="flex items-center gap-2">
                    <span className="text-label font-semibold text-foreground">
                      {displayName}
                    </span>
                    <span className="text-caption text-green-600 dark:text-green-400">✓</span>
                  </div>

                  {/* Agent's shared context data - rendered as documents/charts */}
                  {agentContext && (
                    <SharedContextRenderer context={agentContext} sessionId={sessionId} />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  // Real-time mode - requires progress prop
  if (!progress) return null;

  const { isActive, currentNode, currentAction, agentSteps } = progress;

  if (!isActive && status === 'idle') return null;

  const displayName = SWARM_AGENT_DISPLAY_NAMES[currentNode] || currentNode;
  const isComplete = status === 'completed' || status === 'failed';

  // Filter steps: exclude responder only
  // Responder's content is rendered via normal message flow, not here
  // Coordinator's reasoning and handoff context should be shown
  const intermediateSteps = agentSteps?.filter(step =>
    step.nodeId !== 'responder'
  ) || [];

  // Check if there's any content to show in agents section
  const hasAgentContent = intermediateSteps.some(s =>
    s.reasoningText?.trim() || s.responseText?.trim() ||
    (s.toolCalls && s.toolCalls.length > 0) || s.handoffMessage || s.handoffContext
  );

  return (
    <div className={cn("flex justify-start mb-4 group", className)}>
      <div className="w-full max-w-4xl min-w-0 space-y-3 pt-0.5">
          {/* Agents section - collapsible */}
          {(hasAgentContent || !isComplete) && (
            <div className="mb-2">
              <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="flex items-center gap-2 text-muted-foreground hover:text-foreground transition-colors py-1"
              >
                <Sparkles className="h-4 w-4 text-blue-500" />
                <span className="text-label font-medium">
                  {isComplete ? 'Show progress' : (currentAction || `${displayName} working...`)}
                </span>
                <ChevronDown
                  className={cn(
                    "h-4 w-4 transition-transform duration-200",
                    isExpanded && "rotate-180"
                  )}
                />
              </button>

              {/* Expanded content - agent steps */}
              {isExpanded && (
                <div className="mt-2 border-l-2 border-blue-500/30 pl-4 space-y-3 animate-fade-in">
                  {intermediateSteps.map((step, index) => (
                    <AgentStepSection
                      key={`${step.nodeId}-${index}`}
                      step={step}
                      isRunning={!isComplete && index === intermediateSteps.length - 1}
                      sessionId={sessionId}
                    />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Note: Responder's content is rendered via normal message flow, not here */}
      </div>
    </div>
  );
}

/**
 * Individual agent step section
 */
function AgentStepSection({ step, isRunning, sessionId }: { step: SwarmAgentStep; isRunning?: boolean; sessionId?: string }) {
  const duration = step.endTime && step.startTime
    ? Math.round((step.endTime - step.startTime) / 1000)
    : null;

  const hasReasoning = step.reasoningText && step.reasoningText.trim().length > 0;
  const hasResponse = step.responseText && step.responseText.trim().length > 0;
  const hasToolCalls = step.toolCalls && step.toolCalls.length > 0;
  const hasHandoff = step.handoffMessage && step.handoffMessage.trim().length > 0;
  const hasContext = step.handoffContext && Object.keys(step.handoffContext).length > 0;

  // Show even if just running (for real-time feedback)
  if (!hasReasoning && !hasToolCalls && !hasResponse && !hasHandoff && !hasContext && !isRunning) {
    return null;
  }

  return (
    <div className="space-y-1.5">
      {/* Agent header */}
      <div className="flex items-center gap-2">
        <span className="text-label font-semibold text-foreground">
          {step.displayName}
        </span>
        {duration !== null && (
          <span className="text-caption text-muted-foreground">
            ({duration}s)
          </span>
        )}
        {isRunning && (
          <span className="flex gap-0.5">
            <span className="w-1 h-1 bg-blue-500 rounded-full animate-pulse"></span>
            <span className="w-1 h-1 bg-blue-500 rounded-full animate-pulse" style={{ animationDelay: '150ms' }}></span>
            <span className="w-1 h-1 bg-blue-500 rounded-full animate-pulse" style={{ animationDelay: '300ms' }}></span>
          </span>
        )}
      </div>

      {/* Reasoning text */}
      {hasReasoning && (
        <div className="text-label text-muted-foreground/80 italic leading-relaxed">
          {step.reasoningText}
        </div>
      )}

      {/* Tool calls */}
      {hasToolCalls && (
        <div className="flex flex-wrap gap-1.5">
          {step.toolCalls!.map((tool, i) => (
            <span
              key={`${tool.toolName}-${i}`}
              className={cn(
                "inline-flex items-center gap-1 px-2 py-0.5 rounded-sm text-caption",
                tool.status === 'completed' && "bg-green-100/50 text-green-700 dark:bg-green-900/20 dark:text-green-400",
                tool.status === 'failed' && "bg-red-100/50 text-red-700 dark:bg-red-900/20 dark:text-red-400",
                tool.status === 'running' && "bg-purple-100/50 text-purple-700 dark:bg-purple-900/20 dark:text-purple-400"
              )}
            >
              <Wrench className="h-2.5 w-2.5" />
              {tool.toolName}
            </span>
          ))}
        </div>
      )}

      {/* Agent's response text (intermediate, not final) */}
      {hasResponse && (
        <div className="text-label text-muted-foreground leading-relaxed pl-2 border-l border-muted">
          {step.responseText}
        </div>
      )}

      {/* Handoff message */}
      {hasHandoff && (
        <div className="flex items-start gap-1.5 text-caption text-muted-foreground/70 mt-1">
          <ArrowRight className="h-3 w-3 mt-0.5 flex-shrink-0" />
          <span className="italic">{step.handoffMessage}</span>
        </div>
      )}

      {/* Handoff context data - rendered as documents/charts */}
      {hasContext && (
        <SharedContextRenderer context={step.handoffContext!} sessionId={sessionId} />
      )}
    </div>
  );
}

export default SwarmProgress;
