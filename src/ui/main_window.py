from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, 
                             QStackedWidget, QPushButton, QLabel, QMessageBox)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QPixmap, QMovie

from ui.views.flow_image import FlowImageView
from ui.views.flow_video import FlowVideoView
from ui.views.gemini import GeminiView
from ui.views.accounts import AccountsView

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
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
        self.sidebar.setStyleSheet("background-color: #1e1e2e;")
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
        self.btn_accounts = QPushButton("⚙️ Cài đặt hệ thống")
        
        for btn in [self.btn_flow_image, self.btn_flow_video, self.btn_gemini, self.btn_accounts]:
            sidebar_layout.addWidget(btn)
            
        main_layout.addWidget(self.sidebar)
        
        # Stacked Widget for Tabs
        self.stacked_widget = QStackedWidget()
        
        # Flow Image Tab
        self.tab_flow_image = FlowImageView()
        self.tab_flow_image.setStyleSheet("background-color: #181825;")
        self.stacked_widget.addWidget(self.tab_flow_image)
        
        # Flow Video Tab
        self.tab_flow_video = FlowVideoView()
        self.tab_flow_video.setStyleSheet("background-color: #181825;")
        self.stacked_widget.addWidget(self.tab_flow_video)

        # Gemini Tab
        self.tab_gemini = GeminiView()
        self.tab_gemini.setStyleSheet("background-color: #181825;")
        self.stacked_widget.addWidget(self.tab_gemini)
        
        # Accounts Tab
        self.tab_accounts = AccountsView()
        self.tab_accounts.setStyleSheet("background-color: #181825;")
        self.stacked_widget.addWidget(self.tab_accounts)
        
        main_layout.addWidget(self.stacked_widget)
        
        # Connections
        self.btn_flow_image.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(0))
        self.btn_flow_video.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(1))
        self.btn_gemini.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(2))
        self.btn_accounts.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(3))
        
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
        self.update_sidebar_styles(0)

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

        buttons = [self.btn_flow_image, self.btn_flow_video, self.btn_gemini, self.btn_accounts]
        for i, btn in enumerate(buttons):
            if i == index:
                btn.setStyleSheet("""
                    QPushButton {
                        text-align: left;
                        padding: 12px 15px;
                        border: none;
                        color: #ffffff;
                        background-color: #313244;
                        border-left: 4px solid #8b5cf6;
                        font-weight: bold;
                        font-size: 14px;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        text-align: left;
                        padding: 12px 15px;
                        border: none;
                        color: #a6adc8;
                        background-color: transparent;
                        font-size: 14px;
                    }
                    QPushButton:hover {
                        background-color: #1e1e2e;
                        color: #ffffff;
                    }
                """)
        if index == 3 and hasattr(self, "tab_accounts"):
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
        
        if flow_image and (getattr(flow_image, "active_workers_count", 0) > 0 or getattr(flow_image, "task_queue", [])):
            running = True
        if flow_video and (getattr(flow_video, "active_workers_count", 0) > 0 or getattr(flow_video, "task_queue", [])):
            running = True
        if gemini and (getattr(gemini, "active_workers_count", 0) > 0 or getattr(gemini, "task_queue", [])):
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
            
        try:
            from core.browser_manager import kill_all_registered_chromes
            kill_all_registered_chromes()
        except Exception:
            pass
            
        super().closeEvent(event)
