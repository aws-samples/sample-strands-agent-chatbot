"use client"

import React, { useEffect, useState } from "react"
import { motion } from "framer-motion"
import { FileIcon, FileText, Image as ImageIcon, X } from "lucide-react"

// Support both File objects and simple file info
interface FileInfo {
  name: string
  type: string
  size?: number
}

interface BaseFilePreviewProps {
  onRemove?: () => void
  compact?: boolean // For sent messages (smaller, no animation)
}

interface FilePreviewProps extends BaseFilePreviewProps {
  file: File
}

interface SentFilePreviewProps extends BaseFilePreviewProps {
  fileInfo: FileInfo
}

// Main FilePreview for input area (with File object)
export const FilePreview = React.forwardRef<HTMLDivElement, FilePreviewProps>(
  ({ file, onRemove, compact = false }, ref) => {
    if (file.type.startsWith("image/")) {
      return <ImageFilePreview file={file} onRemove={onRemove} compact={compact} ref={ref} />
    }

    if (
      file.type.startsWith("text/") ||
      file.name.endsWith(".txt") ||
      file.name.endsWith(".md")
    ) {
      return <TextFilePreview file={file} onRemove={onRemove} compact={compact} ref={ref} />
    }

    return <GenericFilePreview file={file} onRemove={onRemove} compact={compact} ref={ref} />
  }
)
FilePreview.displayName = "FilePreview"

// SentFilePreview for sent messages (with simple file info)
export const SentFilePreview = React.forwardRef<HTMLDivElement, SentFilePreviewProps>(
  ({ fileInfo, compact = true }, ref) => {
    const isImage = fileInfo.type.startsWith("image/")
    const isText = fileInfo.type.startsWith("text/") ||
                   fileInfo.name.endsWith(".txt") ||
                   fileInfo.name.endsWith(".md")
    const isPdf = fileInfo.type === "application/pdf"

    const getIcon = () => {
      if (isImage) return <ImageIcon className="h-4 w-4" />
      if (isPdf || isText) return <FileText className="h-4 w-4" />
      return <FileIcon className="h-4 w-4" />
    }

    const truncateName = (name: string, maxLength: number = 20) => {
      if (name.length <= maxLength) return name
      const ext = name.split('.').pop() || ''
      const baseName = name.slice(0, name.length - ext.length - 1)
      const truncatedBase = baseName.slice(0, maxLength - ext.length - 4)
      return `${truncatedBase}...${ext}`
    }

    return (
      <div
        ref={ref}
        className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border/60 bg-muted/50 text-sm"
      >
        <span className="text-muted-foreground">{getIcon()}</span>
        <span className="text-foreground/80 truncate max-w-[150px]">
          {truncateName(fileInfo.name)}
        </span>
      </div>
    )
  }
)
SentFilePreview.displayName = "SentFilePreview"

// Image preview (for File objects with actual image data)
const ImageFilePreview = React.forwardRef<HTMLDivElement, FilePreviewProps>(
  ({ file, onRemove, compact }, ref) => {
    const [objectUrl, setObjectUrl] = useState<string>("")

    useEffect(() => {
      const url = URL.createObjectURL(file)
      setObjectUrl(url)
      return () => URL.revokeObjectURL(url)
    }, [file])

    if (compact) {
      return (
        <div ref={ref} className="relative group rounded-lg overflow-hidden">
          {objectUrl && (
            <img
              alt={`Attachment ${file.name}`}
              className="h-16 w-16 object-cover rounded-lg border border-border"
              src={objectUrl}
            />
          )}
          <div className="absolute inset-x-0 bottom-0 bg-black/60 text-white text-[9px] px-1 py-0.5 truncate opacity-0 group-hover:opacity-100 transition-opacity">
            {file.name}
          </div>
        </div>
      )
    }

    return (
      <motion.div
        ref={ref}
        className="relative group rounded-lg overflow-hidden"
        layout
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.9 }}
      >
        {objectUrl && (
          <img
            alt={`Attachment ${file.name}`}
            className="h-20 w-20 object-cover rounded-lg border border-border"
            src={objectUrl}
          />
        )}
        <div className="absolute inset-x-0 bottom-0 bg-black/60 text-white text-[10px] px-1 py-0.5 truncate opacity-0 group-hover:opacity-100 transition-opacity">
          {file.name}
        </div>
        {onRemove && (
          <button
            className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full border bg-background shadow-xs hover:bg-destructive hover:text-destructive-foreground hover:border-destructive transition-colors"
            type="button"
            onClick={onRemove}
            aria-label="Remove attachment"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </motion.div>
    )
  }
)
ImageFilePreview.displayName = "ImageFilePreview"

// Text file preview
const TextFilePreview = React.forwardRef<HTMLDivElement, FilePreviewProps>(
  ({ file, onRemove, compact }, ref) => {
    const [preview, setPreview] = useState<string>("")

    useEffect(() => {
      const reader = new FileReader()
      reader.onload = (e) => {
        const text = e.target?.result as string
        setPreview(text.slice(0, 50) + (file.size > 50 ? "..." : ""))
      }
      reader.readAsText(file.slice(0, 64))
    }, [file])

    if (compact) {
      return (
        <div
          ref={ref}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border/60 bg-muted/50 text-sm"
        >
          <FileText className="h-4 w-4 text-muted-foreground" />
          <span className="text-foreground/80 truncate max-w-[150px]">{file.name}</span>
        </div>
      )
    }

    return (
      <motion.div
        ref={ref}
        className="relative flex max-w-[200px] rounded-lg border border-border bg-background p-2 text-sm"
        layout
        initial={{ opacity: 0, y: "100%" }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: "100%" }}
      >
        <div className="flex w-full items-center gap-2">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border bg-muted p-0.5">
            <div className="h-full w-full overflow-hidden text-[6px] leading-none text-muted-foreground">
              {preview || "Loading..."}
            </div>
          </div>
          <span className="flex-1 truncate text-muted-foreground">{file.name}</span>
        </div>
        {onRemove && (
          <button
            className="absolute -right-2 -top-2 flex h-5 w-5 items-center justify-center rounded-full border border-border bg-background hover:bg-destructive hover:text-destructive-foreground transition-colors"
            type="button"
            onClick={onRemove}
            aria-label="Remove attachment"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </motion.div>
    )
  }
)
TextFilePreview.displayName = "TextFilePreview"

// Generic file preview
const GenericFilePreview = React.forwardRef<HTMLDivElement, FilePreviewProps>(
  ({ file, onRemove, compact }, ref) => {
    if (compact) {
      return (
        <div
          ref={ref}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border/60 bg-muted/50 text-sm"
        >
          <FileIcon className="h-4 w-4 text-muted-foreground" />
          <span className="text-foreground/80 truncate max-w-[150px]">{file.name}</span>
        </div>
      )
    }

    return (
      <motion.div
        ref={ref}
        className="relative flex max-w-[200px] rounded-lg border border-border bg-background p-2 text-sm"
        layout
        initial={{ opacity: 0, y: "100%" }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: "100%" }}
      >
        <div className="flex w-full items-center gap-2">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-border bg-muted">
            <FileIcon className="h-5 w-5 text-muted-foreground" />
          </div>
          <span className="flex-1 truncate text-muted-foreground">{file.name}</span>
        </div>
        {onRemove && (
          <button
            className="absolute -right-2 -top-2 flex h-5 w-5 items-center justify-center rounded-full border border-border bg-background hover:bg-destructive hover:text-destructive-foreground transition-colors"
            type="button"
            onClick={onRemove}
            aria-label="Remove attachment"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </motion.div>
    )
  }
)
GenericFilePreview.displayName = "GenericFilePreview"
