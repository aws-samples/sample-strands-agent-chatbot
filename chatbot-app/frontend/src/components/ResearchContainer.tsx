"use client"

import React from 'react'
import { FlaskConical, Loader2, Check, ArrowRight, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface ResearchContainerProps {
  query: string
  status: 'idle' | 'searching' | 'analyzing' | 'generating' | 'complete' | 'error' | 'declined'
  isLoading: boolean
  hasResult?: boolean
  onClick: () => void
  agentName?: string  // Display name for the agent (e.g., "Research Agent" or "Browser Use Agent")
  currentStatus?: string  // Real-time status from research_progress events
  showCanvasButton?: boolean  // Show "View in Canvas" instead of "Open"
  onCanvasClick?: () => void  // Handler for "View in Canvas" button
}

export function ResearchContainer({
  query,
  status,
  isLoading,
  hasResult = true,
  onClick,
  agentName = 'Research Agent',
  currentStatus,
  showCanvasButton = false,
  onCanvasClick
}: ResearchContainerProps) {
  const getStatusText = () => {
    // Use real-time status if available and still loading
    if (currentStatus && isLoading && status !== 'complete') {
      return currentStatus
    }

    switch (status) {
      case 'searching':
        return 'Searching web sources'
      case 'analyzing':
        return 'Analyzing information'
      case 'generating':
        return 'Generating report'
      case 'complete':
        return 'Research complete'
      case 'declined':
        return 'Research declined'
      case 'error':
        return 'Research failed'
      default:
        return 'Starting research'
    }
  }

  const isComplete = status === 'complete' && hasResult
  const isDeclined = status === 'declined'
  const isError = status === 'error'
  // Show button during loading if we have partial results (for real-time viewing)
  const showOpenButton = isComplete || (isLoading && hasResult)

  return (
    <div
      onClick={isComplete ? onClick : undefined}
      className={`
        group rounded-lg border bg-card transition-colors
        ${isComplete ? 'cursor-pointer hover:bg-muted/35 hover:border-primary/30' : ''}
        ${isError ? 'border-destructive/30' : 'border-border'}
      `}
    >
      <div className="p-3.5">
        <div className="flex items-start gap-3">
          {/* Icon */}
          <div className={`
            relative flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md
            ${isError ? 'bg-destructive/10 text-destructive' : 'bg-muted text-muted-foreground'}
          `}>
            {isComplete ? (
              <Sparkles className="w-4 h-4 text-primary" />
            ) : (
              <FlaskConical className="w-4 h-4" />
            )}
            {isComplete && (
              <div className="absolute -top-1 -right-1 rounded-full bg-primary p-0.5">
                <Check className="w-2.5 h-2.5 text-primary-foreground" strokeWidth={3} />
              </div>
            )}
            {isLoading && !isComplete && (
              <div className="absolute -top-1 -right-1">
                <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
              </div>
            )}
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h4 className="font-medium text-body text-foreground">
                  {agentName}
                </h4>
                <p className="text-label text-muted-foreground mt-0.5 line-clamp-2 leading-relaxed">
                  {query}
                </p>
              </div>
              {showOpenButton && (
                showCanvasButton && onCanvasClick ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 shrink-0 px-3 gap-1.5 rounded-md"
                    onClick={(e) => {
                      e.stopPropagation()
                      onCanvasClick()
                    }}
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                    Canvas
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 shrink-0 px-2.5 gap-1 rounded-md text-muted-foreground"
                    onClick={(e) => {
                      e.stopPropagation()
                      onClick()
                    }}
                  >
                    Open
                    <ArrowRight className="w-3.5 h-3.5" />
                  </Button>
                )
              )}
            </div>

            <div className="flex items-center gap-1.5 mt-2 text-caption">
              {isLoading && !isComplete && (
                <Loader2 className="w-3 h-3 animate-spin text-primary" />
              )}
              {isComplete && <Check className="w-3 h-3 text-primary" />}
              <span className={
                isError
                  ? 'text-destructive'
                  : isDeclined
                    ? 'text-muted-foreground'
                    : isComplete
                      ? 'text-primary'
                      : 'text-muted-foreground'
              }>
                {getStatusText()}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
