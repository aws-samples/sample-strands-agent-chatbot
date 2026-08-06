import { describe, expect, it } from 'vitest'

import { groupChatMessages } from '@/lib/chat-message-groups'
import type { Message } from '@/types/chat'

function bot(id: string, startsNewAssistantTurn = false): Message {
  return {
    id,
    text: id,
    sender: 'bot',
    timestamp: 'now',
    startsNewAssistantTurn,
  }
}

describe('groupChatMessages', () => {
  it('keeps consecutive assistant messages in one turn by default', () => {
    const groups = groupChatMessages([bot('started'), bot('follow-up')])

    expect(groups).toHaveLength(1)
    expect(groups[0].messages.map(message => message.id)).toEqual([
      'started',
      'follow-up',
    ])
  })

  it('starts a separate assistant turn for background delivery', () => {
    const groups = groupChatMessages([
      bot('research-started'),
      bot('research-complete', true),
    ])

    expect(groups).toHaveLength(2)
    expect(groups.map(group => group.messages[0].id)).toEqual([
      'research-started',
      'research-complete',
    ])
  })
})
