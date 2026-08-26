# PowerPoint Workflow

## Slide Plan Schema

Use this compact plan before execution:

```json
{
  "audience": "...",
  "objective": "...",
  "narrative": ["context", "evidence", "decision"],
  "slides": [
    {
      "number": 1,
      "function": "opening",
      "message": "...",
      "source_slide": null,
      "layout": "Title Slide",
      "visual": "product image",
      "content_budget": "title + subtitle"
    }
  ]
}
```

## Existing Deck

1. Verify the source exists.
2. Inspect and persist a deck spec.
3. Review a montage.
4. Identify only the slides requiring changes.
5. Analyze those slides for element IDs.
6. Open one hidden draft with `begin_presentation_edit`.
7. Apply atomic edit batches to the same `edit_id`.
8. Validate package and geometry using the `edit_id`.
9. Render affected slides and re-run the montage.
10. Publish one final file with `finalize_presentation_edit`.

## Template-Based Deck

1. Inspect the template before deleting slides.
2. Inventory functional slide types represented by examples.
3. Map the slide plan to representative examples.
4. Duplicate representative slides.
5. Replace text and images.
6. Reorder the completed narrative.
7. Delete unused examples.
8. Validate and render.

## New Deck

1. Confirm no source fidelity is required.
2. Write the slide plan.
3. Define the design tokens.
4. Choose masters or repeatable layout helpers.
5. Generate slides with PptxGenJS.
6. Validate the package and geometry.
7. Review the montage, then detailed slides.
8. Iterate only affected slides.

## Continuation Across Turns

Use the active `edit_id` as the source of truth. `begin_presentation_edit`
resumes the existing draft for an unchanged source across turns. Re-run
`inspect_presentation` with the edit ID after structural changes. Do not
reconstruct design decisions solely from conversation history.

## Failure Policy

Stop rather than changing workflows when:

- The requested source file is absent.
- The output would not preserve required source features.
- A text or image target cannot be matched exactly.
- Structural validation fails.
- A required font or renderer materially changes the intended output.

Report the failed precondition and the exact artifact or user decision needed.
