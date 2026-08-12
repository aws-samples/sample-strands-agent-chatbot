---
name: workspace
description: Read and write files in the shared session workspace. Use this to access files created by any skill — code-agent outputs, office documents, images, and more. All within the same isolated session.
---

# Workspace

Provides unified read/write access to all files in the current session. The `userId` and `sessionId` are injected automatically — you only specify the logical path.

## Path Conventions

| Prefix | What it accesses |
|--------|-----------------|
| `uploads/<file>` | Files uploaded by the user |
| `code-agent/<file>` | Files created by the code agent (auto-synced) |
| `code-interpreter/<file>` | Files created in the Code Interpreter workspace |
| `documents/powerpoint/<file>` | PowerPoint presentations |
| `documents/word/<file>` | Word documents |
| `documents/excel/<file>` | Excel spreadsheets |
| `documents/image/<file>` | Images from other tools |

> **Note:** Files written under `/mnt/workspace` persist for the chat session,
> and user uploads are available under `/mnt/workspace/inputs`. Code Agent
> downloads session uploads into its own working directory when delegated.

## Usage

**See everything in the session:**
```
workspace_list()
workspace_list("uploads/")
workspace_list("code-agent/")
```

**Read a file the code agent created:**
```
workspace_read("uploads/data.jsonl")           # uploaded structured data
workspace_read("code-agent/calculator.png")   # binary → base64
workspace_read("code-agent/report.md")        # text → string
```

**Pass a file from one skill to another:**
```
result = workspace_read("documents/excel/data.xlsx")   # encoding: base64
workspace_write("code-agent/data.xlsx", result["content"], encoding="base64")
```

## Notes

- Text files return `encoding: "text"` with plain string content. Large text
  reads are bounded; when `truncated` is true, use Code Interpreter for the
  complete file instead of repeatedly reading it into model context. Code
  Interpreter uses `/mnt/workspace/inputs/<file>`.
- JSON, JSONL, and NDJSON uploads are text files; large chat attachments may
  be truncated in model context, but the complete file remains under `uploads/`
- Binary files (images, Office docs, PDF, etc.) return `encoding: "base64"`
- `workspace_write` accepts both encodings — use `"base64"` for binary
- Files written here are immediately available to all other skills in the session

## UI Guidance (from tools-config)

**Path conventions (userId/sessionId injected automatically):**
- `uploads/<file>` — files uploaded by the user
- `code-agent/<file>` — files from the code agent
- `documents/powerpoint/<file>` — PowerPoint
- `documents/word/<file>` — Word
- `documents/excel/<file>` — Excel
- `documents/image/<file>` — images

**Binary vs text:** images/PDFs/Office files are returned base64-encoded (`encoding: "base64"`); use the same encoding when writing them back.
