# PptxGenJS Reference

The `pres` instance (PptxGenJS, `LAYOUT_WIDE`) is already in scope. Start with `pres.addSlide()`.

> Layout dimensions for `LAYOUT_WIDE`: **13.3" × 7.5"**
> Other layouts: `LAYOUT_16x9` 10"×5.625" · `LAYOUT_16x10` 10"×6.25" · `LAYOUT_4x3` 10"×7.5"

## Presentation Setup

Set document metadata and theme before adding content:

```javascript
pres.author = "Organization";
pres.company = "Organization";
pres.subject = "Launch decision";
pres.title = "Product Launch Plan";
pres.lang = "en-US";
pres.theme = {
  headFontFace: "Aptos Display",
  bodyFontFace: "Aptos",
  lang: "en-US"
};
```

Use a slide master for repeated chrome and stable geometry. Define it before the
first `addSlide` call, then create slides by master name:

```javascript
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: "F7F8FA" },
  objects: [
    {
      rect: {
        x: 0, y: 0, w: 0.12, h: 7.5,
        fill: { color: "007A78" },
        line: { color: "007A78" }
      }
    },
    {
      text: {
        text: "CONFIDENTIAL",
        options: {
          x: 10.8, y: 7.08, w: 1.9, h: 0.2,
          fontFace: "Aptos", fontSize: 8, color: "5B6773",
          align: "right", margin: 0
        }
      }
    }
  ],
  slideNumber: {
    x: 12.75, y: 7.08, w: 0.25, h: 0.2,
    fontFace: "Aptos", fontSize: 8, color: "5B6773",
    align: "right", margin: 0
  }
});

let slide = pres.addSlide("CONTENT");
```

Create one master per repeated functional layout, not one master per slide.
Keep content in slide code and repeated visual furniture in masters.

---

## Text & Formatting

```javascript
// Basic text
slide.addText("Simple Text", {
  x: 1, y: 1, w: 8, h: 2, fontSize: 24, fontFace: "Arial",
  color: "363636", bold: true, align: "center", valign: "middle"
});

// Character spacing (use charSpacing, not letterSpacing — letterSpacing is silently ignored)
slide.addText("SPACED TEXT", { x: 1, y: 1, w: 8, h: 1, charSpacing: 6 });

// Rich text arrays (partial formatting)
slide.addText([
  { text: "Bold ", options: { bold: true } },
  { text: "Italic ", options: { italic: true } }
], { x: 1, y: 3, w: 8, h: 1 });

// Multi-line text (requires breakLine: true between lines)
slide.addText([
  { text: "Line 1", options: { breakLine: true } },
  { text: "Line 2", options: { breakLine: true } },
  { text: "Line 3" }  // Last item doesn't need breakLine
], { x: 0.5, y: 0.5, w: 8, h: 2 });

// Text box margin (internal padding)
// Set margin: 0 when aligning text precisely with shapes/icons at the same x-position
slide.addText("Title", { x: 0.5, y: 0.3, w: 9, h: 0.6, margin: 0 });
```

---

## Lists & Bullets

```javascript
// ✅ CORRECT: Multiple bullets using array
slide.addText([
  { text: "First item", options: { bullet: true, breakLine: true } },
  { text: "Second item", options: { bullet: true, breakLine: true } },
  { text: "Third item", options: { bullet: true } }
], { x: 0.5, y: 0.5, w: 8, h: 3 });

// ❌ WRONG: Never use unicode bullet characters
slide.addText("• First item", { ... });  // Creates double bullets

// Sub-items and numbered lists
{ text: "Sub-item", options: { bullet: true, indentLevel: 1 } }
{ text: "First", options: { bullet: { type: "number" }, breakLine: true } }
```

---

## Shapes

```javascript
// Rectangle
slide.addShape(pres.shapes.RECTANGLE, {
  x: 0.5, y: 0.8, w: 1.5, h: 3.0,
  fill: { color: "1E2761" }, line: { color: "000000", width: 2 }
});

// Oval / circle
slide.addShape(pres.shapes.OVAL, { x: 4, y: 1, w: 2, h: 2, fill: { color: "408EC6" } });

// Line with dash
slide.addShape(pres.shapes.LINE, {
  x: 1, y: 3, w: 5, h: 0, line: { color: "408EC6", width: 3, dashType: "dash" }
});

// Transparency
slide.addShape(pres.shapes.RECTANGLE, {
  x: 1, y: 1, w: 3, h: 2,
  fill: { color: "0088CC", transparency: 50 }
});

// Rounded rectangle
// ⚠️ Don't pair with rectangular accent overlays — they won't cover rounded corners. Use RECTANGLE instead.
slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
  x: 1, y: 1, w: 3, h: 2,
  fill: { color: "FFFFFF" }, rectRadius: 0.1
});

// With shadow
slide.addShape(pres.shapes.RECTANGLE, {
  x: 1, y: 1, w: 3, h: 2,
  fill: { color: "FFFFFF" },
  shadow: { type: "outer", color: "000000", blur: 6, offset: 2, angle: 135, opacity: 0.15 }
});
```

**Shadow options:**

| Property | Range | Notes |
|----------|-------|-------|
| `type` | `"outer"`, `"inner"` | |
| `color` | 6-char hex, no `#` | Never encode opacity in color string |
| `blur` | 0-100 pt | |
| `offset` | 0-200 pt | **Must be non-negative** |
| `angle` | 0-359 degrees | 135 = bottom-right, 270 = upward |
| `opacity` | 0.0-1.0 | |

To cast a shadow upward (e.g. footer bar), use `angle: 270` with positive offset — do **not** use negative offset.

> Gradient fills are not natively supported. Use a gradient image as slide background instead.

---

## Images

```javascript
// From file path
slide.addImage({ path: "images/chart.png", x: 1, y: 1, w: 5, h: 3 });

// From URL
slide.addImage({ path: "https://example.com/image.jpg", x: 1, y: 1, w: 5, h: 3 });

// From base64 (faster, no file I/O)
slide.addImage({ data: "image/png;base64,iVBORw0KGgo...", x: 1, y: 1, w: 5, h: 3 });

// Options
slide.addImage({
  path: "image.png",
  x: 1, y: 1, w: 5, h: 3,
  rotate: 45,              // 0-359 degrees
  rounding: true,          // Circular crop
  transparency: 50,        // 0-100
  flipH: true,             // Horizontal flip
  altText: "Description"
});

// Sizing modes
{ sizing: { type: 'contain', w: 4, h: 3 } }   // Fit inside, preserve ratio
{ sizing: { type: 'cover', w: 4, h: 3 } }     // Fill area, preserve ratio (may crop)
{ sizing: { type: 'crop', x: 0.5, y: 0.5, w: 2, h: 2 } }  // Cut specific portion
```

**Preserve aspect ratio:**
```javascript
const origWidth = 1978, origHeight = 923, maxHeight = 3.0;
const calcWidth = maxHeight * (origWidth / origHeight);
const centerX = (13.3 - calcWidth) / 2;  // LAYOUT_WIDE is 13.3" wide
slide.addImage({ path: "image.png", x: centerX, y: 1.2, w: calcWidth, h: maxHeight });
```

---

## Slide Backgrounds

```javascript
// Solid color
slide.background = { color: "1E2761" };

// Image
slide.background = { path: "https://example.com/bg.jpg" };
slide.background = { data: "image/png;base64,iVBORw0KGgo..." };
```

---

## Tables

```javascript
slide.addTable([
  ["Header 1", "Header 2"],
  ["Cell 1", "Cell 2"]
], {
  x: 1, y: 1, w: 8, h: 2,
  border: { pt: 1, color: "999999" }, fill: { color: "F1F1F1" }
});

// Advanced with merged cells and styled headers
let tableData = [
  [{ text: "Header", options: { fill: { color: "1E2761" }, color: "FFFFFF", bold: true } }, "Cell"],
  [{ text: "Merged", options: { colspan: 2 } }]
];
slide.addTable(tableData, { x: 1, y: 3.5, w: 8, colW: [4, 4] });
```

---

## Charts

```javascript
// Bar chart
slide.addChart(pres.charts.BAR, [{
  name: "Sales", labels: ["Q1", "Q2", "Q3", "Q4"], values: [4500, 5500, 6200, 7100]
}], { x: 0.5, y: 0.6, w: 6, h: 3, barDir: 'col', showTitle: true, title: 'Quarterly Sales' });

// Line chart
slide.addChart(pres.charts.LINE, [{
  name: "Trend", labels: ["Jan", "Feb", "Mar"], values: [32, 35, 42]
}], { x: 0.5, y: 4, w: 6, h: 3, lineSize: 3, lineSmooth: true });

// Pie chart
slide.addChart(pres.charts.PIE, [{
  name: "Share", labels: ["A", "B", "Other"], values: [35, 45, 20]
}], { x: 7, y: 1, w: 5, h: 4, showPercent: true });
```

**Modern, clean chart styling** — default charts look dated, apply these:

```javascript
slide.addChart(pres.charts.BAR, chartData, {
  x: 0.5, y: 1, w: 9, h: 4, barDir: "col",

  chartColors: ["0D9488", "14B8A6", "5EEAD4"],  // Match your palette
  chartArea: { fill: { color: "FFFFFF" }, roundedCorners: true },

  catAxisLabelColor: "64748B",   // Muted axis labels
  valAxisLabelColor: "64748B",

  valGridLine: { color: "E2E8F0", size: 0.5 },  // Subtle grid (value only)
  catGridLine: { style: "none" },

  showValue: true,
  dataLabelPosition: "outEnd",
  dataLabelColor: "1E293B",

  showLegend: false,  // Hide for single series
});
```

**Key chart options:** `chartColors`, `chartArea`, `catGridLine/valGridLine`, `lineSmooth`, `legendPos` ("b"/"t"/"l"/"r"/"tr")

Available chart types: BAR, LINE, PIE, DOUGHNUT, SCATTER, BUBBLE, RADAR

---

## Common Pitfalls

⚠️ These cause file corruption, visual bugs, or broken output.

1. **NEVER use "#" with hex colors** — corrupts file
   ```javascript
   color: "FF0000"   // ✅
   color: "#FF0000"  // ❌
   ```

2. **NEVER encode opacity in hex color strings** — 8-char hex corrupts the file
   ```javascript
   shadow: { color: "00000020" }                        // ❌ CORRUPTS FILE
   shadow: { color: "000000", opacity: 0.12 }           // ✅
   ```

3. **Use `bullet: true`**, never unicode "•" (creates double bullets)

4. **Use `breakLine: true`** between items in text arrays

5. **Avoid `lineSpacing` with bullets** — causes excessive gaps; use `paraSpaceAfter` instead

6. **NEVER reuse option objects across calls** — PptxGenJS mutates objects in-place (converts values to EMU). Sharing one object between multiple addText/addShape calls corrupts the second shape.
   ```javascript
   // ❌ Wrong: shadow object gets mutated after first call
   const shadow = { type: "outer", blur: 6, offset: 2, color: "000000", opacity: 0.15 };
   slide.addShape(pres.shapes.RECTANGLE, { x:1, y:1, w:3, h:2, fill:{color:"FFF"}, shadow });
   slide.addShape(pres.shapes.RECTANGLE, { x:5, y:1, w:3, h:2, fill:{color:"FFF"}, shadow });

   // ✅ Correct: factory function creates fresh object each time
   const makeShadow = () => ({ type: "outer", blur: 6, offset: 2, color: "000000", opacity: 0.15 });
   slide.addShape(pres.shapes.RECTANGLE, { x:1, y:1, w:3, h:2, fill:{color:"FFF"}, shadow: makeShadow() });
   slide.addShape(pres.shapes.RECTANGLE, { x:5, y:1, w:3, h:2, fill:{color:"FFF"}, shadow: makeShadow() });
   ```

7. **Don't use `ROUNDED_RECTANGLE` with accent borders** — rectangular overlay bars won't cover rounded corners
   ```javascript
   // ❌ Accent bar doesn't cover rounded corners
   slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:1, y:1, w:3, h:1.5, fill:{color:"FFFFFF"} });
   slide.addShape(pres.shapes.RECTANGLE, { x:1, y:1, w:0.08, h:1.5, fill:{color:"0891B2"} });

   // ✅ Use RECTANGLE for clean alignment
   slide.addShape(pres.shapes.RECTANGLE, { x:1, y:1, w:3, h:1.5, fill:{color:"FFFFFF"} });
   slide.addShape(pres.shapes.RECTANGLE, { x:1, y:1, w:0.08, h:1.5, fill:{color:"0891B2"} });
   ```

---

## Spatial Reference (LAYOUT_WIDE: 13.3" × 7.5")

Use these as mental anchors when placing elements. All coordinates are in inches.

### Slide zones

```
┌─────────────────────────────────────────┐  y=0
│  safe margin top: 0.4"                  │
│  ┌───────────────────────────────────┐  │  y=0.4
│  │  Title zone    (~0.5–1.2" tall)   │  │
│  ├───────────────────────────────────┤  │  y=1.2–1.5
│  │                                   │  │
│  │  Content zone  (~4.5–5.5" tall)   │  │
│  │                                   │  │
│  └───────────────────────────────────┘  │  y=6.8–7.1
│  safe margin bottom: 0.4"              │
└─────────────────────────────────────────┘  y=7.5
  x=0                                    x=13.3
  safe margin left/right: 0.5–0.6"
```

### Font size → visual height

| Font size | Text box height needed | Typical use |
|-----------|----------------------|-------------|
| 44pt bold | ~0.7" | Main title |
| 32–36pt bold | ~0.55" | Section title |
| 24pt bold | ~0.42" | Card header / subtitle |
| 18pt | ~0.32" | Large body |
| 16pt | ~0.28" | Normal body |
| 14pt | ~0.25" | Small body |
| 11–12pt | ~0.20" | Caption / footnote |

Add **0.1–0.15" padding** to the h value to avoid clipping. Multi-line text multiplies height linearly.

### Horizontal space budget

At common font sizes, approximate characters per line for a given width:

| Width | 16pt | 20pt | 24pt |
|-------|------|------|------|
| 4" | ~55 chars | ~44 chars | ~36 chars |
| 6" | ~82 chars | ~66 chars | ~55 chars |
| 8" | ~110 chars | ~88 chars | ~73 chars |
| 11" | ~150 chars | ~120 chars | ~100 chars |

These are rough estimates (varies by font face). Wrap generously — narrow boxes cause unexpected wrapping.

### Common layout patterns (inches)

```javascript
// Full-width title bar (left of chrome)
{ x: 0.6, y: 0.35, w: 11.5, h: 0.75 }   // 44pt title

// Two-column split
// Left column:  x:0.5,  w:5.9
// Right column: x:6.9,  w:5.9

// Three-column split
// Col 1: x:0.5,  w:3.7
// Col 2: x:4.8,  w:3.7
// Col 3: x:9.1,  w:3.7

// 2×2 grid (cards)
// Top-left:     x:0.5,  y:1.5, w:5.9, h:2.3
// Top-right:    x:6.9,  y:1.5, w:5.9, h:2.3
// Bottom-left:  x:0.5,  y:4.1, w:5.9, h:2.3
// Bottom-right: x:6.9,  y:4.1, w:5.9, h:2.3

// Large stat callout
{ x: 1.0, y: 1.8, w: 5.0, h: 2.2 }   // 72pt number + 14pt label below

// Bottom accent bar (above chrome bar)
{ x: 0, y: 7.1, w: 13.3, h: 0.4 }

// Left side stripe (chrome)
{ x: 0, y: 0, w: 0.15, h: 7.5 }
```

### Gaps and spacing rules

- **Slide edge → first element**: 0.5" minimum (use 0.6" for comfort)
- **Between sibling elements**: 0.2–0.3" minimum; 0.4–0.5" for breathing room
- **Title bottom → content top**: 0.3–0.5"
- **Content bottom → slide edge / bar**: 0.3" minimum
- **Card internal padding** (text inside a card shape): 0.2" on all sides → offset text box by 0.2" from shape edges

---

## Quick Reference

- **Shapes**: `pres.shapes.RECTANGLE`, `OVAL`, `LINE`, `ROUNDED_RECTANGLE`
- **Charts**: `pres.charts.BAR`, `LINE`, `PIE`, `DOUGHNUT`, `SCATTER`, `BUBBLE`, `RADAR`
- **Alignment**: `"left"`, `"center"`, `"right"`
- **Data label positions**: `"outEnd"`, `"inEnd"`, `"center"`
