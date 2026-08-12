'use client';

import React, { useMemo } from 'react';
import { Trash2 } from 'lucide-react';
import { ChatSession } from '@/hooks/useChatSessions';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

interface ChatSessionListProps {
  sessions: ChatSession[];
  currentSessionId: string | null;
  isLoading: boolean;
  onLoadSession?: (sessionId: string) => Promise<void>;
  onDeleteSession: (sessionId: string) => Promise<void>;
}

interface DateGroup {
  label: string;
  sessions: ChatSession[];
}

function groupSessionsByDate(sessions: ChatSession[]): DateGroup[] {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const weekAgo = new Date(today);
  weekAgo.setDate(weekAgo.getDate() - 7);
  const monthAgo = new Date(today);
  monthAgo.setDate(monthAgo.getDate() - 30);

  const groups: Record<string, ChatSession[]> = {
    Today: [],
    Yesterday: [],
    'Last 7 days': [],
    'Last 30 days': [],
    Older: [],
  };

  for (const session of sessions) {
    const date = new Date(
      session.lastActivityAt || session.lastMessageAt || session.createdAt,
    );
    if (date >= today) {
      groups['Today'].push(session);
    } else if (date >= yesterday) {
      groups['Yesterday'].push(session);
    } else if (date >= weekAgo) {
      groups['Last 7 days'].push(session);
    } else if (date >= monthAgo) {
      groups['Last 30 days'].push(session);
    } else {
      groups['Older'].push(session);
    }
  }

  return Object.entries(groups)
    .filter(([, s]) => s.length > 0)
    .map(([label, s]) => ({ label, sessions: s }));
}

const MAX_DISPLAY_LENGTH = 28;

function truncateTitle(title: string): { display: string; isTruncated: boolean } {
  if (title.length <= MAX_DISPLAY_LENGTH) return { display: title, isTruncated: false };
  return { display: title.slice(0, MAX_DISPLAY_LENGTH) + '...', isTruncated: true };
}

export function ChatSessionList({
  sessions,
  currentSessionId,
  isLoading,
  onLoadSession,
  onDeleteSession,
}: ChatSessionListProps) {
  const dateGroups = useMemo(() => groupSessionsByDate(sessions), [sessions]);

  const handleDeleteSession = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await onDeleteSession(sessionId);
    } catch (error) {
      alert('Failed to delete session. Please try again.');
    }
  };

  if (isLoading) {
    return (
      <div className="px-2">
        <div className="text-center py-8 text-sidebar-foreground/60">
          <p className="text-[15px]">Loading...</p>
        </div>
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="px-2">
        <div className="py-4 text-sidebar-foreground/50">
          <p className="text-[15px] px-2">No conversations yet</p>
        </div>
      </div>
    );
  }

  return (
    <TooltipProvider delayDuration={600}>
      <div className="px-2">
        {dateGroups.map((group) => (
          <div key={group.label} className="mb-3">
            <div className="px-3 py-2">
              <span className="text-[11px] font-medium text-sidebar-foreground/45">
                {group.label}
              </span>
            </div>
            <div className="space-y-1">
              {group.sessions.map((session) => {
                const isCurrentSession = session.sessionId === currentSessionId;
                const showUnseen =
                  session.hasUnseenUpdate === true && !isCurrentSession;
                const { display, isTruncated } = truncateTitle(session.title);

                const row = (
                  <div
                    className={`group/session flex items-center gap-2 py-2.5 px-3 rounded-md hover:bg-sidebar-accent/70 transition-colors cursor-pointer ${
                      isCurrentSession ? 'bg-sidebar-accent text-sidebar-accent-foreground' : ''
                    }`}
                    onClick={() => {
                      if (onLoadSession) {
                        onLoadSession(session.sessionId);
                      }
                    }}
                  >
                    <span className="text-[13px] text-sidebar-foreground leading-snug flex-1 min-w-0">
                      {display}
                    </span>
                    <span className="relative h-5 w-5 flex-shrink-0">
                      {showUnseen && (
                        <span
                          className="absolute inset-0 flex items-center justify-center opacity-100 group-hover/session:opacity-0 transition-opacity"
                          role="status"
                          aria-label="New activity"
                          title="New activity"
                        >
                          <span className="h-1.5 w-1.5 rounded-full bg-sky-500 dark:bg-sky-400" />
                        </span>
                      )}
                      <button
                        onClick={(e) => handleDeleteSession(session.sessionId, e)}
                        className="absolute inset-0 flex items-center justify-center opacity-0 group-hover/session:opacity-100 transition-opacity rounded-sm hover:bg-destructive/10 text-sidebar-foreground/40 hover:text-destructive"
                        title="Delete"
                        aria-label={`Delete ${session.title}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </span>
                  </div>
                );

                if (!isTruncated) {
                  return <React.Fragment key={session.sessionId}>{row}</React.Fragment>;
                }

                return (
                  <Tooltip key={session.sessionId}>
                    <TooltipTrigger asChild>
                      {row}
                    </TooltipTrigger>
                    <TooltipContent
                      side="bottom"
                      align="start"
                      className="max-w-[280px] text-[12px]"
                      sideOffset={4}
                    >
                      <p>{session.title}</p>
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </TooltipProvider>
  );
}
