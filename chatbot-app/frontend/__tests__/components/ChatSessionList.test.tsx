import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ChatSessionList } from '@/components/sidebar/ChatSessionList'
import type { ChatSession } from '@/hooks/useChatSessions'

const sessions: ChatSession[] = [
  {
    sessionId: 'session-current',
    title: 'Current session',
    lastMessageAt: '2099-08-11T10:00:00Z',
    lastActivityAt: '2099-08-11T10:05:00Z',
    hasUnseenUpdate: true,
    messageCount: 2,
    status: 'active',
    createdAt: '2099-08-11T09:00:00Z',
  },
  {
    sessionId: 'session-background',
    title: 'Background session',
    lastMessageAt: '2099-08-11T09:00:00Z',
    lastActivityAt: '2099-08-11T10:10:00Z',
    hasUnseenUpdate: true,
    messageCount: 3,
    status: 'active',
    createdAt: '2099-08-11T08:00:00Z',
  },
]

describe('ChatSessionList attention badge', () => {
  it('shows one minimal marker only for an inactive unseen session', () => {
    render(
      <ChatSessionList
        sessions={sessions}
        currentSessionId="session-current"
        isLoading={false}
        onLoadSession={vi.fn()}
        onDeleteSession={vi.fn()}
      />,
    )

    expect(screen.getAllByRole('status', { name: 'New activity' })).toHaveLength(1)
  })

  it('loads the session when its row is clicked', () => {
    const onLoadSession = vi.fn().mockResolvedValue(undefined)
    render(
      <ChatSessionList
        sessions={sessions}
        currentSessionId="session-current"
        isLoading={false}
        onLoadSession={onLoadSession}
        onDeleteSession={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByText('Background session'))

    expect(onLoadSession).toHaveBeenCalledWith('session-background')
  })
})
