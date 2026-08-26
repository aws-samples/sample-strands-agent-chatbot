"use client"

import React from 'react'

/**
 * Extract domain from URL (removes www. prefix)
 */
export const getDomain = (url: string): string => {
  try {
    const hostname = new URL(url).hostname
    return hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/**
 * Citation chip CSS classes for external links
 */
export const citationChipClasses =
  "inline-flex items-center gap-1 px-2 py-0.5 mx-0.5 text-caption bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 rounded-full hover:bg-blue-100 dark:hover:bg-blue-900 hover:text-blue-700 dark:hover:text-blue-300 no-underline transition-colors"

/**
 * External link icon SVG component
 */
export const ExternalLinkIcon = ({ className = "w-3 h-3 flex-shrink-0" }: { className?: string }) => (
  <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
    />
  </svg>
)

interface CitationLinkProps {
  href?: string
  children?: React.ReactNode
  className?: string
  showDomain?: boolean // If true, show domain instead of children
  [key: string]: any
}

/**
 * Citation link component - renders external links as domain chips
 * For use in ReactMarkdown components prop
 */
export const CitationLink = ({
  href,
  children,
  className,
  showDomain = true,
  ...props
}: CitationLinkProps) => {
  if (!href) {
    return <span className={className}>{children}</span>
  }

  const domain = href ? getDomain(href) : ''
  const isExternalLink = href?.startsWith('http')

  // External links: show as domain chip
  if (isExternalLink && domain) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={className || citationChipClasses}
        title={href}
        {...props}
      >
        <ExternalLinkIcon />
        <span className="truncate max-w-[150px]">
          {showDomain ? domain : children}
        </span>
      </a>
    )
  }

  // Internal/anchor links: simple underlined style
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-600 dark:text-blue-400 hover:underline"
      {...props}
    >
      {children}
    </a>
  )
}

/**
 * ReactMarkdown components object for citation styling
 * Usage: <ReactMarkdown components={citationComponents}>...</ReactMarkdown>
 */
export const citationComponents = {
  a: ({ node, children, href, ...props }: any) => (
    <CitationLink href={href} {...props}>
      {children}
    </CitationLink>
  ),
}

/**
 * PDF/Print CSS for citation chips
 * Use in print stylesheets
 */
export const citationPrintCSS = `
  /* Citation chip style for external links */
  a[href^="http"] {
    display: inline-flex !important;
    align-items: center !important;
    gap: 4px !important;
    padding: 3px 10px !important;
    margin: 2px 3px !important;
    background: #f1f5f9 !important;
    color: #475569 !important;
    border-radius: 14px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    text-decoration: none !important;
    white-space: nowrap !important;
    line-height: 1.4 !important;
    vertical-align: middle !important;
  }
  a[href^="http"]::before {
    content: "↗ " !important;
    font-size: 11px !important;
    flex-shrink: 0 !important;
  }
  /* Regular links */
  a:not([href^="http"]) { color: #2563eb; text-decoration: underline; }
  @media print {
    a[href^="http"] {
      background: #f1f5f9 !important;
      print-color-adjust: exact !important;
      -webkit-print-color-adjust: exact !important;
    }
    a[href^="http"]::after { content: none !important; }
  }
`
