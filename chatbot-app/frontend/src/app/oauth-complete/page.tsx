'use client'

import { useEffect, useState, useRef } from 'react'
import { fetchAuthSession } from 'aws-amplify/auth'

export default function OAuthCompletePage() {
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [message, setMessage] = useState('Completing authorization...')
  const hasRun = useRef(false)

  useEffect(() => {
    if (hasRun.current) return
    hasRun.current = true

    const urlParams = new URLSearchParams(window.location.search)
    const oauthSessionUri = urlParams.get('session_id')

    console.log(`[OAuth] Callback received, session_id: ${oauthSessionUri}`)

    if (!oauthSessionUri) {
      setStatus('error')
      setMessage('No session_id found in URL. Please try the authorization again.')
      return
    }

    // Read pending OAuth context saved by the parent window before opening this popup.
    // Cross-origin OAuth redirects null out window.opener, so we cannot rely on postMessage.
    let pending: { sessionId?: string; elicitationId?: string } = {}
    try {
      const raw = localStorage.getItem('oauth_pending')
      if (raw) {
        pending = JSON.parse(raw)
        localStorage.removeItem('oauth_pending')
      }
    } catch { /* ignore parse errors */ }

    const signalCompletion = async () => {
      if (!pending.sessionId || !pending.elicitationId) {
        setStatus('error')
        setMessage('The pending authorization request was not found. Please try again.')
        return false
      }

      try {
        const authSession = await fetchAuthSession()
        const accessToken = authSession.tokens?.accessToken?.toString()
        if (!accessToken) {
          throw new Error('Authenticated OAuth session context is missing')
        }

        const response = await fetch('/api/stream/elicitation-complete', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${accessToken}`,
          },
          body: JSON.stringify({
            sessionId: pending.sessionId,
            elicitationId: pending.elicitationId,
            oauthSessionUri,
          }),
        })
        if (!response.ok) {
          throw new Error(`OAuth completion failed (${response.status})`)
        }
      } catch (e) {
        console.error('[OAuth] Failed to signal via BFF:', e)
        setStatus('error')
        setMessage('Could not verify the authorization session. Please try again.')
        return false
      }

      if (window.opener && !window.opener.closed) {
        try {
          window.opener.postMessage(
            { type: 'oauth_elicitation_complete', sessionId: oauthSessionUri },
            window.location.origin
          )
        } catch (e) {
          console.warn('[OAuth] Could not notify parent window:', e)
        }
      }
      localStorage.setItem('oauth_completed', JSON.stringify({
        elicitationId: pending.elicitationId,
        completedAt: Date.now(),
      }))
      return true
    }

    signalCompletion().then((completed) => {
      if (!completed) return
      setStatus('success')
      setMessage('Authorization completed! This window will close automatically.')
      setTimeout(() => window.close(), 1500)
    })
  }, [])

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
      <div className="max-w-md w-full p-8 bg-white dark:bg-gray-800 rounded-lg shadow-lg text-center">
        {status === 'loading' && (
          <>
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4" />
            <h1 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">
              Completing Authorization
            </h1>
            <p className="text-gray-600 dark:text-gray-400">
              Please wait...
            </p>
          </>
        )}

        {status === 'success' && (
          <>
            <div className="text-green-500 text-5xl mb-4">&#10003;</div>
            <h1 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">
              Authorization Successful
            </h1>
            <p className="text-gray-600 dark:text-gray-400 mb-4">
              {message}
            </p>
            <button
              onClick={() => window.close()}
              className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
            >
              Close Window
            </button>
          </>
        )}

        {status === 'error' && (
          <>
            <div className="text-red-500 text-5xl mb-4">&#10005;</div>
            <h1 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">
              Authorization Failed
            </h1>
            <p className="text-red-600 dark:text-red-400 mb-4">
              {message}
            </p>
            <button
              onClick={() => window.close()}
              className="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors"
            >
              Close Window
            </button>
          </>
        )}
      </div>
    </div>
  )
}
