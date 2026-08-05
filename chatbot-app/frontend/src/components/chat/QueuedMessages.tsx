"use client"

/**
 * Queued turns shown above the composer.
 *
 * Deliberately reuses the attachment-chip treatment from FilePreview
 * (rounded-lg / border-border/60 / bg-muted/50, lucide icon at h-4 w-4 in
 * text-muted-foreground, X button on hover) so a queued message reads as the
 * same class of object as a pending attachment rather than a new UI concept.
 */

import React from "react"
import { motion } from "framer-motion"
import { Clock3, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { QueuedMessage, QueueHoldReason } from "@/hooks/useMessageQueue"

const HOLD_MESSAGE: Record<QueueHoldReason, string> = {
  error: "The last turn ended with an error.",
  stopped: "You stopped the last turn.",
  interrupt: "The last turn is waiting for your approval.",
  oauth: "The last turn is waiting for authorization.",
}

interface QueuedMessagesProps {
  queue: QueuedMessage[]
  holdReason: QueueHoldReason | null
  onRemove: (id: string) => void
  onSendNow: () => void
  onDiscardAll: () => void
}

export function QueuedMessages({
  queue,
  holdReason,
  onRemove,
  onSendNow,
  onDiscardAll,
}: QueuedMessagesProps) {
  if (queue.length === 0) return null

  return (
    <div className="mx-auto px-4 w-full md:max-w-4xl mb-2">
      {holdReason && (
        <div className="flex items-center justify-between gap-3 mb-2 px-3 py-2 rounded-lg border border-amber-500/40 bg-amber-500/5 text-sm">
          <span className="text-muted-foreground">
            {HOLD_MESSAGE[holdReason]}{" "}
            {queue.length === 1 ? "1 message is" : `${queue.length} messages are`} still queued.
          </span>
          <div className="flex items-center gap-1 shrink-0">
            <Button type="button" variant="ghost" size="sm" className="h-7 px-2" onClick={onSendNow}>
              Send
            </Button>
            {/* Says how many it drops: "Discard" next to a list of chips does
                not make clear whether it clears one or all of them. */}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-muted-foreground"
              onClick={onDiscardAll}
            >
              {queue.length === 1 ? "Discard" : `Discard all ${queue.length}`}
            </Button>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        {queue.map(message => (
          <motion.div
            key={message.id}
            layout
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border/60 bg-muted/50 text-sm"
          >
            <Clock3 className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="flex-1 truncate text-foreground/80">
              {message.text || `${message.files.length} attachment(s)`}
            </span>
            {message.files.length > 0 && message.text && (
              <span className="shrink-0 text-xs text-muted-foreground">
                +{message.files.length}
              </span>
            )}
            {/* Always visible: a hover-only control is unreachable on touch and
                reads as "no way to cancel this" everywhere else. Matches the
                attachment chips, which also keep their remove button on show. */}
            <button
              type="button"
              onClick={() => onRemove(message.id)}
              aria-label={`Remove queued message: ${message.text || 'attachments'}`}
              title="Remove from queue"
              className="shrink-0 flex h-6 w-6 items-center justify-center rounded-full border border-border/60 bg-background text-muted-foreground hover:bg-destructive hover:text-destructive-foreground hover:border-destructive transition-colors"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </motion.div>
        ))}
      </div>
    </div>
  )
}
