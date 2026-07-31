---
name: gmail
description: Read, send, draft, delete, and organize Gmail emails
---

# Gmail

## Available Tools

- **list_labels()**: List all Gmail labels (system: INBOX, SENT, TRASH, SPAM, DRAFT, IMPORTANT, STARRED, UNREAD; plus user-created labels).

- **list_emails(label?, max_results?, include_spam_trash?)**: List emails by label.
  - `label` (string, optional, default: "INBOX"): Label to filter by (INBOX, SENT, DRAFT, TRASH, SPAM, STARRED, IMPORTANT, or custom label ID)
  - `max_results` (integer, optional, default: 10, max: 100): Number of emails to return
  - `include_spam_trash` (boolean, optional, default: false): Include SPAM and TRASH

- **search_emails(query, max_results?)**: Search Gmail using query syntax.
  - `query` (string, required): Gmail search query (from:, to:, subject:, is:unread, has:attachment, after:, before:, older_than:, newer_than:)
  - `max_results` (integer, optional, default: 10, max: 50): Number of results

- **read_email(message_id)**: Read a full email including body and attachments.
  - `message_id` (string, required): Gmail message ID (from list_emails or search_emails)

- **send_email(to, subject, body, cc?, bcc?, reply_to?, in_reply_to?, html_body?)**: Send an email.
  - `to` (string, required): Recipient(s), comma-separated
  - `subject` (string, required): Email subject
  - `body` (string, required): Plain text email body
  - `cc` (string, optional): CC recipient(s)
  - `bcc` (string, optional): BCC recipient(s)
  - `reply_to` (string, optional): Reply-To address
  - `in_reply_to` (string, optional): Message-ID to reply to (for threading)
  - `html_body` (string, optional): HTML version of email body

- **draft_email(to, subject, body, cc?, bcc?, reply_to?, in_reply_to?, html_body?)**: Create a draft for later editing/sending. Same parameters as `send_email`.

- **delete_email(message_id, permanent?)**: Delete an email.
  - `message_id` (string, required): Gmail message ID
  - `permanent` (boolean, optional, default: false): If true, permanently delete; if false, move to Trash

- **bulk_delete_emails(query, reason, max_delete?)**: Permanently delete multiple emails by query. **Cannot be undone.**
  - `query` (string, required): Gmail search query to find emails
  - `reason` (string, required): Human-readable explanation (shown to user for approval)
  - `max_delete` (integer, optional, default: 50, max: 100): Maximum emails to delete

- **modify_email(message_id, add_labels?, remove_labels?)**: Add/remove labels on an email.
  - `message_id` (string, required): Gmail message ID
  - `add_labels` (string, optional): Comma-separated label IDs to add (e.g., "STARRED,IMPORTANT")
  - `remove_labels` (string, optional): Comma-separated label IDs to remove (e.g., "UNREAD,INBOX")

- **get_email_thread(thread_id)**: Get all messages in an email thread/conversation.
  - `thread_id` (string, required): Gmail thread ID (from read_email or search_emails)

## Usage Guidelines

- Call `list_emails` directly for standard labels such as INBOX, SENT, DRAFT,
  TRASH, SPAM, STARRED, and IMPORTANT. Use `list_labels` only when the user
  asks for a custom label or the label name is unknown.
- Gmail search query syntax: `from:sender`, `to:recipient`, `subject:text`, `is:unread`, `has:attachment`, `after:2024/01/01`, `before:2024/12/31`.
- Use `get_email_thread` to view entire conversation context before replying.

## Common Operations

Label operations via `modify_email`:

| Operation | Parameters |
|-----------|-----------|
| Mark as read | `remove_labels="UNREAD"` |
| Mark as unread | `add_labels="UNREAD"` |
| Archive | `remove_labels="INBOX"` |
| Star | `add_labels="STARRED"` |
| Unstar | `remove_labels="STARRED"` |
| Mark important | `add_labels="IMPORTANT"` |

## Bulk Deletion

`bulk_delete_emails(query, reason)` **permanently** deletes all emails matching a Gmail query. This cannot be undone.

- Always provide a `reason` parameter explaining the intent.
- Always explain what will be deleted and get user confirmation before calling.

Common queries:
- Spam cleanup: `"is:spam older_than:7d"`
- Old unread: `"in:inbox is:unread older_than:14d"`
- Promotions purge: `"category:promotions older_than:30d"`
- From specific sender: `"from:noreply@example.com older_than:30d"`

## UI Guidance (from tools-config)

**Gmail Tool Usage:**
- list_labels: Discover custom labels when the requested label is unknown
- list_emails: Browse emails by label (INBOX, SENT, DRAFT, TRASH, etc.)
- search_emails: Use Gmail query syntax (from:, to:, subject:, is:unread, has:attachment, after:, before:)
- read_email: Get full email content with attachments
- send_email: Send emails with optional CC, BCC, and HTML body
- draft_email: Create drafts for later editing/sending
- delete_email: Move to trash (permanent=True for permanent deletion)
- bulk_delete_emails: Permanently delete multiple emails by query (requires user approval)
- modify_email: Add/remove labels (mark read: remove UNREAD, archive: remove INBOX, star: add STARRED)
- get_email_thread: View entire conversation thread

**Bulk Deletion (bulk_delete_emails):**
- Use for spam cleanup, old email purge, etc.
- Always provide 'reason' parameter explaining the intent
- Common queries: "is:spam older_than:7d", "in:inbox is:unread older_than:14d", "category:promotions older_than:30d"
- WARNING: Permanently deletes emails - cannot be recovered

**Common Operations:**
- Mark as read: modify_email(id, remove_labels="UNREAD")
- Archive: modify_email(id, remove_labels="INBOX")
- Star: modify_email(id, add_labels="STARRED")
- Mark important: modify_email(id, add_labels="IMPORTANT")
