import logging
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from core.capcut_tools import (
    apply_perfect_motion,
    build_basic_timeline,
    build_timeline_video_project,
    build_pairs,
    clear_project,
    create_timeline,
    discover_projects,
    generated_motion,
    parse_zoom_settings,
    render_pairs,
    render_project,
    render_timeline_video,
    scan_batch_folders,
    sorted_files,
    validate_fps,
    validate_quality,
    prepare_timeline_video_jobs,
)
from core.system_config import load_system_config, save_system_config


class CopyableTableWidget(QTableWidget):
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            indexes = sorted(self.selectedIndexes(), key=lambda item: (item.row(), item.column()))
            if indexes:
                rows = {}
                for index in indexes:
                    rows.setdefault(index.row(), []).append(index.data() or "")
                QApplication.clipboard().setText(
                    "\n".join("\t".join(values) for values in rows.values())
                )
            return
        super().keyPressEvent(event)


class CapcutWorker(QThread):
    progress = pyqtSignal(str, int, int)
    completed = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, operation, **options):
        super().__init__()
        self.operation = operation
        self.options = options
        self._cancelled = False

    def stop(self):
        self._cancelled = True

    def run(self):
        try:
            if self.operation == "timeline":
                self.progress.emit("Đang tạo timeline và chuyển động...", 0, 1)
                pair_count, motion_count = create_timeline(
                    self.options["project"], self.options["images"], self.options["audios"], self.options["zoom"]
                )
                self.progress.emit("Đã tạo timeline", 1, 1)
                self.completed.emit({"operation": "timeline", "pairs": pair_count, "motions": motion_count})
            elif self.operation == "clear":
                self.progress.emit("Đang dọn sạch dự án...", 0, 1)
                clear_project(self.options["project"])
                self.progress.emit("Đã dọn sạch dự án", 1, 1)
                self.completed.emit({"operation": "clear"})
            elif self.operation == "export":
                output, encoder = render_project(
                    self.options["project"], self.options["project_name"], self.options["output"],
                    self.options["quality"], self.options["fps"],
                    self._render_progress, lambda: self._cancelled,
                    self.options["smooth_zoom"],
                )
                self.completed.emit({
                    "operation": "export", "output": str(output),
                    "encoder": encoder, "fps": self.options["fps"],
                })
            elif self.operation == "batch":
                self._run_batch()
            elif self.operation == "srt_video":
                self._run_srt_video()
        except InterruptedError as exc:
            self.completed.emit({"operation": self.operation, "stopped": True, "message": str(exc)})
        except Exception as exc:
            self.failed.emit(str(exc))

    def _render_progress(self, current, total):
        self.progress.emit(f"Đang render đoạn {current}/{total}", current, total)

    def _run_batch(self):
        jobs, skipped = scan_batch_folders(self.options["root"])
        if not jobs:
            self.completed.emit({
                "operation": "batch", "success": [], "skipped": skipped, "errors": [],
                "encoder": None, "fps": self.options["fps"],
            })
            return
        success, errors, encoder_label = [], [], None
        for index, job in enumerate(jobs, 1):
            if self._cancelled:
                self.completed.emit({
                    "operation": "batch", "success": success, "skipped": skipped,
                    "errors": errors, "stopped": True,
                    "encoder": encoder_label, "fps": self.options["fps"],
                })
                return
            self.progress.emit(f"Đang xử lý {index}/{len(jobs)}: {job.name}", index - 1, len(jobs))
            try:
                clear_project(self.options["project"], unique_backup=True)
                pairs = build_pairs(job.images, job.audios)
                json_path = Path(self.options["project"]) / "draft_content.json"
                build_basic_timeline(json_path, pairs)
                apply_perfect_motion(json_path, *self.options["zoom"])
                _, encoder_label = render_pairs(
                    job.name, job.path, pairs, generated_motion(json_path), self.options["quality"],
                    self.options["fps"],
                    cancelled=lambda: self._cancelled,
                    smooth_zoom=self.options["smooth_zoom"],
                )
                success.append(job.name)
            except InterruptedError:
                self.completed.emit({
                    "operation": "batch", "success": success, "skipped": skipped,
                    "errors": errors, "stopped": True,
                    "encoder": encoder_label, "fps": self.options["fps"],
                })
                return
            except Exception as exc:
                errors.append((job.name, str(exc)))
            self.progress.emit(f"Đã xử lý {index}/{len(jobs)}", index, len(jobs))
        self.completed.emit({
            "operation": "batch", "success": success, "skipped": skipped, "errors": errors,
            "encoder": encoder_label, "fps": self.options["fps"],
        })

    def _run_srt_video(self):
        jobs = self.options["jobs"]
        records = list(self.options["skipped"])
        encoder_label = None

        def stopped_record(job, elapsed=0.0):
            return {
                "timeline": job.timeline_path.name, "mp3": job.audio_path.name,
                "elapsed": elapsed, "status": "ĐÃ DỪNG", "result": "",
                "detail": "Chưa hoàn tất do người dùng dừng phiên.",
            }

        def finish_stopped(remaining_jobs):
            records.extend(stopped_record(job) for job in remaining_jobs)
            self.completed.emit({
                "operation": "srt_video", "records": records, "stopped": True,
                "encoder": encoder_label, "fps": self.options["fps"],
                "quality": self.options["quality"],
            })

        for offset, job in enumerate(jobs):
            index = offset + 1
            if self._cancelled:
                finish_stopped(jobs[offset:])
                return
            self.progress.emit(
                f"Đang tạo video {index}/{len(jobs)}: {job.audio_path.name}", index - 1, len(jobs)
            )
            started_at = time.perf_counter()
            try:
                clear_project(self.options["project"], unique_backup=True)
                json_path = Path(self.options["project"]) / "draft_content.json"
                build_timeline_video_project(json_path, job)
                apply_perfect_motion(json_path, *self.options["zoom"])
                motions = generated_motion(json_path)
                output, encoder_label = render_timeline_video(
                    job, self.options["quality"], self.options["fps"], self.options["zoom"],
                    cancelled=lambda: self._cancelled,
                    smooth_zoom=self.options["smooth_zoom"],
                    motions=motions,
                )
                records.append({
                    "timeline": job.timeline_path.name, "mp3": job.audio_path.name,
                    "elapsed": time.perf_counter() - started_at, "status": "THÀNH CÔNG",
                    "result": str(output), "detail": "",
                })
            except InterruptedError:
                records.append(stopped_record(job, time.perf_counter() - started_at))
                finish_stopped(jobs[offset + 1:])
                return
            except Exception as exc:
                records.append({
                    "timeline": job.timeline_path.name, "mp3": job.audio_path.name,
                    "elapsed": time.perf_counter() - started_at, "status": "LỖI",
                    "result": "", "detail": str(exc),
                })
            self.progress.emit(f"Đã xử lý {index}/{len(jobs)} video", index, len(jobs))
        self.completed.emit({
            "operation": "srt_video", "records": records, "encoder": encoder_label,
            "fps": self.options["fps"], "quality": self.options["quality"],
        })


class PathRow(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, placeholder, button_text="Chọn folder"):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.button = QPushButton(f"📁 {button_text}")
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)
        self.button.clicked.connect(self._browse)
        self.edit.textChanged.connect(self.changed)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Chọn folder", self.edit.text().strip())
        if folder:
            self.edit.setText(folder)

    def text(self):
        return self.edit.text().strip()

    def setText(self, text):
        self.edit.setText(str(text or ""))

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.edit.setEnabled(enabled)
        self.button.setEnabled(enabled)


class MultiFilePicker(QWidget):
    changed = pyqtSignal()

    def __init__(self, title, file_filter, suffixes, allow_folder=False):
        super().__init__()
        self.title = title
        self.file_filter = file_filter
        self.suffixes = tuple(suffix.lower() for suffix in suffixes)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setMaximumHeight(76)
        self.list.model().rowsInserted.connect(lambda *args: self.changed.emit())
        self.list.model().rowsRemoved.connect(lambda *args: self.changed.emit())
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        choose = QPushButton("📄 Chọn nhiều file")
        choose.clicked.connect(self._choose_files)
        buttons.addWidget(choose)
        if allow_folder:
            folder = QPushButton("📁 Quét folder")
            folder.clicked.connect(self._choose_folder)
            buttons.addWidget(folder)
        remove = QPushButton("Bỏ file đã chọn")
        remove.clicked.connect(self._remove_selected)
        clear = QPushButton("Xóa danh sách")
        clear.clicked.connect(self.list.clear)
        buttons.addWidget(remove)
        buttons.addWidget(clear)
        buttons.addStretch()
        layout.addLayout(buttons)

    def _choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, self.title, "", self.file_filter)
        self.add_paths(paths)

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, self.title)
        if folder:
            paths = sorted(
                (path for path in Path(folder).rglob("*") if path.is_file() and path.suffix.lower() in self.suffixes),
                key=lambda path: str(path).lower(),
            )
            self.add_paths(paths)

    def add_paths(self, paths):
        existing = {self.list.item(row).text().casefold() for row in range(self.list.count())}
        for path in paths:
            path = str(Path(path))
            if Path(path).suffix.lower() in self.suffixes and path.casefold() not in existing:
                self.list.addItem(path)
                existing.add(path.casefold())

    def _remove_selected(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def paths(self):
        return [self.list.item(row).text() for row in range(self.list.count())]


class ZoomRow(QWidget):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.minimum = self._spin(100)
        self.maximum = self._spin(135)
        self.difference = self._spin(15)
        for label, spin in (("Min", self.minimum), ("Max", self.maximum), ("Min Diff", self.difference)):
            layout.addWidget(QLabel(label))
            layout.addWidget(spin)
            spin.valueChanged.connect(lambda *args: self.changed.emit())
        layout.addStretch()

    @staticmethod
    def _spin(value):
        spin = QDoubleSpinBox()
        spin.setRange(1, 500)
        spin.setDecimals(1)
        spin.setSuffix(" %")
        spin.setValue(value)
        return spin

    def values(self):
        return parse_zoom_settings(
            str(self.minimum.value()), str(self.maximum.value()), str(self.difference.value())
        )

    def set_values(self, values):
        if not isinstance(values, (list, tuple)) or len(values) != 3:
            return
        try:
            minimum, maximum, difference = map(float, values)
            parse_zoom_settings(str(minimum), str(maximum), str(difference))
        except (TypeError, ValueError):
            return
        self.minimum.setValue(minimum)
        self.maximum.setValue(maximum)
        self.difference.setValue(difference)


class CapcutView(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.projects = {}
        self._controls = []
        self._build_ui()
        self.refresh_projects()
        self._load_settings()
        self._connect_setting_signals()

    @property
    def is_processing(self):
        return bool(self.worker and self.worker.isRunning())

    @staticmethod
    def _primary(color="#2563eb"):
        return (
            f"QPushButton{{background:{color};color:white;font-weight:800;padding:10px 18px;"
            "border:0;border-radius:6px;}QPushButton:disabled{background:#4b5563;color:#9ca3af;}"
        )

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)
        banner = QLabel("🎬 CAPCUT — TIMELINE & XUẤT VIDEO")
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #7c3aed,stop:.5 #db2777,stop:1 #ea580c);"
            "color:white;font-size:20px;font-weight:900;padding:14px;border-radius:8px;letter-spacing:1px;"
        )
        root.addWidget(banner)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._single_tab(), "🎞 Một dự án")
        self.tabs.addTab(self._batch_tab(), "📚 Hàng loạt")
        self.tabs.addTab(self._srt_video_tab(), "Tạo Video Ảnh Srt")
        root.addWidget(self.tabs, 1)

        status_frame = QFrame()
        status_frame.setStyleSheet("QFrame{background:#252536;border:1px solid #3b3b52;border-radius:7px;}")
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 4, 10, 4)
        status_layout.setSpacing(3)
        self.status = QLabel("Sẵn sàng")
        self.status.setFixedHeight(15)
        self.status.setStyleSheet("color:#cdd6f4;font-size:11px;font-weight:700;border:0;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setFormat("Chưa chạy")
        self.progress.setFixedHeight(14)
        self.progress.setStyleSheet(
            "QProgressBar{font-size:10px;border:1px solid #3b3b52;border-radius:3px;text-align:center;}"
            "QProgressBar::chunk{border-radius:2px;}"
        )
        status_layout.addWidget(self.status)
        status_layout.addWidget(self.progress)
        root.addWidget(status_frame)

        self.result_group = QGroupBox("Kết quả gần nhất")
        result_layout = QVBoxLayout(self.result_group)
        self.result_title = QLabel("—")
        self.result_title.setStyleSheet("color:#22c55e;font-size:15px;font-weight:800;")
        result_layout.addWidget(self.result_title)
        self.result_table = CopyableTableWidget(0, 2)
        self.result_table.setHorizontalHeaderLabels(["Mục", "Kết quả"])
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.result_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.result_table.setMaximumHeight(230)
        result_layout.addWidget(self.result_table)
        self.result_group.hide()
        root.addWidget(self.result_group)

    def _project_row(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        combo = QComboBox()
        refresh = QPushButton("↻ Làm mới")
        refresh.clicked.connect(self.refresh_projects)
        layout.addWidget(combo, 1)
        layout.addWidget(refresh)
        self._controls.extend((combo, refresh))
        return widget, combo

    def _single_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        sources = QGroupBox("1. Dữ liệu và dự án")
        form = QFormLayout(sources)
        self.single_audio = PathRow("Folder chứa MP3")
        self.single_images = PathRow("Folder chứa ảnh JPG/PNG")
        project_row, self.single_project = self._project_row()
        self.single_zoom = ZoomRow()
        self.single_counts = QLabel("0 ảnh  •  0 MP3")
        self.single_counts.setStyleSheet("color:#60a5fa;font-weight:700;")
        form.addRow("Folder MP3:", self.single_audio)
        form.addRow("Folder ảnh:", self.single_images)
        form.addRow("Đã nhận:", self.single_counts)
        form.addRow("Dự án CapCut:", project_row)
        form.addRow("Zoom chuyển động:", self.single_zoom)
        layout.addWidget(sources)

        timeline_actions = QHBoxLayout()
        self.btn_timeline = QPushButton("✨ TẠO TIMELINE + PERFECT MOTION")
        self.btn_timeline.setStyleSheet(self._primary("#16a34a"))
        self.btn_clear = QPushButton("🧹 DỌN SẠCH DỰ ÁN")
        self.btn_clear.setStyleSheet(self._primary("#dc2626"))
        timeline_actions.addWidget(self.btn_timeline)
        timeline_actions.addWidget(self.btn_clear)
        timeline_actions.addStretch()
        layout.addLayout(timeline_actions)

        export = QGroupBox("2. Xuất video nền bằng FFmpeg — không cần mở CapCut")
        export_form = QFormLayout(export)
        self.single_output = PathRow("Folder nhận video MP4")
        self.single_quality = QComboBox()
        self.single_quality.addItems(("1080P", "2K", "4K"))
        self.single_fps = QComboBox()
        self.single_fps.addItems(("24 FPS", "25 FPS", "30 FPS", "50 FPS", "60 FPS"))
        self.single_fps.setCurrentText("30 FPS")
        self.single_smooth_zoom = QCheckBox("Zoom mượt, căn tâm (nội suy sub-pixel)")
        export_form.addRow("Folder xuất:", self.single_output)
        export_form.addRow("Độ phân giải:", self.single_quality)
        export_form.addRow("Tốc độ khung hình:", self.single_fps)
        export_form.addRow("Kiểu zoom:", self.single_smooth_zoom)
        self.btn_export = QPushButton("🚀 XUẤT VIDEO")
        self.btn_export.setStyleSheet(self._primary())
        export_form.addRow("", self.btn_export)
        layout.addWidget(export)
        layout.addStretch()
        self._controls.extend((self.single_audio, self.single_images, self.single_zoom, self.single_output,
                               self.single_quality, self.single_fps, self.single_smooth_zoom,
                               self.btn_timeline, self.btn_clear, self.btn_export))
        self.btn_timeline.clicked.connect(self.start_timeline)
        self.btn_clear.clicked.connect(self.start_clear)
        self.btn_export.clicked.connect(self.start_export)
        self.single_audio.changed.connect(self._update_single_counts)
        self.single_images.changed.connect(self._update_single_counts)
        return tab

    def _batch_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        config = QGroupBox("Cấu hình xử lý hàng loạt")
        form = QFormLayout(config)
        project_row, self.batch_project = self._project_row()
        self.batch_root = PathRow("Folder tổng chứa các folder con")
        self.batch_zoom = ZoomRow()
        self.batch_quality = QComboBox()
        self.batch_quality.addItems(("1080P", "2K", "4K"))
        self.batch_fps = QComboBox()
        self.batch_fps.addItems(("24 FPS", "25 FPS", "30 FPS", "50 FPS", "60 FPS"))
        self.batch_fps.setCurrentText("30 FPS")
        self.batch_smooth_zoom = QCheckBox("Zoom mượt, căn tâm (nội suy sub-pixel)")
        form.addRow("Dự án CapCut:", project_row)
        form.addRow("Folder tổng:", self.batch_root)
        form.addRow("Zoom chuyển động:", self.batch_zoom)
        form.addRow("Độ phân giải:", self.batch_quality)
        form.addRow("Tốc độ khung hình:", self.batch_fps)
        form.addRow("Kiểu zoom:", self.batch_smooth_zoom)
        layout.addWidget(config)
        note = QLabel(
            "Mỗi folder con cần có Ảnh\\ và full\\audio\\. Folder đã có MP4 hoặc số ảnh/MP3 không khớp sẽ được bỏ qua."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#a6adc8;padding:8px;")
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.btn_batch = QPushButton("▶ BẮT ĐẦU XỬ LÝ HÀNG LOẠT")
        self.btn_batch.setStyleSheet(self._primary("#16a34a"))
        self.btn_stop = QPushButton("■ DỪNG")
        self.btn_stop.setStyleSheet(self._primary("#dc2626"))
        self.btn_stop.setEnabled(False)
        actions.addWidget(self.btn_batch)
        actions.addWidget(self.btn_stop)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self._controls.extend((
            self.batch_project, self.batch_root, self.batch_zoom, self.batch_quality,
            self.batch_fps, self.batch_smooth_zoom, self.btn_batch,
        ))
        self.btn_batch.clicked.connect(self.start_batch)
        self.btn_stop.clicked.connect(self.stop_processing)
        return tab

    def _srt_video_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        inputs = QGroupBox("Dữ liệu đầu vào — chọn nhiều timeline và nhiều MP3")
        form = QFormLayout(inputs)
        project_row, self.srt_project = self._project_row()
        self.srt_timelines = MultiFilePicker(
            "Chọn timeline JSON hoặc folder JSON", "Timeline JSON (*.json)", (".json",), allow_folder=True
        )
        self.srt_images = PathRow("Folder gốc chứa các folder ảnh con")
        self.srt_audios = MultiFilePicker(
            "Chọn MP3 hoặc folder MP3", "Audio MP3 (*.mp3)", (".mp3",), allow_folder=True
        )
        self.srt_output = PathRow("Để trống: xuất cạnh từng file MP3")
        form.addRow("Dự án CapCut:", project_row)
        form.addRow("Timeline JSON:", self.srt_timelines)
        form.addRow("Folder gốc ảnh:", self.srt_images)
        form.addRow("File/Folder MP3:", self.srt_audios)
        form.addRow("Folder output:", self.srt_output)
        layout.addWidget(inputs)

        settings = QGroupBox("Thiết lập video")
        settings_form = QFormLayout(settings)
        self.srt_zoom = ZoomRow()
        self.srt_quality = QComboBox()
        self.srt_quality.addItems(("1080P", "2K", "4K"))
        self.srt_fps = QComboBox()
        self.srt_fps.addItems(("24 FPS", "25 FPS", "30 FPS", "50 FPS", "60 FPS"))
        self.srt_fps.setCurrentText("30 FPS")
        self.srt_smooth_zoom = QCheckBox("Zoom mượt, căn tâm (nội suy sub-pixel)")
        quality_fps = QWidget()
        quality_layout = QHBoxLayout(quality_fps)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.addWidget(QLabel("Độ phân giải"))
        quality_layout.addWidget(self.srt_quality)
        quality_layout.addSpacing(20)
        quality_layout.addWidget(QLabel("FPS"))
        quality_layout.addWidget(self.srt_fps)
        quality_layout.addStretch()
        settings_form.addRow("Zoom / Pan:", self.srt_zoom)
        settings_form.addRow("Xuất video:", quality_fps)
        settings_form.addRow("Kiểu zoom:", self.srt_smooth_zoom)
        layout.addWidget(settings)

        note = QLabel(
            "Ghép JSON ↔ MP3 ↔ folder ảnh con bằng phần tên trước dấu “_”. "
            "Trong folder ảnh, tên 002_* sẽ khớp với scene 2; ảnh _2K được ưu tiên."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#a6adc8;padding:5px;")
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.btn_srt_run = QPushButton("▶ KIỂM TRA VÀ CHẠY")
        self.btn_srt_run.setStyleSheet(self._primary("#16a34a"))
        self.btn_srt_stop = QPushButton("■ DỪNG")
        self.btn_srt_stop.setStyleSheet(self._primary("#dc2626"))
        self.btn_srt_stop.setEnabled(False)
        actions.addWidget(self.btn_srt_run)
        actions.addWidget(self.btn_srt_stop)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self._controls.extend((
            self.srt_project, self.srt_timelines, self.srt_images, self.srt_audios, self.srt_output,
            self.srt_zoom, self.srt_quality, self.srt_fps, self.btn_srt_run,
            self.srt_smooth_zoom,
        ))
        self.btn_srt_run.clicked.connect(self.start_srt_video)
        self.btn_srt_stop.clicked.connect(self.stop_processing)
        return tab

    def refresh_projects(self):
        selected_single = self.single_project.currentText() if hasattr(self, "single_project") else ""
        selected_batch = self.batch_project.currentText() if hasattr(self, "batch_project") else ""
        selected_srt = self.srt_project.currentText() if hasattr(self, "srt_project") else ""
        self.projects = discover_projects()
        names = list(self.projects)
        selections = (
            (self.single_project, selected_single),
            (self.batch_project, selected_batch),
            (self.srt_project, selected_srt),
        )
        for combo, selected in selections:
            combo.clear()
            combo.addItems(names)
            if selected in names:
                combo.setCurrentText(selected)
        if not names:
            self._show_error("Không tìm thấy dự án CapCut trong LOCALAPPDATA.")

    @staticmethod
    def _restore_combo(combo, value):
        if value is None:
            return
        index = combo.findText(str(value))
        if index >= 0:
            combo.setCurrentIndex(index)

    def _load_settings(self):
        settings = load_system_config().get("capcut", {})
        if not isinstance(settings, dict):
            settings = {}

        single = settings.get("single", {})
        if isinstance(single, dict):
            self.single_audio.setText(single.get("audio", ""))
            self.single_images.setText(single.get("images", ""))
            self.single_output.setText(single.get("output", ""))
            self.single_zoom.set_values(single.get("zoom"))
            self._restore_combo(self.single_project, single.get("project"))
            self._restore_combo(self.single_quality, single.get("quality"))
            self._restore_combo(self.single_fps, single.get("fps"))
            self.single_smooth_zoom.setChecked(bool(single.get("smooth_zoom", False)))

        batch = settings.get("batch", {})
        if isinstance(batch, dict):
            self.batch_root.setText(batch.get("root", ""))
            self.batch_zoom.set_values(batch.get("zoom"))
            self._restore_combo(self.batch_project, batch.get("project"))
            self._restore_combo(self.batch_quality, batch.get("quality"))
            self._restore_combo(self.batch_fps, batch.get("fps"))
            self.batch_smooth_zoom.setChecked(bool(batch.get("smooth_zoom", False)))

        srt = settings.get("srt_video", {})
        if isinstance(srt, dict):
            self._restore_combo(self.srt_project, srt.get("project"))
            self.srt_timelines.add_paths(srt.get("timelines", []))
            self.srt_images.setText(srt.get("images", ""))
            self.srt_audios.add_paths(srt.get("audios", []))
            self.srt_output.setText(srt.get("output", ""))
            self.srt_zoom.set_values(srt.get("zoom"))
            self._restore_combo(self.srt_quality, srt.get("quality"))
            self._restore_combo(self.srt_fps, srt.get("fps"))
            self.srt_smooth_zoom.setChecked(bool(srt.get("smooth_zoom", False)))

        try:
            tab_index = int(settings.get("tab", 0))
        except (TypeError, ValueError):
            tab_index = 0
        if 0 <= tab_index < self.tabs.count():
            self.tabs.setCurrentIndex(tab_index)
        self._update_single_counts()

    def _connect_setting_signals(self):
        self.tabs.currentChanged.connect(self._save_settings)
        for path_row in (
            self.single_audio, self.single_images, self.single_output,
            self.batch_root, self.srt_images, self.srt_output,
        ):
            path_row.changed.connect(self._save_settings)
        for picker in (self.srt_timelines, self.srt_audios):
            picker.changed.connect(self._save_settings)
        for zoom in (self.single_zoom, self.batch_zoom, self.srt_zoom):
            zoom.changed.connect(self._save_settings)
        for checkbox in (
            self.single_smooth_zoom, self.batch_smooth_zoom, self.srt_smooth_zoom,
        ):
            checkbox.toggled.connect(self._save_settings)
        for combo in (
            self.single_project, self.single_quality, self.single_fps,
            self.batch_project, self.batch_quality, self.batch_fps,
            self.srt_project, self.srt_quality, self.srt_fps,
        ):
            combo.currentIndexChanged.connect(self._save_settings)

    @staticmethod
    def _zoom_state(zoom):
        return [zoom.minimum.value(), zoom.maximum.value(), zoom.difference.value()]

    def _save_settings(self, *args):
        try:
            save_system_config({
                "capcut": {
                    "tab": self.tabs.currentIndex(),
                    "single": {
                        "audio": self.single_audio.text(),
                        "images": self.single_images.text(),
                        "project": self.single_project.currentText(),
                        "zoom": self._zoom_state(self.single_zoom),
                        "output": self.single_output.text(),
                        "quality": self.single_quality.currentText(),
                        "fps": self.single_fps.currentText(),
                        "smooth_zoom": self.single_smooth_zoom.isChecked(),
                    },
                    "batch": {
                        "project": self.batch_project.currentText(),
                        "root": self.batch_root.text(),
                        "zoom": self._zoom_state(self.batch_zoom),
                        "quality": self.batch_quality.currentText(),
                        "fps": self.batch_fps.currentText(),
                        "smooth_zoom": self.batch_smooth_zoom.isChecked(),
                    },
                    "srt_video": {
                        "project": self.srt_project.currentText(),
                        "timelines": self.srt_timelines.paths(),
                        "images": self.srt_images.text(),
                        "audios": self.srt_audios.paths(),
                        "output": self.srt_output.text(),
                        "zoom": self._zoom_state(self.srt_zoom),
                        "quality": self.srt_quality.currentText(),
                        "fps": self.srt_fps.currentText(),
                        "smooth_zoom": self.srt_smooth_zoom.isChecked(),
                    },
                }
            })
        except Exception as exc:
            logging.warning("[CapCut] Không thể lưu thiết lập: %s", exc)

    def _update_single_counts(self):
        image_folder, audio_folder = self.single_images.text(), self.single_audio.text()
        images = len(sorted_files(image_folder, (".png", ".jpg", ".jpeg"))) if image_folder else 0
        audios = len(sorted_files(audio_folder, (".mp3",))) if audio_folder else 0
        self.single_counts.setText(f"{images} ảnh  •  {audios} MP3")
        self.single_counts.setStyleSheet(
            f"color:{'#22c55e' if images and images == audios else '#f59e0b'};font-weight:700;"
        )

    def _selected_project(self, combo):
        name = combo.currentText()
        project = self.projects.get(name)
        if not project:
            raise ValueError("Hãy chọn một dự án CapCut hợp lệ.")
        return name, project

    def start_timeline(self):
        try:
            _, project = self._selected_project(self.single_project)
            if not Path(self.single_audio.text()).is_dir() or not Path(self.single_images.text()).is_dir():
                raise ValueError("Hãy chọn đủ folder MP3 và folder ảnh.")
            self._start_worker("timeline", project=project, images=self.single_images.text(),
                               audios=self.single_audio.text(), zoom=self.single_zoom.values())
        except ValueError as exc:
            self._show_error(str(exc))

    def start_clear(self):
        try:
            name, project = self._selected_project(self.single_project)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        answer = QMessageBox.question(
            self, "Xác nhận dọn dự án", f"Dọn toàn bộ timeline của “{name}”?\n\nFile ảnh, MP3 và video bên ngoài không bị xóa.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._start_worker("clear", project=project)

    def start_export(self):
        try:
            name, project = self._selected_project(self.single_project)
            output = self.single_output.text()
            if not output:
                raise ValueError("Hãy chọn folder xuất video.")
            quality = validate_quality(self.single_quality.currentText())
            fps = validate_fps(self.single_fps.currentText().split()[0])
            self._start_worker(
                "export", project=project, project_name=name, output=output,
                quality=quality, fps=fps, smooth_zoom=self.single_smooth_zoom.isChecked(),
            )
        except ValueError as exc:
            self._show_error(str(exc))

    def start_batch(self):
        try:
            _, project = self._selected_project(self.batch_project)
            root = self.batch_root.text()
            if not Path(root).is_dir():
                raise ValueError("Hãy chọn folder tổng hợp lệ.")
            self._start_worker("batch", project=project, root=root, zoom=self.batch_zoom.values(),
                               quality=validate_quality(self.batch_quality.currentText()),
                               fps=validate_fps(self.batch_fps.currentText().split()[0]),
                               smooth_zoom=self.batch_smooth_zoom.isChecked())
        except ValueError as exc:
            self._show_error(str(exc))

    def start_srt_video(self):
        if self.is_processing:
            return
        try:
            _, project = self._selected_project(self.srt_project)
            quality = validate_quality(self.srt_quality.currentText())
            fps = validate_fps(self.srt_fps.currentText().split()[0])
            zoom = self.srt_zoom.values()
            jobs, skipped, errors = prepare_timeline_video_jobs(
                self.srt_timelines.paths(), self.srt_audios.paths(), self.srt_images.text(),
                self.srt_output.text() or None,
            )
        except (OSError, ValueError) as exc:
            self._show_validation_errors([("Cấu hình", str(exc))])
            return
        if errors:
            self._show_validation_errors(errors)
            return
        if not jobs:
            self._on_completed({
                "operation": "srt_video", "records": skipped,
                "encoder": None, "fps": fps, "quality": quality,
            })
            return
        self._start_worker(
            "srt_video", jobs=jobs, skipped=skipped, quality=quality, fps=fps, zoom=zoom,
            smooth_zoom=self.srt_smooth_zoom.isChecked(), project=project,
        )

    def _show_validation_errors(self, errors):
        self.status.setText(f"Dữ liệu chưa hợp lệ: {len(errors)} lỗi — chưa render video nào")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Kiểm tra thất bại")
        records = [
            {
                "timeline": name, "mp3": "", "elapsed": 0,
                "status": "LỖI", "result": "", "detail": reason,
            }
            for name, reason in errors
        ]
        self._show_srt_results(
            records,
            {"quality": self.srt_quality.currentText(),
             "fps": self.srt_fps.currentText().split()[0], "encoder": None},
            title_override=f"Dữ liệu chưa hợp lệ — {len(errors)} lỗi, chưa render video nào",
        )

    def _start_worker(self, operation, **options):
        if self.is_processing:
            return
        self.result_group.hide()
        self._set_controls(False)
        can_stop = operation in ("export", "batch", "srt_video")
        self.btn_stop.setEnabled(can_stop)
        self.btn_srt_stop.setEnabled(can_stop)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Đang chuẩn bị...")
        self.worker = CapcutWorker(operation, **options)
        self.worker.progress.connect(self._on_progress)
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def stop_processing(self):
        if self.is_processing:
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.btn_srt_stop.setEnabled(False)
            self.status.setText("Đang dừng sau đoạn hiện tại...")

    def _set_controls(self, enabled):
        for control in self._controls:
            control.setEnabled(enabled)

    def _on_progress(self, text, current, total):
        self.status.setText(text)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(current)
        self.progress.setFormat(f"{current}/{total}")

    def _on_completed(self, result):
        self._set_controls(True)
        self.btn_stop.setEnabled(False)
        self.btn_srt_stop.setEnabled(False)
        stopped = result.get("stopped", False)
        operation = result.get("operation")
        rows = []
        if operation == "timeline":
            rows = [("Cặp ảnh + MP3", result["pairs"]), ("Ảnh có chuyển động", result["motions"])]
        elif operation == "clear":
            rows = [("Dự án", "Đã dọn sạch"), ("Backup", "Đã lưu trong _tool_backups")]
        elif operation == "export":
            rows = [
                ("Video", result.get("output", "—")),
                ("Encoder", result.get("encoder", "Tự động")),
                ("FPS", result.get("fps", "—")),
            ]
        elif operation == "batch":
            rows = [
                ("Xuất thành công", len(result.get("success", []))),
                ("Bỏ qua", len(result.get("skipped", []))),
                ("Lỗi", len(result.get("errors", []))),
                ("Encoder", result.get("encoder") or "Tự động"),
                ("FPS", result.get("fps", "—")),
            ]
            details = result.get("skipped", []) + result.get("errors", [])
            if details:
                rows.append(("Chi tiết", "; ".join(f"{name}: {reason}" for name, reason in details)))
        self.status.setText("Đã dừng" if stopped else "Đã chạy xong")
        self.progress.setValue(self.progress.maximum())
        self.progress.setFormat("Đã dừng" if stopped else "Hoàn tất")
        if operation == "srt_video":
            self._show_srt_results(result.get("records", []), result, stopped)
        else:
            self._show_result("Đã dừng" if stopped else "Đã chạy xong", rows, stopped)

    def _on_failed(self, message):
        self._set_controls(True)
        self.btn_stop.setEnabled(False)
        self.btn_srt_stop.setEnabled(False)
        self.progress.setFormat("Lỗi")
        self.status.setText("Tác vụ thất bại")
        self._show_error(message)

    def _show_result(self, title, rows, error=False):
        self.result_group.setTitle("Kết quả gần nhất")
        self.result_title.setText(title)
        self.result_title.setStyleSheet(
            f"color:{'#ef4444' if error else '#22c55e'};font-size:15px;font-weight:800;"
        )
        self.result_table.clear()
        self.result_table.setColumnCount(2)
        self.result_table.setHorizontalHeaderLabels(["Mục", "Kết quả"])
        self.result_table.setRowCount(len(rows))
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, (label, value) in enumerate(rows):
            self.result_table.setItem(row, 0, QTableWidgetItem(str(label)))
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            self.result_table.setItem(row, 1, item)
        self.result_group.show()

    @staticmethod
    def _format_elapsed(seconds):
        seconds = max(0, int(round(float(seconds or 0))))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _show_srt_results(self, records, result, stopped=False, title_override=None):
        records = sorted(records, key=lambda item: item.get("mp3", "").casefold())
        counts = {
            "success": sum(item.get("status") == "THÀNH CÔNG" for item in records),
            "skipped": sum(item.get("status") == "BỎ QUA" for item in records),
            "error": sum(item.get("status") == "LỖI" for item in records),
            "stopped": sum(item.get("status") == "ĐÃ DỪNG" for item in records),
        }
        title = title_override or (
            f"{'Đã dừng' if stopped else 'Đã chạy xong'} — "
            f"Thành công: {counts['success']} | Bỏ qua: {counts['skipped']} | "
            f"Lỗi: {counts['error']} | Đã dừng: {counts['stopped']}"
        )
        self.result_title.setText(title)
        self.result_title.setStyleSheet(
            f"color:{'#ef4444' if title_override else '#f59e0b' if stopped else '#22c55e'};"
            "font-size:15px;font-weight:800;"
        )
        headers = [
            "STT", "Tên file timeline", "Tên file MP3", "Thời gian xử lý",
            "Trạng thái", "Đường dẫn kết quả",
        ]
        self.result_table.clear()
        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.setRowCount(len(records))
        self.result_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.result_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        colors = {
            "THÀNH CÔNG": "#22c55e", "BỎ QUA": "#f59e0b",
            "LỖI": "#ef4444", "ĐÃ DỪNG": "#a6adc8",
        }
        for row, record in enumerate(records):
            values = [
                row + 1,
                record.get("timeline", ""),
                record.get("mp3", ""),
                self._format_elapsed(record.get("elapsed", 0)),
                record.get("status", ""),
                record.get("result", ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                tooltip = record.get("detail", "") if column == 4 else str(value)
                item.setToolTip(tooltip or str(value))
                if column == 4:
                    item.setForeground(QColor(colors.get(str(value), "#cdd6f4")))
                self.result_table.setItem(row, column, item)
        header = self.result_table.horizontalHeader()
        for column in (0, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.result_group.setTitle(
            f"Kết quả — {result.get('quality', '—')} / {result.get('fps', '—')} FPS / "
            f"{result.get('encoder') or 'Không render'} — chọn ô và nhấn Ctrl+C để sao chép"
        )
        self.result_group.show()

    def _show_error(self, message):
        self._show_result("Có lỗi", [("Chi tiết", message)], True)

    def shutdown_tasks(self):
        self._save_settings()
        if self.is_processing:
            self.worker.stop()
            self.worker.wait(10000)
