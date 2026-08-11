from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QLineEdit, QTableWidget, QTableWidgetItem, 
                             QHeaderView, QMessageBox, QGroupBox, QComboBox, 
                             QCheckBox, QInputDialog, QDialog, QAbstractItemView)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
import logging
from data.database import SessionLocal
from data.models import Account
from core.browser_manager import login_and_save_cookies, get_local_chrome_profiles, get_browser_launch_params
from core.system_config import load_system_config, save_system_config
from datetime import datetime, timedelta, timezone

class ReorderableTableWidget(QTableWidget):
    def __init__(self, rows, cols, parent=None):
        super().__init__(rows, cols, parent)
        self.row_moved_callback = None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropOverwriteMode(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def dropEvent(self, event):
        if event.source() == self:
            rows = self.selectionModel().selectedRows()
            if rows:
                source_row = rows[0].row()
                try:
                    position = event.position().toPoint()
                except AttributeError:
                    position = event.pos()
                drop_index = self.indexAt(position)
                to_row = drop_index.row()
                
                if to_row == -1:
                    to_row = self.rowCount() - 1
                
                if source_row != to_row and self.row_moved_callback:
                    self.row_moved_callback(source_row, to_row)
                    event.accept()
                    return
        super().dropEvent(event)

class ProxyDialog(QDialog):
    def __init__(self, parent=None, current_proxy="", use_proxy=True):
        super().__init__(parent)
        self.setWindowTitle("Cấu hình Proxy")
        self.setMinimumWidth(350)
        
        layout = QVBoxLayout(self)
        
        self.combo_status = QComboBox()
        self.combo_status.addItems(["ON (Sử dụng proxy)", "OFF (Sử dụng mạng máy)"])
        self.combo_status.setCurrentIndex(0 if use_proxy else 1)
        
        layout.addWidget(QLabel("Trạng thái Proxy:"))
        layout.addWidget(self.combo_status)
        
        self.line_proxy = QLineEdit()
        self.line_proxy.setPlaceholderText("host:port hoặc host:port:user:pass")
        self.line_proxy.setText(current_proxy)
        
        layout.addWidget(QLabel("Địa chỉ Proxy:"))
        layout.addWidget(self.line_proxy)
        
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Lưu")
        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6; 
                color: white; 
                font-weight: bold; 
                padding: 6px 15px; 
                border-radius: 4px;
                border: none;
            }
            QPushButton:hover {
                background-color: #a78bfa;
            }
        """)
        self.btn_save.clicked.connect(self.accept)
        
        self.btn_cancel = QPushButton("Hủy")
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: transparent; 
                border: 1px solid #4b5563; 
                padding: 6px 15px; 
                color: #d1d5db; 
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #374151;
            }
        """)
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)
        
    def get_data(self):
        use_proxy = (self.combo_status.currentIndex() == 0)
        proxy_str = self.line_proxy.text().strip()
        return use_proxy, proxy_str

class OpenBrowserWorker(QThread):
    browser_finished = pyqtSignal(int, str) # acc_id, cookies_json
    error = pyqtSignal(str)

    def __init__(self, acc_id, email, proxy, cookies_json, chrome_profile="_tool_profile_"):
        super().__init__()
        self.acc_id = acc_id
        self.email = email
        self.proxy = proxy
        self.cookies_json = cookies_json
        self.chrome_profile = chrome_profile

    def run(self):
        try:
            from playwright.sync_api import sync_playwright
            import json
            import time
            from core.browser_manager import launch_chrome_and_connect
            
            with sync_playwright() as p:
                context = launch_chrome_and_connect(p, self.email, self.chrome_profile, self.proxy)
                
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto("https://labs.google/fx/tools/image-fx", timeout=60000)
                except Exception:
                    try:
                        page.goto("https://www.google.com", timeout=30000)
                    except:
                        pass
                
                # Chờ cho đến khi tab/trình duyệt bị đóng bởi người dùng
                try:
                    while not page.is_closed():
                        page.wait_for_timeout(1000)
                except Exception:
                    pass
                
                # Thu thập lại cookies mới sau khi người dùng tương tác xong
                try:
                    updated_cookies = context.cookies()
                    cookie_names = {c['name'] for c in updated_cookies}
                    if "SID" in cookie_names:
                        self.browser_finished.emit(self.acc_id, json.dumps(updated_cookies))
                except:
                    pass
                
                try:
                    context.close()
                except:
                    pass
        except Exception as e:
            self.error.emit(str(e))

class LoginWorker(QThread):
    login_finished = pyqtSignal(str, str, str, int, str)
    # email, cookies_json, account_type, credits, chrome_profile
    error = pyqtSignal(str)

    def __init__(self, proxy, email, chrome_profile="_tool_profile_"):
        super().__init__()
        self.proxy = proxy
        self.email = email
        self.chrome_profile = chrome_profile

    def run(self):
        try:
            cookies_json, account_type, credits = login_and_save_cookies(self.proxy, self.email, self.chrome_profile)
            if cookies_json:
                self.login_finished.emit(
                    self.email, cookies_json, account_type, credits, self.chrome_profile
                )
            else:
                self.error.emit("Không lấy được cookie hoặc hết thời gian chờ.")
        except Exception as e:
            self.error.emit(str(e))

class AccountsView(QWidget):
    def __init__(self):
        super().__init__()
        self.refreshing_account_id = None
        self.open_workers = {}
        self.init_ui()
        self.load_accounts()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Top panel: Add account
        add_group = QGroupBox("Thêm tài khoản mới (Login Trình duyệt)")
        add_layout = QHBoxLayout()
        
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Nhập Email Google của bạn")
        add_layout.addWidget(self.email_input)
        
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("Proxy (Tùy chọn) - host:port:user:pass")
        add_layout.addWidget(self.proxy_input)

        add_layout.addWidget(QLabel("Profile:"))
        self.combo_login_profile = QComboBox()
        self.combo_login_profile.setMinimumWidth(220)
        self.combo_login_profile.setToolTip(
            "Chọn Profile Chrome sẽ dùng ngay khi đăng nhập và lưu cho account."
        )
        for folder, display_name in get_local_chrome_profiles():
            self.combo_login_profile.addItem(display_name, folder)
        add_layout.addWidget(self.combo_login_profile)
        
        self.btn_login = QPushButton("Mở Chrome để Đăng nhập")
        self.btn_login.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6; 
                color: white; 
                padding: 6px 15px; 
                border-radius: 5px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover {
                background-color: #a78bfa;
            }
            QPushButton:pressed {
                background-color: #7c3aed;
            }
            QPushButton:disabled {
                background-color: #4b5563;
                color: #9ca3af;
            }
        """)
        self.btn_login.clicked.connect(self.start_login)
        add_layout.addWidget(self.btn_login)
        
        add_group.setLayout(add_layout)
        layout.addWidget(add_group)

        chrome_options_group = QGroupBox("Tuỳ chọn Chrome khi chạy tự động")
        chrome_options_layout = QHBoxLayout()

        self.chk_show_chrome_when_running = QCheckBox("Hiện Chrome khi chạy ảnh/video")
        self.chk_show_chrome_when_running.setToolTip(
            "Bật để nhìn thấy Chrome khi tool chạy. Tắt để mở Chrome thu nhỏ và đưa ra ngoài màn hình."
        )
        self.chk_show_chrome_when_running.stateChanged.connect(self.save_system_settings)
        chrome_options_layout.addWidget(self.chk_show_chrome_when_running)
        chrome_options_layout.addStretch()

        chrome_options_group.setLayout(chrome_options_layout)
        layout.addWidget(chrome_options_group)
        
        # Selector Tool panel
        tool_group = QGroupBox("Công cụ hỗ trợ lấy Selector (Playwright Inspector)")
        tool_layout = QHBoxLayout()
        
        self.selector_acc_combo = QComboBox()
        tool_layout.addWidget(QLabel("Tài khoản:"))
        tool_layout.addWidget(self.selector_acc_combo)
        
        self.selector_url_input = QLineEdit()
        self.selector_url_input.setPlaceholderText("Nhập URL (Ví dụ: https://labs.google/fx/tools/image-fx)")
        self.selector_url_input.setText("https://labs.google/fx/tools/image-fx")
        tool_layout.addWidget(QLabel("URL:"))
        tool_layout.addWidget(self.selector_url_input)
        
        self.btn_open_codegen = QPushButton("Mở Tool Lấy Selector")
        self.btn_open_codegen.setStyleSheet("""
            QPushButton {
                background-color: #0d6efd; 
                color: white; 
                padding: 6px 15px; 
                border-radius: 5px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover {
                background-color: #0b5ed7;
            }
            QPushButton:pressed {
                background-color: #0a58ca;
            }
        """)
        self.btn_open_codegen.clicked.connect(self.open_codegen_tool)
        tool_layout.addWidget(self.btn_open_codegen)
        
        tool_group.setLayout(tool_layout)
        layout.addWidget(tool_group)
        
        # Table panel
        self.table = ReorderableTableWidget(0, 10)
        self.table.row_moved_callback = self.on_row_moved
        self.table.setHorizontalHeaderLabels([
            "ID", "Email", "Loại", "Ảnh", "Video", "Gemini", "Proxy",
            "Profile Chrome", "Trạng thái", "Hành động",
        ])
        
        # Cấu hình chiều rộng các cột để tránh font wrapped và tối ưu hóa diện tích hiển thị
        self.table.setColumnWidth(0, 40)   # ID
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch) # Email (rộng hơn và tự co giãn)
        self.table.setColumnWidth(2, 130)  # Loại (FREE, PRO, ULTRA và số dư credit)
        self.table.setColumnWidth(3, 50)   # Ảnh
        self.table.setColumnWidth(4, 50)   # Video
        self.table.setColumnWidth(5, 65)   # Gemini
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch) # Proxy (tự co giãn)
        self.table.setColumnWidth(7, 180)  # Profile Chrome
        self.table.setColumnWidth(8, 140)  # Trạng thái (để hiển thị icon đẹp mắt)
        self.table.setColumnWidth(9, 280)  # Hành động (Mở, Làm mới, Proxy, Xóa)
        
        layout.addWidget(self.table)
        self.load_system_settings()

    def load_system_settings(self):
        config = load_system_config()
        self.chk_show_chrome_when_running.blockSignals(True)
        self.chk_show_chrome_when_running.setChecked(config.get("show_chrome_when_running", False))
        self.chk_show_chrome_when_running.blockSignals(False)

    def save_system_settings(self):
        try:
            save_system_config({
                "show_chrome_when_running": self.chk_show_chrome_when_running.isChecked()
            })
            logging.info(
                f"[Settings] Show Chrome khi chạy tự động: {self.chk_show_chrome_when_running.isChecked()}"
            )
        except Exception as e:
            logging.warning(f"[Settings] Không thể lưu tuỳ chọn Chrome: {e}")

    def load_accounts(self):
        self.table.setRowCount(0)
        db = SessionLocal()
        accounts = db.query(Account).order_by(Account.position.asc()).all()
        
        # Cập nhật selector_acc_combo
        current_selection_id = self.selector_acc_combo.currentData()
        self.selector_acc_combo.clear()
        self.selector_acc_combo.addItem("Mặc định (Không dùng profile/Không proxy)", "_none_")
        for acc in accounts:
            self.selector_acc_combo.addItem(f"ID {acc.id}: {acc.email}", acc.id)
            
        # Khôi phục lựa chọn cũ nếu có
        if current_selection_id:
            idx = self.selector_acc_combo.findData(current_selection_id)
            if idx >= 0:
                self.selector_acc_combo.setCurrentIndex(idx)
        # Lấy danh sách local chrome profiles một lần duy nhất trước vòng lặp để tránh scan ổ đĩa liên tục gây lag
        profiles = get_local_chrome_profiles()
        for i, acc in enumerate(accounts):
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(str(acc.id)))
            self.table.setItem(i, 1, QTableWidgetItem(acc.email or "Unknown"))
            
            # Loại (Disabled/Không chỉnh sửa được)
            type_str = f"{acc.account_type or 'FREE'}({acc.credits or 0} credit)"
            type_item = QTableWidgetItem(type_str)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 2, type_item)
            
            # Ảnh (CheckBox)
            chk_image_container = QWidget()
            chk_image_layout = QHBoxLayout(chk_image_container)
            chk_image_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_image_layout.setContentsMargins(0, 0, 0, 0)
            chk_image = QCheckBox()
            chk_image.setChecked(acc.is_image)
            chk_image.stateChanged.connect(lambda state, a_id=acc.id: self.update_feature_toggle(a_id, "is_image", state == Qt.CheckState.Checked.value))
            chk_image_layout.addWidget(chk_image)
            self.table.setCellWidget(i, 3, chk_image_container)
            
            # Video (CheckBox)
            chk_video_container = QWidget()
            chk_video_layout = QHBoxLayout(chk_video_container)
            chk_video_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_video_layout.setContentsMargins(0, 0, 0, 0)
            chk_video = QCheckBox()
            chk_video.setChecked(acc.is_video)
            chk_video.stateChanged.connect(lambda state, a_id=acc.id: self.update_feature_toggle(a_id, "is_video", state == Qt.CheckState.Checked.value))
            chk_video_layout.addWidget(chk_video)
            self.table.setCellWidget(i, 4, chk_video_container)

            # Gemini (CheckBox)
            chk_gemini_container = QWidget()
            chk_gemini_layout = QHBoxLayout(chk_gemini_container)
            chk_gemini_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_gemini_layout.setContentsMargins(0, 0, 0, 0)
            chk_gemini = QCheckBox()
            chk_gemini.setChecked(bool(acc.is_gemini))
            chk_gemini.stateChanged.connect(
                lambda state, a_id=acc.id: self.update_feature_toggle(
                    a_id, "is_gemini", state == Qt.CheckState.Checked.value
                )
            )
            chk_gemini_layout.addWidget(chk_gemini)
            self.table.setCellWidget(i, 5, chk_gemini_container)
            
            proxy_str = acc.proxy or "Không có"
            if acc.proxy:
                prefix = "[ON] " if acc.use_proxy else "[OFF] "
                display_proxy = f"{prefix}{proxy_str}"
            else:
                display_proxy = "Không có"
            self.table.setItem(i, 6, QTableWidgetItem(display_proxy))
            
            # Profile Chrome (ComboBox)
            combo_profile = QComboBox()
            for folder, display_name in profiles:
                combo_profile.addItem(display_name, folder)
            
            current_profile = acc.chrome_profile or "_tool_profile_"
            idx = combo_profile.findData(current_profile)
            if idx >= 0:
                combo_profile.setCurrentIndex(idx)
            else:
                combo_profile.setCurrentIndex(0)
                
            combo_profile.currentIndexChanged.connect(
                lambda index, a_id=acc.id, cb=combo_profile: self.update_chrome_profile(a_id, cb.itemData(index))
            )
            self.table.setCellWidget(i, 7, combo_profile)
            
            # Xác định trạng thái hiển thị dựa trên is_active VÀ cookie_expiry
            now_utc = datetime.utcnow()
            cookie_expired = (
                acc.cookie_expiry is not None and acc.cookie_expiry < now_utc
            )
            if not acc.is_active:
                status_text = "❌ KHÔNG HOẠT ĐỘNG"
                status_color = "#ef4444"
            elif cookie_expired:
                status_text = "⚠️ Cookie hết hạn"
                status_color = "#b45309"
            else:
                status_text = "✅ HOẠT ĐỘNG"
                status_color = "#15803d"
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            from PyQt6.QtGui import QBrush, QColor
            status_item.setBackground(QBrush(QColor(status_color)))
            status_item.setForeground(QBrush(QColor("#ffffff")))
            self.table.setItem(i, 8, status_item)
            
            # Hành động (Làm mới, Proxy, Xóa)
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(4)
            
            # Button Mở Chrome xem trực tiếp
            btn_open = QPushButton("Mở")
            btn_open.setStyleSheet("""
                QPushButton {
                    background-color: #007bff; 
                    color: white; 
                    border-radius: 3px; 
                    padding: 2px 8px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #0069d9;
                }
                QPushButton:pressed {
                    background-color: #0056b3;
                }
            """)
            btn_open.clicked.connect(lambda _, a_id=acc.id: self.open_chrome_session(a_id))
            
            btn_refresh = QPushButton("Làm mới")
            btn_refresh.setStyleSheet("""
                QPushButton {
                    background-color: #28a745; 
                    color: white; 
                    border-radius: 3px; 
                    padding: 2px 8px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #218838;
                }
                QPushButton:pressed {
                    background-color: #1e7e34;
                }
            """)
            btn_refresh.clicked.connect(lambda _, a_id=acc.id: self.refresh_account(a_id))
            
            btn_proxy = QPushButton("Proxy")
            btn_proxy.setStyleSheet("""
                QPushButton {
                    background-color: #fd7e14; 
                    color: white; 
                    border-radius: 3px; 
                    padding: 2px 8px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #e0690b;
                }
                QPushButton:pressed {
                    background-color: #c95f08;
                }
            """)
            btn_proxy.clicked.connect(lambda _, a_id=acc.id: self.edit_proxy(a_id))
            
            btn_delete = QPushButton("Xóa")
            btn_delete.setStyleSheet("""
                QPushButton {
                    background-color: #dc3545; 
                    color: white; 
                    border-radius: 3px; 
                    padding: 2px 8px;
                    border: none;
                }
                QPushButton:hover {
                    background-color: #c82333;
                }
                QPushButton:pressed {
                    background-color: #bd2130;
                }
            """)
            btn_delete.clicked.connect(lambda _, a_id=acc.id: self.delete_account(a_id))
            
            action_layout.addWidget(btn_open)
            action_layout.addWidget(btn_refresh)
            action_layout.addWidget(btn_proxy)
            action_layout.addWidget(btn_delete)
            self.table.setCellWidget(i, 9, action_widget)
        db.close()

    def start_login(self):
        self.refreshing_account_id = None
        email = self.email_input.text().strip()
        if not email:
            QMessageBox.warning(self, "Lỗi", "Vui lòng nhập Email Google!")
            return
            
        proxy = self.proxy_input.text().strip()
        chrome_profile = self.combo_login_profile.currentData() or "_tool_profile_"
        logging.info(
            "[Account] Khởi động đăng nhập Chrome cho email=%s; proxy=%s; profile=%s",
            email, proxy or "None", chrome_profile,
        )
        self.btn_login.setEnabled(False)
        self.btn_login.setText("Đang mở trình duyệt...")
        
        self.worker = LoginWorker(proxy, email, chrome_profile)
        self.worker.login_finished.connect(self.on_login_finished)
        self.worker.error.connect(self.on_login_error)
        self.worker.start()

    def on_login_finished(self, email, cookies_json, account_type, credits, chrome_profile):
        self.btn_login.setEnabled(True)
        self.btn_login.setText("Mở Chrome để Đăng nhập")
        
        db = SessionLocal()
        if self.refreshing_account_id:
            acc = db.query(Account).filter(Account.id == self.refreshing_account_id).first()
            if acc:
                acc.cookies_json = cookies_json
                acc.account_type = account_type
                acc.credits = credits
                acc.is_active = True
                acc.chrome_profile = chrome_profile
                acc.cookie_expiry = datetime.utcnow() + timedelta(days=30)
                db.commit()
                logging.info(f"[Account] Đã làm mới cookie thành công cho ID: {self.refreshing_account_id} ({email}) - Loại: {account_type} ({credits} credits)")
                QMessageBox.information(self, "Thành công", "Đã làm mới cookie thành công!")
            self.refreshing_account_id = None
        else:
            position_val = db.query(Account).count()
            acc = Account(
                email=email,
                proxy=self.proxy_input.text().strip() or None,
                cookies_json=cookies_json,
                account_type=account_type,
                credits=credits,
                is_active=True,
                chrome_profile=chrome_profile,
                cookie_expiry=datetime.utcnow() + timedelta(days=30),
                position=position_val
            )
            db.add(acc)
            db.commit()
            logging.info(f"[Account] Thêm tài khoản thành công cho email: {email} - Loại: {account_type} ({credits} credits)")
            QMessageBox.information(self, "Thành công", "Đã thêm tài khoản thành công!")
        db.close()
        self.load_accounts()

    def on_login_error(self, err):
        logging.error(f"[Account] Đăng nhập thất bại: {err}")
        self.btn_login.setEnabled(True)
        self.btn_login.setText("Mở Chrome để Đăng nhập")
        self.refreshing_account_id = None
        message = str(err)
        if not message.lower().startswith("lỗi đăng nhập:"):
            message = f"Lỗi đăng nhập: {message}"
        QMessageBox.warning(self, "Lỗi", message)

    def delete_account(self, acc_id):
        reply = QMessageBox.question(self, "Xóa", "Bạn có chắc muốn xóa tài khoản này?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            logging.info(f"[Account] Xóa tài khoản ID: {acc_id}")
            db = SessionLocal()
            acc = db.query(Account).filter(Account.id == acc_id).first()
            if acc:
                db.delete(acc)
                db.commit()
                # Re-index positions of remaining accounts
                remaining = db.query(Account).order_by(Account.position.asc()).all()
                for idx, a in enumerate(remaining):
                    a.position = idx
                db.commit()
            db.close()
            self.load_accounts()

    def open_chrome_session(self, acc_id):
        if acc_id in self.open_workers and self.open_workers[acc_id].isRunning():
            QMessageBox.information(self, "Thông báo", "Trình duyệt của tài khoản này đang mở!")
            return
            
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if not acc:
            db.close()
            return
            
        proxy = acc.proxy if acc.use_proxy else None
        cookies_json = acc.cookies_json
        chrome_profile = acc.chrome_profile or "_tool_profile_"
        db.close()
        
        logging.info(f"[Account] Khởi chạy Chrome xem trực tiếp cho ID: {acc_id} sử dụng profile {chrome_profile}")
        
        worker = OpenBrowserWorker(acc_id, acc.email, proxy, cookies_json, chrome_profile)
        worker.browser_finished.connect(self.on_open_browser_finished)
        worker.error.connect(lambda err: QMessageBox.warning(self, "Lỗi", f"Lỗi trình duyệt: {err}"))
        self.open_workers[acc_id] = worker
        worker.start()
        
    def on_open_browser_finished(self, acc_id, updated_cookies_json):
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if acc:
            acc.cookies_json = updated_cookies_json
            acc.is_active = True
            acc.cookie_expiry = datetime.utcnow() + timedelta(days=30)  # Reset hạn cookie sau khi đăng nhập lại
            db.commit()
            logging.info(f"[Account] Đã cập nhật lại cookies và reset hạn cho ID {acc_id} từ phiên Chrome trực tiếp.")
        db.close()
        self.load_accounts()

    def update_chrome_profile(self, acc_id, profile_folder):
        logging.info(f"[Account] Cập nhật Chrome Profile cho ID {acc_id} thành: {profile_folder}")
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if acc:
            acc.chrome_profile = profile_folder
            db.commit()
        db.close()

    def update_account_type(self, acc_id, new_type):
        logging.info(f"[Account] Thay đổi loại tài khoản ID {acc_id} thành: {new_type}")
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if acc:
            acc.account_type = new_type
            db.commit()
        db.close()

    def update_feature_toggle(self, acc_id, feature, is_checked):
        logging.info(f"[Account] Thay đổi toggle {feature} cho ID {acc_id} thành: {is_checked}")
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if acc:
            if feature == "is_image":
                acc.is_image = is_checked
            elif feature == "is_video":
                acc.is_video = is_checked
            elif feature == "is_gemini":
                acc.is_gemini = is_checked
            db.commit()
        db.close()

    def edit_proxy(self, acc_id):
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if not acc:
            db.close()
            return
            
        current_proxy = acc.proxy or ""
        use_proxy = acc.use_proxy
        db.close()
        
        dialog = ProxyDialog(self, current_proxy, use_proxy)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_use_proxy, new_proxy_str = dialog.get_data()
            
            db = SessionLocal()
            acc = db.query(Account).filter(Account.id == acc_id).first()
            if acc:
                logging.info(f"[Account] Cập nhật Proxy tài khoản ID {acc_id}: Trạng thái={new_use_proxy}, String='{new_proxy_str}'")
                acc.proxy = new_proxy_str or None
                acc.use_proxy = new_use_proxy
                db.commit()
            db.close()
            self.load_accounts()

    def refresh_account(self, acc_id):
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if not acc:
            db.close()
            return
            
        proxy = acc.proxy if acc.use_proxy else None
        email = acc.email
        chrome_profile = acc.chrome_profile or "_tool_profile_"
        db.close()
        
        logging.info(f"[Account] Khởi động làm mới cookie tài khoản ID {acc_id} ({email}) với profile {chrome_profile}")
        self.refreshing_account_id = acc_id
        self.btn_login.setEnabled(False)
        self.btn_login.setText("Đang làm mới cookie...")
        
        self.worker = LoginWorker(proxy, email, chrome_profile)
        self.worker.login_finished.connect(self.on_login_finished)
        self.worker.error.connect(self.on_login_error)
        self.worker.start()

    def open_codegen_tool(self):
        acc_val = self.selector_acc_combo.currentData()
        url = self.selector_url_input.text().strip() or "https://labs.google/fx/tools/image-fx"
        
        email = ""
        chrome_profile = "_tool_profile_"
        proxy = ""
        
        if acc_val and acc_val != "_none_":
            acc_id = acc_val
            db = SessionLocal()
            acc = db.query(Account).filter(Account.id == acc_id).first()
            if acc:
                email = acc.email or ""
                chrome_profile = acc.chrome_profile or "_tool_profile_"
                if acc.proxy and acc.use_proxy:
                    proxy = acc.proxy
            db.close()
            
        import subprocess
        import sys
        import os
        from data.database import BASE_DIR
        
        script_path = os.path.join(BASE_DIR, "src", "core", "open_inspector.py")
        
        cmd = [sys.executable, script_path, "--email", email, "--profile", chrome_profile, "--url", url]
        if proxy:
            cmd.extend(["--proxy", proxy])
            
        logging.info(f"[SelectorTool] Chạy lệnh: {' '.join(cmd)}")
        
        try:
            subprocess.Popen(cmd)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", f"Không thể mở công cụ Selector: {e}")

    def on_row_moved(self, source_row, to_row):
        logging.info(f"[Account] Kéo thả di chuyển dòng từ {source_row} sang {to_row}")
        db = SessionLocal()
        try:
            accounts = db.query(Account).order_by(Account.position.asc()).all()
            if not accounts or source_row >= len(accounts) or to_row >= len(accounts):
                return
                
            # Di chuyển trong danh sách Python
            acc = accounts.pop(source_row)
            accounts.insert(to_row, acc)
            
            # Cập nhật vị trí mới trong DB
            for idx, a in enumerate(accounts):
                a.position = idx
            db.commit()
            logging.info("[Account] Cập nhật vị trí tài khoản trong database thành công.")
        except Exception as e:
            logging.error(f"[Account] Lỗi khi đổi vị trí tài khoản: {e}")
        finally:
            db.close()
        
        # Tải lại bảng để cập nhật giao diện
        self.load_accounts()
