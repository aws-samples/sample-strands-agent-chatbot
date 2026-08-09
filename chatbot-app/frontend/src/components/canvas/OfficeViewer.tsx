"use client"

import React, { useState, useEffect } from 'react'
import { Loader2, AlertCircle } from 'lucide-react'

interface OfficeViewerProps {
  s3Url?: string  // s3://bucket/path/file.docx
  previewUrl?: string
  filename: string
}

/**
 * Office document viewer using Microsoft Office Online.
 */
export function OfficeViewer({ s3Url, previewUrl, filename }: OfficeViewerProps) {
  const [viewerUrl, setViewerUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const loadDocument = async () => {
      setLoading(true)
      setError(null)

      try {
        if (previewUrl) {
          const officeViewerUrl = `https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(previewUrl)}`
          setViewerUrl(officeViewerUrl)
          return
        }

        if (!s3Url?.startsWith('s3://')) {
          throw new Error(`Invalid S3 URL format: ${s3Url}`)
        }

        const ext = s3Url.split('.').pop()?.toLowerCase()
        let docUrl: string

        const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'

        if (ext === 'xlsx' || isLocal) {
          // Excel or local dev: presigned URL (Office Online can't reach localhost)
          const response = await fetch('/api/s3/presigned-url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ s3Key: s3Url })
          })
          if (!response.ok) {
            const errorData = await response.json().catch(() => ({}))
            throw new Error(errorData.error || 'Failed to generate presigned URL')
          }
          const { url } = await response.json()
          docUrl = url
        } else {
          // Word/PPT in production: proxy avoids X-Amz-* param issues
          docUrl = `${window.location.origin}/api/s3/proxy?key=${encodeURIComponent(s3Url)}`
        }

        // Build Office Online viewer URL
        const officeViewerUrl = `https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(docUrl)}`
        setViewerUrl(officeViewerUrl)
      } catch (err) {
        console.error('[OfficeViewer] Error:', err)
        setError(err instanceof Error ? err.message : 'Failed to load document')
      } finally {
        setLoading(false)
      }
    }

    if (s3Url || previewUrl) {
      loadDocument()
    }
  }, [previewUrl, s3Url])

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-sidebar-foreground/60">
        <Loader2 className="h-8 w-8 animate-spin" />
        <p className="text-label">Loading document...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-sidebar-foreground/60">
        <AlertCircle className="h-8 w-8 text-destructive" />
        <p className="text-label">{error}</p>
      </div>
    )
  }

  return (
    <div className="h-full">
      {viewerUrl && (
        <iframe
          src={viewerUrl}
          className="w-full h-full border-0"
          title={`Preview: ${filename}`}
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads"
        />
      )}
    </div>
  )
}

/**
 * Check if content is an Office file S3 URL
 */
export function isOfficeFileUrl(content: string): boolean {
  if (!content || typeof content !== 'string') return false
  return content.startsWith('s3://') && /\.(docx|xlsx|pptx)$/i.test(content)
}

/**
 * Check if content is specifically a Word document S3 URL
 */
export function isWordFileUrl(content: string): boolean {
  if (!content || typeof content !== 'string') return false
  return content.startsWith('s3://') && /\.docx$/i.test(content)
}

/**
 * Extract filename from S3 URL
 */
export function getFilenameFromS3Url(s3Url: string): string {
  if (!s3Url) return 'document'
  const parts = s3Url.split('/')
  return parts[parts.length - 1] || 'document'
}
