"""
db_browser.py - PyQt database browser for Openwater scan SQLite databases.

Usage
-----
python db_browser.py
python db_browser.py data/sqlite.db
"""

from __future__ import annotations

import json
import sqlite3
import struct
import sys
import zlib
from pathlib import Path
from typing import Any, Optional

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets, QtGui


HIST_BINS = 1024
HIST_STRUCT = struct.Struct(f"<{HIST_BINS}I")
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "sqlite.db"
MAX_DATA_ROWS = 1000


class DatabaseBrowser(QtWidgets.QMainWindow):
    def __init__(self, db_path: Optional[Path] = None) -> None:
        super().__init__()
        self.setWindowTitle("Openwater DB Browser")
        self.resize(1720, 1020)
        self.setMinimumSize(1380, 860)

        self.conn: Optional[sqlite3.Connection] = None
        self.compress_raw_hist = False
        self.selected_session_id: Optional[int] = None
        self.session_lookup: dict[str, int] = {}
        self.frame_lookup: dict[str, dict[str, Any]] = {}

        self._configure_theme()
        self._build_ui()

        initial_path = db_path or (DEFAULT_DB_PATH if DEFAULT_DB_PATH.exists() else None)
        if initial_path is not None:
            self.open_database(initial_path)

    def _configure_theme(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return

        app.setStyle("Fusion")
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#f3efe7"))
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor("#241f18"))
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#fffdf9"))
        palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#f7f1e7"))
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor("#fffdf9"))
        palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor("#241f18"))
        palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#241f18"))
        palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor("#e0d6c6"))
        palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor("#241f18"))
        palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor("#cfdcc0"))
        palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor("#1a2115"))
        app.setPalette(palette)

        app.setStyleSheet(
            """
            QWidget {
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            QMainWindow, QWidget#central {
                background: #f3efe7;
                color: #241f18;
            }
            QFrame[panel="true"], QGroupBox {
                background: #fbf8f2;
                border: 1px solid #d8cdbd;
                border-radius: 12px;
            }
            QGroupBox {
                font-weight: 600;
                margin-top: 12px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #3b3227;
            }
            QPushButton {
                background: #ddd2c2;
                border: 1px solid #ccbea9;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #d3c6b4;
            }
            QComboBox, QLineEdit, QListWidget, QPlainTextEdit, QTableWidget {
                background: #fffdf9;
                border: 1px solid #d5cab8;
                border-radius: 8px;
                padding: 6px;
            }
            QListWidget::item:selected, QTableWidget::item:selected {
                background: #dfe8d5;
                color: #1d2618;
            }
            QLabel#title {
                font-size: 20pt;
                font-weight: 700;
                color: #1e1912;
            }
            QLabel#subtitle {
                color: #665d50;
                font-size: 10pt;
            }
            QLabel#status {
                color: #665d50;
                padding: 4px 0;
            }
            """
        )

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        title = QtWidgets.QLabel("Openwater Database Browser")
        title.setObjectName("title")
        subtitle = QtWidgets.QLabel(
            "Browse sessions, choose side/camera/frame, and inspect monochrome 10-bit histograms."
        )
        subtitle.setObjectName("subtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        top_bar = QtWidgets.QFrame()
        top_bar.setProperty("panel", True)
        top_layout = QtWidgets.QGridLayout(top_bar)
        top_layout.setContentsMargins(14, 14, 14, 14)
        top_layout.setHorizontalSpacing(10)
        top_layout.setVerticalSpacing(10)

        self.db_path_edit = QtWidgets.QLineEdit()
        self.db_path_edit.setPlaceholderText("Select a database file")
        browse_btn = QtWidgets.QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_database)
        reload_btn = QtWidgets.QPushButton("Reload")
        reload_btn.clicked.connect(self.reload_database)

        self.session_combo = QtWidgets.QComboBox()
        self.session_combo.currentTextChanged.connect(self.on_session_combo_changed)

        top_layout.addWidget(QtWidgets.QLabel("Database"), 0, 0)
        top_layout.addWidget(self.db_path_edit, 0, 1)
        top_layout.addWidget(browse_btn, 0, 2)
        top_layout.addWidget(reload_btn, 0, 3)
        top_layout.addWidget(QtWidgets.QLabel("Session"), 1, 0)
        top_layout.addWidget(self.session_combo, 1, 1, 1, 3)
        top_layout.setColumnStretch(1, 1)
        root.addWidget(top_bar)

        content = QtWidgets.QHBoxLayout()
        content.setSpacing(10)
        root.addLayout(content, 1)

        hist_panel = QtWidgets.QGroupBox("Histogram Viewer")
        hist_layout = QtWidgets.QVBoxLayout(hist_panel)
        hist_layout.setContentsMargins(14, 18, 14, 14)
        hist_layout.setSpacing(10)
        content.addWidget(hist_panel, 2)

        control_bar = QtWidgets.QHBoxLayout()
        control_bar.setSpacing(10)
        hist_layout.addLayout(control_bar)

        self.side_combo = QtWidgets.QComboBox()
        self.side_combo.currentTextChanged.connect(self.on_side_changed)
        self.camera_combo = QtWidgets.QComboBox()
        self.camera_combo.currentTextChanged.connect(self.on_camera_changed)
        self.frame_combo = QtWidgets.QComboBox()
        self.frame_combo.currentTextChanged.connect(self.on_frame_changed)
        show_btn = QtWidgets.QPushButton("Show")
        show_btn.clicked.connect(self.show_selected_frame)

        control_bar.addWidget(QtWidgets.QLabel("Side"))
        control_bar.addWidget(self.side_combo, 1)
        control_bar.addWidget(QtWidgets.QLabel("Camera"))
        control_bar.addWidget(self.camera_combo, 1)
        control_bar.addWidget(QtWidgets.QLabel("Frame"))
        control_bar.addWidget(self.frame_combo, 2)
        control_bar.addWidget(show_btn)

        self.hist_plot = pg.PlotWidget()
        self.hist_plot.setBackground("#fffdfa")
        self.hist_plot.showGrid(x=True, y=True, alpha=0.18)
        self.hist_plot.setLabel("bottom", "Histogram Bin")
        self.hist_plot.setLabel("left", "Count")
        self.hist_plot.getAxis("bottom").setPen(pg.mkPen("#7d715f", width=1))
        self.hist_plot.getAxis("left").setPen(pg.mkPen("#7d715f", width=1))
        self.hist_plot.getAxis("bottom").setTextPen(pg.mkColor("#463d31"))
        self.hist_plot.getAxis("left").setTextPen(pg.mkColor("#463d31"))
        self.hist_plot.getViewBox().setMouseEnabled(x=True, y=True)
        self.hist_plot.getViewBox().setMenuEnabled(False)
        self.hist_plot.addLegend(offset=(12, 12))
        self.hist_curve = self.hist_plot.plot(
            pen=pg.mkPen("#111111", width=2),
            name="Histogram",
        )
        hist_layout.addWidget(self.hist_plot, 1)

        self.hist_stats_label = QtWidgets.QLabel("No histogram selected.")
        self.hist_stats_label.setWordWrap(True)
        hist_layout.addWidget(self.hist_stats_label)

        right_col = QtWidgets.QVBoxLayout()
        right_col.setSpacing(10)
        content.addLayout(right_col, 1)

        frame_panel = QtWidgets.QGroupBox("Frame Details")
        frame_layout = QtWidgets.QVBoxLayout(frame_panel)
        frame_layout.setContentsMargins(14, 18, 14, 14)
        self.frame_details_text = QtWidgets.QPlainTextEdit()
        self.frame_details_text.setReadOnly(True)
        self.frame_details_text.setPlainText("No frame selected.")
        frame_layout.addWidget(self.frame_details_text)
        right_col.addWidget(frame_panel, 1)

        session_panel = QtWidgets.QGroupBox("Session Details")
        session_layout = QtWidgets.QVBoxLayout(session_panel)
        session_layout.setContentsMargins(14, 18, 14, 14)

        self.session_details_text = QtWidgets.QPlainTextEdit()
        self.session_details_text.setReadOnly(True)
        self.session_details_text.setPlainText("No session selected.")
        session_layout.addWidget(self.session_details_text, 2)

        session_layout.addWidget(QtWidgets.QLabel(f"Showing up to {MAX_DATA_ROWS} session_data rows"))
        self.data_table = QtWidgets.QTableWidget(0, 9)
        self.data_table.setHorizontalHeaderLabels(
            ["ID", "Raw ID", "Cam", "Side", "Timestamp", "BFI", "BVI", "Contrast", "Mean"]
        )
        self.data_table.horizontalHeader().setStretchLastSection(True)
        self.data_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.data_table.verticalHeader().setVisible(False)
        self.data_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.data_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.data_table.setAlternatingRowColors(True)
        session_layout.addWidget(self.data_table, 3)

        right_col.addWidget(session_panel, 2)

        self.status_label = QtWidgets.QLabel("Select a database to begin.")
        self.status_label.setObjectName("status")
        root.addWidget(self.status_label)

    def browse_database(self) -> None:
        initial_dir = str(Path(self.db_path_edit.text()).parent) if self.db_path_edit.text() else str(DEFAULT_DB_PATH.parent)
        selected, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select SQLite Database",
            initial_dir,
            "SQLite Database (*.db *.sqlite *.sqlite3);;All Files (*.*)",
        )
        if selected:
            self.open_database(Path(selected))

    def reload_database(self) -> None:
        current = self.db_path_edit.text().strip()
        if not current:
            QtWidgets.QMessageBox.information(self, "Reload", "Select a database first.")
            return
        self.open_database(Path(current))

    def open_database(self, path: Path) -> None:
        try:
            self.close_connection()
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            self.conn = conn
            self.compress_raw_hist = self.load_compression_setting()
            self.db_path_edit.setText(str(path))
            self.load_sessions()
            self.status_label.setText(
                f"Opened {path} | raw histogram compression: {'on' if self.compress_raw_hist else 'off'}"
            )
        except Exception as exc:
            self.conn = None
            QtWidgets.QMessageBox.critical(self, "Open Database", f"Could not open database:\n{exc}")
            self.status_label.setText("Failed to open database.")

    def close_connection(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.compress_raw_hist = False
        self.selected_session_id = None
        self.session_lookup.clear()
        self.frame_lookup.clear()

    def load_compression_setting(self) -> bool:
        assert self.conn is not None
        try:
            row = self.conn.execute(
                "SELECT value FROM database_settings WHERE key = 'compress_raw_hist'"
            ).fetchone()
        except sqlite3.Error:
            return False
        if row is None:
            return False
        return str(row["value"]) == "1"

    def load_sessions(self) -> None:
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        self.session_lookup.clear()
        self.frame_lookup.clear()
        self.selected_session_id = None
        self.session_details_text.setPlainText("No session selected.")
        self.frame_details_text.setPlainText("No frame selected.")
        self.hist_stats_label.setText("No histogram selected.")
        self.side_combo.clear()
        self.camera_combo.clear()
        self.frame_combo.clear()
        self.data_table.setRowCount(0)
        self.clear_histogram()

        if self.conn is None:
            self.session_combo.blockSignals(False)
            return

        rows = self.conn.execute(
            """
            SELECT id, session_label, session_start
            FROM sessions
            ORDER BY session_start, id
            """
        ).fetchall()

        labels: list[str] = []
        for row in rows:
            display = f"{row['session_label']}   [{format_timestamp(row['session_start'])}]"
            labels.append(display)
            self.session_lookup[display] = int(row["id"])

        self.session_combo.addItems(labels)
        self.session_combo.blockSignals(False)

        if labels:
            self.session_combo.setCurrentIndex(0)
            self.select_session_by_display(labels[0])

    def on_session_combo_changed(self, display: str) -> None:
        if display:
            self.select_session_by_display(display)

    def select_session_by_display(self, display: str) -> None:
        if self.conn is None:
            return
        session_id = self.session_lookup.get(display)
        if session_id is None:
            return
        self.selected_session_id = session_id
        self.load_session_details(session_id)
        self.load_frame_controls(session_id)
        self.load_session_data(session_id)

    def load_session_details(self, session_id: int) -> None:
        assert self.conn is not None
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            self.session_details_text.setPlainText("Session not found.")
            return

        meta_text = pretty_json(row["session_meta"])
        details = [
            f"ID: {row['id']}",
            f"Label: {row['session_label']}",
            f"Start: {format_timestamp(row['session_start'])}",
            f"End: {format_timestamp(row['session_end'])}",
            "",
            f"Notes:\n{row['session_notes'] or ''}",
            "",
            f"Meta:\n{meta_text}",
        ]
        self.session_details_text.setPlainText("\n".join(details))

    def load_frame_controls(self, session_id: int) -> None:
        assert self.conn is not None
        self.frame_lookup.clear()
        self.frame_details_text.setPlainText("No frame selected.")
        self.hist_stats_label.setText("No histogram selected.")
        self.clear_histogram()

        side_rows = self.conn.execute(
            """
            SELECT DISTINCT side
            FROM session_raw
            WHERE session_id = ?
            ORDER BY side
            """,
            (session_id,),
        ).fetchall()
        sides = [str(row["side"]) for row in side_rows]

        self.side_combo.blockSignals(True)
        self.side_combo.clear()
        self.side_combo.addItems(sides)
        self.side_combo.blockSignals(False)

        if sides:
            self.side_combo.setCurrentIndex(0)
            self.load_camera_options()
        else:
            self.camera_combo.clear()
            self.frame_combo.clear()

    def on_side_changed(self, _value: str) -> None:
        self.load_camera_options()

    def load_camera_options(self) -> None:
        if self.conn is None or self.selected_session_id is None:
            return
        side = self.side_combo.currentText()
        rows = self.conn.execute(
            """
            SELECT DISTINCT cam_id
            FROM session_raw
            WHERE session_id = ? AND side = ?
            ORDER BY cam_id
            """,
            (self.selected_session_id, side),
        ).fetchall()

        cameras = [str(int(row["cam_id"])) for row in rows]
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        self.camera_combo.addItems(cameras)
        self.camera_combo.blockSignals(False)

        if cameras:
            self.camera_combo.setCurrentIndex(0)
            self.load_frame_options()
        else:
            self.frame_combo.clear()
            self.frame_lookup.clear()
            self.clear_histogram()
            self.frame_details_text.setPlainText("No frame selected.")

    def on_camera_changed(self, _value: str) -> None:
        self.load_frame_options()

    def load_frame_options(self) -> None:
        if self.conn is None or self.selected_session_id is None:
            return

        side = self.side_combo.currentText()
        camera_text = self.camera_combo.currentText()
        if not camera_text:
            self.frame_combo.clear()
            self.frame_lookup.clear()
            return

        rows = self.conn.execute(
            """
            SELECT id, frame_id, timestamp_s, sum, temp, tcm, tcl, pdc, hist
            FROM session_raw
            WHERE session_id = ? AND side = ? AND cam_id = ?
            ORDER BY timestamp_s, frame_id, id
            """,
            (self.selected_session_id, side, int(camera_text)),
        ).fetchall()

        labels: list[str] = []
        self.frame_lookup.clear()
        for row in rows:
            label = f"frame {row['frame_id']} | t={row['timestamp_s']:.6f}"
            labels.append(label)
            self.frame_lookup[label] = {
                "id": int(row["id"]),
                "frame_id": int(row["frame_id"]),
                "timestamp_s": float(row["timestamp_s"]),
                "sum": row["sum"],
                "temp": row["temp"],
                "tcm": row["tcm"],
                "tcl": row["tcl"],
                "pdc": row["pdc"],
                "hist": bytes(row["hist"]),
                "side": side,
                "cam_id": int(camera_text),
            }

        self.frame_combo.blockSignals(True)
        self.frame_combo.clear()
        self.frame_combo.addItems(labels)
        self.frame_combo.blockSignals(False)

        if labels:
            self.frame_combo.setCurrentIndex(0)
            self.show_selected_frame()
        else:
            self.clear_histogram()
            self.hist_stats_label.setText("No histogram selected.")
            self.frame_details_text.setPlainText("No frame selected.")

    def on_frame_changed(self, _value: str) -> None:
        self.show_selected_frame()

    def load_session_data(self, session_id: int) -> None:
        assert self.conn is not None
        rows = self.conn.execute(
            f"""
            SELECT id, session_raw_id, cam_id, side, timestamp_s, bfi, bvi, contrast, mean
            FROM session_data
            WHERE session_id = ?
            ORDER BY timestamp_s, side, cam_id, id
            LIMIT {MAX_DATA_ROWS}
            """,
            (session_id,),
        ).fetchall()

        self.data_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                row["id"],
                row["session_raw_id"],
                row["cam_id"],
                "left" if row["side"] == 0 else "right",
                f"{row['timestamp_s']:.6f}",
                format_float(row["bfi"]),
                format_float(row["bvi"]),
                format_float(row["contrast"]),
                format_float(row["mean"]),
            )
            for col_index, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.data_table.setItem(row_index, col_index, item)

        self.status_label.setText(
            f"Session {session_id}: {len(rows)} session_data row(s) loaded"
        )

    def show_selected_frame(self) -> None:
        label = self.frame_combo.currentText().strip()
        if not label:
            self.clear_histogram()
            self.hist_stats_label.setText("No histogram selected.")
            self.frame_details_text.setPlainText("No frame selected.")
            return

        frame = self.frame_lookup.get(label)
        if frame is None:
            return

        try:
            histogram = decode_histogram(frame["hist"], self.compress_raw_hist)
        except (ValueError, zlib.error) as exc:
            self.clear_histogram()
            self.frame_details_text.setPlainText("Unable to decode histogram.")
            self.hist_stats_label.setText(str(exc))
            return

        x = list(range(HIST_BINS))
        self.hist_curve.setData(x=x, y=histogram)
        self.hist_plot.setXRange(0, HIST_BINS - 1, padding=0.01)
        self.hist_plot.enableAutoRange(axis="y", enable=True)

        max_value = max(histogram) if histogram else 0
        total = sum(histogram)
        non_zero = sum(1 for value in histogram if value)
        self.hist_stats_label.setText(
            f"Bins: {len(histogram)}   Total counts: {total:,}   "
            f"Max bin: {max_value:,}   Non-zero bins: {non_zero}"
        )
        self.frame_details_text.setPlainText(
            "\n".join(
                [
                    f"Raw ID: {frame['id']}",
                    f"Side: {frame['side']}",
                    f"Camera: {frame['cam_id']}",
                    f"Frame ID: {frame['frame_id']}",
                    f"Timestamp: {frame['timestamp_s']:.6f}",
                    f"Sum: {frame['sum']}",
                    f"Temperature: {format_float(frame['temp'])}",
                    f"TCM: {format_float(frame['tcm'])}",
                    f"TCL: {format_float(frame['tcl'])}",
                    f"PDC: {format_float(frame['pdc'])}",
                ]
            )
        )
        self.status_label.setText(
            f"Viewing {frame['side']} camera {frame['cam_id']} frame {frame['frame_id']}"
        )

    def clear_histogram(self) -> None:
        self.hist_curve.setData([], [])

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.close_connection()
        super().closeEvent(event)


def decode_histogram(blob: bytes, compress_raw_hist: bool = False) -> list[int]:
    if compress_raw_hist:
        blob = zlib.decompress(blob)
    if len(blob) != HIST_STRUCT.size:
        raise ValueError(
            f"Histogram blob size {len(blob)} does not match expected {HIST_STRUCT.size} bytes"
        )
    return list(HIST_STRUCT.unpack(blob))


def format_timestamp(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


def format_float(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


def pretty_json(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(value, indent=2, sort_keys=True)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    db_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    browser = DatabaseBrowser(db_arg)
    browser.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
