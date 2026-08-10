import type { TurnPhase } from '@/types/events'
import { getGlobalTurnActivity } from '@/lib/turn-activity'

interface TurnActivityIndicatorProps {
  phase: TurnPhase
  hidden?: boolean
}

export const TurnActivityIndicator = ({
  phase,
  hidden = false,
}: TurnActivityIndicatorProps) => {
  const activity = getGlobalTurnActivity(phase)
  if (hidden || !activity) return null

  return (
    <div className="mx-auto w-full max-w-4xl min-w-0 px-4 animate-fade-in">
      <div
        className="flex min-h-8 items-center gap-2 py-1.5 px-2 -mx-2 text-label text-muted-foreground"
        role="status"
        aria-label={activity.ariaLabel}
      >
        <span className="flex gap-0.5 shrink-0" aria-hidden="true">
          <span className="h-1 w-1 rounded-full bg-primary animate-pulse" />
          <span className="h-1 w-1 rounded-full bg-primary animate-pulse [animation-delay:150ms]" />
          <span className="h-1 w-1 rounded-full bg-primary animate-pulse [animation-delay:300ms]" />
        </span>
        <span>{activity.label}</span>
      </div>
    </div>
  )
}
