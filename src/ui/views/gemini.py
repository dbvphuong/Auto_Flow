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
    QDialog, QPlainTextEdit, QApplication,
)

from core.workers import GeminiWorker, GEMINI_MAX_RETRIES
from common.gemini_languages import COUNTRIES, GEMINI_COUNTRY_OPTIONS, LANGUAGE_BY_COUNTRY
from data.database import SessionLocal
from data.models import Account, GeminiBatch


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
        self.line_master_file = QLineEdit()
        self.line_master_file.setReadOnly(True)
        self.line_master_file.setPlaceholderText("Nhập text bên dưới hoặc chọn một file")
        self.btn_master_file = QPushButton("📄 Chọn file")
        self.text_master_prompt = QTextEdit()
        self.text_master_prompt.setPlaceholderText("Nhập Master Prompt...")
        self.text_master_prompt.setMaximumHeight(130)

        self.line_story_file = QLineEdit()
        self.line_story_file.setReadOnly(True)
        self.line_story_file.setPlaceholderText("Nhập text bên dưới hoặc chọn một file")
        self.btn_story_file = QPushButton("📄 Chọn file")
        self.text_story = QTextEdit()
        self.text_story.setPlaceholderText("Nhập nội dung cốt truyện...")
        self.text_story.setMaximumHeight(160)

        content_layout.addWidget(QLabel("Master Prompt:"), 0, 0)
        content_layout.addWidget(self.line_master_file, 0, 1)
        content_layout.addWidget(self.btn_master_file, 0, 2)
        content_layout.addWidget(self.text_master_prompt, 1, 0, 1, 3)
        content_layout.addWidget(QLabel("Cốt truyện:"), 2, 0)
        content_layout.addWidget(self.line_story_file, 2, 1)
        content_layout.addWidget(self.btn_story_file, 2, 2)
        content_layout.addWidget(self.text_story, 3, 0, 1, 3)
        left_layout.addWidget(content_group)

        country_group = QGroupBox("Quốc gia — tích bao nhiêu sẽ tạo bấy nhiêu batch")
        country_layout = QGridLayout(country_group)
        self.chk_all_countries = QCheckBox("Chọn tất cả")
        self.chk_all_countries.setStyleSheet("font-weight:bold;color:#c4b5fd;")
        country_layout.addWidget(self.chk_all_countries, 0, 0, 1, 3)
        for index, (country, display_name) in enumerate(GEMINI_COUNTRY_OPTIONS):
            checkbox = QCheckBox(display_name)
            checkbox.setToolTip(f"File đầu ra: {display_name}.txt")
            self.country_checks[country] = checkbox
            country_layout.addWidget(checkbox, index // 3 + 1, index % 3)
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
        self.btn_master_file.clicked.connect(
            lambda: self._choose_text_file(self.line_master_file, self.text_master_prompt)
        )
        self.btn_story_file.clicked.connect(
            lambda: self._choose_text_file(self.line_story_file, self.text_story)
        )
        self.btn_output_dir.clicked.connect(self._browse_output_folder)
        self.chk_all_countries.toggled.connect(self._toggle_all_countries)
        self.chk_all_batches.toggled.connect(self._toggle_all_batches)
        self.btn_create_queue.clicked.connect(self.create_country_batches)
        self.btn_delete.clicked.connect(self.delete_selected)
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
            self.text_master_prompt.textChanged, self.text_story.textChanged,
            self.line_output_dir.textChanged, self.line_done_marker.textChanged,
            self.spin_threads.valueChanged, self.spin_max_continuations.valueChanged,
            self.line_smooth_folder.textChanged, self.spin_smooth_min_words.valueChanged,
            self.line_split_path.textChanged, self.spin_split_min_words.valueChanged,
            self.spin_split_max_words.valueChanged,
            self.combo_split_output_mode.currentIndexChanged,
        ):
            widget_signal.connect(self.save_config)
        for checkbox in self.country_checks.values():
            checkbox.toggled.connect(self.save_config)

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
            target_name = f"{path_number}_{source_name}"
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
            f"Tiền tố số  : {path_number}_",
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
        master_prompt = self.text_master_prompt.toPlainText().strip()
        story = self.text_story.toPlainText().strip()
        output_dir = self.line_output_dir.text().strip()
        done_marker = self.line_done_marker.text().strip()
        countries = self._selected_countries()
        if not master_prompt:
            QMessageBox.warning(self, "Thiếu Master Prompt", "Hãy nhập text hoặc chọn file Master Prompt.")
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

    def _on_continuation(self, batch_id, current, total):
        logging.info("[Gemini UI] Batch id=%s progress signal=%s/%s", batch_id, current, total)
        self._set_row(batch_id, continuation=f"{current}/{total}")

    def _on_finished(self, batch_id, result_path):
        logging.info("[Gemini UI] Batch id=%s finished; result=%s", batch_id, result_path)
        self._set_row(batch_id, status="SUCCESS", detail=result_path)
        QTimer.singleShot(0, self.process_queue)

    def _on_error(self, batch_id, message):
        logging.error("[Gemini UI] Batch id=%s error=%s", batch_id, message)
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
        logging.warning(
            "[Gemini UI] Retry batch id=%s lần %s/%s; "
            "tránh account id=%s ở lượt kế; queue=%s",
            batch_id, retry_number, GEMINI_MAX_RETRIES,
            account_id, self.task_queue,
        )
        self._set_row(batch_id, account="—", status="PENDING", detail=detail)
        QTimer.singleShot(0, self.process_queue)

    def _on_account_unavailable(self, batch_id, account_id, message):
        self.session_skipped_account_ids.add(account_id)
        if batch_id not in self.task_queue:
            self.task_queue.append(batch_id)
        logging.warning(
            "[Gemini UI] Bỏ qua account id=%s trong phiên hiện tại; "
            "đưa batch id=%s về cuối queue; lý do=%s",
            account_id, batch_id, message,
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
        config = {
            "master_file": self.line_master_file.text(),
            "story_file": self.line_story_file.text(),
            "master_prompt": self.text_master_prompt.toPlainText(),
            "story": self.text_story.toPlainText(),
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
        }
        try:
            with open(self._config_path(), "w", encoding="utf-8") as config_file:
                json.dump(config, config_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            logging.warning(f"[Gemini UI] Không lưu được cấu hình: {exc}")

    def load_config(self):
        try:
            with open(self._config_path(), "r", encoding="utf-8") as config_file:
                config = json.load(config_file)
        except (OSError, ValueError):
            return
        widgets = (
            self.line_master_file, self.line_story_file, self.text_master_prompt,
            self.text_story, self.line_output_dir, self.spin_threads,
            self.spin_max_continuations, self.line_done_marker,
            self.line_smooth_folder, self.spin_smooth_min_words,
            self.line_split_path, self.spin_split_min_words, self.spin_split_max_words,
            self.combo_split_output_mode,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.line_master_file.setText(config.get("master_file", ""))
        self.line_story_file.setText(config.get("story_file", ""))
        self.text_master_prompt.setPlainText(config.get("master_prompt", ""))
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
        for widget in widgets:
            widget.blockSignals(False)
        selected = set(config.get("countries", []))
        for country, checkbox in self.country_checks.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(country in selected)
            checkbox.blockSignals(False)
