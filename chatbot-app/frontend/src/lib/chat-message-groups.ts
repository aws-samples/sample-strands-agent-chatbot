import type { Message } from '@/types/chat'

export interface GroupedChatMessage {
  type: 'user' | 'assistant_turn'
  messages: Message[]
  id: string
}

export function groupChatMessages(messages: Message[]): GroupedChatMessage[] {
  const grouped: GroupedChatMessage[] = []
  let currentAssistantTurn: Message[] = []

  const flushAssistantTurn = () => {
    if (currentAssistantTurn.length === 0) return
    grouped.push({
      type: 'assistant_turn',
      messages: currentAssistantTurn,
      id: `turn_${currentAssistantTurn[0].id}`,
    })
    currentAssistantTurn = []
  }

  for (const message of messages) {
    if (message.sender === 'user') {
      flushAssistantTurn()
      grouped.push({
        type: 'user',
        messages: [message],
        id: `user_${message.id}`,
      })
      continue
    }

    if (message.startsNewAssistantTurn) flushAssistantTurn()
    currentAssistantTurn.push(message)
  }

  flushAssistantTurn()
  return grouped
}
