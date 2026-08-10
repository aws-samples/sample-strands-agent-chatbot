import type { TurnPhase } from '@/types/events'

export interface TurnActivity {
  label: string
  ariaLabel: string
}

export type TurnActivityOwner =
  | 'global'
  | 'tool'
  | 'response'
  | 'approval'
  | 'connection'
  | 'none'

const PHASE_OWNERS: Record<TurnPhase, TurnActivityOwner> = {
  idle: 'none',
  submitting: 'global',
  waiting_for_model: 'global',
  reasoning: 'global',
  preparing_tool: 'tool',
  running_tool: 'tool',
  processing_tool_result: 'global',
  streaming_response: 'response',
  waiting_for_user: 'approval',
  reconnecting: 'connection',
}

export const getTurnActivityOwner = (phase: TurnPhase): TurnActivityOwner =>
  PHASE_OWNERS[phase]

export const getGlobalTurnActivity = (phase: TurnPhase): TurnActivity | null => {
  if (getTurnActivityOwner(phase) !== 'global') return null

  switch (phase) {
    case 'submitting':
      return { label: 'Starting...', ariaLabel: 'Starting request' }
    case 'waiting_for_model':
      return { label: 'Thinking...', ariaLabel: 'Waiting for the model' }
    case 'reasoning':
      return { label: 'Reasoning...', ariaLabel: 'Agent is reasoning' }
    case 'processing_tool_result':
      return { label: 'Reviewing tool results...', ariaLabel: 'Reviewing tool results' }
    default:
      return null
  }
}
