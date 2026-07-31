"use client"

import { useState } from "react"
import { ExternalLink, KeyRound } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { PendingOAuthState } from "@/types/events"

interface OAuthElicitationDialogProps {
  oauth: PendingOAuthState
  sessionId: string
  onCancel: () => void
}

export function OAuthElicitationDialog({
  oauth,
  sessionId,
  onCancel,
}: OAuthElicitationDialogProps) {
  const [popupBlocked, setPopupBlocked] = useState(false)

  const openAuthorization = () => {
    setPopupBlocked(false)
    localStorage.setItem("oauth_pending", JSON.stringify({
      sessionId,
      elicitationId: oauth.elicitationId,
    }))

    const popup = window.open(
      oauth.authUrl,
      "oauth_popup",
      "width=500,height=700,scrollbars=yes,resizable=yes"
    )
    if (popup) {
      popup.focus()
    } else {
      setPopupBlocked(true)
    }
  }

  return (
    <Dialog open onOpenChange={() => {}}>
      <DialogContent className="max-w-md mx-4">
        <DialogHeader>
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-md bg-primary/10 text-primary">
              <KeyRound className="size-5" />
            </div>
            <div>
              <DialogTitle>Connect {oauth.serviceName}</DialogTitle>
              <DialogDescription>
                Authorization is required to continue this request.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {popupBlocked && (
          <p role="alert" className="text-sm text-destructive">
            The authorization window was blocked. Allow popups for this site and try again.
          </p>
        )}

        <div className="flex items-center justify-end gap-2 pt-2">
          <Button variant="outline" size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button size="sm" onClick={openAuthorization}>
            <ExternalLink />
            Continue
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
