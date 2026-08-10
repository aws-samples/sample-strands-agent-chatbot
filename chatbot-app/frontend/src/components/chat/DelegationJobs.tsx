"use client"

import { Check, CircleStop, Loader2, SearchCheck } from 'lucide-react'
import type { DelegationJob } from '@/lib/delegation-jobs'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'

interface DelegationJobsProps {
  jobs: DelegationJob[]
  onCancel: (jobId: string) => Promise<boolean>
}

export function DelegationJobs({ jobs, onCancel }: DelegationJobsProps) {
  const visible = jobs.filter(job =>
    ['queued', 'running', 'failed', 'cancelled'].includes(job.executionStatus),
  )
  if (visible.length === 0) return null

  return (
    <div className="mx-auto w-full max-w-4xl px-4 pb-2">
      <div className="space-y-1">
        {visible.map(job => {
          const active = ['queued', 'running'].includes(job.executionStatus)
          const label = job.profile === 'reviewer' ? 'Reviewer' : 'Analyst'
          return (
            <div
              key={job.jobId}
              className="group flex min-h-9 items-center gap-2 px-1 text-sm"
            >
              {active ? (
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
              ) : job.executionStatus === 'failed' ? (
                <CircleStop className="h-3.5 w-3.5 shrink-0 text-destructive" />
              ) : (
                <Check className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              )}
              <SearchCheck className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="font-medium text-foreground">{label}</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">
                {job.progress?.content || job.request.goal}
              </span>
              {active && (
                <TooltipProvider delayDuration={250}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 shrink-0 text-muted-foreground"
                        aria-label={`Cancel ${label.toLowerCase()} delegation`}
                        onClick={() => void onCancel(job.jobId)}
                      >
                        <CircleStop className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Cancel delegated task</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              )}
              {!active && job.error && (
                <span className="max-w-48 truncate text-xs text-destructive">
                  {job.error}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
