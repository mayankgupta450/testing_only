# Gate jali designs

Eight laser-cut jali designs for the front gate: the top transom panel, its two
square side jalis, and the two tall side panels on the gate leaves.

| Folder | Style |
|---|---|
| `1-geometric-jaali` | Mughal/Rajasthani eight-point star lattice |
| `2-lotus-mandala` | Central lotus mandala, lotus buds, rosette chain on side panels |
| `3-modern-minimal` | Flowing wave slots and slim vertical fins |
| `4-tree-of-life-peacock` | Tree of life, peacock feathers, leaf vine on side panels |
| `5-simple-frame-diamond` | Thin broken border line with small diamonds (simple) |
| `6-simple-thin-lines` | Thin parallel lines with a small centre ring (simple) |
| `7-simple-dot-screen` | Perforated dots that grow toward the centre (simple) |
| `8-simple-leaf-sprig` | One delicate leaf branch per panel (simple) |

Each folder has one SVG per panel (`transom`, `square_left`, `square_right`,
`side_left`, `side_right`). Black fill = cut-out, outline = panel edge. The
files keep each panel's proportions, not real size: scale each SVG to the
measured opening before cutting. The right side panel leaves a clear circle
for the existing round emblem. Every file has been checked to have no loose
metal pieces.

Regenerate with `python3 make_jali.py SOURCE_PHOTO OUTPUT_DIR` (needs
`pillow`, `numpy`, `shapely`). Photo mockups are written next to the SVGs as
`mockup.jpg` and are not committed.
