# Presentation Design System

Read this reference when designing a new deck or intentionally redesigning one.
For source-preserving edits, derive these values from `inspect_presentation`.

## Define Tokens First

Create a compact design record before generating slides:

```json
{
  "audience": "executive leadership",
  "objective": "approve the launch plan",
  "slide_size": "LAYOUT_WIDE",
  "colors": {
    "background": "F7F8FA",
    "surface": "FFFFFF",
    "text": "18212B",
    "muted": "5B6773",
    "accent": "007A78",
    "signal": "D1495B"
  },
  "fonts": {
    "heading": "Aptos Display",
    "body": "Aptos"
  },
  "spacing": {
    "edge": 0.55,
    "gap": 0.3
  },
  "motif": "thin vertical section marker"
}
```

Use one dominant neutral, readable text, one primary accent, and at most one
semantic signal color. Do not select a palette from a generic theme table without
considering the subject, audience, source assets, and rendering environment.

## Design by Slide Function

Assign each slide one function and one message:

| Function | Suitable composition |
|---|---|
| Opening | Literal title, restrained visual, minimal metadata |
| Context | Annotated image, map, timeline, or compact evidence |
| Argument | Claim plus two or three supporting proof points |
| Data | One chart with a takeaway title and direct labels |
| Comparison | Aligned columns with a consistent comparison basis |
| Process | Ordered stages with clear direction and ownership |
| Decision | Options, criteria, recommendation, and consequence |
| Closing | Decision/request and next action |

Vary composition when the content requires it, not merely to avoid repetition.
Repeated slide functions should use consistent geometry.

## Density Budgets

- Use one message per slide.
- Prefer a takeaway sentence over a topic label as the title.
- Keep body copy readable at presentation distance.
- Split a slide when text must be reduced below the deck's established body size.
- Use charts for quantitative relationships, diagrams for systems, and images for
  concrete subjects. Decorative shapes do not count as evidence.

## Geometry

- Keep content at least 0.5 inches from the slide edge unless intentionally full bleed.
- Use stable grids and repeat exact alignment coordinates.
- Keep 0.25 to 0.5 inches between peer elements.
- Reserve space for source labels, footnotes, and page furniture.
- Crop images intentionally; never distort aspect ratio.

## Typography and Fonts

- Use fonts present in the target rendering environment.
- Use no more than one heading family and one body family.
- Establish a small type scale and reuse it.
- Left-align paragraphs; center only content designed for scanning as a unit.
- Do not use uniform auto-fit as a substitute for editing content.

## Data and Accessibility

- Encode critical distinctions with labels or shape as well as color.
- Use sufficient foreground/background contrast.
- Add meaningful alternative text to new images.
- Cite sources close to charts and claims.
- Avoid 3D charts, legends for a single series, and unnecessary gridlines.
