# QUY TẮC DỰ ÁN (RULES)

> [!IMPORTANT]
> **QUY TẮC BẮT BUỘC:** Mọi thay đổi trong mã nguồn, thiết kế hệ thống và cấu trúc thư mục đều **KIÊN QUYẾT** phải tuân thủ nghiêm ngặt các quy tắc được định nghĩa trong tài liệu này. Việc vi phạm hoặc tự ý thay đổi cấu trúc mà không cập nhật tài liệu sẽ không được chấp nhận.

Dự án này sử dụng Python (PyQt6 cho UI và Playwright cho Automation). Dưới đây là các quy tắc về mã nguồn và cấu trúc thư mục.

## 1. Cấu trúc thư mục (Folder Structure)

> [!WARNING]
> **QUY TẮC CẬP NHẬT:** Khi tạo bất kỳ file hoặc thư mục mới nào trong dự án, lập trình viên **BẮT BUỘC** phải cập nhật ngay lập tức cấu trúc thư mục này vào sơ đồ bên dưới.

```text
E:\GG\
├── data/                  # Nơi lưu trữ database và tài nguyên cục bộ
│   ├── data.db            # Database SQLite
│   └── config.json        # File cấu hình tự động lưu của tool
├── logs/                  # Thư mục chứa các file log của từng phiên chạy (tự sinh)
├── src/                   # Source code chính của tool
│   ├── core/              # Xử lý logic nghiệp vụ, đa luồng, tự động hóa
│   │   ├── automations/   # Các kịch bản Playwright (image_fx.py, gemini.py, video_fx.py)
│   │   ├── browser_manager.py # Quản lý khởi tạo trình duyệt, cookies, proxy
│   │   └── workers.py     # Lớp QThread quản lý các luồng chạy ngầm
│   ├── data/              # Quản lý Database Models và Session
│   │   ├── database.py    # Cấu hình SQLAlchemy
│   │   └── models.py      # Định nghĩa các bảng (Account, Task, ImageSession, VideoSession, GeminiBatch)
│   ├── ui/                # Giao diện người dùng (PyQt6)
│   │   ├── components/    # Các widget UI dùng chung (buttons, dialogs, ...)
│   │   ├── views/         # Các tab chính (flow_image.py, flow_video.py, gemini.py, accounts.py)
│   │   ├── styles/        # CSS/QSS styling cho UI
│   │   └── main_window.py # Khởi tạo cửa sổ chính của ứng dụng
│   ├── common/            # Các tiện ích và module dùng chung
│   │   ├── gemini_languages.py # Mã file và tên ngôn ngữ có dấu cho Gemini
│   │   └── logger.py      # Thiết lập ghi log hệ thống và phiên chạy
│   └── main.py            # Entry point của ứng dụng
├── PLAN.md                # Kế hoạch phát triển và quản lý các đầu việc
├── RULES.md               # Quy tắc dự án (File này)
├── run.bat                # File launcher khởi chạy nhanh tool trên Windows
└── requirements.txt       # Các thư viện phụ thuộc (PyQt6, playwright, sqlalchemy, ...)
```

## 2. Quy tắc Coding (Coding Conventions)

### 2.1. Ngôn ngữ & Framework
- Ngôn ngữ: Python 3.x
- UI Framework: PyQt6
- Automation Framework: Playwright (Sync API)
- Database: SQLAlchemy + SQLite

### 2.2. Quy tắc UI (PyQt6)
- Tách biệt UI và Logic: Không viết code automation (Playwright) trực tiếp trong các file UI (views). UI chỉ nên gọi các `Worker` (`QThread`) để thực thi tiến trình nặng.
- Xử lý bất đồng bộ: Các thao tác liên quan tới network, browser phải được chạy trong `QThread` (như `AutomationWorker` hoặc `LoginWorker`) để tránh làm đơ giao diện chính (GUI Thread).
- Đặt tên biến UI: 
  - Button: `btn_` (VD: `btn_run`, `btn_pause`)
  - Label: `lbl_`
  - Input/LineEdit: `line_` hoặc `input_`
  - ComboBox: `combo_`
  - CheckBox: `chk_`
  - Table: `table_`
- Responsive Layout: Luôn sử dụng Layouts (`QHBoxLayout`, `QVBoxLayout`, `QGridLayout`) để giao diện tự động co giãn.

### 2.3. Quy tắc Automation (Playwright)
- Headless Mode: Hiện tại đa số dùng `headless=False` (hoặc tùy biến) để vượt qua detection của Google, lưu ý có thể set biến môi trường hoặc UI setting để toggle headless.
- Anti-Detection: Cần kèm theo các cờ `--disable-blink-features=AutomationControlled` và `--disable-http2`.
- Timeout: Mạng dùng Proxy thường chậm, nên cài đặt `timeout` cao (30s - 60s) cho các thao tác `wait_for_selector` hoặc `goto`.
- Error Handling: Các kịch bản auto luôn phải được bọc trong `try...except` và bắn signal `error` về UI thay vì crash. Luôn nhớ `browser.close()` trong block `finally`.
- **Xử lý Selectors:** Khi xây dựng kịch bản tự động hóa, nếu gặp bất kỳ selector nào không chắc chắn hoặc không tìm thấy, **tuyệt đối không được đoán bừa**. Hãy dừng lại và hỏi người dùng để được cung cấp HTML hoặc selector chính xác.

### 2.4. Quy định làm việc với Database
- Mở/Đóng Session: Mỗi khi thao tác với DB trong một thread, cần tạo một phiên `SessionLocal()` và đảm bảo gọi `db.close()` ở block `finally` hoặc sau khi thao tác xong.
- Trạng thái Task: Sử dụng các chuỗi chuẩn để lưu trạng thái: `PENDING`, `RUNNING`, `COMPLETED`, `ERROR`, `STOPPED`.

### 2.5. Git & Quy trình làm việc
- Khi thực hiện một tính năng mới trong `PLAN.md`, đổi trạng thái từ `PENDING` -> `IN_PROGRESS`.
- Sau khi code xong và tự test (Playwright chạy ổn định, UI cập nhật đúng), đổi thành `VERIFY`.
- Sau khi được kiểm duyệt/hoạt động tốt trong thực tế, cập nhật thành `DONE`.

### 2.6. Quy tắc cấu trúc Code và các Thành phần chung (Common)
- **Chia nhóm thư mục chính:** Toàn bộ code trong `src/` phải được phân cấp rõ ràng theo các nhóm chức năng lớn: `core/`, `ui/`, `data/`, `common/`.
- **Module dùng chung (Common):** Không viết các hàm tiện ích, cấu hình hoặc các widget dùng chung rải rác ở nhiều nơi.
  - Các hàm xử lý chuỗi, đọc/ghi file chung, logger, parser cấu hình... phải được đưa vào thư mục `src/common/`.
  - Các widget giao diện dùng chung phải được đưa vào `src/ui/components/`.
