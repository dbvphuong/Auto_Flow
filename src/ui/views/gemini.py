import json
import logging
import os
import re
import tempfile

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFontDatabase
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QGroupBox, QLabel,
    QLineEdit, QTextEdit, QPushButton, QSpinBox, QComboBox, QFileDialog, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QMessageBox,
    QDialog, QPlainTextEdit, QApplication, QListWidget, QDialogButtonBox,
    QInputDialog,
)

from core.workers import GeminiWorker, GEMINI_MAX_RETRIES
from common.gemini_languages import COUNTRIES, GEMINI_COUNTRY_OPTIONS, LANGUAGE_BY_COUNTRY
from common.logger import activity, progress as log_progress, warning as log_warning
from common.text_splitter import combine_single_line_chunks, split_text_into_single_line_chunks
from data.database import SessionLocal
from data.models import Account, GeminiBatch


class MasterPromptSettingsDialog(QDialog):
    """Create and maintain the Master Prompt templates used by GeminiView."""

    def __init__(self, parent, templates, selected_name=""):
        super().__init__(parent)
        self.templates = dict(templates)
        self.selected_name = selected_name
        self.setWindowTitle("Cài đặt Master Prompt")
        self.resize(760, 480)

        layout = QHBoxLayout(self)
        left_layout = QVBoxLayout()
        self.list_templates = QListWidget()
        left_layout.addWidget(QLabel("Danh sách template"))
        left_layout.addWidget(self.list_templates, 1)

        self.btn_new = QPushButton("＋ Tạo mới")
        self.btn_import = QPushButton("📄 Nạp file TXT")
        self.btn_rename = QPushButton("Đổi tên")
        self.btn_delete = QPushButton("Xóa")
        for button in (self.btn_new, self.btn_import, self.btn_rename, self.btn_delete):
            left_layout.addWidget(button)
        layout.addLayout(left_layout, 1)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Nội dung Master Prompt"))
        self.text_prompt = QPlainTextEdit()
        self.text_prompt.setPlaceholderText(
            "Điền trực tiếp Master Prompt tại đây hoặc dùng nút Nạp file TXT."
        )
        right_layout.addWidget(self.text_prompt, 1)
        self.btn_save = QPushButton("💾 Lưu nội dung")
        right_layout.addWidget(self.btn_save)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        right_layout.addWidget(buttons)
        layout.addLayout(right_layout, 3)

        self.list_templates.currentTextChanged.connect(self._load_current)
        self.btn_new.clicked.connect(self._create_template)
        self.btn_import.clicked.connect(self._import_template)
        self.btn_save.clicked.connect(self._save_current)
        self.btn_rename.clicked.connect(self._rename_current)
        self.btn_delete.clicked.connect(self._delete_current)
        self._refresh_list(selected_name)

    def _refresh_list(self, selected_name=""):
        self.list_templates.blockSignals(True)
        self.list_templates.clear()
        self.list_templates.addItems(self.templates.keys())
        matches = self.list_templates.findItems(
            selected_name, Qt.MatchFlag.MatchExactly
        )
        if matches:
            self.list_templates.setCurrentItem(matches[0])
        elif self.list_templates.count():
            self.list_templates.setCurrentRow(0)
        self.list_templates.blockSignals(False)
        self._load_current(self.list_templates.currentItem().text() if self.list_templates.currentItem() else "")

    def _ask_unique_name(self, title, initial=""):
        name, accepted = QInputDialog.getText(self, title, "Tên template:", text=initial)
        name = name.strip()
        if not accepted or not name:
            return ""
        duplicate = next(
            (item for item in self.templates if item.casefold() == name.casefold() and item != initial),
            None,
        )
        if duplicate:
            QMessageBox.warning(self, "Trùng tên", f"Template “{duplicate}” đã tồn tại.")
            return ""
        return name

    def _load_current(self, name):
        self.selected_name = name
        self.text_prompt.setPlainText(self.templates.get(name, ""))
        enabled = bool(name)
        self.btn_save.setEnabled(enabled)
        self.btn_rename.setEnabled(enabled)
        self.btn_delete.setEnabled(enabled)

    def _create_template(self):
        name = self._ask_unique_name("Tạo Master Prompt mới")
        if not name:
            return
        self.templates[name] = ""
        self._refresh_list(name)
        self.text_prompt.setFocus()

    def _import_template(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Nạp Master Prompt", "", "Text Files (*.txt *.md);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as source_file:
                content = source_file.read()
        except (OSError, UnicodeError) as exc:
            QMessageBox.critical(self, "Không đọc được file", str(exc))
            return
        default_name = os.path.splitext(os.path.basename(path))[0]
        name = self._ask_unique_name("Tên Master Prompt", default_name)
        if not name:
            return
        self.templates[name] = content
        self._refresh_list(name)

    def _save_current(self):
        name = self.list_templates.currentItem().text() if self.list_templates.currentItem() else ""
        if not name:
            return
        content = self.text_prompt.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "Nội dung trống", "Master Prompt không được để trống.")
            return
        self.templates[name] = content
        self.selected_name = name

    def _rename_current(self):
        old_name = self.list_templates.currentItem().text() if self.list_templates.currentItem() else ""
        if not old_name:
            return
        new_name = self._ask_unique_name("Đổi tên Master Prompt", old_name)
        if not new_name or new_name == old_name:
            return
        items = list(self.templates.items())
        self.templates = {
            (new_name if name == old_name else name): value for name, value in items
        }
        self._refresh_list(new_name)

    def _delete_current(self):
        name = self.list_templates.currentItem().text() if self.list_templates.currentItem() else ""
        if not name:
            return
        answer = QMessageBox.question(
            self, "Xóa Master Prompt", f"Xóa template “{name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self.templates[name]
        self._refresh_list()


class GeminiView(QWidget):
    WORD_PATTERN = re.compile(r"[^\W_]+(?:[’'.,-][^\W_]+)*", flags=re.UNICODE)
    STATUS_COLORS = {
        "PENDING": "#f59e0b", "RUNNING": "#3b82f6",
        "SUCCESS": "#22c55e", "FAILED": "#ef4444",
    }

    def __init__(self):
        super().__init__()
        self.workers = []
        self.task_queue = []
        self.active_workers_count = 0
        self.account_cursor = 0
        self.window_slot_count = 1
        self.is_paused = False
        self.session_skipped_account_ids = set()
        self.retry_last_account_ids = {}
        self.no_pro_accounts_notified = False
        self.country_checks = {}
        self.master_templates = {}
        self.country_templates = {}
        self._custom_country_selection = []
        self._active_country_template = None
        self._changing_country_template = False
        self._build_ui()
        self._connect_signals()
        self.queue_timer = QTimer(self)
        self.queue_timer.timeout.connect(self.process_queue)
        self._reset_interrupted_batches()
        self.load_config()
        self.load_batches()

    def _build_ui(self):
        main_layout = QHBoxLayout(self)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 10, 0)

        banner = QLabel("✦ GEMINI STORY ✦")
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1d4ed8,"
            "stop:0.5 #7c3aed,stop:1 #db2777);color:white;font-size:20px;"
            "font-weight:900;padding:12px;border-radius:8px;letter-spacing:3px;"
        )
        left_layout.addWidget(banner)

        content_group = QGroupBox("Nội dung đầu vào")
        content_layout = QGridLayout(content_group)
        self.combo_master_template = QComboBox()
        self.combo_master_template.setPlaceholderText("Chọn Master Prompt...")
        self.btn_master_settings = QPushButton("⚙ Cài đặt")
        self.btn_master_settings.setToolTip(
            "Nạp, tạo, chỉnh sửa, đổi tên hoặc xóa Master Prompt"
        )

        self.line_story_file = QLineEdit()
        self.line_story_file.setReadOnly(True)
        self.line_story_file.setPlaceholderText("Nhập text bên dưới hoặc chọn một file")
        self.btn_story_file = QPushButton("📄 Chọn file")
        self.text_story = QTextEdit()
        self.text_story.setPlaceholderText("Nhập nội dung cốt truyện...")
        self.text_story.setMaximumHeight(160)

        content_layout.addWidget(QLabel("Master Prompt:"), 0, 0)
        content_layout.addWidget(self.combo_master_template, 0, 1)
        content_layout.addWidget(self.btn_master_settings, 0, 2)
        content_layout.addWidget(QLabel("Cốt truyện:"), 1, 0)
        content_layout.addWidget(self.line_story_file, 1, 1)
        content_layout.addWidget(self.btn_story_file, 1, 2)
        content_layout.addWidget(self.text_story, 2, 0, 1, 3)
        left_layout.addWidget(content_group)

        country_group = QGroupBox("Quốc gia — tích bao nhiêu sẽ tạo bấy nhiêu batch")
        country_layout = QGridLayout(country_group)
        self.combo_country_template = QComboBox()
        self.combo_country_template.addItem("Tùy chọn", None)
        self.btn_save_country_template = QPushButton("💾 Lưu cấu hình")
        self.btn_rename_country_template = QPushButton("Đổi tên")
        self.btn_delete_country_template = QPushButton("Xóa")
        country_layout.addWidget(QLabel("Cấu hình:"), 0, 0)
        country_layout.addWidget(self.combo_country_template, 0, 1, 1, 2)
        country_layout.addWidget(self.btn_save_country_template, 1, 0)
        country_layout.addWidget(self.btn_rename_country_template, 1, 1)
        country_layout.addWidget(self.btn_delete_country_template, 1, 2)
        self.chk_all_countries = QCheckBox("Chọn tất cả")
        self.chk_all_countries.setStyleSheet("font-weight:bold;color:#c4b5fd;")
        country_layout.addWidget(self.chk_all_countries, 2, 0, 1, 3)
        for index, (country, display_name) in enumerate(GEMINI_COUNTRY_OPTIONS):
            checkbox = QCheckBox(display_name)
            checkbox.setToolTip(f"File đầu ra: {display_name}.txt")
            self.country_checks[country] = checkbox
            country_layout.addWidget(checkbox, index // 3 + 3, index % 3)
        left_layout.addWidget(country_group)

        run_group = QGroupBox("Điều kiện chạy")
        run_layout = QGridLayout(run_group)
        self.line_output_dir = QLineEdit()
        self.line_output_dir.setPlaceholderText("Folder lưu các file Quốc_gia.txt")
        self.btn_output_dir = QPushButton("📁")
        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 50)
        self.spin_max_continuations = QSpinBox()
        self.spin_max_continuations.setRange(1, 999)
        self.spin_max_continuations.setValue(10)
        self.line_done_marker = QLineEdit("[[DONE]]")
        self.line_done_marker.setPlaceholderText("Từ khóa hoàn thành")
        run_layout.addWidget(QLabel("Folder đầu ra:"), 0, 0)
        run_layout.addWidget(self.line_output_dir, 0, 1)
        run_layout.addWidget(self.btn_output_dir, 0, 2)
        run_layout.addWidget(QLabel("Số luồng đồng thời:"), 1, 0)
        run_layout.addWidget(self.spin_threads, 1, 1)
        run_layout.addWidget(QLabel("Số lần gõ '1' tối đa:"), 2, 0)
        run_layout.addWidget(self.spin_max_continuations, 2, 1)
        run_layout.addWidget(QLabel("Từ khóa Done:"), 3, 0)
        run_layout.addWidget(self.line_done_marker, 3, 1, 1, 2)
        self.btn_create_queue = QPushButton("＋ TẠO / CẬP NHẬT QUEUE THEO QUỐC GIA")
        self.btn_create_queue.setStyleSheet(self._button_style("#2563eb", "#3b82f6"))
        run_layout.addWidget(self.btn_create_queue, 4, 0, 1, 3)
        left_layout.addWidget(run_group)
        left_layout.addStretch()

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        title_row = QHBoxLayout()
        self.lbl_stats = QLabel()
        self.lbl_stats.setStyleSheet("font-weight:bold;color:#c4b5fd;padding:6px;")
        self.chk_all_batches = QCheckBox("Chọn tất cả")
        self.btn_delete = QPushButton("🗑 Xóa mục chọn")
        title_row.addWidget(self.lbl_stats)
        title_row.addStretch()
        title_row.addWidget(self.chk_all_batches)
        title_row.addWidget(self.btn_delete)
        right_layout.addLayout(title_row)

        self.table_queue = QTableWidget(0, 6)
        self.table_queue.setHorizontalHeaderLabels([
            "Chọn", "Quốc gia / File", "Account đang chạy",
            "Lần gõ 1", "Trạng thái", "Kết quả / Lỗi",
        ])
        self.table_queue.verticalHeader().setVisible(False)
        self.table_queue.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        header = self.table_queue.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        right_layout.addWidget(self.table_queue, 1)

        multi_split_controls = QHBoxLayout()
        self.btn_split_many_files = QPushButton("✂ TÁCH NHIỀU FILE TXT CON")
        self.btn_split_many_files.setStyleSheet(self._button_style("#b45309", "#d97706"))
        self.btn_split_many_files.setToolTip(
            "Tách một file TXT thành folder full chứa 1.txt, 2.txt, ...\n"
            "Mỗi dòng nguồn tạo ít nhất một file; mỗi file con chỉ có một dòng.\n"
            "Dòng quá dài sẽ ưu tiên tách ở cuối câu, sau đó đến dấu câu."
        )
        self.spin_many_min_words = QSpinBox()
        self.spin_many_min_words.setRange(1, 1000)
        self.spin_many_min_words.setValue(6)
        self.spin_many_max_words = QSpinBox()
        self.spin_many_max_words.setRange(1, 1000)
        self.spin_many_max_words.setValue(25)
        self.combo_many_source_mode = QComboBox()
        self.combo_many_source_mode.addItem("Giữ nguyên file gốc", "keep")
        self.combo_many_source_mode.addItem("Ghi đè file gốc", "overwrite")
        self.combo_many_source_mode.setToolTip(
            "Giữ nguyên: không thay đổi file đã chọn.\n"
            "Ghi đè: gộp nội dung các file con vào file gốc, mỗi file con là một dòng."
        )
        self.line_many_split_file = QLineEdit()
        self.line_many_split_file.setReadOnly(True)
        self.line_many_split_file.setPlaceholderText("Chọn một file .txt cần tách...")
        self.btn_many_split_file = QPushButton("📄 Chọn file")
        multi_split_controls.addWidget(self.btn_split_many_files)
        multi_split_controls.addWidget(QLabel("Từ tối thiểu:"))
        multi_split_controls.addWidget(self.spin_many_min_words)
        multi_split_controls.addWidget(QLabel("Từ tối đa:"))
        multi_split_controls.addWidget(self.spin_many_max_words)
        multi_split_controls.addWidget(QLabel("File gốc:"))
        multi_split_controls.addWidget(self.combo_many_source_mode)
        multi_split_controls.addWidget(self.line_many_split_file, 1)
        multi_split_controls.addWidget(self.btn_many_split_file)
        right_layout.addLayout(multi_split_controls)

        split_controls = QHBoxLayout()
        self.btn_split_lines = QPushButton("↵ CHIA DÒNG")
        self.btn_split_lines.setStyleSheet(self._button_style("#7c3aed", "#8b5cf6"))
        self.btn_split_lines.setToolTip(
            "Chia các dòng text quá dài theo số từ tối thiểu/tối đa.\n"
            "Ưu tiên ngắt ở cuối câu hoặc dấu câu hợp lý.\n"
            "Không thêm, xóa hay thay đổi nội dung."
        )
        self.spin_split_min_words = QSpinBox()
        self.spin_split_min_words.setRange(1, 1000)
        self.spin_split_min_words.setValue(15)
        self.spin_split_max_words = QSpinBox()
        self.spin_split_max_words.setRange(1, 1000)
        self.spin_split_max_words.setValue(30)
        self.combo_split_output_mode = QComboBox()
        self.combo_split_output_mode.addItem("Tạo mới", "new")
        self.combo_split_output_mode.addItem("Ghi đè", "overwrite")
        self.combo_split_output_mode.setToolTip(
            "Tạo mới: lưu cạnh file nguồn với đuôi _chia_dong. "
            "Ghi đè: thay trực tiếp file nguồn."
        )
        self.line_split_path = QLineEdit()
        self.line_split_path.setPlaceholderText("Chọn một file .txt hoặc folder chứa file .txt...")
        self.btn_split_file = QPushButton("📄 Chọn file")
        self.btn_split_folder = QPushButton("📁 Chọn folder")
        split_controls.addWidget(self.btn_split_lines)
        split_controls.addWidget(QLabel("Từ tối thiểu:"))
        split_controls.addWidget(self.spin_split_min_words)
        split_controls.addWidget(QLabel("Từ tối đa:"))
        split_controls.addWidget(self.spin_split_max_words)
        split_controls.addWidget(QLabel("Cách lưu:"))
        split_controls.addWidget(self.combo_split_output_mode)
        split_controls.addWidget(self.line_split_path, 1)
        split_controls.addWidget(self.btn_split_file)
        split_controls.addWidget(self.btn_split_folder)
        right_layout.addLayout(split_controls)

        smooth_controls = QHBoxLayout()
        self.btn_smooth_text = QPushButton("✨ LÀM MỊN TEXT")
        self.btn_smooth_text.setStyleSheet(self._button_style("#0f766e", "#0d9488"))
        self.btn_smooth_text.setToolTip(
            "Chỉ lấy và ghép nội dung nằm giữa các marker PART:\n\n"
            "[[PART_1_START]]\n"
            "Nội dung cần lấy\n"
            "[[PART_1_END]]\n\n"
            "Loại bỏ marker, hướng dẫn gõ tiếp và các dòng trống."
        )
        self.spin_smooth_min_words = QSpinBox()
        self.spin_smooth_min_words.setRange(0, 10_000_000)
        self.spin_smooth_min_words.setValue(0)
        self.spin_smooth_min_words.setMinimumWidth(110)
        self.spin_smooth_min_words.setToolTip(
            "Không tạo file nếu nội dung sau xử lý có ít hơn số từ này."
        )
        self.line_smooth_folder = QLineEdit()
        self.line_smooth_folder.setPlaceholderText("Folder chứa các file text Gemini...")
        self.btn_smooth_folder = QPushButton("📁 Chọn folder")
        smooth_controls.addWidget(self.btn_smooth_text)
        smooth_controls.addWidget(QLabel("Số từ tối thiểu:"))
        smooth_controls.addWidget(self.spin_smooth_min_words)
        smooth_controls.addWidget(self.line_smooth_folder, 1)
        smooth_controls.addWidget(self.btn_smooth_folder)
        right_layout.addLayout(smooth_controls)

        controls = QHBoxLayout()
        self.btn_close_chrome = QPushButton("🛑 Đóng Chrome")
        self.btn_run = QPushButton("▶ CHẠY NGAY")
        self.btn_run_selected = QPushButton("▶ CHẠY MỤC CHỌN")
        self.btn_pause = QPushButton("⏸ TẠM DỪNG")
        self.btn_stop = QPushButton("■ DỪNG")
        self.btn_retry = QPushButton("↻ CHẠY LẠI LỖI")
        self.btn_close_chrome.setStyleSheet(self._button_style("#ef4444", "#dc2626"))
        self.btn_run.setStyleSheet(self._button_style("#16a34a", "#22c55e"))
        self.btn_run_selected.setStyleSheet(self._button_style("#2563eb", "#3b82f6"))
        self.btn_pause.setStyleSheet(self._button_style("#d97706", "#f59e0b"))
        self.btn_stop.setStyleSheet(self._button_style("#dc2626", "#ef4444"))
        self.btn_retry.setStyleSheet(self._button_style("#7c3aed", "#8b5cf6"))
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        for button in (
            self.btn_close_chrome, self.btn_run, self.btn_run_selected,
            self.btn_pause, self.btn_stop, self.btn_retry,
        ):
            controls.addWidget(button)
        right_layout.addLayout(controls)

        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(right_panel)
        self.splitter.setSizes([480, 800])

    @staticmethod
    def _button_style(color, hover):
        return (
            f"QPushButton{{background:{color};color:white;font-weight:bold;padding:10px;"
            f"border:none;border-radius:5px;}}QPushButton:hover{{background:{hover};}}"
            "QPushButton:disabled{background:#4b5563;color:#9ca3af;}"
        )

    def _connect_signals(self):
        self.btn_master_settings.clicked.connect(self._open_master_prompt_settings)
        self.combo_master_template.currentIndexChanged.connect(self.save_config)
        self.btn_story_file.clicked.connect(
            lambda: self._choose_text_file(self.line_story_file, self.text_story)
        )
        self.combo_country_template.currentIndexChanged.connect(self._country_template_changed)
        self.btn_save_country_template.clicked.connect(self._save_country_template)
        self.btn_rename_country_template.clicked.connect(self._rename_country_template)
        self.btn_delete_country_template.clicked.connect(self._delete_country_template)
        self.btn_output_dir.clicked.connect(self._browse_output_folder)
        self.chk_all_countries.toggled.connect(self._toggle_all_countries)
        self.chk_all_batches.toggled.connect(self._toggle_all_batches)
        self.btn_create_queue.clicked.connect(self.create_country_batches)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_split_many_files.clicked.connect(self.run_many_file_splitting)
        self.btn_many_split_file.clicked.connect(self._choose_many_split_file)
        self.btn_split_lines.clicked.connect(self.run_line_splitting)
        self.btn_split_file.clicked.connect(self._choose_split_file)
        self.btn_split_folder.clicked.connect(self._choose_split_folder)
        self.btn_smooth_text.clicked.connect(self.run_text_smoothing)
        self.btn_smooth_folder.clicked.connect(self._choose_smooth_folder)
        self.btn_close_chrome.clicked.connect(self.close_chrome)
        self.btn_run.clicked.connect(lambda: self.start_tasks(selected_only=False))
        self.btn_run_selected.clicked.connect(lambda: self.start_tasks(selected_only=True))
        self.btn_pause.clicked.connect(self.pause_tasks)
        self.btn_stop.clicked.connect(self.stop_tasks)
        self.btn_retry.clicked.connect(self.retry_failed)
        self.table_queue.cellDoubleClicked.connect(self._open_result)
        for widget_signal in (
            self.text_story.textChanged,
            self.line_output_dir.textChanged, self.line_done_marker.textChanged,
            self.spin_threads.valueChanged, self.spin_max_continuations.valueChanged,
            self.line_smooth_folder.textChanged, self.spin_smooth_min_words.valueChanged,
            self.line_split_path.textChanged, self.spin_split_min_words.valueChanged,
            self.spin_split_max_words.valueChanged,
            self.combo_split_output_mode.currentIndexChanged,
            self.line_many_split_file.textChanged, self.spin_many_min_words.valueChanged,
            self.spin_many_max_words.valueChanged,
            self.combo_many_source_mode.currentIndexChanged,
        ):
            widget_signal.connect(self.save_config)
        for checkbox in self.country_checks.values():
            checkbox.toggled.connect(self.save_config)

    def _open_master_prompt_settings(self):
        selected_name = self.combo_master_template.currentData() or ""
        dialog = MasterPromptSettingsDialog(self, self.master_templates, selected_name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.master_templates = dialog.templates
        self._refresh_master_templates(dialog.selected_name)
        self.save_config()

    def _refresh_master_templates(self, selected_name=""):
        self.combo_master_template.blockSignals(True)
        self.combo_master_template.clear()
        for name in self.master_templates:
            self.combo_master_template.addItem(name, name)
        selected_index = self.combo_master_template.findData(selected_name)
        if selected_index < 0 and self.combo_master_template.count():
            selected_index = 0
        self.combo_master_template.setCurrentIndex(selected_index)
        self.combo_master_template.blockSignals(False)

    def _current_master_prompt(self):
        return self.master_templates.get(self.combo_master_template.currentData(), "")

    def _apply_country_selection(self, countries):
        selected_countries = set(countries)
        for country, checkbox in self.country_checks.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(country in selected_countries)
            checkbox.blockSignals(False)
        self.chk_all_countries.blockSignals(True)
        self.chk_all_countries.setChecked(
            bool(self.country_checks) and len(selected_countries) == len(self.country_checks)
        )
        self.chk_all_countries.blockSignals(False)

    def _refresh_country_templates(self, selected_name=None):
        self.combo_country_template.blockSignals(True)
        self.combo_country_template.clear()
        self.combo_country_template.addItem("Tùy chọn", None)
        for name in self.country_templates:
            self.combo_country_template.addItem(name, name)
        selected_index = self.combo_country_template.findData(selected_name)
        self.combo_country_template.setCurrentIndex(max(0, selected_index))
        self.combo_country_template.blockSignals(False)
        self._active_country_template = selected_name if selected_index > 0 else None
        self._update_country_template_buttons()

    def _country_template_changed(self):
        if self._changing_country_template:
            return
        if self._active_country_template is None:
            self._custom_country_selection = self._selected_countries()
        selected_name = self.combo_country_template.currentData()
        self._active_country_template = selected_name
        countries = (
            self.country_templates.get(selected_name, [])
            if selected_name is not None else self._custom_country_selection
        )
        self._changing_country_template = True
        try:
            self._apply_country_selection(countries)
        finally:
            self._changing_country_template = False
        self._update_country_template_buttons()
        self.save_config()

    def _update_country_template_buttons(self):
        is_template = self.combo_country_template.currentData() is not None
        self.btn_rename_country_template.setEnabled(is_template)
        self.btn_delete_country_template.setEnabled(is_template)
        self.btn_save_country_template.setText(
            "💾 Cập nhật cấu hình" if is_template else "💾 Lưu cấu hình"
        )

    def _unique_country_template_name(self, title, initial=""):
        name, accepted = QInputDialog.getText(self, title, "Tên template:", text=initial)
        name = name.strip()
        if not accepted or not name:
            return ""
        duplicate = next(
            (item for item in self.country_templates if item.casefold() == name.casefold() and item != initial),
            None,
        )
        if duplicate:
            QMessageBox.warning(self, "Trùng tên", f"Template “{duplicate}” đã tồn tại.")
            return ""
        return name

    def _save_country_template(self):
        selected_name = self.combo_country_template.currentData()
        if selected_name is None:
            selected_name = self._unique_country_template_name("Lưu cấu hình quốc gia")
            if not selected_name:
                return
        self.country_templates[selected_name] = self._selected_countries()
        self._refresh_country_templates(selected_name)
        self.save_config()

    def _rename_country_template(self):
        old_name = self.combo_country_template.currentData()
        if old_name is None:
            return
        new_name = self._unique_country_template_name("Đổi tên cấu hình quốc gia", old_name)
        if not new_name or new_name == old_name:
            return
        items = list(self.country_templates.items())
        self.country_templates = {
            (new_name if name == old_name else name): value for name, value in items
        }
        self._refresh_country_templates(new_name)
        self.save_config()

    def _delete_country_template(self):
        name = self.combo_country_template.currentData()
        if name is None:
            return
        answer = QMessageBox.question(
            self, "Xóa cấu hình quốc gia", f"Xóa template “{name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self.country_templates[name]
        self._refresh_country_templates(None)
        self._apply_country_selection(self._custom_country_selection)
        self.save_config()

    def _choose_text_file(self, path_line, text_edit):
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file văn bản", "", "Text Files (*.txt *.md);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as source_file:
                text_edit.setPlainText(source_file.read())
            path_line.setText(os.path.abspath(path))
            self.save_config()
        except (OSError, UnicodeError) as exc:
            QMessageBox.critical(self, "Không đọc được file", str(exc))

    def _browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Chọn folder đầu ra", self.line_output_dir.text()
        )
        if folder:
            self.line_output_dir.setText(folder)

    def _choose_smooth_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Chọn folder text Gemini", self.line_smooth_folder.text()
        )
        if folder:
            self.line_smooth_folder.setText(os.path.abspath(folder))

    def _choose_split_file(self):
        start_path = self.line_split_path.text().strip()
        if not os.path.isdir(start_path):
            start_path = os.path.dirname(start_path) if start_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file text cần chia dòng", start_path, "Text Files (*.txt)"
        )
        if path:
            self.line_split_path.setText(os.path.abspath(path))

    def _choose_split_folder(self):
        start_path = self.line_split_path.text().strip()
        if os.path.isfile(start_path):
            start_path = os.path.dirname(start_path)
        folder = QFileDialog.getExistingDirectory(
            self, "Chọn folder text cần chia dòng", start_path
        )
        if folder:
            self.line_split_path.setText(os.path.abspath(folder))

    def _choose_many_split_file(self):
        start_path = self.line_many_split_file.text().strip()
        if not os.path.isdir(start_path):
            start_path = os.path.dirname(start_path) if start_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file text cần tách thành nhiều file", start_path, "Text Files (*.txt)"
        )
        if path:
            self.line_many_split_file.setText(os.path.abspath(path))

    def run_many_file_splitting(self):
        source_path = self.line_many_split_file.text().strip()
        minimum_words = self.spin_many_min_words.value()
        maximum_words = self.spin_many_max_words.value()
        source_mode = self.combo_many_source_mode.currentData() or "keep"
        if minimum_words > maximum_words:
            QMessageBox.warning(
                self, "Giới hạn không hợp lệ",
                "Số từ tối thiểu không được lớn hơn số từ tối đa.",
            )
            return
        if not os.path.isfile(source_path) or not source_path.lower().endswith(".txt"):
            QMessageBox.warning(self, "File không hợp lệ", "Hãy chọn một file .txt đầu vào.")
            return

        source_path = os.path.abspath(source_path)
        output_dir = os.path.join(os.path.dirname(source_path), "full")
        try:
            with open(source_path, "r", encoding="utf-8-sig", newline="") as source_file:
                raw_text = source_file.read()
            chunks = split_text_into_single_line_chunks(
                raw_text, minimum_words, maximum_words
            )
            if not chunks:
                QMessageBox.warning(self, "File trống", "File đầu vào không có nội dung để tách.")
                return

            os.makedirs(output_dir, exist_ok=True)
            for index, chunk in enumerate(chunks, start=1):
                target_path = os.path.join(output_dir, f"{index}.txt")
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w", encoding="utf-8-sig", newline="", delete=False,
                        dir=output_dir, prefix=f".{index}.", suffix=".tmp",
                    ) as target_file:
                        temp_path = target_file.name
                        target_file.write(chunk)
                    os.replace(temp_path, target_path)
                    temp_path = None
                finally:
                    if temp_path and os.path.exists(temp_path):
                        os.remove(temp_path)

            numbered_file_pattern = re.compile(r"^(\d+)\.txt$", flags=re.IGNORECASE)
            for entry in os.scandir(output_dir):
                match = numbered_file_pattern.match(entry.name)
                if entry.is_file() and match and int(match.group(1)) > len(chunks):
                    os.remove(entry.path)

            if source_mode == "overwrite":
                combined_text = combine_single_line_chunks(chunks)
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w", encoding="utf-8-sig", newline="", delete=False,
                        dir=os.path.dirname(source_path), prefix=".gemini_source.", suffix=".tmp",
                    ) as source_file:
                        temp_path = source_file.name
                        source_file.write(combined_text)
                    os.replace(temp_path, source_path)
                    temp_path = None
                finally:
                    if temp_path and os.path.exists(temp_path):
                        os.remove(temp_path)

            output_word_counts = [self._count_words(chunk) for chunk in chunks]
            source_result = (
                f"Đã ghi đè file gốc thành {len(chunks):,} dòng."
                if source_mode == "overwrite"
                else "File gốc được giữ nguyên."
            )
            QMessageBox.information(
                self, "Tách file thành công",
                f"Đã tạo {len(chunks):,} file TXT trong:\n{output_dir}\n\n"
                f"Giới hạn: {minimum_words:,}–{maximum_words:,} từ/file\n"
                f"Tổng số từ: {sum(output_word_counts):,}\n"
                "Mỗi file con chứa đúng một dòng nội dung.\n"
                f"{source_result}",
            )
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        except (OSError, UnicodeError, ValueError) as exc:
            logging.exception("[Gemini Tách File Con] Lỗi xử lý %s", source_path)
            QMessageBox.critical(self, "Không tách được file", str(exc))

    @classmethod
    def _split_line_by_word_limits(cls, line, minimum_words, maximum_words):
        matches = list(cls.WORD_PATTERN.finditer(line))
        if len(matches) <= maximum_words:
            return [line]

        segments = []
        start = 0
        total = len(matches)
        while total - start > maximum_words:
            minimum_end = start + minimum_words
            maximum_end = min(start + maximum_words, total)
            valid_ends = [
                end for end in range(minimum_end, maximum_end + 1)
                if total - end == 0 or total - end >= minimum_words
            ]
            if not valid_ends:
                valid_ends = [maximum_end]

            strong_boundaries = []
            clause_boundaries = []
            for end in valid_ends:
                if end >= total:
                    delimiter = line[matches[end - 1].end():]
                else:
                    delimiter = line[matches[end - 1].end():matches[end].start()]
                if re.search(r"[.!?…]", delimiter):
                    strong_boundaries.append(end)
                elif re.search(r"[;:,—–]", delimiter):
                    clause_boundaries.append(end)

            end = (
                strong_boundaries[-1] if strong_boundaries
                else clause_boundaries[-1] if clause_boundaries
                else valid_ends[-1]
            )
            segment_start = matches[start].start()
            segment_end = matches[end].start() if end < total else len(line)
            segments.append(line[segment_start:segment_end].strip())
            start = end

        if start < total:
            segments.append(line[matches[start].start():].strip())
        return segments

    @classmethod
    def _split_text_lines(cls, raw_text, minimum_words, maximum_words):
        output_paragraphs = []
        for paragraph in re.split(r"(?:\r?\n)\s*(?:\r?\n)+", raw_text):
            flattened = re.sub(r"\s*\r?\n\s*", " ", paragraph).strip()
            if not flattened:
                continue
            output_paragraphs.append("\n".join(cls._split_line_by_word_limits(
                flattened, minimum_words, maximum_words
            )))
        return "\n\n".join(output_paragraphs)

    @staticmethod
    def _format_line_split_details(rows):
        headers = ("STT", "TRẠNG THÁI", "FILE NGUỒN", "FILE ĐÍCH", "SỐ TỪ", "SỐ DÒNG", "GHI CHÚ")
        display_rows = [
            (str(index), *row) for index, row in enumerate(rows, start=1)
        ]
        all_rows = [headers, *display_rows]
        widths = [max(len(row[index]) for row in all_rows) for index in range(len(headers))]

        def format_row(row):
            return " | ".join(
                value.rjust(widths[index]) if index in (0, 4, 5) else value.ljust(widths[index])
                for index, value in enumerate(row)
            )

        return "\n".join([
            format_row(headers),
            "-+-".join("-" * width for width in widths),
            *(format_row(row) for row in display_rows),
        ])

    def run_line_splitting(self):
        input_path = self.line_split_path.text().strip()
        minimum_words = self.spin_split_min_words.value()
        maximum_words = self.spin_split_max_words.value()
        output_mode = self.combo_split_output_mode.currentData() or "new"
        if minimum_words > maximum_words:
            QMessageBox.warning(
                self, "Giới hạn không hợp lệ",
                "Số từ tối thiểu không được lớn hơn số từ tối đa.",
            )
            return

        if os.path.isfile(input_path):
            if not input_path.lower().endswith(".txt"):
                QMessageBox.warning(self, "File không hợp lệ", "Chỉ hỗ trợ file .txt.")
                return
            source_files = [os.path.abspath(input_path)]
            source_root = os.path.dirname(os.path.abspath(input_path))
        elif os.path.isdir(input_path):
            source_root = os.path.abspath(input_path)
            try:
                source_files = sorted(
                    (
                        entry.path for entry in os.scandir(source_root)
                        if entry.is_file()
                        and entry.name.lower().endswith(".txt")
                        and not entry.name.lower().endswith("_chia_dong.txt")
                    ),
                    key=lambda path: os.path.basename(path).casefold(),
                )
            except OSError as exc:
                QMessageBox.critical(self, "Không quét được folder", str(exc))
                return
        else:
            QMessageBox.warning(
                self, "Đường dẫn không hợp lệ", "Hãy chọn một file .txt hoặc một folder."
            )
            return

        overwrite_source = output_mode == "overwrite"
        output_dir = source_root

        success = existing = errors = 0
        detail_rows = []
        for source_path in source_files:
            source_name = os.path.basename(source_path)
            if overwrite_source:
                target_name = source_name
                target_path = source_path
            else:
                source_stem, source_extension = os.path.splitext(source_name)
                target_name = f"{source_stem}_chia_dong{source_extension}"
                target_path = os.path.join(source_root, target_name)
            if not overwrite_source and os.path.exists(target_path):
                existing += 1
                detail_rows.append((
                    "BỎ QUA", source_name, target_name, "—", "—", "File đích đã tồn tại",
                ))
                continue
            try:
                with open(source_path, "r", encoding="utf-8-sig") as source_file:
                    raw_text = source_file.read()
                split_text = self._split_text_lines(
                    raw_text, minimum_words, maximum_words
                )
                if re.sub(r"\s+", "", raw_text) != re.sub(r"\s+", "", split_text):
                    raise ValueError("Kiểm tra bảo toàn nội dung thất bại")

                word_count = self._count_words(split_text)
                line_count = len(split_text.splitlines()) if split_text else 0
                if overwrite_source:
                    temp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            mode="w", encoding="utf-8-sig", newline="\n", delete=False,
                            dir=source_root, prefix=".autoflow_chia_dong_", suffix=".tmp",
                        ) as target_file:
                            temp_path = target_file.name
                            target_file.write(split_text + ("\n" if split_text else ""))
                        os.replace(temp_path, target_path)
                        temp_path = None
                    finally:
                        if temp_path and os.path.exists(temp_path):
                            os.remove(temp_path)
                else:
                    try:
                        with open(target_path, "x", encoding="utf-8-sig", newline="\n") as target_file:
                            target_file.write(split_text + ("\n" if split_text else ""))
                    except FileExistsError:
                        existing += 1
                        detail_rows.append((
                            "BỎ QUA", source_name, target_name, "—", "—", "File đích đã tồn tại",
                        ))
                        continue
                success += 1
                detail_rows.append((
                    "THÀNH CÔNG", source_name, target_name,
                    f"{word_count:,}", f"{line_count:,}",
                    "Đã ghi đè file gốc" if overwrite_source else "Đã tạo file mới",
                ))
            except (OSError, UnicodeError, ValueError) as exc:
                errors += 1
                detail_rows.append(("LỖI", source_name, "—", "—", "—", str(exc)))
                logging.exception("[Gemini Chia Dòng] Lỗi xử lý %s", source_path)

        report_lines = [
            "BÁO CÁO CHIA DÒNG TEXT",
            "=" * 48,
            f"Đường dẫn nguồn: {input_path}",
            f"Folder đích    : {output_dir}",
            f"Cách lưu       : {'Ghi đè file gốc' if overwrite_source else 'Tạo mới'}",
            f"Giới hạn       : {minimum_words:,}–{maximum_words:,} từ/dòng",
            "",
            f"Tổng file .txt : {len(source_files)}",
            f"Thành công     : {success}",
            f"Đã tồn tại     : {existing}",
            f"Lỗi            : {errors}",
            "",
            "CHI TIẾT",
            "-" * 48,
            self._format_line_split_details(detail_rows)
            if detail_rows else "Không có file .txt nào để xử lý.",
        ]
        self._show_smoothing_report(
            "\n".join(report_lines), title="Báo cáo chia dòng text"
        )

    @staticmethod
    def _extract_path_number(folder):
        components = os.path.normpath(os.path.abspath(folder)).split(os.sep)
        for component in reversed(components):
            match = re.match(r"^\s*(\d+)\s*\.", component)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _smooth_part_text(raw_text):
        parts = re.findall(
            r"\[\[PART_(\d+)_START\]\](.*?)\[\[PART_\1_END\]\]",
            raw_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        parts.sort(key=lambda item: int(item[0]))
        cleaned_lines = []
        for _part_number, content in parts:
            cleaned_lines.extend(
                line.strip() for line in content.splitlines() if line.strip()
            )
        return "\n".join(cleaned_lines).strip(), len(parts)

    @staticmethod
    def _count_words(text):
        # Gần với cách đếm của trình soạn thảo: hỗ trợ Unicode và không tách
        # don't, well-known, 1,560 hoặc 3.14 thành nhiều từ.
        return len(GeminiView.WORD_PATTERN.findall(text))

    @staticmethod
    def _format_smoothing_details(rows):
        headers = ("STT", "TRẠNG THÁI", "FILE NGUỒN", "FILE ĐÍCH", "PART", "SỐ TỪ", "GHI CHÚ")
        display_rows = [
            (
                str(index), status, source_name, target_name,
                str(part_count), str(word_count), note,
            )
            for index, (status, source_name, target_name, part_count, word_count, note)
            in enumerate(rows, start=1)
        ]
        all_rows = [headers, *display_rows]
        widths = [max(len(row[index]) for row in all_rows) for index in range(len(headers))]

        def format_row(row):
            cells = []
            for index, value in enumerate(row):
                cells.append(value.rjust(widths[index]) if index in (0, 4, 5) else value.ljust(widths[index]))
            return " | ".join(cells)

        separator = "-+-".join("-" * width for width in widths)
        return "\n".join([
            format_row(headers), separator,
            *(format_row(row) for row in display_rows),
        ])

    def run_text_smoothing(self):
        source_dir = self.line_smooth_folder.text().strip()
        if not source_dir or not os.path.isdir(source_dir):
            QMessageBox.warning(
                self, "Folder không hợp lệ", "Hãy chọn folder chứa các file text Gemini."
            )
            return

        path_number = self._extract_path_number(source_dir)
        if not path_number:
            QMessageBox.warning(
                self, "Không tìm thấy số thứ tự",
                "Không tìm thấy thành phần đường dẫn có dạng “6. Tên nội dung”.",
            )
            return

        try:
            source_files = sorted(
                (
                    entry.path for entry in os.scandir(source_dir)
                    if entry.is_file() and entry.name.lower().endswith(".txt")
                ),
                key=lambda path: os.path.basename(path).casefold(),
            )
        except OSError as exc:
            QMessageBox.critical(self, "Không quét được folder", str(exc))
            return

        output_dir = os.path.join(source_dir, "full")
        minimum_words = self.spin_smooth_min_words.value()
        stats = {
            "success": 0, "existing": 0, "no_marker": 0,
            "too_short": 0, "error": 0,
        }
        detail_rows = []

        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Không tạo được folder full", str(exc))
            return

        for source_path in source_files:
            source_name = os.path.basename(source_path)
            target_name = f"{path_number}.{source_name}"
            target_path = os.path.join(output_dir, target_name)

            if os.path.exists(target_path):
                stats["existing"] += 1
                detail_rows.append((
                    "BỎ QUA", source_name, target_name, "—", "—", "File đích đã tồn tại",
                ))
                continue

            try:
                with open(source_path, "r", encoding="utf-8-sig") as source_file:
                    raw_text = source_file.read()
                if "START]]" not in raw_text:
                    stats["no_marker"] += 1
                    detail_rows.append((
                        "BỎ QUA", source_name, "—", "—", "—", "Không có marker START]]",
                    ))
                    continue

                smoothed_text, part_count = self._smooth_part_text(raw_text)
                if not part_count:
                    stats["error"] += 1
                    detail_rows.append((
                        "LỖI", source_name, "—", "—", "—", "Không có cặp START/END hợp lệ",
                    ))
                    continue
                word_count = self._count_words(smoothed_text)
                if word_count < minimum_words:
                    stats["too_short"] += 1
                    detail_rows.append((
                        "THIẾU TỪ", source_name, "—", str(part_count), f"{word_count:,}",
                        f"Tối thiểu {minimum_words:,} từ",
                    ))
                    continue

                try:
                    with open(target_path, "x", encoding="utf-8-sig", newline="\n") as target_file:
                        target_file.write(smoothed_text + "\n")
                except FileExistsError:
                    stats["existing"] += 1
                    detail_rows.append((
                        "BỎ QUA", source_name, target_name, "—", "—", "File đích đã tồn tại",
                    ))
                    continue

                stats["success"] += 1
                detail_rows.append((
                    "THÀNH CÔNG", source_name, target_name, str(part_count),
                    f"{word_count:,}", "",
                ))
            except (OSError, UnicodeError, ValueError) as exc:
                stats["error"] += 1
                detail_rows.append(("LỖI", source_name, "—", "—", "—", str(exc)))
                logging.exception("[Gemini Smooth Text] Lỗi xử lý %s", source_path)

        report_lines = [
            "BÁO CÁO LÀM MỊN TEXT GEMINI",
            "=" * 48,
            f"Folder nguồn : {source_dir}",
            f"Folder đích  : {output_dir}",
            f"Tiền tố số  : {path_number}.",
            f"Số từ tối thiểu: {minimum_words:,}",
            "",
            f"Tổng file .txt         : {len(source_files)}",
            f"Thành công              : {stats['success']}",
            f"Đã tồn tại              : {stats['existing']}",
            f"Không có START]]        : {stats['no_marker']}",
            f"Không đạt số từ tối thiểu: {stats['too_short']}",
            f"Lỗi                     : {stats['error']}",
            "",
            "CHI TIẾT",
            "-" * 48,
        ]
        report_lines.append(
            self._format_smoothing_details(detail_rows)
            if detail_rows else "Không có file .txt nào trong folder nguồn."
        )
        self._show_smoothing_report("\n".join(report_lines))

    def _show_smoothing_report(self, report, title="Báo cáo làm mịn text Gemini"):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(850, 620)
        layout = QVBoxLayout(dialog)
        report_box = QPlainTextEdit()
        report_box.setReadOnly(True)
        report_box.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        report_box.setPlainText(report)
        report_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(report_box, 1)
        buttons = QHBoxLayout()
        btn_copy = QPushButton("📋 Sao chép báo cáo")
        btn_close = QPushButton("Đóng")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(report))
        btn_close.clicked.connect(dialog.accept)
        buttons.addStretch()
        buttons.addWidget(btn_copy)
        buttons.addWidget(btn_close)
        layout.addLayout(buttons)
        dialog.exec()

    def _toggle_all_countries(self, checked):
        for checkbox in self.country_checks.values():
            checkbox.setChecked(checked)

    def _selected_countries(self):
        return [name for name, checkbox in self.country_checks.items() if checkbox.isChecked()]

    def _validate_inputs(self):
        master_prompt = self._current_master_prompt().strip()
        story = self.text_story.toPlainText().strip()
        output_dir = self.line_output_dir.text().strip()
        done_marker = self.line_done_marker.text().strip()
        countries = self._selected_countries()
        if not master_prompt:
            QMessageBox.warning(
                self, "Thiếu Master Prompt",
                "Hãy chọn một template Master Prompt có nội dung. Dùng nút Cài đặt để tạo template mới.",
            )
            return None
        if not story:
            QMessageBox.warning(self, "Thiếu cốt truyện", "Hãy nhập text hoặc chọn file cốt truyện.")
            return None
        if not countries:
            QMessageBox.warning(self, "Thiếu quốc gia", "Hãy tích chọn ít nhất một quốc gia.")
            return None
        if not output_dir:
            QMessageBox.warning(self, "Thiếu folder", "Hãy chọn folder đầu ra.")
            return None
        if not done_marker:
            QMessageBox.warning(self, "Thiếu từ khóa", "Từ khóa Done không được để trống.")
            return None
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Folder không hợp lệ", str(exc))
            return None
        return master_prompt, story, os.path.abspath(output_dir), done_marker, countries

    def create_country_batches(self):
        values = self._validate_inputs()
        if not values:
            logging.warning("[Gemini UI] Không tạo queue vì dữ liệu đầu vào chưa hợp lệ")
            return
        master_prompt, story, output_dir, done_marker, countries = values
        maximum = self.spin_max_continuations.value()
        logging.info(
            "[Gemini UI] Tạo/cập nhật queue; countries=%s; master_chars=%s; story_chars=%s; "
            "output=%s; max_gõ_1=%s; marker=%r",
            countries, len(master_prompt), len(story), output_dir, maximum, done_marker,
        )
        db = SessionLocal()
        created = updated = 0
        try:
            for country in countries:
                display_name = LANGUAGE_BY_COUNTRY.get(country, country)
                batch = db.query(GeminiBatch).filter(GeminiBatch.country == country).first()
                if batch and batch.status == "RUNNING":
                    logging.warning(
                        "[Gemini UI] Bỏ qua country=%s vì batch id=%s đang RUNNING",
                        country, batch.id,
                    )
                    continue
                if batch:
                    updated += 1
                    batch.name = display_name
                    batch.story_content = story
                    batch.master_prompt = master_prompt
                    batch.output_dir = output_dir
                    batch.max_continuations = maximum
                    batch.done_marker = done_marker
                    batch.total_parts = maximum
                    batch.current_part = 0
                    batch.status = "PENDING"
                    batch.account_id = None
                    batch.result_path = None
                    batch.error_message = None
                    batch.retry_count = 0
                    logging.info("[Gemini UI] Reset batch id=%s country=%s về PENDING", batch.id, country)
                else:
                    created += 1
                    db.add(GeminiBatch(
                        name=display_name, country=country, story_content=story,
                        master_prompt=master_prompt, output_dir=output_dir,
                        max_continuations=maximum, done_marker=done_marker,
                        total_parts=maximum, current_part=0, status="PENDING",
                    ))
                    logging.info("[Gemini UI] Thêm batch mới country=%s", country)
            db.commit()
            logging.info(
                "[Gemini UI] Commit queue thành công; created=%s; updated=%s",
                created, updated,
            )
        finally:
            db.close()
        self.load_batches()
        QMessageBox.information(
            self, "Đã tạo queue", f"Tạo mới: {created} | Cập nhật: {updated} | Tổng chọn: {len(countries)}"
        )

    def load_batches(self):
        db = SessionLocal()
        batches = db.query(GeminiBatch).filter(
            GeminiBatch.country.in_(COUNTRIES)
        ).order_by(GeminiBatch.id.asc()).all()
        account_ids = {batch.account_id for batch in batches if batch.account_id}
        accounts = {
            account.id: account.email
            for account in db.query(Account).filter(Account.id.in_(account_ids)).all()
        } if account_ids else {}
        db.close()
        logging.debug("[Gemini UI] Load %s batch quốc gia từ DB", len(batches))
        self.table_queue.setRowCount(0)
        for batch in batches:
            row = self.table_queue.rowCount()
            self.table_queue.insertRow(row)
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            checkbox = QCheckBox()
            checkbox.setChecked(batch.status in ("PENDING", "FAILED"))
            checkbox.toggled.connect(self._sync_all_batches_checkbox)
            layout.addWidget(checkbox)
            self.table_queue.setCellWidget(row, 0, container)
            output_name = LANGUAGE_BY_COUNTRY.get(batch.country, batch.name or batch.country)
            name_item = QTableWidgetItem(f"{output_name}.txt")
            name_item.setData(Qt.ItemDataRole.UserRole, batch.id)
            self.table_queue.setItem(row, 1, name_item)
            self.table_queue.setItem(row, 2, QTableWidgetItem(accounts.get(batch.account_id, "—")))
            maximum = batch.max_continuations or batch.total_parts or 10
            self.table_queue.setItem(row, 3, QTableWidgetItem(f"{batch.current_part}/{maximum}"))
            status_item = QTableWidgetItem(batch.status)
            status_item.setForeground(QColor(self.STATUS_COLORS.get(batch.status, "#e5e7eb")))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_queue.setItem(row, 4, status_item)
            status_combo = QComboBox()
            for status in ("PENDING", "SUCCESS", "FAILED"):
                status_combo.addItem(status, status)
            self._set_status_combo(status_combo, batch.status)
            status_combo.currentIndexChanged.connect(
                lambda _index, batch_id=batch.id, combo=status_combo:
                self._manual_status_changed(batch_id, combo)
            )
            self.table_queue.setCellWidget(row, 4, status_combo)
            if batch.status == "SUCCESS":
                detail = batch.result_path
            elif batch.status == "PENDING" and (batch.retry_count or 0) > 0:
                detail = (
                    f"Retry {batch.retry_count}/{GEMINI_MAX_RETRIES} đang chờ; "
                    f"lỗi gần nhất: {batch.error_message or ''}"
                )
            else:
                detail = batch.error_message or ""
            detail_item = QTableWidgetItem(detail or "")
            detail_item.setToolTip(detail or "")
            self.table_queue.setItem(row, 5, detail_item)
        self._sync_all_batches_checkbox()
        self._update_stats()

    def _set_status_combo(self, combo, status):
        combo.blockSignals(True)
        running_index = combo.findData("RUNNING")
        if status == "RUNNING" and running_index < 0:
            combo.insertItem(1, "RUNNING", "RUNNING")
        elif status != "RUNNING" and running_index >= 0:
            combo.removeItem(running_index)
        status_index = combo.findData(status)
        combo.setCurrentIndex(max(0, status_index))
        combo.setEnabled(status != "RUNNING")
        combo.setToolTip(
            "Không thể sửa khi batch đang chạy."
            if status == "RUNNING" else "Chọn trạng thái mới cho batch."
        )
        color = self.STATUS_COLORS.get(status, "#e5e7eb")
        combo.setStyleSheet(
            f"QComboBox{{color:{color};font-weight:bold;padding:2px 6px;}}"
            "QComboBox:disabled{color:#60a5fa;}"
        )
        combo.blockSignals(False)

    def _manual_status_changed(self, batch_id, combo):
        new_status = combo.currentData()
        if new_status not in ("PENDING", "SUCCESS", "FAILED"):
            return
        db = SessionLocal()
        try:
            batch = db.query(GeminiBatch).filter(GeminiBatch.id == batch_id).first()
            if not batch:
                QTimer.singleShot(0, self.load_batches)
                return
            if batch.status == "RUNNING":
                logging.warning(
                    "[Gemini UI] Không đổi trạng thái thủ công cho batch id=%s đang RUNNING",
                    batch_id,
                )
                QTimer.singleShot(0, self.load_batches)
                return
            old_status = batch.status
            batch.status = new_status
            batch.account_id = None
            if new_status == "PENDING":
                batch.current_part = 0
                batch.retry_count = 0
                batch.result_path = None
                batch.error_message = None
            elif new_status == "SUCCESS":
                batch.retry_count = 0
                batch.error_message = None
            else:
                batch.result_path = None
                batch.error_message = batch.error_message or "Đã chuyển sang FAILED thủ công"
            db.commit()
            self.task_queue = [item for item in self.task_queue if item != batch_id]
            self.retry_last_account_ids.pop(batch_id, None)
            logging.info(
                "[Gemini UI] Đổi trạng thái thủ công batch id=%s: %s -> %s",
                batch_id, old_status, new_status,
            )
        finally:
            db.close()
        self.load_batches()

    def _selected_batch_ids(self):
        selected = []
        for row in range(self.table_queue.rowCount()):
            checkbox = self.table_queue.cellWidget(row, 0).findChild(QCheckBox)
            item = self.table_queue.item(row, 1)
            if checkbox and checkbox.isChecked() and item:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected

    def _toggle_all_batches(self, checked):
        for row in range(self.table_queue.rowCount()):
            container = self.table_queue.cellWidget(row, 0)
            checkbox = container.findChild(QCheckBox) if container else None
            if checkbox:
                checkbox.setChecked(checked)

    def _sync_all_batches_checkbox(self):
        checkboxes = []
        for row in range(self.table_queue.rowCount()):
            container = self.table_queue.cellWidget(row, 0)
            checkbox = container.findChild(QCheckBox) if container else None
            if checkbox:
                checkboxes.append(checkbox)
        self.chk_all_batches.blockSignals(True)
        self.chk_all_batches.setChecked(bool(checkboxes) and all(
            checkbox.isChecked() for checkbox in checkboxes
        ))
        self.chk_all_batches.blockSignals(False)

    def start_tasks(self, selected_only=False):
        db = SessionLocal()
        accounts = db.query(Account).filter(
            Account.is_active == True, Account.is_gemini == True
        ).order_by(Account.position.asc()).all()
        selected_ids = self._selected_batch_ids() if selected_only else []
        batch_query = db.query(GeminiBatch).filter(
            GeminiBatch.status == "PENDING", GeminiBatch.country.in_(COUNTRIES)
        )
        if selected_only:
            batch_query = batch_query.filter(GeminiBatch.id.in_(selected_ids))
        batches = batch_query.order_by(GeminiBatch.id.asc()).all() if (
            not selected_only or selected_ids
        ) else []
        db.close()
        if not accounts:
            logging.error("[Gemini UI] Không chạy: không có account active được bật Gemini")
            QMessageBox.warning(
                self, "Thiếu tài khoản",
                "Không có account đang hoạt động và được tích ở cột Gemini.",
            )
            return
        if not batches:
            logging.warning(
                "[Gemini UI] Không chạy: mode=%s; selected_ids=%s; không có batch PENDING",
                "selected" if selected_only else "all_pending", selected_ids,
            )
            message = (
                "Hãy chọn ít nhất một batch PENDING."
                if selected_only else "Không có batch PENDING để chạy."
            )
            QMessageBox.warning(self, "Không có batch", message)
            return
        queued = set(self.task_queue)
        running = {worker.batch_id for worker in self.workers if worker.isRunning()}
        self.task_queue.extend(batch.id for batch in batches if batch.id not in queued | running)
        self.log_run_total = len(self.task_queue) + len(running)
        self.log_run_done = 0
        self.window_slot_count = max(
            1, min(self.spin_threads.value(), len(accounts), len(self.task_queue) + len(running))
        )
        logging.info(
            "[Gemini UI] Bắt đầu run mode=%s; batch_ids=%s; queue=%s; active_accounts=%s; "
            "threads_config=%s; effective_threads=%s",
            "selected" if selected_only else "all_pending",
            [batch.id for batch in batches], self.task_queue,
            [(account.id, account.email, account.position) for account in accounts],
            self.spin_threads.value(), min(self.spin_threads.value(), len(accounts)),
        )
        self.is_paused = False
        activity("[Gemini] Bắt đầu xử lý 0/%s batch", self.log_run_total)
        self.account_cursor = 0
        self.session_skipped_account_ids.clear()
        self.retry_last_account_ids = {
            batch.id: batch.account_id
            for batch in batches
            if (batch.retry_count or 0) > 0 and batch.account_id
        }
        self.no_pro_accounts_notified = False
        self.btn_run.setEnabled(False)
        self.btn_run_selected.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.btn_pause.setText("⏸ TẠM DỪNG")
        if not self.queue_timer.isActive():
            self.queue_timer.start(300)
        self.process_queue()

    def process_queue(self):
        self.workers = [worker for worker in self.workers if not worker.isFinished()]
        active_workers = [worker for worker in self.workers if worker.isRunning()]
        self.active_workers_count = len(active_workers)
        if self.is_paused:
            return
        if not self.task_queue and not active_workers:
            logging.info("[Gemini UI] Queue đã hoàn tất; không còn worker hoạt động")
            if getattr(self, "log_run_total", 0):
                activity(
                    "[Gemini] Đã kết thúc: %s/%s batch",
                    self.log_run_done, self.log_run_total,
                )
            self.queue_timer.stop()
            self.btn_run.setEnabled(True)
            self.btn_run_selected.setEnabled(True)
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.load_batches()
            return
        db = SessionLocal()
        accounts = db.query(Account).filter(
            Account.is_active == True, Account.is_gemini == True
        ).order_by(Account.position.asc()).all()
        db.close()
        accounts = [
            account for account in accounts
            if account.id not in self.session_skipped_account_ids
        ]
        if not accounts:
            if active_workers:
                return
            logging.error("[Gemini UI] Không còn account dùng được model Pro trong phiên này")
            self.queue_timer.stop()
            self.task_queue.clear()
            self.btn_run.setEnabled(True)
            self.btn_run_selected.setEnabled(True)
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.load_batches()
            if not self.no_pro_accounts_notified:
                self.no_pro_accounts_notified = True
                QMessageBox.warning(
                    self, "Hết account Pro",
                    "Không còn account Gemini nào chọn được model 3.1 Pro. "
                    "Các batch còn lại được giữ ở trạng thái PENDING.",
                )
            return
        # window_slot_count được cố định khi bắt đầu phiên để vị trí Chrome không
        # thay đổi giữa các batch. Một worker có thể báo account không dùng được
        # Pro trước khi thread đóng hẳn, nên vẫn chiếm slot trong chốc lát.
        max_workers = min(
            self.spin_threads.value(), len(accounts), self.window_slot_count
        )
        used_account_ids = {worker.account_id for worker in active_workers}
        used_window_slots = {worker.window_slot for worker in active_workers}
        while self.task_queue and len(active_workers) < max_workers:
            window_slot = next(
                (
                    slot for slot in range(self.window_slot_count)
                    if slot not in used_window_slots
                ),
                None,
            )
            if window_slot is None:
                logging.debug(
                    "[Gemini UI] Chờ worker cũ đóng để giải phóng window slot; "
                    "slots_đang_dùng=%s/%s; queue_còn=%s",
                    sorted(used_window_slots), self.window_slot_count,
                    len(self.task_queue),
                )
                break
            selected_account = None
            selected_index = None
            queue_index = None
            # Batch retry ưu tiên Chrome/account khác với lần vừa lỗi. Nếu Chrome
            # đó đang bận, xét batch kế tiếp thay vì chặn toàn bộ hàng chờ.
            for candidate_queue_index, candidate_batch_id in enumerate(self.task_queue):
                previous_account_id = self.retry_last_account_ids.get(candidate_batch_id)
                for offset in range(len(accounts)):
                    index = (self.account_cursor + offset) % len(accounts)
                    account = accounts[index]
                    if account.id in used_account_ids:
                        continue
                    if len(accounts) > 1 and account.id == previous_account_id:
                        continue
                    selected_account = account
                    selected_index = index
                    queue_index = candidate_queue_index
                    break
                if selected_account is not None:
                    break
            if selected_account is None or queue_index is None:
                break
            self.account_cursor = (selected_index + 1) % len(accounts)
            batch_id = self.task_queue.pop(queue_index)
            self.retry_last_account_ids.pop(batch_id, None)
            logging.info(
                "[Gemini UI] Round-robin assign batch_id=%s -> account_id=%s email=%s "
                "position=%s; cursor_next=%s; queue_còn=%s; account_đang_dùng=%s; window_slot=%s/%s",
                batch_id, selected_account.id, selected_account.email,
                selected_account.position, self.account_cursor, len(self.task_queue),
                sorted(used_account_ids), window_slot + 1, self.window_slot_count,
            )
            worker = GeminiWorker(
                batch_id, selected_account.id,
                window_slot=window_slot, window_count=self.window_slot_count,
            )
            worker.progress.connect(self._on_status)
            worker.part_progress.connect(self._on_continuation)
            worker.batch_finished.connect(self._on_finished)
            worker.error.connect(self._on_error)
            worker.retry_requested.connect(self._on_retry_requested)
            worker.account_unavailable.connect(self._on_account_unavailable)
            self.workers.append(worker)
            active_workers.append(worker)
            used_account_ids.add(selected_account.id)
            used_window_slots.add(window_slot)
            self._set_row(
                batch_id, account=selected_account.email, status="RUNNING", detail=""
            )
            worker.start()
        self.active_workers_count = len(active_workers)
        self._update_stats()

    def pause_tasks(self):
        self.is_paused = not self.is_paused
        activity("[Gemini] %s", "Đã tạm dừng" if self.is_paused else "Tiếp tục chạy")
        logging.info(
            "[Gemini UI] %s queue; workers_running=%s; queue_waiting=%s",
            "TẠM DỪNG" if self.is_paused else "TIẾP TỤC",
            sum(1 for worker in self.workers if worker.isRunning()), len(self.task_queue),
        )
        for worker in self.workers:
            if worker.isRunning():
                worker.pause() if self.is_paused else worker.resume()
        self.btn_pause.setText("▶ TIẾP TỤC" if self.is_paused else "⏸ TẠM DỪNG")
        if not self.is_paused:
            self.process_queue()

    def stop_tasks(self):
        if self.task_queue or any(worker.isRunning() for worker in self.workers):
            activity("[Gemini] Đang dừng toàn bộ tác vụ")
        logging.warning(
            "[Gemini UI] DỪNG queue; queue_waiting=%s; workers_running=%s",
            len(self.task_queue), sum(1 for worker in self.workers if worker.isRunning()),
        )
        self.queue_timer.stop()
        self.task_queue.clear()
        self.is_paused = False
        running_ids = []
        for worker in self.workers:
            if worker.isRunning():
                running_ids.append(worker.batch_id)
                worker.stop()
        logging.warning("[Gemini UI] Đã gửi stop đến batch_ids=%s", running_ids)
        db = SessionLocal()
        try:
            if running_ids:
                db.query(GeminiBatch).filter(GeminiBatch.id.in_(running_ids)).update(
                    {"status": "PENDING", "account_id": None}, synchronize_session=False
                )
            db.commit()
        finally:
            db.close()
        self.btn_run.setEnabled(True)
        self.btn_run_selected.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.load_batches()

    def close_chrome(self):
        running = bool(self.task_queue) or any(worker.isRunning() for worker in self.workers)
        if running:
            reply = QMessageBox.question(
                self,
                "Xác nhận",
                "Các luồng Gemini đang chạy dở. Bạn có chắc chắn muốn dừng tác vụ và đóng Chrome?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.stop_tasks()
        try:
            from core.browser_manager import kill_all_registered_chromes
            kill_all_registered_chromes()
        except Exception as exc:
            logging.error("[Gemini UI] Lỗi khi đóng Chrome: %s", exc)
            QMessageBox.warning(self, "Không đóng được Chrome", str(exc))
            return

        QMessageBox.information(
            self, "Thông báo", "Đã đóng tất cả trình duyệt Chrome do Bot mở."
        )

    def retry_failed(self):
        selected_ids = self._selected_batch_ids()
        db = SessionLocal()
        query = db.query(GeminiBatch).filter(
            GeminiBatch.status == "FAILED", GeminiBatch.country.in_(COUNTRIES)
        )
        if selected_ids:
            query = query.filter(GeminiBatch.id.in_(selected_ids))
        batches = query.order_by(GeminiBatch.id.asc()).all()
        for batch in batches:
            batch.status = "PENDING"
            batch.account_id = None
            batch.current_part = 0
            batch.error_message = None
            batch.retry_count = 0
        db.commit()
        ids = [batch.id for batch in batches]
        for batch_id in ids:
            self.retry_last_account_ids.pop(batch_id, None)
        logging.info(
            "[Gemini UI] Chạy lại lỗi; selected_ids=%s; failed_batch_ids=%s",
            selected_ids, ids,
        )
        db.close()
        self.load_batches()
        if not ids:
            QMessageBox.information(self, "Không có lỗi", "Không có batch FAILED để chạy lại.")
            return
        for row in range(self.table_queue.rowCount()):
            item = self.table_queue.item(row, 1)
            if item and item.data(Qt.ItemDataRole.UserRole) in ids:
                self.table_queue.cellWidget(row, 0).findChild(QCheckBox).setChecked(True)

    def delete_selected(self):
        ids = self._selected_batch_ids()
        running_ids = {worker.batch_id for worker in self.workers if worker.isRunning()}
        ids = [batch_id for batch_id in ids if batch_id not in running_ids]
        if not ids:
            return
        db = SessionLocal()
        db.query(GeminiBatch).filter(GeminiBatch.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        db.close()
        self.task_queue = [batch_id for batch_id in self.task_queue if batch_id not in ids]
        self.load_batches()

    def _on_status(self, batch_id, status):
        logging.info("[Gemini UI] Batch id=%s status signal=%s", batch_id, status)
        self._set_row(batch_id, status=status)
        if status == "RUNNING":
            log_progress(
                "Gemini", getattr(self, "log_run_done", 0),
                getattr(self, "log_run_total", 0), f"Đang chạy batch #{batch_id}",
            )

    def _on_continuation(self, batch_id, current, total):
        logging.info("[Gemini UI] Batch id=%s progress signal=%s/%s", batch_id, current, total)
        self._set_row(batch_id, continuation=f"{current}/{total}")
        log_progress("Gemini", current, total, f"Batch #{batch_id}")

    def _on_finished(self, batch_id, result_path):
        logging.info("[Gemini UI] Batch id=%s finished; result=%s", batch_id, result_path)
        self._set_row(batch_id, status="SUCCESS", detail=result_path)
        self.log_run_done = getattr(self, "log_run_done", 0) + 1
        log_progress(
            "Gemini", self.log_run_done, getattr(self, "log_run_total", 0),
            f"Hoàn thành batch #{batch_id}",
        )
        QTimer.singleShot(0, self.process_queue)

    def _on_error(self, batch_id, message):
        self.log_run_done = getattr(self, "log_run_done", 0) + 1
        logging.error(
            "[Gemini] Batch #%s thất bại (%s/%s): %s",
            batch_id, self.log_run_done, getattr(self, "log_run_total", 0), message,
        )
        self._set_row(batch_id, status="FAILED", detail=message)
        QTimer.singleShot(0, self.process_queue)

    def _on_retry_requested(self, batch_id, account_id, retry_number, message):
        self.retry_last_account_ids[batch_id] = account_id
        if batch_id not in self.task_queue:
            self.task_queue.append(batch_id)
        detail = (
            f"Retry {retry_number}/{GEMINI_MAX_RETRIES}; "
            f"đã đưa về cuối queue sau lỗi: {message}"
        )
        log_warning(
            "[Gemini] Batch #%s lỗi, thử lại %s/%s: %s",
            batch_id, retry_number, GEMINI_MAX_RETRIES, message,
        )
        self._set_row(batch_id, account="—", status="PENDING", detail=detail)
        QTimer.singleShot(0, self.process_queue)

    def _on_account_unavailable(self, batch_id, account_id, message):
        self.session_skipped_account_ids.add(account_id)
        if batch_id not in self.task_queue:
            self.task_queue.append(batch_id)
        log_warning(
            "[Gemini] Chuyển batch #%s sang tài khoản khác: %s",
            batch_id, message,
        )
        self._set_row(batch_id, account="—", status="PENDING", detail=message)
        QTimer.singleShot(0, self.process_queue)

    def _set_row(self, batch_id, account=None, continuation=None, status=None, detail=None):
        for row in range(self.table_queue.rowCount()):
            item = self.table_queue.item(row, 1)
            if item and item.data(Qt.ItemDataRole.UserRole) == batch_id:
                if account is not None:
                    self.table_queue.item(row, 2).setText(account)
                if continuation is not None:
                    self.table_queue.item(row, 3).setText(continuation)
                if status is not None:
                    status_item = self.table_queue.item(row, 4)
                    status_item.setText(status)
                    status_item.setForeground(QColor(self.STATUS_COLORS.get(status, "#e5e7eb")))
                    status_combo = self.table_queue.cellWidget(row, 4)
                    if isinstance(status_combo, QComboBox):
                        self._set_status_combo(status_combo, status)
                if detail is not None:
                    self.table_queue.item(row, 5).setText(detail)
                    self.table_queue.item(row, 5).setToolTip(detail)
                break
        self._update_stats()

    def _update_stats(self):
        counts = {status: 0 for status in self.STATUS_COLORS}
        for row in range(self.table_queue.rowCount()):
            item = self.table_queue.item(row, 4)
            if item and item.text() in counts:
                counts[item.text()] += 1
        self.lbl_stats.setText(
            f"Tổng: {sum(counts.values())} | Chờ: {counts['PENDING']} | "
            f"Đang chạy: {counts['RUNNING']} | Thành công: {counts['SUCCESS']} | Lỗi: {counts['FAILED']}"
        )

    def _open_result(self, row, _column):
        status = self.table_queue.item(row, 4)
        path = self.table_queue.item(row, 5)
        if status and path and status.text() == "SUCCESS" and os.path.isfile(path.text()):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path.text()))

    def _reset_interrupted_batches(self):
        db = SessionLocal()
        db.query(GeminiBatch).filter(GeminiBatch.status == "RUNNING").update(
            {"status": "PENDING", "account_id": None}, synchronize_session=False
        )
        db.commit()
        db.close()

    def shutdown_tasks(self):
        self.stop_tasks()

    @staticmethod
    def _config_path():
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return os.path.join(project_root, "data", "config_gemini.json")

    def save_config(self):
        if not hasattr(self, "country_checks"):
            return
        if self.combo_country_template.currentData() is None and not self._changing_country_template:
            self._custom_country_selection = self._selected_countries()
        config = {
            # Keep the old flat fields so an older build can still read the current values.
            "master_file": "",
            "story_file": self.line_story_file.text(),
            "master_prompt": self._current_master_prompt(),
            "story": self.text_story.toPlainText(),
            "master_templates": self.master_templates,
            "selected_master_template": self.combo_master_template.currentData(),
            "country_templates": self.country_templates,
            "selected_country_template": self.combo_country_template.currentData(),
            "custom_country_selection": self._custom_country_selection,
            "output_dir": self.line_output_dir.text(),
            "threads": self.spin_threads.value(),
            "max_continuations": self.spin_max_continuations.value(),
            "done_marker": self.line_done_marker.text(),
            "countries": self._selected_countries(),
            "smooth_folder": self.line_smooth_folder.text(),
            "smooth_min_words": self.spin_smooth_min_words.value(),
            "split_path": self.line_split_path.text(),
            "split_min_words": self.spin_split_min_words.value(),
            "split_max_words": self.spin_split_max_words.value(),
            "split_output_mode": self.combo_split_output_mode.currentData(),
            "many_split_file": self.line_many_split_file.text(),
            "many_min_words": self.spin_many_min_words.value(),
            "many_max_words": self.spin_many_max_words.value(),
            "many_source_mode": self.combo_many_source_mode.currentData(),
        }
        try:
            os.makedirs(os.path.dirname(self._config_path()), exist_ok=True)
            with open(self._config_path(), "w", encoding="utf-8") as config_file:
                json.dump(config, config_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            logging.warning(f"[Gemini UI] Không lưu được cấu hình: {exc}")

    def load_config(self):
        try:
            with open(self._config_path(), "r", encoding="utf-8") as config_file:
                config = json.load(config_file)
        except (OSError, ValueError):
            self._refresh_master_templates()
            self._refresh_country_templates()
            return

        raw_master_templates = config.get("master_templates", {})
        if isinstance(raw_master_templates, dict):
            self.master_templates = {
                str(name): value for name, value in raw_master_templates.items()
                if str(name).strip() and isinstance(value, str)
            }
        legacy_master_prompt = config.get("master_prompt", "")
        if not self.master_templates and legacy_master_prompt:
            legacy_path = config.get("master_file", "")
            legacy_name = os.path.splitext(os.path.basename(legacy_path))[0] or "Master Prompt cũ"
            self.master_templates[legacy_name] = legacy_master_prompt
        selected_master = config.get("selected_master_template", "")
        if selected_master not in self.master_templates:
            selected_master = next(iter(self.master_templates), "")
        self._refresh_master_templates(selected_master)

        raw_country_templates = config.get("country_templates", {})
        if isinstance(raw_country_templates, dict):
            self.country_templates = {
                str(name): [country for country in value if country in self.country_checks]
                for name, value in raw_country_templates.items()
                if str(name).strip() and isinstance(value, list)
            }

        # Migrate configs created by the short-lived story-template implementation.
        old_story_templates = config.get("story_templates", {})
        if not self.country_templates and isinstance(old_story_templates, dict):
            self.country_templates = {
                str(name): [
                    country for country in value.get("countries", [])
                    if country in self.country_checks
                ]
                for name, value in old_story_templates.items()
                if str(name).strip() and isinstance(value, dict)
            }
            old_selected_story = config.get("selected_story_template")
            old_custom_story = config.get("custom_story_config", {})
            if old_selected_story and isinstance(old_custom_story, dict):
                config["story"] = old_custom_story.get("story", config.get("story", ""))
                config["story_file"] = old_custom_story.get(
                    "story_file", config.get("story_file", "")
                )
        widgets = (
            self.line_story_file, self.text_story, self.line_output_dir, self.spin_threads,
            self.spin_max_continuations, self.line_done_marker,
            self.line_smooth_folder, self.spin_smooth_min_words,
            self.line_split_path, self.spin_split_min_words, self.spin_split_max_words,
            self.combo_split_output_mode,
            self.line_many_split_file, self.spin_many_min_words, self.spin_many_max_words,
            self.combo_many_source_mode,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.line_story_file.setText(config.get("story_file", ""))
        self.text_story.setPlainText(config.get("story", ""))
        self.line_output_dir.setText(config.get("output_dir", ""))
        self.spin_threads.setValue(int(config.get("threads", 1)))
        self.spin_max_continuations.setValue(int(config.get("max_continuations", 10)))
        self.line_done_marker.setText(config.get("done_marker", "[[DONE]]"))
        self.line_smooth_folder.setText(config.get("smooth_folder", ""))
        self.spin_smooth_min_words.setValue(int(config.get("smooth_min_words", 0)))
        self.line_split_path.setText(config.get("split_path", ""))
        self.spin_split_min_words.setValue(int(config.get("split_min_words", 15)))
        self.spin_split_max_words.setValue(int(config.get("split_max_words", 30)))
        split_mode_index = self.combo_split_output_mode.findData(
            config.get("split_output_mode", "new")
        )
        self.combo_split_output_mode.setCurrentIndex(max(0, split_mode_index))
        self.line_many_split_file.setText(config.get("many_split_file", ""))
        self.spin_many_min_words.setValue(int(config.get("many_min_words", 6)))
        self.spin_many_max_words.setValue(int(config.get("many_max_words", 25)))
        many_source_mode_index = self.combo_many_source_mode.findData(
            config.get("many_source_mode", "keep")
        )
        self.combo_many_source_mode.setCurrentIndex(max(0, many_source_mode_index))
        for widget in widgets:
            widget.blockSignals(False)
        selected = set(config.get("countries", []))
        for country, checkbox in self.country_checks.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(country in selected)
            checkbox.blockSignals(False)

        custom_countries = config.get("custom_country_selection")
        if not isinstance(custom_countries, list):
            old_custom_story = config.get("custom_story_config", {})
            custom_countries = (
                old_custom_story.get("countries", config.get("countries", []))
                if isinstance(old_custom_story, dict) else config.get("countries", [])
            )
        self._custom_country_selection = [
            country for country in custom_countries if country in self.country_checks
        ]
        selected_country = config.get(
            "selected_country_template", config.get("selected_story_template")
        )
        if selected_country not in self.country_templates:
            selected_country = None
        self._refresh_country_templates(selected_country)
        self._changing_country_template = True
        try:
            self._apply_country_selection(
                self.country_templates[selected_country]
                if selected_country is not None else self._custom_country_selection
            )
        finally:
            self._changing_country_template = False
        self.save_config()
