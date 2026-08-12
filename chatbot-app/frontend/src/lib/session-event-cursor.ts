export const SESSION_EVENT_CURSOR_PREFIX = 'OUTBOX_V2#'

export function sessionEventCursor(event: {
  createdAt: string
  eventId: string
}): string {
  return `${SESSION_EVENT_CURSOR_PREFIX}${event.createdAt}#${event.eventId}`
}
