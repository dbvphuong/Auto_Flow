from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                             QStackedWidget, QPushButton, QLabel, QMessageBox,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QAbstractItemView)
from PyQt6.QtCore import Qt, QSize, QObject, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QMovie, QColor

from ui.views.flow_image import FlowImageView
from ui.views.flow_video import FlowVideoView
from ui.views.gemini import GeminiView
from ui.views.media_tools import MediaToolsView
from ui.views.capcut import CapcutView
from ui.views.accounts import AccountsView
from ui.theme import THEME_NAMES, next_theme, normalize_theme, theme_palette, theme_stylesheet
from core.system_config import load_system_config, save_system_config
from common.logger import subscribe, unsubscribe


class LogBridge(QObject):
    entry_received = pyqtSignal(dict)

    def publish(self, entry):
        self.entry_received.emit(entry)


class LogView(QWidget):
    """Thread-safe realtime view of the concise application log."""

    MAX_ROWS = 1000
    LEVEL_COLORS = {
        "WARNING": "#f59e0b",
        "ERROR": "#ef4444",
        "CRITICAL": "#ef4444",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)

        title = QLabel("Log hoạt động")
        title.setStyleSheet("font-size: 20px; font-weight: 800;")
        hint = QLabel("Hiển thị realtime các bước chính, tiến độ và lỗi cần xử lý.")
        layout.addWidget(title)
        layout.addWidget(hint)

        self.table_log = QTableWidget(0, 3)
        self.table_log.setHorizontalHeaderLabels(["Thời gian", "Mức", "Nội dung"])
        self.table_log.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_log.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_log.setWordWrap(True)
        self.table_log.verticalHeader().setVisible(False)
        header = self.table_log.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table_log)

        self.bridge = LogBridge(self)
        self.bridge.entry_received.connect(self.append_entry)
        self._log_callback = self.bridge.publish
        history = subscribe(self._log_callback, replay=True)
        for entry in history:
            self.append_entry(entry)

    def append_entry(self, entry):
        if self.table_log.rowCount() >= self.MAX_ROWS:
            self.table_log.removeRow(0)
        row = self.table_log.rowCount()
        self.table_log.insertRow(row)
        values = (entry.get("time", ""), entry.get("level", ""), entry.get("message", ""))
        color = QColor(self.LEVEL_COLORS.get(values[1], "#22c55e"))
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            if column == 1:
                item.setForeground(color)
            self.table_log.setItem(row, column, item)
        self.table_log.scrollToBottom()

    def closeEvent(self, event):
        unsubscribe(self._log_callback)
        super().closeEvent(event)

    def dispose(self):
        unsubscribe(self._log_callback)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_theme = normalize_theme(load_system_config().get("theme"))
        self.setWindowTitle("CF_Flow v1.0.0 (by Phươnng)")
        self.setWindowIcon(QIcon(r"e:\GG\Auto_Flow\data\logo.gif"))
        self.setMinimumSize(1200, 800)
        
        self.init_ui()
        
    def init_ui(self):
        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Sidebar
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(200)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 10, 0, 0)
        sidebar_layout.setSpacing(5)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        # Logo GIF
        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setStyleSheet("margin-top: -10px; margin-bottom: -10px; padding: 0px;")
        
        self.logo_movie = QMovie(r"e:\GG\Auto_Flow\data\logo.gif")
        if self.logo_movie.isValid():
            self.logo_movie.setScaledSize(QSize(80, 80))
            logo_label.setMovie(self.logo_movie)
            self.logo_movie.start()
        else:
            # Fallback to static
            pixmap = QPixmap(r"e:\GG\Auto_Flow\data\logo.gif")
            if not pixmap.isNull():
                logo_label.setPixmap(pixmap.scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                
        sidebar_layout.addWidget(logo_label)
        
        # Navigation Buttons
        self.btn_flow_image = QPushButton("📷 Flow Ảnh")
        self.btn_flow_video = QPushButton("📹 Flow Video")
        self.btn_gemini = QPushButton("✦ Gemini")
        self.btn_media_tools = QPushButton("🛠 Sửa Ảnh/Video")
        self.btn_capcut = QPushButton("🎬 CAPCUT")
        self.btn_accounts = QPushButton("⚙️ Cài đặt hệ thống")
        self.btn_theme = QPushButton()
        self.btn_log = QPushButton("▤ Log")
        
        for btn in [
            self.btn_flow_image, self.btn_flow_video, self.btn_gemini,
            self.btn_media_tools, self.btn_capcut, self.btn_accounts,
        ]:
            sidebar_layout.addWidget(btn)
        sidebar_layout.addStretch(1)
        sidebar_layout.addWidget(self.btn_theme)
        sidebar_layout.addWidget(self.btn_log)
            
        main_layout.addWidget(self.sidebar)
        
        # Stacked Widget for Tabs
        self.stacked_widget = QStackedWidget()
        
        # Flow Image Tab
        self.tab_flow_image = FlowImageView()
        self.stacked_widget.addWidget(self.tab_flow_image)
        
        # Flow Video Tab
        self.tab_flow_video = FlowVideoView()
        self.stacked_widget.addWidget(self.tab_flow_video)

        # Gemini Tab
        self.tab_gemini = GeminiView()
        self.stacked_widget.addWidget(self.tab_gemini)

        # Media processing tools tab
        self.tab_media_tools = MediaToolsView()
        self.stacked_widget.addWidget(self.tab_media_tools)

        # CapCut timeline and background export tools
        self.tab_capcut = CapcutView()
        self.stacked_widget.addWidget(self.tab_capcut)
        
        # Accounts Tab
        self.tab_accounts = AccountsView()
        self.stacked_widget.addWidget(self.tab_accounts)

        # Concise realtime application log
        self.tab_log = LogView()
        self.stacked_widget.addWidget(self.tab_log)
        
        main_layout.addWidget(self.stacked_widget)
        
        # Connections
        self.btn_flow_image.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(0))
        self.btn_flow_video.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(1))
        self.btn_gemini.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(2))
        self.btn_media_tools.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(3))
        self.btn_capcut.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(4))
        self.btn_accounts.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(5))
        self.btn_log.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(6))
        self.btn_theme.clicked.connect(self.cycle_theme)
        
        # Đồng bộ di chuyển splitter giữa Flow Ảnh và Flow Video
        self.tab_flow_image.splitter.splitterMoved.connect(
            lambda pos, index: self.tab_flow_video.splitter.setSizes(self.tab_flow_image.splitter.sizes())
        )
        self.tab_flow_video.splitter.splitterMoved.connect(
            lambda pos, index: self.tab_flow_image.splitter.setSizes(self.tab_flow_video.splitter.sizes())
        )
        self.tab_gemini.splitter.splitterMoved.connect(
            lambda pos, index: self.tab_flow_image.splitter.setSizes(self.tab_gemini.splitter.sizes())
        )
        
        self.stacked_widget.currentChanged.connect(self.update_sidebar_styles)
        self.apply_theme(self.current_theme, save=False)
        self.update_sidebar_styles(0)

    def apply_theme(self, theme, save=True):
        self.current_theme = normalize_theme(theme)
        colors = theme_palette(self.current_theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme_stylesheet(self.current_theme))

        self.sidebar.setStyleSheet(f"background-color: {colors['sidebar']};")
        for page in (
            self.tab_flow_image, self.tab_flow_video, self.tab_gemini,
            self.tab_media_tools, self.tab_capcut, self.tab_accounts, self.tab_log,
        ):
            page.setObjectName("theme_page")
            page.setStyleSheet(
                f"QWidget#theme_page {{ background-color: {colors['window']}; "
                f"color: {colors['text']}; }}"
            )

        self.btn_theme.setText(f"🎨 Theme: {THEME_NAMES[self.current_theme]}")
        self.btn_theme.setToolTip("Nhấn để đổi: Tối → Sáng → Tím nhạt")
        self.update_sidebar_styles(self.stacked_widget.currentIndex())
        if save:
            save_system_config({"theme": self.current_theme})

    def cycle_theme(self):
        self.apply_theme(next_theme(self.current_theme))

    def update_sidebar_styles(self, index):
        # Đồng bộ kích thước splitter giữa các tab khi chuyển tab
        if index == 0:
            sizes = self.tab_flow_video.splitter.sizes()
            if sum(sizes) > 0:
                self.tab_flow_image.splitter.setSizes(sizes)
        elif index == 1:
            sizes = self.tab_flow_image.splitter.sizes()
            if sum(sizes) > 0:
                self.tab_flow_video.splitter.setSizes(sizes)
        elif index == 2:
            sizes = self.tab_flow_image.splitter.sizes()
            if sum(sizes) > 0:
                self.tab_gemini.splitter.setSizes(sizes)

        buttons = [
            self.btn_flow_image, self.btn_flow_video, self.btn_gemini,
            self.btn_media_tools, self.btn_capcut, self.btn_accounts, self.btn_log,
        ]
        colors = theme_palette(self.current_theme)
        for i, btn in enumerate(buttons):
            if i == index:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        text-align: left;
                        padding: 12px 15px;
                        border: none;
                        color: {colors['text']};
                        background-color: {colors['surface_hover']};
                        border-left: 4px solid {colors['accent']};
                        font-weight: bold;
                        font-size: 14px;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        text-align: left;
                        padding: 12px 15px;
                        border: none;
                        color: {colors['muted']};
                        background-color: transparent;
                        font-size: 14px;
                    }}
                    QPushButton:hover {{
                        background-color: {colors['surface_hover']};
                        color: {colors['text']};
                    }}
                """)
        self.btn_theme.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 9px 15px;
                margin-top: 3px;
                border: none;
                border-top: 1px solid {colors['border']};
                color: {colors['muted']};
                background-color: transparent;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {colors['surface_hover']};
                color: {colors['text']};
            }}
        """)
        if index == 5 and hasattr(self, "tab_accounts"):
            try:
                self.tab_accounts.load_accounts()
            except Exception as e:
                import logging
                logging.error(f"[UI] Lỗi khi tự động tải lại danh sách tài khoản: {e}")

    def closeEvent(self, event):
        running = False
        flow_image = getattr(self, "tab_flow_image", None)
        flow_video = getattr(self, "tab_flow_video", None)
        gemini = getattr(self, "tab_gemini", None)
        media_tools = getattr(self, "tab_media_tools", None)
        capcut = getattr(self, "tab_capcut", None)
        
        if flow_image and (getattr(flow_image, "active_workers_count", 0) > 0 or getattr(flow_image, "task_queue", [])):
            running = True
        if flow_video and (getattr(flow_video, "active_workers_count", 0) > 0 or getattr(flow_video, "task_queue", [])):
            running = True
        if gemini and (getattr(gemini, "active_workers_count", 0) > 0 or getattr(gemini, "task_queue", [])):
            running = True
        if media_tools and media_tools.is_processing:
            running = True
        if capcut and capcut.is_processing:
            running = True
            
        if running:
            reply = QMessageBox.question(
                self, 
                "Xác nhận thoát", 
                "Các luồng đang chạy dở. Bạn có chắc chắn muốn dừng tác vụ và đóng tool?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
                
        if flow_image:
            flow_image.shutdown_tasks()
        if flow_video:
            flow_video.shutdown_tasks()
        if gemini:
            gemini.shutdown_tasks()
        if media_tools:
            media_tools.shutdown_tasks()
        if capcut:
            capcut.shutdown_tasks()
        if hasattr(self, "tab_log"):
            self.tab_log.dispose()
            
        try:
            from core.browser_manager import kill_all_registered_chromes
            kill_all_registered_chromes()
        except Exception:
            pass
            
        super().closeEvent(event)
