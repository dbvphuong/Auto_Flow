from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QLineEdit, QTableWidget, QTableWidgetItem, 
                             QHeaderView, QMessageBox, QGroupBox, QComboBox, 
                             QCheckBox, QInputDialog, QAbstractItemView,
                             QListWidget, QListWidgetItem, QListView, QSizePolicy)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal
import logging
from data.database import SessionLocal
from data.models import Account
from core.browser_manager import login_and_save_cookies, get_local_chrome_profiles, get_browser_launch_params
from core.system_config import (
    CHROME_RUN_MODE_HEADLESS,
    CHROME_RUN_MODE_MINIMIZED,
    CHROME_RUN_MODE_VISIBLE,
    get_chrome_run_mode,
    load_system_config,
    save_system_config,
)
from common.urban_vpn import (
    DIRECT_CODE, FREE_URBAN_VPN_LOCATIONS, URBAN_VPN_LABELS,
    normalize_vpn_order,
)
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


class VpnOrderListWidget(QListWidget):
    """Three-column priority grid whose drag/drop operation swaps two entries."""

    items_swapped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Snap)
        # Keep the viewport width stable.  With an automatic vertical scrollbar,
        # five items can alternate between a 3x2 and a 2x3 layout: one layout
        # hides the scrollbar, the wider viewport then changes the column width,
        # and that layout needs the scrollbar again.  Qt repeatedly relayouts and
        # repaints the list, which looks like rapid flashing.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSpacing(2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        available_width = max(540, self.viewport().width() - 6)
        grid_size = QSize(available_width // 3, 32)
        if self.gridSize() != grid_size:
            self.setGridSize(grid_size)

    def refresh_numbering(self):
        for row in range(self.count()):
            item = self.item(row)
            code = item.data(Qt.ItemDataRole.UserRole)
            label = (
                URBAN_VPN_LABELS.get(code, str(code))
                if code == DIRECT_CODE
                else f"{URBAN_VPN_LABELS.get(code, str(code))} ({code})"
            )
            item.setText(f"{row + 1}.  {label}")
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )

    def swap_rows(self, source_row, target_row):
        if (
            source_row == target_row
            or source_row < 0
            or target_row < 0
            or source_row >= self.count()
            or target_row >= self.count()
        ):
            return False
        source_item = self.item(source_row)
        target_item = self.item(target_row)
        source_code = source_item.data(Qt.ItemDataRole.UserRole)
        target_code = target_item.data(Qt.ItemDataRole.UserRole)
        source_item.setData(Qt.ItemDataRole.UserRole, target_code)
        target_item.setData(Qt.ItemDataRole.UserRole, source_code)
        self.refresh_numbering()
        self.setCurrentRow(target_row)
        self.items_swapped.emit()
        return True

    def dropEvent(self, event):
        selected = self.selectedIndexes()
        source_row = selected[0].row() if selected else -1
        target_row = self.indexAt(event.position().toPoint()).row()
        if self.swap_rows(source_row, target_row):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

class OpenBrowserWorker(QThread):
    browser_finished = pyqtSignal(int, str) # acc_id, cookies_json
    error = pyqtSignal(str)

    def __init__(self, acc_id, email, cookies_json, chrome_profile="_tool_profile_"):
        super().__init__()
        self.acc_id = acc_id
        self.email = email
        self.cookies_json = cookies_json
        self.chrome_profile = chrome_profile

    def run(self):
        try:
            from playwright.sync_api import sync_playwright
            import json
            import time
            from core.browser_manager import launch_chrome_and_connect
            
            with sync_playwright() as p:
                context = launch_chrome_and_connect(p, self.email, self.chrome_profile)
                
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

    def __init__(self, email, chrome_profile="_tool_profile_"):
        super().__init__()
        self.email = email
        self.chrome_profile = chrome_profile

    def run(self):
        try:
            cookies_json, account_type, credits = login_and_save_cookies(
                None, self.email, self.chrome_profile
            )
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

        chrome_options_layout.addWidget(QLabel("Chế độ Chrome khi chạy ảnh/video:"))
        self.combo_chrome_run_mode = QComboBox()
        self.combo_chrome_run_mode.addItem("Hiện Chrome", CHROME_RUN_MODE_VISIBLE)
        self.combo_chrome_run_mode.addItem(
            "Thu nhỏ và đưa ra ngoài màn hình", CHROME_RUN_MODE_MINIMIZED
        )
        self.combo_chrome_run_mode.addItem("Ẩn hoàn toàn (Headless)", CHROME_RUN_MODE_HEADLESS)
        self.combo_chrome_run_mode.setMinimumWidth(270)
        self.combo_chrome_run_mode.setToolTip(
            "Hiện Chrome; chạy có cửa sổ nhưng thu nhỏ; hoặc chạy headless hoàn toàn."
        )
        self.combo_chrome_run_mode.currentIndexChanged.connect(self.save_system_settings)
        chrome_options_layout.addWidget(self.combo_chrome_run_mode)
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

        vpn_group = QGroupBox("Thứ tự Urban VPN Proxy (chỉ tài khoản ULTRA)")
        vpn_group.setObjectName("vpn_order_group")
        vpn_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        vpn_group.setMaximumHeight(160)
        vpn_layout = QVBoxLayout(vpn_group)
        vpn_layout.setContentsMargins(8, 8, 8, 8)
        vpn_layout.setSpacing(5)

        vpn_controls = QHBoxLayout()
        vpn_controls.setSpacing(6)
        vpn_controls.addWidget(QLabel("Thêm vị trí:"))
        self.vpn_country_combo = QComboBox()
        self.vpn_country_combo.setMinimumWidth(190)
        for code, name in FREE_URBAN_VPN_LOCATIONS:
            self.vpn_country_combo.addItem(f"{name} ({code})", code)
        vpn_controls.addWidget(self.vpn_country_combo)
        self.btn_add_vpn_country = QPushButton("Thêm")
        self.btn_add_vpn_country.clicked.connect(self.add_vpn_country)
        vpn_controls.addWidget(self.btn_add_vpn_country)
        self.btn_remove_vpn_country = QPushButton("Bỏ mục đã chọn")
        self.btn_remove_vpn_country.clicked.connect(self.remove_vpn_country)
        vpn_controls.addWidget(self.btn_remove_vpn_country)
        vpn_controls.addStretch()
        vpn_controls.addWidget(QLabel("Kéo các dòng để đổi thứ tự ưu tiên"))
        vpn_layout.addLayout(vpn_controls)

        self.vpn_order_list = VpnOrderListWidget()
        self.vpn_order_list.setFixedHeight(98)
        self.vpn_order_list.items_swapped.connect(self.save_vpn_order)
        self.vpn_order_list.setToolTip(
            "Kéo để đổi thứ tự IP dùng ở lần đầu và các lần thử lại. "
            "IP gốc luôn có trong danh sách."
        )
        vpn_layout.addWidget(self.vpn_order_list)
        layout.addWidget(vpn_group)
        
        # Table panel
        self.table = ReorderableTableWidget(0, 10)
        self.table.row_moved_callback = self.on_row_moved
        self.table.setHorizontalHeaderLabels([
            "ID", "Email", "Loại", "Ảnh", "Video", "Gemini", "VPN",
            "Profile Chrome", "Trạng thái", "Hành động",
        ])
        
        # Cấu hình chiều rộng các cột để tránh font wrapped và tối ưu hóa diện tích hiển thị
        self.table.setColumnWidth(0, 40)   # ID
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch) # Email (rộng hơn và tự co giãn)
        self.table.setColumnWidth(2, 130)  # Loại (FREE, PRO, ULTRA và số dư credit)
        self.table.setColumnWidth(3, 50)   # Ảnh
        self.table.setColumnWidth(4, 50)   # Video
        self.table.setColumnWidth(5, 65)   # Gemini
        self.table.setColumnWidth(6, 55)   # VPN
        self.table.setColumnWidth(7, 180)  # Profile Chrome
        self.table.setColumnWidth(8, 140)  # Trạng thái (để hiển thị icon đẹp mắt)
        self.table.setColumnWidth(9, 210)  # Hành động (Mở, Làm mới, Xóa)

        bulk_toggle_layout = QHBoxLayout()
        bulk_toggle_layout.setSpacing(6)
        bulk_toggle_layout.addWidget(QLabel("Chọn nhanh:"))
        for label, feature in (
            ("Ảnh", "is_image"),
            ("Video", "is_video"),
            ("Gemini", "is_gemini"),
            ("VPN", "use_vpn"),
        ):
            bulk_toggle_layout.addWidget(QLabel(f"{label}:"))
            btn_select_all = QPushButton("Chọn tất cả")
            btn_clear_all = QPushButton("Bỏ chọn tất cả")
            btn_select_all.setToolTip(f"Bật {label} cho tất cả tài khoản")
            btn_clear_all.setToolTip(f"Tắt {label} cho tất cả tài khoản")
            btn_select_all.clicked.connect(
                lambda _, key=feature: self.update_all_feature_toggles(key, True)
            )
            btn_clear_all.clicked.connect(
                lambda _, key=feature: self.update_all_feature_toggles(key, False)
            )
            bulk_toggle_layout.addWidget(btn_select_all)
            bulk_toggle_layout.addWidget(btn_clear_all)
            if feature != "use_vpn":
                bulk_toggle_layout.addSpacing(10)
        bulk_toggle_layout.addStretch()
        layout.addLayout(bulk_toggle_layout)

        layout.addWidget(self.table)
        self.load_system_settings()

    def load_system_settings(self):
        config = load_system_config()
        self.combo_chrome_run_mode.blockSignals(True)
        mode_index = self.combo_chrome_run_mode.findData(get_chrome_run_mode(config))
        self.combo_chrome_run_mode.setCurrentIndex(max(0, mode_index))
        self.combo_chrome_run_mode.blockSignals(False)
        self.set_vpn_order(config.get("urban_vpn_order"))

    def set_vpn_order(self, order):
        self.vpn_order_list.blockSignals(True)
        self.vpn_order_list.clear()
        for code in normalize_vpn_order(order):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, code)
            self.vpn_order_list.addItem(item)
        self.vpn_order_list.refresh_numbering()
        self.vpn_order_list.blockSignals(False)

    def current_vpn_order(self):
        return normalize_vpn_order([
            self.vpn_order_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.vpn_order_list.count())
        ])

    def save_vpn_order(self, *args):
        save_system_config({"urban_vpn_order": self.current_vpn_order()})

    def add_vpn_country(self):
        code = self.vpn_country_combo.currentData()
        if not code or code in self.current_vpn_order():
            return
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, code)
        self.vpn_order_list.addItem(item)
        self.vpn_order_list.refresh_numbering()
        self.save_vpn_order()

    def remove_vpn_country(self):
        row = self.vpn_order_list.currentRow()
        if row < 0:
            return
        item = self.vpn_order_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == DIRECT_CODE:
            QMessageBox.information(self, "IP gốc", "IP gốc luôn phải có trong danh sách.")
            return
        self.vpn_order_list.takeItem(row)
        self.vpn_order_list.refresh_numbering()
        self.save_vpn_order()

    def save_system_settings(self):
        try:
            chrome_run_mode = self.combo_chrome_run_mode.currentData()
            save_system_config({
                "chrome_run_mode": chrome_run_mode,
                # Keep the legacy value for older builds that may read this config.
                "show_chrome_when_running": chrome_run_mode == CHROME_RUN_MODE_VISIBLE,
            })
            logging.info(
                "[Settings] Chế độ Chrome khi chạy tự động: %s", chrome_run_mode
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
        self.selector_acc_combo.addItem("Mặc định (không dùng profile)", "_none_")
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

            # Urban VPN is intentionally available only for ULTRA accounts.
            chk_vpn_container = QWidget()
            chk_vpn_layout = QHBoxLayout(chk_vpn_container)
            chk_vpn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_vpn_layout.setContentsMargins(0, 0, 0, 0)
            chk_vpn = QCheckBox()
            is_ultra = "ULTRA" in (acc.account_type or "").upper()
            chk_vpn.setEnabled(is_ultra)
            chk_vpn.setChecked(bool(acc.use_vpn) and is_ultra)
            chk_vpn.setToolTip(
                "Dùng danh sách Urban VPN phía trên" if is_ultra
                else "Urban VPN chỉ áp dụng cho tài khoản ULTRA"
            )
            chk_vpn.stateChanged.connect(
                lambda state, a_id=acc.id: self.update_feature_toggle(
                    a_id, "use_vpn", state == Qt.CheckState.Checked.value
                )
            )
            chk_vpn_layout.addWidget(chk_vpn)
            self.table.setCellWidget(i, 6, chk_vpn_container)
            
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
            
            # Hành động (Mở, Làm mới, Xóa)
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
            action_layout.addWidget(btn_delete)
            self.table.setCellWidget(i, 9, action_widget)
        db.close()

    def start_login(self):
        self.refreshing_account_id = None
        email = self.email_input.text().strip()
        if not email:
            QMessageBox.warning(self, "Lỗi", "Vui lòng nhập Email Google!")
            return
            
        chrome_profile = self.combo_login_profile.currentData() or "_tool_profile_"
        logging.info(
            "[Account] Khởi động đăng nhập Chrome cho email=%s; profile=%s",
            email, chrome_profile,
        )
        self.btn_login.setEnabled(False)
        self.btn_login.setText("Đang mở trình duyệt...")
        
        self.worker = LoginWorker(email, chrome_profile)
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
                if "ULTRA" not in (account_type or "").upper():
                    acc.use_vpn = False
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
            
        cookies_json = acc.cookies_json
        chrome_profile = acc.chrome_profile or "_tool_profile_"
        db.close()
        
        logging.info(f"[Account] Khởi chạy Chrome xem trực tiếp cho ID: {acc_id} sử dụng profile {chrome_profile}")
        
        worker = OpenBrowserWorker(acc_id, acc.email, cookies_json, chrome_profile)
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
            elif feature == "use_vpn":
                is_ultra = "ULTRA" in (acc.account_type or "").upper()
                acc.use_vpn = bool(is_checked and is_ultra)
            db.commit()
        db.close()

    def update_all_feature_toggles(self, feature, is_checked):
        feature_columns = {
            "is_image": 3,
            "is_video": 4,
            "is_gemini": 5,
            "use_vpn": 6,
        }
        if feature not in feature_columns:
            logging.warning(f"[Account] Từ chối cập nhật hàng loạt feature không hợp lệ: {feature}")
            return

        logging.info(
            f"[Account] Cập nhật {feature}={is_checked} cho tất cả tài khoản."
        )
        db = SessionLocal()
        try:
            query = db.query(Account)
            if feature == "use_vpn" and is_checked:
                query = query.filter(Account.account_type.ilike("%ULTRA%"))
            query.update({getattr(Account, feature): is_checked}, synchronize_session=False)
            db.commit()
        finally:
            db.close()

        column = feature_columns[feature]
        for row in range(self.table.rowCount()):
            container = self.table.cellWidget(row, column)
            checkbox = container.findChild(QCheckBox) if container else None
            if checkbox is None:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(is_checked and checkbox.isEnabled()))
            checkbox.blockSignals(False)

    def refresh_account(self, acc_id):
        db = SessionLocal()
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if not acc:
            db.close()
            return
            
        email = acc.email
        chrome_profile = acc.chrome_profile or "_tool_profile_"
        db.close()
        
        logging.info(f"[Account] Khởi động làm mới cookie tài khoản ID {acc_id} ({email}) với profile {chrome_profile}")
        self.refreshing_account_id = acc_id
        self.btn_login.setEnabled(False)
        self.btn_login.setText("Đang làm mới cookie...")
        
        self.worker = LoginWorker(email, chrome_profile)
        self.worker.login_finished.connect(self.on_login_finished)
        self.worker.error.connect(self.on_login_error)
        self.worker.start()

    def open_codegen_tool(self):
        acc_val = self.selector_acc_combo.currentData()
        url = self.selector_url_input.text().strip() or "https://labs.google/fx/tools/image-fx"
        
        email = ""
        chrome_profile = "_tool_profile_"
        if acc_val and acc_val != "_none_":
            acc_id = acc_val
            db = SessionLocal()
            acc = db.query(Account).filter(Account.id == acc_id).first()
            if acc:
                email = acc.email or ""
                chrome_profile = acc.chrome_profile or "_tool_profile_"
            db.close()
            
        import subprocess
        import sys
        import os
        from data.database import BASE_DIR
        
        script_path = os.path.join(BASE_DIR, "src", "core", "open_inspector.py")
        
        cmd = [sys.executable, script_path, "--email", email, "--profile", chrome_profile, "--url", url]
            
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
