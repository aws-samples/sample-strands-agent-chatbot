---
name: powerpoint-presentations
description: Create, inspect, edit, and validate professional PowerPoint presentations (.pptx), including uploaded source decks and templates, with design-system extraction, OOXML-preserving edits, structural linting, and rendered visual QA.
---

# PowerPoint Presentations

Use a staged workflow: **classify → inspect → plan → execute → validate → render → iterate**.
Do not skip source verification or QA.

## 1. Classify the Request

Choose exactly one path:

| Path | Use when | Primary tools |
|---|---|---|
| Edit source | Modify an uploaded or existing deck | `inspect_presentation`, `begin_presentation_edit`, edit tools |
| Build from template | Create content in an uploaded branded deck | `begin_presentation_edit`, `duplicate_slide`, `update_slide_content` |
| Create new | No source/template must be preserved | `create_presentation` |
| Analyze | Review content, design, or structure without editing | `inspect_presentation`, preview tools |

Never use `create_presentation` for an edit or template request. It creates a new
package and cannot preserve the source master, layouts, relationships, notes, or
embedded assets.

## 2. Verify and Inspect the Source

For edit and template paths:

1. Call `list_my_powerpoint_presentations`.
2. Confirm the exact source filename exists.
3. Call `inspect_presentation` with `persist_spec=true`.
4. Call `preview_presentation_montage` for a deck-level visual pass.
5. Call `analyze_presentation` only for slides that require detailed element IDs.

If the source cannot be loaded, stop and report the missing file. Do not recreate
the deck from a description, silently switch to a new-deck workflow, or delegate
the task to an environment where the source file has not been verified.

Treat the persisted deck spec as the design-system record across turns. After
inspection, call `begin_presentation_edit` once and retain its `edit_id`.
Re-run `inspect_presentation` with that `edit_id` after structural changes; do
not rely on conversation memory for fonts, colors, layouts, or slide structure.

## 3. Plan Before Editing

Define:

- Audience and decision or outcome
- Narrative arc and one message per slide
- Functional slide type for each slide
- Content-density budget
- Source slides/layouts to preserve or duplicate
- Slides that require new visuals or data

For substantive creation, write a compact slide plan before generating code or
mutating the package. Read [workflow-guide.md](workflow-guide.md) for the planning
schema and route-specific procedure.

## 4. Establish the Design System

For source/template work, derive tokens from `deck_spec`: slide size, theme colors,
theme fonts, explicit fonts, layout names, and repeated visual motifs. Preserve
those tokens unless the user explicitly requests a redesign.

For a new deck, define one compact design system before creating slides. Read
[design-guide.md](design-guide.md). Use [pptxgenjs.md](pptxgenjs.md) only for
new-deck implementation details.

## 5. Execute Conservatively

### Edit an Existing Deck

1. Call `begin_presentation_edit` once and retain its `edit_id`.
2. Analyze target slides.
3. Build one complete operation batch.
4. Call `update_slide_content` with the same `edit_id`.
5. Use `find` and `replace` for `replace_text`.

Unknown actions, empty find strings, duplicate slide batches, and unmatched text
are errors. Correct the operation rather than substituting another workflow.
Read [editing-guide.md](editing-guide.md) before editing.

### Build from a Template

Prefer duplicating a representative source slide for each functional slide type.
This best preserves placeholder geometry, visual chrome, and relationships.

1. Inspect the template and identify representative slides.
2. Duplicate the closest representative slide.
3. Replace its text/images in one batch.
4. Reorder slides.
5. Delete unused example slides only after the working deck is complete.

Use `add_slide` only when a suitable layout exists and a blank layout-based slide
is genuinely required. Never delete all examples before identifying which slide
types and layouts must be preserved.

### Create a New Deck

Use `create_presentation` with PptxGenJS. Define shared constants and helper
functions in each slide snippet as needed; option objects must not be reused
across calls because PptxGenJS mutates them.

## 6. Validate and Render

After every mutation:

1. Call `validate_presentation` with `presentation_name=edit_id`.
2. Fix structural errors before any further work.
3. Review warnings for bounds, overlap, overflow risk, placeholders, and fonts.
4. Call `preview_presentation_montage` for the full-deck pass.
5. Call `preview_presentation_slides` only for affected slides at higher detail.
6. Fix issues and repeat validation plus targeted rendering.
7. Call `finalize_presentation_edit` once with the desired output name.

LibreOffice rendering is an approximation of Microsoft PowerPoint. Missing fonts
or complex Office features require final inspection in PowerPoint when available.
Read [qa-guide.md](qa-guide.md) for the acceptance criteria.

Do not declare success until:

- The package has zero structural errors.
- No unresolved placeholder or out-of-bounds warnings remain.
- Every changed slide has been rendered after its final edit.
- A final montage shows coherent content, design, and narrative flow.

## Non-Negotiable Rules

- Preserve the uploaded source; edit only the hidden draft returned by
  `begin_presentation_edit`.
- Reuse one `edit_id` for the complete job. Never create `v2`, `v3`, or similar
  intermediate presentation files.
- Publish only once with `finalize_presentation_edit`.
- Batch related edits; conditional draft writes reject stale concurrent updates.
- Use 0-based slide indices for edit tools and 1-based numbers for preview tools.
- Use letters, numbers, and hyphens in presentation names.
- Do not use a generated approximation when the request requires source fidelity.
- Do not treat successful tool execution as proof of visual correctness.
