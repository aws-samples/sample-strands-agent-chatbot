"use client"

import React, { useState, useRef, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Upload, Send, Square, Loader2, Mic, CornerDownLeft, Zap } from "lucide-react"
import { FilePreview } from "@/components/ui/file-preview"
import { AnimatePresence } from "framer-motion"
import { VoiceAnimation } from "@/components/VoiceAnimation"
import { ModelConfigDialog } from "@/components/ModelConfigDialog"
import { SlashCommandPopover } from "@/components/chat/SlashCommandPopover"
import { filterCommands, SlashCommand } from "@/components/chat/slashCommands"
import { AgentStatus } from "@/types/events"

interface ChatInputAreaProps {
  selectedFiles: File[]
  setSelectedFiles: React.Dispatch<React.SetStateAction<File[]>>
  agentStatus: AgentStatus
  isBusy: boolean
  isVoiceActive: boolean
  isVoiceSupported: boolean
  isCanvasOpen: boolean
  sessionId: string | null
  currentModelId?: string
  onModelChange?: (modelId: string) => void
  onSendMessage: (text: string, files: File[]) => Promise<void>
  /**
   * Queue a turn instead of sending it, used while the agent is busy. The
   * composer stays enabled so the user can keep typing during a long run.
   */
  onEnqueueMessage: (text: string, files: File[]) => void
  /** Concise response style: on while the toggle is lit. */
  conciseMode: boolean
  onToggleConciseMode: () => void
  onStopGeneration: () => void
  onConnectVoice: () => Promise<void>
  onDisconnectVoice: () => void
  onExportConversation: () => void
  onNewChat: () => Promise<void>
  onCompact: () => void
  prefillMessage?: string
  onPrefillConsumed?: () => void
}

export const LARGE_PASTE_ATTACHMENT_THRESHOLD = 20_000
export const MAX_PASTED_TEXT_BYTES = 1_000_000

const MAX_AUTO_RESIZE_CHARS = 4_000
const TEXTAREA_MAX_HEIGHT_PX = 128
const MAX_SLASH_COMMAND_LENGTH = 64

export function ChatInputArea({
  selectedFiles,
  setSelectedFiles,
  agentStatus,
  isBusy,
  isVoiceActive,
  isVoiceSupported,
  isCanvasOpen,
  sessionId,
  currentModelId,
  onModelChange,
  onSendMessage,
  onEnqueueMessage,
  conciseMode,
  onToggleConciseMode,
  onStopGeneration,
  onConnectVoice,
  onDisconnectVoice,
  onExportConversation,
  onNewChat,
  onCompact,
  prefillMessage,
  onPrefillConsumed,
}: ChatInputAreaProps) {
  const [inputMessage, setInputMessage] = useState('')
  const [inputError, setInputError] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isComposingRef = useRef(false)

  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>([])
  const [selectedCommandIndex, setSelectedCommandIndex] = useState(0)
  const [inputRect, setInputRect] = useState<DOMRect | null>(null)

  // Slash command autocomplete
  useEffect(() => {
    const isShortSingleLine =
      inputMessage.length <= MAX_SLASH_COMMAND_LENGTH &&
      !inputMessage.includes('\n')
    const trimmed = isShortSingleLine ? inputMessage.trim() : ''
    if (trimmed.startsWith('/')) {
      const filtered = filterCommands(trimmed)
      setSlashCommands(filtered)
      setSelectedCommandIndex(0)
      if (textareaRef.current) {
        setInputRect(textareaRef.current.getBoundingClientRect())
      }
    } else {
      setSlashCommands(current => current.length === 0 ? current : [])
    }
  }, [inputMessage])

  const closeSlashCommands = useCallback(() => {
    setSlashCommands(current => current.length === 0 ? current : [])
  }, [])

  const handleSlashCommand = useCallback((command: SlashCommand) => {
    closeSlashCommands()
    setInputMessage('')

    switch (command.name) {
      case '/export':
        onExportConversation()
        break
      case '/clear':
        onNewChat()
        break
      case '/compact':
        onCompact()
        break
    }
  }, [closeSlashCommands, onExportConversation, onNewChat, onCompact])

  // Single submit path shared by Enter, the form, and the send button.
  // While the agent is busy the composer stays open and the turn is queued
  // instead of sent; the parent decides when it is safe to dispatch it.
  const hasContent = /\S/.test(inputMessage) || selectedFiles.length > 0

  const submit = useCallback(() => {
    if (!hasContent || isVoiceActive) return
    // Slash commands are handled in handleKeyDown.
    if (/^\s*\//.test(inputMessage)) return

    if (isBusy) {
      onEnqueueMessage(inputMessage, selectedFiles)
    } else {
      void onSendMessage(inputMessage, selectedFiles)
    }
    setInputMessage('')
    setSelectedFiles([])
  }, [
    hasContent, isVoiceActive, inputMessage, selectedFiles, isBusy,
    onEnqueueMessage, onSendMessage, setSelectedFiles,
  ])

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])
    setInputError(null)
    setSelectedFiles(prev => [...prev, ...files])
    event.target.value = ""
  }

  const removeFile = (index: number) => {
    setSelectedFiles(prev => prev.filter((_, i) => i !== index))
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Handle slash commands navigation
    if (slashCommands.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSelectedCommandIndex(prev => prev < slashCommands.length - 1 ? prev + 1 : 0)
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setSelectedCommandIndex(prev => prev > 0 ? prev - 1 : slashCommands.length - 1)
        return
      }
      if (e.key === "Enter") {
        e.preventDefault()
        handleSlashCommand(slashCommands[selectedCommandIndex])
        return
      }
      if (e.key === "Escape") {
        e.preventDefault()
        closeSlashCommands()
        return
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      if (isComposingRef.current) return

      const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0
      if (isTouchDevice) return

      e.preventDefault()
      submit()
    }
  }

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items
    if (!items) return

    const imageFiles: File[] = []
    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) {
          const extension = item.type.split('/')[1] || 'png'
          const namedFile = new File([file], `clipboard-image-${Date.now()}.${extension}`, {
            type: file.type
          })
          imageFiles.push(namedFile)
        }
      }
    }

    if (imageFiles.length > 0) {
      e.preventDefault()
      setInputError(null)
      setSelectedFiles(prev => [...prev, ...imageFiles])
      return
    }

    const pastedText = e.clipboardData.getData('text/plain')
    if (pastedText.length >= LARGE_PASTE_ATTACHMENT_THRESHOLD) {
      e.preventDefault()
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
      const pastedFile = new File(
        [pastedText],
        `pasted-text-${timestamp}.txt`,
        { type: 'text/plain' },
      )
      if (pastedFile.size > MAX_PASTED_TEXT_BYTES) {
        setInputError('Pasted text exceeds the 1 MB limit. Split it into smaller sections.')
        return
      }
      setInputError(null)
      setSelectedFiles(prev => [...prev, pastedFile])
    }
  }

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    if (inputMessage.length > MAX_AUTO_RESIZE_CHARS) {
      textarea.style.height = `${TEXTAREA_MAX_HEIGHT_PX}px`
      return
    }

    const frameId = window.requestAnimationFrame(() => {
      textarea.style.height = "auto"
      const scrollHeight = textarea.scrollHeight
      textarea.style.height = `${Math.min(scrollHeight, TEXTAREA_MAX_HEIGHT_PX)}px`
    })
    return () => window.cancelAnimationFrame(frameId)
  }, [inputMessage])

  useEffect(() => {
    if (prefillMessage) {
      setInputMessage(prefillMessage)
      textareaRef.current?.focus()
      onPrefillConsumed?.()
    }
  }, [prefillMessage, onPrefillConsumed])

  return (
    <>
      {/* File Upload Preview */}
      {selectedFiles.length > 0 && (
        <div className="mx-auto px-4 w-full md:max-w-4xl mb-2">
          <div className="flex flex-wrap gap-2">
            <AnimatePresence>
              {selectedFiles.map((file, index) => (
                <FilePreview
                  key={`${file.name}-${index}`}
                  file={file}
                  onRemove={() => removeFile(index)}
                />
              ))}
            </AnimatePresence>
          </div>
        </div>
      )}

      {/* Input Area */}
      <div className="mx-auto px-4 pb-4 md:pb-6 w-full md:max-w-4xl">
        <div className={`chat-input-container bg-card rounded-xl p-3 border border-border shadow-[0_10px_30px_-24px_hsl(var(--foreground)/0.45)] ${
          ''
        }`}>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              submit()
            }}
          >
            <Input
              type="file"
              accept="image/*,application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.xlsx,application/vnd.openxmlformats-officedocument.presentationml.presentation,.pptx,.zip,application/zip,application/x-zip,application/x-zip-compressed,text/csv,.csv"
              multiple
              onChange={handleFileSelect}
              className="hidden"
              id="file-upload"
            />
            <div className="flex items-end gap-2">
              <Textarea
                ref={textareaRef}
                value={inputMessage}
                onChange={(e) => {
                  setInputError(null)
                  setInputMessage(e.target.value)
                }}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                onCompositionStart={() => { isComposingRef.current = true }}
                onCompositionEnd={() => { isComposingRef.current = false }}
                placeholder={
                  isVoiceActive
                    ? "Voice mode active - click mic to stop"
                    : isBusy
                    ? "Send a follow-up — it'll go next"
                    : "Ask me anything..."
                }
                className="flex-1 min-h-[52px] max-h-36 border-0 focus:ring-0 focus:ring-offset-0 ring-0 ring-offset-0 resize-none py-2 px-1 leading-relaxed overflow-y-auto bg-transparent transition-colors duration-200 placeholder:text-muted-foreground/60 shadow-none"
                // Stays enabled while the agent runs so follow-ups can be queued.
                // Voice mode still owns the composer exclusively.
                disabled={isVoiceActive}
                rows={1}
              />
              <div className="flex items-center gap-1.5 pb-1.5">
                {/* Voice Mode Button */}
                {isVoiceSupported && (agentStatus === 'idle' || isVoiceActive) && (
                  <TooltipProvider delayDuration={300}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={async () => {
                            if (!isVoiceActive) await onConnectVoice()
                            else onDisconnectVoice()
                          }}
                          className={`h-9 w-9 p-0 rounded-lg transition-colors duration-150 ${
                            agentStatus === 'voice_listening'
                              ? 'bg-red-500 hover:bg-red-600 text-white'
                              : agentStatus === 'voice_speaking'
                              ? 'bg-green-500 hover:bg-green-600 text-white'
                              : agentStatus === 'voice_connecting' || agentStatus === 'voice_processing'
                              ? 'bg-yellow-500 hover:bg-yellow-600 text-white'
                              : 'hover:bg-muted-foreground/10 text-muted-foreground'
                          }`}
                        >
                          {agentStatus === 'voice_connecting' ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : agentStatus === 'voice_listening' ? (
                            <VoiceAnimation type="listening" />
                          ) : agentStatus === 'voice_speaking' ? (
                            <VoiceAnimation type="speaking" />
                          ) : (
                            <Mic className="w-4 h-4" />
                          )}
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>
                        {!isVoiceActive
                          ? 'Start voice chat'
                          : agentStatus === 'voice_connecting'
                          ? 'Connecting...'
                          : agentStatus === 'voice_listening'
                          ? 'Listening... (click to stop)'
                          : agentStatus === 'voice_speaking'
                          ? 'Speaking... (click to stop)'
                          : 'Voice active (click to stop)'}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                )}

                {/* Queue / Send / Stop.
                    While the agent runs, the button only becomes "queue" once
                    there is something to queue — otherwise it stays Stop, so
                    stopping is never more than one click away. */}
                {isVoiceActive ? null : isBusy && !hasContent ? (
                  <Button
                    type="button"
                    onClick={onStopGeneration}
                    variant="ghost"
                    size="sm"
                    className="h-9 w-9 p-0 rounded-lg hover:bg-muted transition-colors duration-150"
                    title={agentStatus === 'stopping' ? "Stopping..." : agentStatus === 'compacting' ? "Compacting..." : "Stop generation"}
                    disabled={agentStatus === 'stopping' || agentStatus === 'compacting'}
                  >
                    {agentStatus === 'stopping' || agentStatus === 'compacting' ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Square className="w-4 h-4" />
                    )}
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    disabled={!hasContent}
                    size="sm"
                    title={isBusy ? "Queue this message" : "Send"}
                    className="h-9 w-9 p-0 bg-primary hover:bg-primary/90 text-primary-foreground rounded-lg transition-colors duration-150 disabled:opacity-40"
                  >
                    {isBusy ? <CornerDownLeft className="w-4 h-4" /> : <Send className="w-4 h-4" />}
                  </Button>
                )}
              </div>
            </div>
            {inputError && (
              <p role="alert" className="mt-2 text-caption text-destructive">
                {inputError}
              </p>
            )}
          </form>

          {/* Bottom Options Bar */}
          <div className="flex items-center justify-between mt-1 pt-1.5 border-t border-border/70">
            <TooltipProvider delayDuration={300}>
              <div className="flex items-center gap-1">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => document.getElementById("file-upload")?.click()}
                      disabled={isVoiceActive}
                      className="h-9 w-9 p-0 hover:bg-muted transition-colors duration-150 disabled:opacity-40 text-muted-foreground"
                    >
                      <Upload className="w-4 h-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>Upload files</p>
                  </TooltipContent>
                </Tooltip>

                {/* Concise mode. Icon-only like Upload beside it; the label
                    lives in the tooltip. When on, the button carries the
                    active state so the mode is visible without text. */}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={onToggleConciseMode}
                      disabled={isVoiceActive}
                      aria-pressed={conciseMode}
                      aria-label="Concise replies"
                      className={`h-9 w-9 p-0 rounded-md transition-colors duration-150 disabled:opacity-40 ${
                        conciseMode
                          ? 'bg-primary/10 text-primary hover:bg-primary/15'
                          : 'hover:bg-muted-foreground/10 text-muted-foreground'
                      }`}
                    >
                      <Zap className={`w-4 h-4 ${conciseMode ? 'fill-current' : ''}`} />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>{conciseMode ? 'Concise replies: on' : 'Concise replies'}</p>
                  </TooltipContent>
                </Tooltip>
              </div>
            </TooltipProvider>

            <div className="flex items-center">
              <ModelConfigDialog sessionId={sessionId} agentStatus={agentStatus} currentModelId={currentModelId} onModelChange={onModelChange} />
            </div>
          </div>
        </div>
      </div>

      {/* Slash Command Autocomplete */}
      {slashCommands.length > 0 && inputRect && (
        <SlashCommandPopover
          commands={slashCommands}
          selectedIndex={selectedCommandIndex}
          onSelect={handleSlashCommand}
          onClose={closeSlashCommands}
          anchorRect={inputRect}
        />
      )}
    </>
  )
}
