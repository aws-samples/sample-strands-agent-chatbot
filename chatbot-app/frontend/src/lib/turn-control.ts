import type { AgentStatus } from '@/types/events'

export interface TurnControlState {
  isBusy: boolean
  isBlocked: boolean
  canInterrupt: boolean
}

interface DeriveTurnControlOptions {
  agentStatus: AgentStatus
  isForegroundRunActive: boolean
  hasStoppableRun: boolean
  isCompacting: boolean
  interruptCount: number
  hasPendingOAuth: boolean
}

/**
 * Single control-state projection shared by the composer and queued turns.
 * AgentStatus remains presentational; this projection owns user actions.
 */
export function deriveTurnControl({
  agentStatus,
  isForegroundRunActive,
  hasStoppableRun,
  isCompacting,
  interruptCount,
  hasPendingOAuth,
}: DeriveTurnControlOptions): TurnControlState {
  const isBlocked = interruptCount > 0 || hasPendingOAuth
  const isBusy =
    isForegroundRunActive ||
    agentStatus !== 'idle' ||
    isBlocked
  const isVoiceActivity = agentStatus.startsWith('voice_')

  return {
    isBusy,
    isBlocked,
    canInterrupt:
      hasStoppableRun &&
      !isBlocked &&
      !isCompacting &&
      agentStatus !== 'stopping' &&
      agentStatus !== 'compacting' &&
      !isVoiceActivity,
  }
}
