'use client'

import { useEffect, useState, useRef } from 'react'
import { fetchAuthSession } from 'aws-amplify/auth'

/**
 * OAuth 3LO callback page (AgentCore Identity session binding).
 *
 * AgentCore redirects here after the user consents at the provider, appending:
 *   - session_id: the Identity authorization session URI, needed for
 *     CompleteResourceTokenAuth
 *   - our customState — the elicitation ID the backend generated when it
 *     requested the authorization URL. AWS documents that customState is
 *     "sent back to the callback URL" but does not name the parameter, so we
 *     read the plausible spellings and log the rest.
 *
 * Everything needed to complete the flow arrives in the URL, so this page
 * works regardless of window.opener or storage partitioning in the popup.
 * The BFF verifies the caller's Cognito session and elicitation ownership
 * before completing — that is the "user verification" step the AgentCore
 * session-binding flow requires.
 */

const STATE_PARAM_ALIASES = ['state', 'customState', 'custom_state']
export default function OAuthCompletePage() {
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [message, setMessage] = useState('Completing authorization...')
  const hasRun = useRef(false)

  useEffect(() => {
    if (hasRun.current) return
    hasRun.current = true

    const urlParams = new URLSearchParams(window.location.search)
    const oauthSessionUri = urlParams.get('session_id')
    const elicitationId = STATE_PARAM_ALIASES.map(p => urlParams.get(p)).find(Boolean)

    console.log(
      `[OAuth] Callback received, session_id: ${oauthSessionUri}, ` +
      `elicitation: ${elicitationId}, params: ${[...urlParams.keys()].join(',')}`
    )

    if (!oauthSessionUri) {
      setStatus('error')
      setMessage('No session_id found in URL. Please try the authorization again.')
      return
    }

    if (!elicitationId) {
      setStatus('error')
      setMessage('No authorization state found in URL. Start the authorization from the chat again.')
      return
    }

    const signalCompletion = async () => {
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
          body: JSON.stringify({ elicitationId, oauthSessionUri }),
        })
        if (!response.ok) {
          throw new Error(`OAuth completion failed (${response.status})`)
        }
        return true
      } catch (e) {
        console.error('[OAuth] Failed to signal via BFF:', e)
        setStatus('error')
        setMessage('Could not verify the authorization session. Please try again.')
        return false
      }
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
