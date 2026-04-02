# Camera Arrangement

## Physical Layout

Each sensor module contains 8 cameras (OV2312) arranged in a 4×2 grid. Cameras are numbered 1–8 (or 0–7 in software, i.e., `channel = camera_number - 1`).

The layout counts **down** the left column (1→4) then **hooks back up** the right column (5→8):

```
┌───────────────────┐
│  Cam 1  │  Cam 8  │  ← top
│  Cam 2  │  Cam 7  │
│  Cam 3  │  Cam 6  │
│  Cam 4  │  Cam 5  │  ← bottom
└───────────────────┘
    left      right
   column     column
```

### Grid Position Mapping

| Camera (1-indexed) | Channel (0-indexed) | Grid Row | Grid Col |
|--------------------|---------------------|----------|----------|
| 1                  | 0                   | 0        | 0        |
| 2                  | 1                   | 1        | 0        |
| 3                  | 2                   | 2        | 0        |
| 4                  | 3                   | 3        | 0        |
| 5                  | 4                   | 3        | 1        |
| 6                  | 5                   | 2        | 1        |
| 7                  | 6                   | 1        | 1        |
| 8                  | 7                   | 0        | 1        |

## System Layout

The full system has two sensor modules: **left** and **right**. Together they provide up to 16 cameras.

- Left sensor: cameras 1–8 (channels 0–7)
- Right sensor: cameras 1–8 (channels 0–7)

## Named Camera Groups

Cameras are often referred to by group based on their position on the sensor:

| Group Name | Cameras (1-indexed) | Channels (0-indexed) | Position         |
|------------|---------------------|----------------------|------------------|
| Outer      | 1, 4, 5, 8          | 0, 3, 4, 7           | Four corners     |
| Inner      | 2, 3, 6, 7          | 1, 2, 5, 6           | Middle rows      |
| Left col   | 1, 2, 3, 4          | 0, 1, 2, 3           | Left column      |
| Right col  | 5, 6, 7, 8          | 4, 5, 6, 7           | Right column     |
| Top pair   | 1, 8                | 0, 7                 | Top row          |
| Bottom pair| 4, 5                | 3, 4                 | Bottom row       |

## Plot Grid Layout

Plots are arranged to mirror the physical sensor layout. The left sensor's plots occupy the left half of the grid; the right sensor's plots occupy the right half.

**Full layout (all 16 cameras active):**

```
┌────────────────────────────────────────────┐
│  LEFT SENSOR       │  RIGHT SENSOR         │
│  Cam1   Cam8       │  Cam1   Cam8          │
│  Cam2   Cam7       │  Cam2   Cam7          │
│  Cam3   Cam6       │  Cam3   Cam6          │
│  Cam4   Cam5       │  Cam4   Cam5          │
└────────────────────────────────────────────┘
   col 0  col 1         col 2  col 3
```

The 4 plot-grid columns map as:

| Plot Col | Content                  |
|----------|--------------------------|
| 0        | Left sensor, left column (cams 1–4)  |
| 1        | Left sensor, right column (cams 5–8) |
| 2        | Right sensor, left column (cams 1–4) |
| 3        | Right sensor, right column (cams 5–8)|

### Sparse / Inactive Channel Handling

If a channel is not active, its plot position is **omitted** — the grid collapses to show only active plots. Empty rows or columns that result from all cameras in that position being inactive are removed.

**Example — Outer cameras only (cams 1, 4, 5, 8) on both sensors:**

Active positions per sensor:
- Row 0, Col 0: Cam 1
- Row 3, Col 0: Cam 4
- Row 3, Col 1: Cam 5
- Row 0, Col 1: Cam 8

After collapsing empty rows (rows 1 and 2 have no active cameras), the per-sensor grid becomes 2×2, and the full plot grid is 2×4:

```
┌──────────────────────────────────┐
│  L:Cam1  L:Cam8  │  R:Cam1  R:Cam8  │  ← top row
│  L:Cam4  L:Cam5  │  R:Cam4  R:Cam5  │  ← bottom row
└──────────────────────────────────┘
```

**General rule:** render only the rows and columns that contain at least one active camera, preserving spatial ordering (top-to-bottom, left-to-right) within each sensor half.

## Software Reference

The channel-to-grid-position mapping in Python:

```python
# Camera number (1-8) to (row, col) in the 4x2 sensor grid
CAMERA_GRID_POS = {
    1: (0, 0),
    2: (1, 0),
    3: (2, 0),
    4: (3, 0),
    5: (3, 1),
    6: (2, 1),
    7: (1, 1),
    8: (0, 1),
}

# Channel index (0-7) version
CHANNEL_GRID_POS = {cam - 1: pos for cam, pos in CAMERA_GRID_POS.items()}
```

Sensor halves in the full plot grid:

```python
# sensor: "left" -> plot columns 0-1, "right" -> plot columns 2-3
SENSOR_COL_OFFSET = {"left": 0, "right": 2}
```
