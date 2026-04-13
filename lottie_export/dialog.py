from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QFileDialog, QSpinBox,
    QCheckBox, QGroupBox
)
from PyQt5.QtCore import QSettings


class LottieExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("krita-lottie-export", "lottie_export")
        self.setWindowTitle("Export as Lottie JSON")
        self.setMinimumWidth(420)
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        # --- Output folder ---
        folder_group = QGroupBox("Output")
        folder_layout = QHBoxLayout()
        folder_group.setLayout(folder_layout)

        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("Choose export folder...")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_folder)
        folder_layout.addWidget(self.folder_input)
        folder_layout.addWidget(browse_btn)
        layout.addWidget(folder_group)

        filename_row = QHBoxLayout()
        filename_row.addWidget(QLabel("File name:"))
        self.filename_input = QLineEdit()
        self.filename_input.setPlaceholderText("animation.json")
        filename_row.addWidget(self.filename_input)
        layout.addLayout(filename_row)

        # --- Options ---
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout()
        options_group.setLayout(options_layout)

        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("Override FPS (0 = use document FPS):"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(0, 120)
        self.fps_spin.setValue(0)
        fps_row.addWidget(self.fps_spin)
        options_layout.addLayout(fps_row)

        self.embed_images = QCheckBox("Embed images as base64 (self-contained JSON)")
        self.embed_images.setChecked(False)
        options_layout.addWidget(self.embed_images)

        self.ignore_invisible = QCheckBox("Skip invisible layers")
        self.ignore_invisible.setChecked(True)
        options_layout.addWidget(self.ignore_invisible)

        layout.addWidget(options_group)

        # --- Layer tag legend ---
        legend_group = QGroupBox("Layer Name Tags (optional)")
        legend_layout = QVBoxLayout()
        legend_group.setLayout(legend_layout)
        legend_layout.addWidget(QLabel("  (Ignore) or [Ignore]  →  Skip this layer"))
        legend_layout.addWidget(QLabel("  (Merge)  or [Merge]   →  Flatten group before export"))
        layout.addWidget(legend_group)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        export_btn = QPushButton("Export")
        export_btn.setDefault(True)
        export_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(export_btn)
        layout.addLayout(btn_row)

    def _browse_folder(self):
        start_dir = self.folder_input.text().strip()
        folder = QFileDialog.getExistingDirectory(
            self, "Select Output Folder", start_dir
        )
        if folder:
            self.folder_input.setText(folder)

    def _load_settings(self):
        last_folder = self._settings.value("output_folder", "", type=str)
        last_filename = self._settings.value(
            "output_filename",
            "animation.json",
            type=str
        )
        if last_folder:
            self.folder_input.setText(last_folder)
        self.filename_input.setText(last_filename or "animation.json")

    def save_settings(self):
        self._settings.setValue("output_folder", self.get_output_folder())
        self._settings.setValue("output_filename", self.get_output_filename())

    # --- Getters ---
    def get_output_folder(self):
        return self.folder_input.text().strip()

    def get_output_filename(self):
        raw = self.filename_input.text().strip()
        if not raw:
            raw = "animation.json"
        if not raw.lower().endswith(".json"):
            raw += ".json"
        return raw

    def get_fps_override(self):
        return self.fps_spin.value()

    def should_embed_images(self):
        return self.embed_images.isChecked()

    def should_skip_invisible(self):
        return self.ignore_invisible.isChecked()
