import { describe, expect, it } from 'vitest'

import {
  isConversationEventVisible,
  type ConversationFence,
} from '@/lib/session-events'

function event(
  epoch: number | undefined,
  eventTime: string,
  timestampKey: 'eventTimestamp' | 'eventTime' = 'eventTimestamp',
) {
  return {
    [timestampKey]: eventTime,
    ...(epoch !== undefined && {
      metadata: {
        conversationEpoch: { stringValue: String(epoch) },
      },
    }),
  }
}

describe('conversation epoch fences', () => {
  it('preserves the old-epoch prefix and rejects stale suffix writes', () => {
    const fence: ConversationFence = {
      conversationEpoch: 1,
      cutoffByEpoch: {
        0: '2026-08-31T12:00:00.000Z',
      },
    }

    expect(isConversationEventVisible(
      event(0, '2026-08-31T11:59:59.000Z', 'eventTime'),
      fence,
    )).toBe(true)
    expect(isConversationEventVisible(
      event(0, '2026-08-31T12:00:00.000Z'),
      fence,
    )).toBe(false)
    expect(isConversationEventVisible(
      event(0, '2026-08-31T12:00:01.000Z'),
      fence,
    )).toBe(false)
    expect(isConversationEventVisible(
      event(1, '2026-08-31T12:00:02.000Z'),
      fence,
    )).toBe(true)
  })

  it('uses each epoch cutoff across repeated truncations', () => {
    const fence: ConversationFence = {
      conversationEpoch: 2,
      cutoffByEpoch: {
        0: '2026-08-31T12:00:00.000Z',
        1: '2026-08-31T13:00:00.000Z',
      },
    }

    expect(isConversationEventVisible(
      event(0, '2026-08-31T11:00:00.000Z'),
      fence,
    )).toBe(true)
    expect(isConversationEventVisible(
      event(0, '2026-08-31T12:30:00.000Z'),
      fence,
    )).toBe(false)
    expect(isConversationEventVisible(
      event(1, '2026-08-31T12:30:00.000Z'),
      fence,
    )).toBe(true)
    expect(isConversationEventVisible(
      event(1, '2026-08-31T13:30:00.000Z'),
      fence,
    )).toBe(false)
  })

  it('keeps legacy metadata-less events and migrates the latest old epoch', () => {
    const fence: ConversationFence = {
      conversationEpoch: 1,
      cutoffByEpoch: {},
      truncatedAt: '2026-08-31T12:00:00.000Z',
    }

    expect(isConversationEventVisible(
      event(undefined, '2026-08-31T13:00:00.000Z'),
      fence,
    )).toBe(true)
    expect(isConversationEventVisible(
      event(0, '2026-08-31T11:59:59.000Z'),
      fence,
    )).toBe(true)
    expect(isConversationEventVisible(
      event(0, '2026-08-31T12:00:01.000Z'),
      fence,
    )).toBe(false)
  })
})
