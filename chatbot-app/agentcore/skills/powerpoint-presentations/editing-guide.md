# Source-Preserving PowerPoint Editing

Read this reference for uploaded decks and templates.

## Preservation Contract

- Verify the exact source with `list_my_powerpoint_presentations`.
- Derive and persist its deck spec with `inspect_presentation`.
- Never replace an edit request with a newly generated approximation.
- Never overwrite the source filename.
- Call `begin_presentation_edit` once and mutate only its hidden draft.
- Preserve masters, layouts, themes, relationships, notes, and unaffected elements.
- Fail when an operation cannot be applied exactly.

## Atomic Edit Procedure

1. Call `begin_presentation_edit` and retain the returned `edit_id`.
2. Call `analyze_presentation` for each target slide.
3. Record the target element IDs and current text/image types.
4. Build all related operations as one batch.
5. Call `update_slide_content` with the same `edit_id`.
6. Call `validate_presentation` with `presentation_name=edit_id`.
7. Render the affected slides using the same `edit_id`.
8. Call `finalize_presentation_edit` once.

```json
{
  "edit_id": "edit-0123456789abcdef01234567",
  "slide_updates": [
    {
      "slide_index": 0,
      "operations": [
        {
          "action": "replace_text",
          "element_id": 2,
          "find": "Q3",
          "replace": "Q4"
        }
      ]
    }
  ]
}
```

## Operations

| Action | Required fields | Behavior |
|---|---|---|
| `set_text` | `element_id`, `text` | Replace all text while retaining the shape's base formatting |
| `replace_text` | `element_id`, `find`, `replace` | Replace exact text, including text split across runs |
| `replace_image` | `element_id`, `image_name` | Replace the media behind an existing picture shape |

`replace_text` uses `find` and `replace`, not `old_text` and `new_text`. An empty
`find` or a string that does not exist is an error.

## Template Procedure

1. Inspect the template and montage.
2. Map required slide functions to representative template slides.
3. Duplicate representatives before deleting anything.
4. Replace content while retaining the source geometry.
5. Reorder the completed slides.
6. Delete unused examples.
7. Validate and render the complete deck.

Prefer duplication over approximate reconstruction. Use `add_slide` only when the
template has a suitable blank layout and no representative slide is appropriate.

## Unsupported Structural Changes

The atomic edit API updates existing text and pictures. If a requested edit needs
new arbitrary shapes, charts, or geometry:

1. First determine whether a representative source slide can be duplicated.
2. If not, state the limitation instead of silently recreating the entire deck.
3. Use a new-deck PptxGenJS workflow only when the user accepts loss of source fidelity.

## Version Discipline

Do not create visible intermediate versions. One source has one hidden draft:

`deck.pptx` (immutable input) → `edit-…` (hidden draft) → `deck-revised.pptx`
(published output)

Every mutation uses the same `edit_id`. S3 conditional writes reject a stale
draft update rather than silently losing work. `finalize_presentation_edit`
publishes the only user-visible result and removes the draft.
