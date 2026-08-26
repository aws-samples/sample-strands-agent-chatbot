# PowerPoint QA

Run structural checks before visual checks. Treat tool success as an intermediate
state, not acceptance.

## Structural Gate

Call `validate_presentation` and require:

- Required OOXML parts exist.
- XML parses successfully.
- Internal relationship targets resolve.
- No element extends beyond slide bounds.
- No unresolved placeholder text remains.

Review rather than blindly suppress:

- Text-box overlap warnings
- Text-overflow estimates
- Missing fonts and possible substitution

Heuristics can produce false positives, but every warning needs a visual decision.

## Visual Gate

Use `preview_presentation_montage` first. Check:

- Narrative and visual rhythm across slides
- Consistent title, footer, and grid placement
- Repeated slide functions use consistent geometry
- Density and whitespace remain balanced
- Charts, images, and labels are legible at montage scale

Then use `preview_presentation_slides` for changed or suspicious slides. Check:

- Clipping, wrapping, and overflow
- Text or shape collisions
- Incorrect crop or stretched imagery
- Low contrast and font substitution
- Misalignment and inconsistent gaps
- Missing labels, sources, or units

## Content Gate

- Every slide has one clear message.
- Titles state takeaways when appropriate.
- Numbers, units, dates, and sources are consistent.
- No example, prompt, placeholder, or stale template content remains.
- Speaker notes and hidden assumptions match the visible slide.

## Compatibility

LibreOffice previews are approximate. Complex SmartArt, charts, animations,
embedded objects, and unavailable fonts may differ in Microsoft PowerPoint.
When those features matter, retain them through source-preserving edits and
perform a final PowerPoint-open check outside this renderer.

## Completion

Complete at least one inspect/fix/re-render cycle for substantive changes.
Validate and render the hidden draft, then publish exactly one result with
`finalize_presentation_edit`. Identify any compatibility warning that could not
be resolved in the available environment.
