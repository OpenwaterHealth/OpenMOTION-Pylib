# Openwater Scan Database

SQLite-backed storage for openwater scan sessions. The database interface lives
in `scan_db.py`, the database files live in `data/`, and example acquisition
input files live in `scan_data/`.

## Current model

The schema is session + raw-frame + session-data oriented:

- a session is one scan run
- raw acquisition data is stored one frame per row in `session_raw`
- the pipeline can still process 15-second windows and a final shorter window
- processed session data is stored per camera and timestamp

## Schema

### `sessions`

```sql
CREATE TABLE sessions (
    id             INTEGER PRIMARY KEY,
    session_label  TEXT    NOT NULL,
    session_start  REAL    NOT NULL,
    session_end    REAL,
    session_notes  TEXT,
    session_meta   TEXT
);
```

`session_meta` stores JSON text for flexible session-level metadata.

### `session_raw`

```sql
CREATE TABLE session_raw (
    id           INTEGER PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    side         TEXT    NOT NULL CHECK(side IN ('left', 'right')),
    cam_id       INTEGER NOT NULL,
    frame_id     INTEGER NOT NULL,
    timestamp_s  REAL    NOT NULL,
    hist         BLOB    NOT NULL,
    temp         REAL,
    sum          INTEGER,
    tcm          REAL    NOT NULL DEFAULT 0,
    tcl          REAL    NOT NULL DEFAULT 0,
    pdc          REAL    NOT NULL DEFAULT 0
);
```

### `session_data`

```sql
CREATE TABLE session_data (
    id               INTEGER PRIMARY KEY,
    session_id       INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    session_raw_id   INTEGER REFERENCES session_raw(id) ON DELETE SET NULL,
    cam_id           INTEGER NOT NULL,
    side             INTEGER NOT NULL CHECK(side IN (0, 1)),
    timestamp_s      REAL    NOT NULL,
    bfi              REAL,
    bvi              REAL,
    contrast         REAL,
    mean             REAL
);
```

## Python interface

### Open the database

```python
from scan_db import ScanDatabase

db = ScanDatabase()

# Create a new database that compresses raw histogram blobs
db = ScanDatabase(db_path="data/compressed.db", compress_raw_hist=True)
```

## Importing scan files

Use [importer.py](/c:/Users/gvigelet/CURRENT_WORK/openwater/test-db/importer.py) to import scan files for one label from a scan-data directory:

```bash
python importer.py ow98NSF5 scan_data
```

To create a new database with compressed raw histogram blobs:

```bash
python importer.py ow98NSF5 scan_data --db-path data/compressed.db --compress-raw-hist
```

The importer:

- finds files matching `scan_<label>_*`
- groups them by `YYYYMMDD_HHMMSS`
- treats each `label + timestamp` combination as one session
- reads the notes file into `session_notes`
- stores sensor module metadata, including decoded camera masks, in `session_meta`
- reads left/right sensor files in frame order
- inserts frame groups interleaved, so frame 1 from left/right is written before frame 2 from left/right
- respects the database `compress_raw_hist` setting and compresses `session_raw.hist` on write when enabled

## Browsing a database

Use [db_browser.py](/c:/Users/gvigelet/CURRENT_WORK/openwater/test-db/db_browser.py) to open a local database file and inspect:

- sessions
- raw histograms with side, camera, and frame selection controls
- session data rows
- decoded monochrome 10-bit histograms from `session_raw.hist`

The browser uses `PyQt5` and `pyqtgraph`.

Launch it with:

```bash
python db_browser.py
```

Or open a specific database directly:

```bash
python db_browser.py data/sqlite.db
```

## Sensor module simulator

Use [sensor_module_simulator.py](/c:/Users/gvigelet/CURRENT_WORK/openwater/test-db/sensor_module_simulator.py) to replay one scan group's left/right CSV data at a fixed rate, batch raw frame inserts, and show the live eight-stream histogram dashboard.

Example GUI run:

```bash
python sensor_module_simulator.py ow98NSF5 scan_data --timestamp-key 20260407_152533
```

Example headless validation run:

```bash
python sensor_module_simulator.py ow98NSF5 scan_data --headless --batch-frames 10 --max-frame-groups 25
```

The simulator:

- replays left/right CSV frame groups at 40 Hz by default
- writes `session_raw` rows in batches every 600 frame groups by default
- stores `session_raw.hist` compressed by default
- creates one session labeled `sim_<label>_<timestamp>`
- displays the active eight histogram streams in a 2x4 live dashboard
- swaps the in-memory write buffer and lets a background thread flush to SQLite while playback keeps collecting data

## Validating a database

Use [db_validator.py](/c:/Users/gvigelet/CURRENT_WORK/openwater/test-db/db_validator.py) to compare imported database content against the reference CSV files:

```bash
python db_validator.py ow98NSF5 scan_data data/sqlite.db
```

The validator checks:

- session presence
- session notes
- session metadata JSON
- session start time
- raw row count
- every raw row field, including the decoded histogram blob

### Create a session

```python
session_id = db.create_session(
    session_label="scan_20260409_subject01",
    session_start=1744214400.0,
    session_notes="Subject resting.",
    session_meta={"fps": 40, "operator": "demo"},
)
```

### Write raw frames

```python
raw_frame_id = db.insert_raw_frame(
    session_id=session_id,
    side="left",
    cam_id=0,
    frame_id=1,
    timestamp_s=1744214400.025,
    hist=hist_blob,
    temp=27.1,
    sum_counts=2457606,
    tcm=0,
    tcl=0,
    pdc=0,
)
```

### Write session data

```python
db.insert_session_data(
    session_id=session_id,
    session_raw_id=raw_frame_id,
    cam_id=0,
    side=0,
    timestamp_s=1744214400.0,
    bfi=3.1,
    bvi=1.4,
    contrast=0.42,
    mean=511.0,
)
```

### Close the session

```python
db.close_session(session_id, session_end=1744214420.0)
```

### Example run of simulator
```bash
python sensor_module_simulator.py ow98NSF5 scan_data --timestamp-key 20260407_152533 --db-path data/simulator_test.db --batch-frames 600
```

## Notes

- `session_raw.side` is stored as `"left"` or `"right"`.
- `session_raw.hist` can be stored compressed when the database is created with `compress_raw_hist=True`.
- `scan_db.py` transparently decompresses raw histogram blobs on reads.
- `session_data.side` is stored as `0` for left and `1` for right.
- `session_data.timestamp_s` is the timestamp chosen by the pipeline.
