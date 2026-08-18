# PowerPoint Editing Guide

## Editing Existing Presentations

### Step 1: Analyze Structure

Always call `analyze_presentation` first to get element IDs and positions.

```json
{ "presentation_name": "my-deck", "slide_index": 0 }
```

The response includes:
- `element_id`: Unique identifier for each element (shape)
- `text`: Current text content
- `position`: Left, top, width, height in EMU (English Metric Units)
- `placeholder_idx`: Placeholder index (for template-based slides)

### Step 2: Build Update Operations

`update_slide_content` accepts a list of `slide_updates`, each targeting a specific slide:

```json
{
  "presentation_name": "my-deck",
  "output_name": "my-deck-v2",
  "slide_updates": [
    {
      "slide_index": 0,
      "operations": [
        {
          "action": "set_text",
          "element_id": 2,
          "text": "New Title"
        }
      ]
    }
  ]
}
```

### Available Actions

| Action | Required Fields | Description |
|--------|----------------|-------------|
| `set_text` | `element_id`, `text` | Replace all text in a shape |
| `replace_text` | `element_id`, `find`, `replace` | Replace specific text within a shape (preserves formatting) |
| `replace_image` | `element_id`, `image_path` | Replace an existing image with a new one |

### EMU Unit Reference

1 inch = 914400 EMU. Common slide dimensions (16:9):
- Slide width: 12192000 EMU (13.333 inches)
- Slide height: 6858000 EMU (7.5 inches)

### When editing actions aren't enough

If you need to add completely new visual elements or restructure a slide:
- Use `create_presentation` with PptxGenJS to build a new deck from scratch
- Use `add_slide` to append a blank slide, then `set_text` to populate existing placeholder shapes

## Batch Editing Rules

- **Always batch** all slide updates into a single `update_slide_content` call.
- Multiple slides can be updated in one call by adding multiple entries to `slide_updates`.
- **Never** call `update_slide_content` multiple times in sequence on the same file — the second call would overwrite the first.
- The `output_name` must differ from `presentation_name`. Use a versioning convention like `-v2`, `-v3`.

## Common Patterns

### Replace all text on a slide

```json
{
  "slide_updates": [
    {
      "slide_index": 0,
      "operations": [
        { "action": "set_text", "element_id": 2, "text": "Updated Title" },
        { "action": "set_text", "element_id": 3, "text": "Updated Subtitle" }
      ]
    }
  ]
}
```

### Replace specific text (preserve formatting)

```json
{
  "slide_updates": [
    {
      "slide_index": 1,
      "operations": [
        { "action": "replace_text", "element_id": 3, "find": "Q3", "replace": "Q4" }
      ]
    }
  ]
}
```
