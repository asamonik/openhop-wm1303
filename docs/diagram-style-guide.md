# Diagram Style Guide

> Visual design standards for all OpenHop WM1303 diagrams and illustrations.
> Based on the WM1303 Manager UI look & feel.

## Color Palette

| Role | Hex | RGB | Usage |
|------|-----|-----|-------|
| Background | `#0f172a` | 15, 23, 42 | Diagram background |
| Card/Box fill | `#1e293b` | 30, 41, 59 | Primary boxes/containers |
| Card border | `#334155` | 51, 65, 85 | Box borders |
| Inner card | `#283548` | 40, 53, 72 | Nested/inner containers |
| Primary accent | `#6366f1` | 99, 102, 241 | Primary highlights, headers, key components |
| Secondary accent | `#8b5cf6` | 139, 92, 246 | Gradient endpoints, secondary highlights |
| Success/OK | `#22c55e` | 34, 197, 94 | Active states, success indicators |
| Error | `#ef4444` | 239, 68, 68 | Error states, critical items |
| Warning | `#f59e0b` | 245, 158, 11 | Warnings, attention items |
| Text primary | `#f8fafc` | 248, 250, 252 | Main labels and titles |
| Text secondary | `#94a3b8` | 148, 163, 184 | Subtitles, descriptions |
| Text muted | `#64748b` | 100, 116, 139 | Annotations, minor labels |
| Info/Light indigo | `#818cf8` | 129, 140, 248 | RX indicators, info badges |
| Teal/TX | `#34d399` | 52, 211, 153 | TX indicators |
| Pink/Channel | `#f472b6` | 244, 114, 182 | Channel indicators |
| Orange | `#fb923c` | 251, 146, 60 | Free/inactive, spectral scan |
| Connector/Arrow | `#6366f1` | 99, 102, 241 | Primary arrows and connectors |
| Connector alt | `#818cf8` | 129, 140, 248 | Secondary/data-flow arrows |

## Layer Colors (Architecture Diagram)

| Layer | Fill | Border | Label Color |
|-------|------|--------|-------------|
| Hardware | `#1a1a2e` | `#6366f1` | `#818cf8` |
| HAL & Forwarder | `#1a2332` | `#8b5cf6` | `#a78bfa` |
| Backend | `#1a2e1a` | `#22c55e` | `#34d399` |
| Web / API | `#2e1a2e` | `#f472b6` | `#f472b6` |

## Typography

| Element | Font | Size | Weight | Color |
|---------|------|------|--------|-------|
| Layer title | Sans-serif (Inter/DejaVu Sans) | 14pt | Bold | Layer label color |
| Component name | Sans-serif | 11pt | Semi-bold | `#f8fafc` |
| Description | Sans-serif | 9pt | Regular | `#94a3b8` |
| Annotation | Sans-serif | 8pt | Regular | `#64748b` |
| Monospace labels | Monospace (DejaVu Sans Mono) | 9pt | Regular | `#94a3b8` |

## Box Styles

- **Outer containers**: Rounded rectangles (12px radius), `#1e293b` fill, `#334155` border (1.5px)
- **Inner components**: Rounded rectangles (8px radius), `#283548` fill, layer-colored border (1px)
- **Highlight boxes**: Same as inner but with layer accent color fill at 20% opacity
- **Shadow**: Subtle drop shadow (2px offset, 6px blur, black at 30% opacity)

## Arrows & Connectors

- **Primary flow**: `#6366f1`, 2px width, filled arrowhead
- **Data flow**: `#818cf8`, 1.5px width, open arrowhead
- **Secondary/feedback**: `#64748b`, 1px width, dashed
- **Bidirectional**: Double arrowheads

## General Rules

1. **Background**: Always `#0f172a` (dark navy)
2. **Contrast**: Ensure all text has minimum 4.5:1 contrast ratio against its background
3. **Spacing**: Minimum 16px padding inside boxes, 12px between elements
4. **Alignment**: Prefer center alignment for labels within boxes
5. **Consistency**: Use the same box style for components at the same abstraction level
6. **Image format**: PNG at 2x resolution (200 DPI) for crisp display
7. **Image dimensions**: Landscape orientation, max 1600px wide at 1x
8. **No specific channel names**: Use generic Channel A, B, C, D in all diagrams
9. **No IP addresses or credentials**: Keep diagrams generic

## File Naming Convention

```
docs/images/
├── architecture-overview.png
├── component-dependencies.png
├── data-flow-rx.png
├── data-flow-tx.png
└── spectral-scan-flow.png
```

---

*This style guide ensures visual consistency across all diagrams in the OpenHop WM1303 documentation.*
