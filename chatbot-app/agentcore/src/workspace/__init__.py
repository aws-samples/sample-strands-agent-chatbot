"""
Workspace Module - Unified document and image storage management

This module provides a centralized workspace for managing documents and images
across all tools. All document types (Word, Excel, PowerPoint, Images) are stored
in a single S3 bucket with proper isolation per user/session.

## Quick Start

```python
from workspace import WordManager, ExcelManager, PowerPointManager, ImageManager

# Initialize a manager for the current user/session
doc_manager = WordManager(user_id="user123", session_id="session456")

# Save document to S3
doc_manager.save_to_s3("report.docx", file_bytes)

# Load document from S3
file_bytes = doc_manager.load_from_s3("report.docx")

# List all documents
documents = doc_manager.list_s3_documents()

# Upload to Code Interpreter
doc_manager.upload_to_code_interpreter(code_interpreter, "report.docx", file_bytes)

# Load workspace images to Code Interpreter (for document generation)
doc_manager.load_workspace_images_to_ci(code_interpreter)
```

## Architecture

```
workspace/
├── config.py          # Bucket configuration
├── base_manager.py    # BaseDocumentManager (common functionality)
├── managers.py        # Specific managers (Word, Excel, PPT, Image)
└── __init__.py        # Public API (this file)
```

## Storage Structure

```
S3: ARTIFACT_BUCKET
├── documents/{user_id}/{session_id}/
│   ├── word/*.docx
│   ├── excel/*.xlsx
│   └── image/*.png, *.jpg, etc.
└── code-interpreter-workspace/{workspace_id}/
    ├── inputs/*.pptx
    ├── artifacts/powerpoint/*.pptx
    └── .drafts/powerpoint/*
```

## Key Features

- **Unified Storage**: All document types in one bucket
- **Session Isolation**: Each user/session gets isolated workspace
- **S3 + Code Interpreter Sync**: Seamless integration
- **Type Safety**: Validation for each document type
- **Image Sharing**: Images accessible across all document tools
"""

from .config import get_workspace_bucket, WorkspaceConfig
from .base_manager import BaseDocumentManager
from .managers import (
    WordManager,
    ExcelManager,
    PowerPointManager,
    ImageManager,
    ZipManager,
    # Backward compatibility aliases
    WordDocumentManager,
    ExcelDocumentManager,
    PowerPointDocumentManager,
    ImageDocumentManager
)

__all__ = [
    # Configuration
    'get_workspace_bucket',
    'WorkspaceConfig',

    # Base class
    'BaseDocumentManager',

    # Modern naming (recommended)
    'WordManager',
    'ExcelManager',
    'PowerPointManager',
    'ImageManager',
    'ZipManager',

    # Legacy naming (backward compatibility)
    'WordDocumentManager',
    'ExcelDocumentManager',
    'PowerPointDocumentManager',
    'ImageDocumentManager',
]

__version__ = '1.0.0'
