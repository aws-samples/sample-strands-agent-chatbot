import { NextRequest } from 'next/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  extractUserFromRequest: vi.fn(),
  getUserSessions: vi.fn(),
  getSession: vi.fn(),
  getSessionAttentionStates: vi.fn(),
  hasUnseenSessionAttention: vi.fn(),
  markSessionAttentionSeen: vi.fn(),
}))

vi.mock('@/lib/auth-utils', () => ({
  extractUserFromRequest: mocks.extractUserFromRequest,
}))

vi.mock('@/lib/dynamodb-client', () => ({
  getUserSessions: mocks.getUserSessions,
  getSession: mocks.getSession,
}))

vi.mock('@/lib/session-events', () => ({
  getSessionAttentionStates: mocks.getSessionAttentionStates,
  hasUnseenSessionAttention: mocks.hasUnseenSessionAttention,
  markSessionAttentionSeen: mocks.markSessionAttentionSeen,
}))

describe('session attention API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.extractUserFromRequest.mockResolvedValue({ userId: 'user-1' })
    mocks.hasUnseenSessionAttention.mockImplementation(
      state => Boolean(
        state.latestAttentionCursor &&
        state.latestAttentionCursor > (state.lastSeenAttentionCursor || ''),
      ),
    )
  })

  it('merges attention state and orders sessions by latest activity', async () => {
    mocks.getUserSessions.mockResolvedValue([
      {
        sessionId: 'session-recent-message',
        title: 'Recent message',
        lastMessageAt: '2026-08-11T10:00:00Z',
        messageCount: 1,
        status: 'active',
        createdAt: '2026-08-11T09:00:00Z',
      },
      {
        sessionId: 'session-background',
        title: 'Background result',
        lastMessageAt: '2026-08-10T10:00:00Z',
        messageCount: 1,
        status: 'active',
        createdAt: '2026-08-10T09:00:00Z',
      },
    ])
    mocks.getSessionAttentionStates.mockResolvedValue(new Map([
      ['session-background', {
        latestAttentionCursor:
          'OUTBOX_V2#2026-08-11T11:00:00Z#event-1',
        latestAttentionAt: '2026-08-11T11:00:00Z',
      }],
    ]))

    const { GET } = await import('@/app/api/session/list/route')
    const response = await GET(new NextRequest(
      'http://localhost/api/session/list?limit=100&status=active',
    ))
    const body = await response.json()

    expect(body.sessions.map((session: any) => session.sessionId)).toEqual([
      'session-background',
      'session-recent-message',
    ])
    expect(body.sessions[0]).toMatchObject({
      lastActivityAt: '2026-08-11T11:00:00Z',
      hasUnseenUpdate: true,
    })
  })

  it('marks the supplied rendered cursor as seen', async () => {
    mocks.getSession.mockResolvedValue({
      sessionId: 'session-1',
      userId: 'user-1',
    })
    const cursor = 'OUTBOX_V2#2026-08-11T11:00:00Z#event-1'
    const { POST } = await import('@/app/api/session/[sessionId]/seen/route')
    const request = new NextRequest(
      'http://localhost/api/session/session-1/seen',
      {
        method: 'POST',
        body: JSON.stringify({ seenThroughCursor: cursor }),
        headers: { 'Content-Type': 'application/json' },
      },
    )

    const response = await POST(request, {
      params: Promise.resolve({ sessionId: 'session-1' }),
    })

    expect(response.status).toBe(200)
    expect(mocks.markSessionAttentionSeen).toHaveBeenCalledWith(
      'user-1',
      'session-1',
      cursor,
    )
  })
})
