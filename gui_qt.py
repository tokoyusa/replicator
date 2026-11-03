#!/usr/bin/env python
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QSpinBox, QTextEdit,
    QVBoxLayout, QWidget,
)

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
STARTUP_LOG = ROOT / "startup.log"
RENDER_LOG = ROOT / "render.log"
CONFIG_FILE = ROOT / "loopizz_config.json"


class TextLayerDialog(QDialog):
    def __init__(self, parent=None, layer=None):
        super().__init__(parent)
        self.setWindowTitle("Text layer")
        data = layer or {}
        form = QFormLayout(self)
        self.text = QLineEdit(str(data.get("text", "")))
        self.text.setPlaceholderText("Enter text to display")
        self.start = QDoubleSpinBox(); self.start.setRange(0, 100000); self.start.setSuffix(" s"); self.start.setValue(float(data.get("start", 0)))
        self.end = QDoubleSpinBox(); self.end.setRange(0, 100000); self.end.setSuffix(" s"); self.end.setValue(float(data.get("end", 9999)))
        self.x = QLineEdit(str(data.get("x", "center"))); self.x.setPlaceholderText("center / left / right / pixel")
        self.y = QLineEdit(str(data.get("y", "center"))); self.y.setPlaceholderText("center / top / bottom / pixel")
        self.size = QSpinBox(); self.size.setRange(8, 200); self.size.setValue(int(data.get("size", 48)))
        self.font = QLineEdit(str(data.get("font", "")))
        font_row = QHBoxLayout(); font_row.addWidget(self.font)
        browse = QPushButton("Browse"); browse.clicked.connect(self.pick_font); font_row.addWidget(browse)
        self.color = QLineEdit(str(data.get("color", "#FFFFFF")))
        color_row = QHBoxLayout(); color_row.addWidget(self.color)
        color_button = QPushButton("Pick color"); color_button.clicked.connect(self.pick_color); color_row.addWidget(color_button)
        self.animation = QComboBox(); self.animation.addItems(["none", "fade", "slide"]); self.animation.setCurrentText(str(data.get("animation", "none")))
        self.fade_in = QDoubleSpinBox(); self.fade_in.setRange(0, 30); self.fade_in.setSuffix(" s"); self.fade_in.setValue(float(data.get("fade_in", 0.5)))
        self.fade_out = QDoubleSpinBox(); self.fade_out.setRange(0, 30); self.fade_out.setSuffix(" s"); self.fade_out.setValue(float(data.get("fade_out", 0.5)))
        form.addRow("Text", self.text); form.addRow("Start", self.start); form.addRow("End", self.end)
        form.addRow("Position X", self.x); form.addRow("Position Y", self.y); form.addRow("Font size", self.size)
        form.addRow("Font file (optional)", font_row); form.addRow("Font color", color_row)
        form.addRow("Animation", self.animation); form.addRow("Fade in", self.fade_in); form.addRow("Fade out", self.fade_out)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); form.addRow(buttons)

    def pick_font(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose font", str(ROOT), "Fonts (*.ttf *.otf)")
        if path: self.font.setText(path)

    def pick_color(self):
        from PySide6.QtGui import QColor
        color = QColorDialog.getColor(QColor(self.color.text()), self)
        if color.isValid(): self.color.setText(color.name())

    def values(self):
        if not self.text.text().strip(): raise ValueError("Text cannot be empty.")
        if self.end.value() <= self.start.value(): raise ValueError("End time must be greater than start time.")
        if self.color.text().strip() and not self.color.text().startswith("#"):
            raise ValueError("Use a color in HEX format, for example #FFFFFF.")
        return {
            "text": self.text.text(), "start": self.start.value(), "end": self.end.value(),
            "x": self.x.text().strip() or "center", "y": self.y.text().strip() or "center",
            "size": self.size.value(), "font": self.font.text().strip(),
            "color": self.color.text().strip() or "#FFFFFF", "animation": self.animation.currentText(),
            "fade_in": self.fade_in.value(), "fade_out": self.fade_out.value(),
        }


class LoopizzWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.process = None
        self.render_log = None
        self.video_files = sorted((ROOT / "video").glob("source*.mp4"))
        self.audio_file = ROOT / "audio" / "voice.mp3"
        self.fx_audio_file = None
        self.overlay_file = ROOT / "overlay.png"
        self.text_layers = []
        self.logo_file = None
        self.setWindowTitle("Loopizz")
        self.resize(1120, 820)
        self.setMinimumSize(900, 700)
        self.build_ui()
        self.refresh_status()

    def build_ui(self):
        self.setStyleSheet("""
            QWidget { background: #101827; color: #e8eef8; font-size: 13px; }
            QGroupBox { background: #172338; border: 1px solid #2c3d59; border-radius: 12px; margin-top: 16px; padding: 14px; font-weight: 700; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #9fb4d5; }
            QLineEdit, QTextEdit, QListWidget, QComboBox, QSpinBox, QDoubleSpinBox { background: #0c1422; color: #f1f5fb; border: 1px solid #344866; border-radius: 7px; padding: 7px; }
            QPushButton { background: #243650; color: #edf4ff; border: 1px solid #405879; border-radius: 8px; padding: 9px 13px; }
            QPushButton:hover { background: #304967; }
            QPushButton#startButton { background: #16a6a0; color: #061c25; border: 0; font-weight: 800; padding: 12px 22px; }
            QPushButton#startButton:hover { background: #39c7bc; }
            QPushButton:disabled { color: #728198; background: #202b3d; }
            QLabel#title { font-size: 32px; font-weight: 900; color: #ffffff; }
            QLabel#brand { color: #48d5c7; font-size: 12px; font-weight: 700; }
            QLabel#muted { color: #9aabc4; }
            QTextEdit#log { background: #080e18; color: #c9e2e8; font-family: Consolas, monospace; }
        """)
        root = QVBoxLayout(self); root.setContentsMargins(22, 18, 22, 22); root.setSpacing(12)
        brand = QLabel("LOOPIZZ  •  VIDEO LOOP STUDIO"); brand.setObjectName("brand")
        title = QLabel("Create seamless music videos"); title.setObjectName("title")
        subtitle = QLabel("Build long-form visuals with timed text, logo overlays and layered audio."); subtitle.setObjectName("muted")
        root.addWidget(brand); root.addWidget(title); root.addWidget(subtitle)

        top = QHBoxLayout(); root.addLayout(top)
        text_group = QGroupBox("Text layers")
        text_layout = QVBoxLayout(text_group)
        self.layer_list = QListWidget(); self.layer_list.setMinimumHeight(180); text_layout.addWidget(self.layer_list)
        row = QHBoxLayout()
        for label, callback in [("＋ Add text", self.add_layer), ("Edit", self.edit_layer), ("Delete", self.delete_layer)]:
            button = QPushButton(label); button.clicked.connect(callback); row.addWidget(button)
        text_layout.addLayout(row)
        hint = QLabel("Each layer has its own text, timing, X/Y position, font, color and animation."); hint.setObjectName("muted"); hint.setWordWrap(True)
        text_layout.addWidget(hint); top.addWidget(text_group, 3)

        logo_group = QGroupBox("Logo overlay")
        logo_form = QFormLayout(logo_group)
        self.logo_status = QLabel("No logo selected (optional)"); self.logo_status.setWordWrap(True)
        choose_logo = QPushButton("Upload logo image"); choose_logo.clicked.connect(self.choose_logo)
        self.logo_width = QSpinBox(); self.logo_width.setRange(24, 1200); self.logo_width.setValue(220); self.logo_width.setSuffix(" px")
        self.logo_x = QLineEdit("right"); self.logo_y = QLineEdit("top")
        self.logo_start = QDoubleSpinBox(); self.logo_start.setRange(0, 100000); self.logo_start.setSuffix(" s")
        self.logo_end = QDoubleSpinBox(); self.logo_end.setRange(0, 100000); self.logo_end.setValue(9999); self.logo_end.setSuffix(" s")
        self.logo_animation = QComboBox(); self.logo_animation.addItems(["none", "fade", "slide"]); self.logo_animation.setCurrentText("fade")
        self.logo_fade_in = QDoubleSpinBox(); self.logo_fade_in.setRange(0, 30); self.logo_fade_in.setValue(0.5); self.logo_fade_in.setSuffix(" s")
        self.logo_fade_out = QDoubleSpinBox(); self.logo_fade_out.setRange(0, 30); self.logo_fade_out.setValue(0.5); self.logo_fade_out.setSuffix(" s")
        logo_form.addRow(choose_logo); logo_form.addRow(self.logo_status)
        logo_form.addRow("Width", self.logo_width); logo_form.addRow("Position X", self.logo_x); logo_form.addRow("Position Y", self.logo_y)
        logo_form.addRow("Start", self.logo_start); logo_form.addRow("End", self.logo_end); logo_form.addRow("Animation", self.logo_animation)
        logo_form.addRow("Fade in", self.logo_fade_in); logo_form.addRow("Fade out", self.logo_fade_out)
        top.addWidget(logo_group, 2)

        media = QGroupBox("Media & audio")
        grid = QGridLayout(media)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setObjectName("muted")
        grid.addWidget(self.status, 0, 0, 1, 2)
        buttons = [
            ("Choose videos", self.choose_videos), ("Choose main audio", self.choose_audio),
            ("Choose background overlay", self.choose_overlay), ("Choose FX / ambience", self.choose_fx_audio),
            ("Reset media", self.reset_defaults),
        ]
        for i, (label, callback) in enumerate(buttons):
            b = QPushButton(label); b.clicked.connect(callback); grid.addWidget(b, 1 + i // 2, i % 2)
        self.duration = QLineEdit(); self.duration.setPlaceholderText("Blank = follow audio duration")
        self.main_volume = QLineEdit("1.0"); self.fx_volume = QLineEdit("0.35")
        self.loop_audio = QCheckBox("Loop main audio until output ends")
        grid.addWidget(QLabel("Output duration (minutes)"), 4, 0); grid.addWidget(self.duration, 4, 1)
        grid.addWidget(QLabel("Main volume (0–2)"), 5, 0); grid.addWidget(self.main_volume, 5, 1)
        grid.addWidget(QLabel("FX volume (0–2)"), 6, 0); grid.addWidget(self.fx_volume, 6, 1)
        grid.addWidget(self.loop_audio, 7, 0, 1, 2)
        root.addWidget(media)

        actions = QHBoxLayout()
        self.start_button = QPushButton("RENDER VIDEO"); self.start_button.setObjectName("startButton"); self.start_button.clicked.connect(self.start_build)
        self.open_button = QPushButton("Open output"); self.open_button.clicked.connect(self.open_result)
        actions.addWidget(self.start_button); actions.addWidget(self.open_button); actions.addStretch(1); root.addLayout(actions)
        log_group = QGroupBox("Render progress"); log_layout = QVBoxLayout(log_group)
        self.log = QTextEdit(); self.log.setObjectName("log"); self.log.setReadOnly(True); self.log.setMinimumHeight(120)
        log_layout.addWidget(self.log); root.addWidget(log_group, 1)

    def add_layer(self):
        dialog = TextLayerDialog(self)
        if dialog.exec() == QDialog.Accepted:
            try: self.text_layers.append(dialog.values()); self.refresh_layers()
            except ValueError as error: QMessageBox.warning(self, "Text layer", str(error))

    def edit_layer(self):
        index = self.layer_list.currentRow()
        if index < 0: return
        dialog = TextLayerDialog(self, self.text_layers[index])
        if dialog.exec() == QDialog.Accepted:
            try: self.text_layers[index] = dialog.values(); self.refresh_layers()
            except ValueError as error: QMessageBox.warning(self, "Text layer", str(error))

    def delete_layer(self):
        index = self.layer_list.currentRow()
        if index >= 0: self.text_layers.pop(index); self.refresh_layers()

    def refresh_layers(self):
        self.layer_list.clear()
        for i, layer in enumerate(self.text_layers, 1):
            self.layer_list.addItem(f'{i}. {layer["text"][:42]}  |  {layer["start"]:g}–{layer["end"]:g}s  |  {layer["animation"]}')

    def choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Upload logo", str(ROOT), "Images (*.png *.jpg *.jpeg *.webp)")
        if path: self.logo_file = Path(path); self.logo_status.setText(self.logo_file.name)

    def choose_videos(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select source videos", str(ROOT), "Videos (*.mp4 *.mov *.mkv *.avi)")
        if paths: self.video_files = [Path(p) for p in paths]; self.refresh_status()

    def choose_audio(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select main audio", str(ROOT), "Audio (*.mp3 *.wav *.m4a *.aac *.flac)")
        if path: self.audio_file = Path(path); self.refresh_status()

    def choose_fx_audio(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select FX / ambience", str(ROOT), "Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
        if path: self.fx_audio_file = Path(path); self.refresh_status()

    def choose_overlay(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select background overlay", str(ROOT), "Images (*.png)")
        if path: self.overlay_file = Path(path); self.refresh_status()

    def reset_defaults(self):
        self.video_files = sorted((ROOT / "video").glob("source*.mp4"))
        self.audio_file = ROOT / "audio" / "voice.mp3"; self.fx_audio_file = None
        self.overlay_file = ROOT / "overlay.png"; self.refresh_status()

    def refresh_status(self):
        vids = ", ".join(p.name for p in self.video_files) or "not selected"
        audio = self.audio_file.name if self.audio_file and self.audio_file.exists() else "not selected"
        fx = self.fx_audio_file.name if self.fx_audio_file and self.fx_audio_file.exists() else "not selected"
        overlay = self.overlay_file.name if self.overlay_file and self.overlay_file.exists() else "not selected"
        self.status.setText(f"Videos: {vids}\nMain audio: {audio}   •   FX: {fx}\nBackground overlay: {overlay}")
        output = ROOT / "youtube_ready.mp4"; self.open_button.setEnabled(output.exists())

    def validate(self):
        if not self.video_files or any(not p.exists() for p in self.video_files): raise ValueError("Choose at least one existing video.")
        if not self.audio_file or not self.audio_file.exists(): raise ValueError("Choose a main audio file.")
        if not self.overlay_file or not self.overlay_file.exists(): raise ValueError("Choose a background PNG overlay.")
        if self.fx_audio_file and not self.fx_audio_file.exists(): raise ValueError("FX audio file was not found.")
        try:
            if self.duration.text().strip() and float(self.duration.text()) <= 0: raise ValueError
            if not 0 <= float(self.main_volume.text() or "1") <= 2: raise ValueError
            if not 0 <= float(self.fx_volume.text() or "0.35") <= 2: raise ValueError
        except ValueError: raise ValueError("Duration must be positive and volumes must be between 0 and 2.")
        if self.logo_file and self.logo_end.value() <= self.logo_start.value(): raise ValueError("Logo end time must be greater than start time.")

    def start_build(self):
        try: self.validate()
        except ValueError as error: QMessageBox.warning(self, "Loopizz", str(error)); return
        logo = None
        if self.logo_file:
            logo = {"path": str(self.logo_file), "width": self.logo_width.value(), "x": self.logo_x.text().strip() or "right",
                    "y": self.logo_y.text().strip() or "top", "start": self.logo_start.value(), "end": self.logo_end.value(),
                    "animation": self.logo_animation.currentText(), "fade_in": self.logo_fade_in.value(), "fade_out": self.logo_fade_out.value()}
        CONFIG_FILE.write_text(json.dumps({"text_layers": self.text_layers, "logo": logo}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log.clear(); self.render_log = RENDER_LOG.open("w", encoding="utf-8", errors="replace")
        self.append_log("Loopizz render started…\n"); self.start_button.setEnabled(False); self.open_button.setEnabled(False)
        python_exe = ROOT / "python" / "python.exe"
        if not python_exe.exists(): python_exe = Path(sys.executable)
        args = [str(ROOT / "replicator.py"), "--no-pause", "--text-config", str(CONFIG_FILE),
                "--audio", str(self.audio_file), "--overlay", str(self.overlay_file),
                "--main-volume", self.main_volume.text().strip() or "1",
                "--fx-volume", self.fx_volume.text().strip() or "0.35"]
        if self.duration.text().strip(): args += ["--duration-minutes", self.duration.text().strip()]
        if self.fx_audio_file: args += ["--fx-audio", str(self.fx_audio_file)]
        if self.loop_audio.isChecked(): args.append("--loop-audio")
        for path in self.video_files: args += ["--video", str(path)]
        self.process = QProcess(self); env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1"); env.insert("QT_OPENGL", "software"); self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(ROOT)); self.process.readyReadStandardOutput.connect(self.read_process)
        self.process.readyReadStandardError.connect(self.read_process); self.process.finished.connect(self.finish_build)
        self.process.errorOccurred.connect(self.process_error); self.process.start(str(python_exe), args)

    def append_log(self, value):
        if self.render_log: self.render_log.write(value); self.render_log.flush()
        self.log.moveCursor(self.log.textCursor().MoveOperation.End); self.log.insertPlainText(value)
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)

    def read_process(self):
        if self.process:
            data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            data += bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
            if data: self.append_log(data)

    def close_log(self):
        if self.render_log: self.render_log.close(); self.render_log = None

    def finish_build(self, code, _status):
        self.close_log(); self.start_button.setEnabled(True); self.refresh_status()
        if code == 0 and (ROOT / "youtube_ready.mp4").exists(): self.append_log("\nRender complete: youtube_ready.mp4\n")
        else:
            self.append_log(f"\nRender failed (exit code {code}).\n")
            QMessageBox.critical(self, "Loopizz", "Render failed. Check render.log.")

    def process_error(self, error):
        self.close_log(); self.start_button.setEnabled(True)
        QMessageBox.critical(self, "Loopizz", f"Could not start render process: {error}")

    def open_result(self):
        path = ROOT / "youtube_ready.mp4"
        if not path.exists(): QMessageBox.information(self, "Loopizz", "Output has not been created yet."); return
        try:
            if os.name == "nt": os.startfile(str(path))
            elif sys.platform == "darwin": subprocess.Popen(["open", str(path)])
            else: subprocess.Popen(["xdg-open", str(path)])
        except Exception as error: QMessageBox.warning(self, "Loopizz", str(error))


def main():
    os.chdir(ROOT)
    try:
        app = QApplication(sys.argv); app.setFont(QFont("Segoe UI", 10))
        window = LoopizzWindow(); window.show(); sys.exit(app.exec())
    except Exception:
        STARTUP_LOG.write_text(traceback.format_exc(), encoding="utf-8", errors="replace")
        raise


if __name__ == "__main__":
    main()
