# KẾ HOẠCH PHÁT TRIỂN (PLAN)

## Context
**Mục tiêu:**
Tạo ảnh, video tự động (Playwright + PyQt6) với đầu vào là các câu prompt, mỗi câu 1 dòng.

**Input:**
- Danh sách các câu prompt (từ file TXT, Excel hoặc nhập trực tiếp mỗi dòng 1 câu).
- Các tuỳ chỉnh: Model, Chất lượng, Tỷ lệ ảnh, Số luồng, Thư mục lưu...
- Tài khoản (Cookie, Proxy).

**Output:**
- File ảnh/video được tạo thành công và tải về thư mục lưu được chỉ định.
- Tiến độ và kết quả (Thành công/Lỗi) được cập nhật trên giao diện quản lý.

## Tasks
Dưới đây là danh sách các đầu việc. Trạng thái các task được đánh dấu bằng: `PENDING`, `IN_PROGRESS`, `BLOCKED`, `VERIFY`, `DONE`.

### 1. Giao diện (UI) - Tab "Flow Ảnh" (Flow Image)
- [x] **DONE**: Nút `Nhập tệp (TXT, Excel)` - **Mục đích:** Cho phép người dùng chọn và đọc danh sách các câu lệnh (prompt) từ file văn bản (.txt) hoặc bảng tính (.xlsx, .csv) rồi tự động đưa vào ô TextBox để chuẩn bị xử lý.
- [x] **REMOVED**: Nút `Chế độ lưu` - **Mục đích:** Chuyển đổi trạng thái để quyết định xem tool có tạo riêng từng thư mục con cho mỗi ảnh/video hay lưu tất cả vào chung một thư mục. (Đã gỡ bỏ theo yêu cầu mới)
- [x] **DONE**: Nút `Đóng Chrome` (thay thế nút `Thêm ảnh` cũ) - **Mục đích:** Đóng nhanh tất cả các trình duyệt Chrome do bot mở tự động trong phiên chạy này, kèm xác nhận chống bấm nhầm khi đang chạy dở.
- [x] **DONE**: Nút `Xóa` (btn_tb_delete) - **Mục đích:** Xóa bỏ một hoặc nhiều dòng công việc đang được chọn (tích chọn) khỏi bảng tiến độ.
- [x] **DONE**: Nút `Xóa hết` (btn_tb_delete_all) - **Mục đích:** Xóa sạch toàn bộ danh sách công việc hiện có trong bảng tiến độ.
- [ ] **PENDING**: Nút `Xóa Cache` (btn_tb_clear_cache) - **Mục đích:** Dọn dẹp bộ nhớ tạm, profile rác của trình duyệt để giải phóng ổ cứng và hạn chế lỗi vặt.
- [x] **DONE**: Nút `Chạy lại lỗi` (btn_tb_rerun_err) - **Mục đích:** Tự động lọc ra những công việc bị lỗi (có status là ERROR) ở lần chạy trước và đưa chúng vào hàng chờ để chạy lại.
- [x] **DONE**: Nút `Chạy mục chọn` (btn_tb_run_sel) - **Mục đích:** Chỉ thực thi quá trình tạo ảnh/video cho những công việc mà người dùng đang chọn thủ công trên bảng.
- [ ] **PENDING**: Nút `Mở phiên cũ` (btn_tb_open_old) - **Mục đích:** Tải lại danh sách công việc từ các phiên làm việc trước đó đã được lưu trong Database.
- [ ] **PENDING**: Nút `Quản lý hàng chờ` - **Mục đích:** Hiển thị số lượng công việc đang chờ. Khi bấm vào sẽ mở một cửa sổ (modal) để người dùng xem chi tiết, thay đổi thứ tự ưu tiên hoặc xóa bớt.
- [x] **DONE**: Nút `Chạy ngay` - **Mục đích:** Bắt đầu đẩy danh sách công việc vào hàng chờ và khởi động luồng auto Playwright ngay lập tức.
- [x] **DONE**: Nút `Tạm dừng` - **Mục đích:** Tạm ngưng tiến trình auto đang chạy mà không làm mất trạng thái hay dữ liệu đang xử lý dở.
- [x] **DONE**: Nút `Dừng task` - **Mục đích:** Hủy bỏ ngay lập tức tiến trình hiện tại và dọn sạch các công việc chưa chạy khỏi hàng chờ.
- [x] **DONE**: Khởi tạo layout, các Input cấu hình: Model (cập nhật Nano Banana Pro, Nano Banana 2, Imagen 4 (Leaving 6/16)), Chất lượng, Tỷ lệ ảnh, Số luồng, Độ trễ, Thư mục lưu...
- [x] **DONE**: Hiển thị Bảng quản lý tiến độ task (Tên Ảnh, Ảnh tham chiếu, Prompt, Kết quả, Tiến độ), kèm Checkbox "Tích tất cả" ở góc trái.
- [x] **DONE**: Tự động đồng bộ TextBox prompt sang Bảng tiến độ (tự động thêm/sửa/xóa các dòng tương ứng khi người dùng nhập văn bản, có debounce 300ms).
- [x] **DONE**: Xác thực cấu hình trước khi chạy (kiểm tra thư mục lưu, danh sách task PENDING, và tài khoản hoạt động khi nhấn "Chạy ngay").
- [x] **DONE**: Code logic cơ bản để map thông tin từ UI sang hàng chờ (Queue).

### 2. Giao diện (UI) - Tab "Cài đặt hệ thống" (Accounts)
- [x] **DONE**: Nút `Thêm tài khoản` (`LoginWorker`) - **Mục đích:** Mở một trình duyệt Chrome sạch (hỗ trợ Proxy) để người dùng tự đăng nhập thủ công với thời gian chờ lên đến 5 phút, sau đó tự động lấy cookies lưu vào DB để các phiên chạy sau không cần đăng nhập lại.
- [x] **DONE**: Nút `Load` - **Mục đích:** Tải lại danh sách các tài khoản hiện có từ DB lên bảng quản lý.
- [x] **DONE**: Nút `Sửa Proxy` - **Mục đích:** Gán hoặc cập nhật thông tin Proxy cho một tài khoản đang được chọn, kèm tùy chọn bật/tắt (ON/OFF) để quyết định chạy qua proxy hay mạng gốc.
- [x] **DONE**: Tự động kiểm tra Loại tài khoản - **Mục đích:** Khi đăng nhập hoặc làm mới, hệ thống tự động kiểm tra và cập nhật cột Loại (FREE, PRO, ULTRA). Cột này được thiết lập chỉ đọc (disabled), không cho phép chọn hay sửa thủ công.
- [x] **DONE**: Nút `Toggle Ảnh/Video` - **Mục đích:** Phân quyền giới hạn để tài khoản đó chỉ được phép chạy sinh Ảnh, Video, hoặc cả hai.
- [x] **DONE**: Nút `Xóa` - **Mục đích:** Xóa vĩnh viễn thông tin và cookies của tài khoản khỏi DB.
- [x] **DONE**: Nút `Refresh Account` - **Mục đích:** Làm mới cookie tài khoản qua Chrome, cập nhật Loại tài khoản, và thay đổi Trạng thái hoạt động hiển thị rõ ràng bằng biểu tượng (✅ HOẠT ĐỘNG / ❌ KHÔNG HOẠT ĐỘNG).
- [x] **DONE**: Nút `Mở` Chrome - **Mục đích:** Khởi chạy một cửa sổ Chrome trực quan (non-headless) với Proxy và Session cũ của tài khoản để xem/kiểm tra trực tiếp, đồng thời tự động cập nhật lại cookies mới khi đóng trình duyệt.
- [x] **DONE**: Cột chọn Profile Chrome - **Mục đích:** Thêm dropdown cho từng tài khoản để chọn sử dụng Profile riêng của Tool hoặc các Profile Google Chrome thực tế có sẵn trên máy tính người dùng. Lựa chọn được lưu vào DB và tự động áp dụng khi Đăng nhập, Làm mới, Mở Chrome hoặc Chạy task tự động.
- [x] **DONE**: Công cụ hỗ trợ lấy Selector - **Mục đích:** Tích hợp panel Playwright Inspector/Codegen, cho phép chọn tài khoản (để kế thừa Profile Chrome & Proxy) và nhập URL để mở trình duyệt kiểm tra, di chuột tự động sinh Selector CSS/XPath chuẩn phục vụ cấu hình kịch bản.
> [!WARNING]
> **Lưu ý về Proxy**: Hiện tại thiết lập bật Proxy (ON) trên môi trường Playwright gặp sự cố kết nối ở một số hệ thống (sẽ được kiểm tra và sửa sau). Khuyến nghị người dùng thiết lập trạng thái Proxy sang **OFF** (sử dụng mạng gốc) để chạy ổn định.

### 3. Automation (Core/Playwright)
- [x] **DONE**: Kịch bản tự động `run_image_fx` (Flow) - Đã tích hợp đầy đủ kịch bản Google Labs Flow và các cấu hình động từ UI (Model, Quality, Ratio).
- [x] **VERIFY**: Kịch bản tự động `run_gemini` - Chỉ đổi model khi chưa phải 3.1 Pro; gửi và đợi Master Prompt, gửi và đợi cốt truyện, sau đó gửi `1` đến khi gặp marker Done hoặc hết giới hạn.
- [x] **VERIFY**: Mỗi lần gửi `1` có timeout tối đa 5 phút; chỉ lưu nội dung từ phản hồi cốt truyện trở đi, không lưu phản hồi thiết lập Master Prompt.
- [x] **DONE**: Kịch bản tự động "Flow Veo3" / Flow Video - Đã xây dựng hoàn chỉnh kịch bản tự động tạo video (Playwright) trên Google Labs Flow.
- [x] **DONE**: Xử lý tải ảnh (Download) và lưu vào `Thư mục lưu` mà người dùng đã chỉ định trên UI.
- [x] **DONE**: Quản lý đa luồng (`AutomationWorker`) với hàng chờ và giới hạn số luồng đồng thời, random delay.
- [x] **DONE**: Quản lý profile trình duyệt (`browser_manager`) với Proxy và Cookies.

### 4. Cơ sở dữ liệu (Database)
- [x] **DONE**: Models (Account, Task) và kết nối DB SQLite.
- [x] **DONE**: Thêm cột `task_type` vào bảng `tasks` để phân biệt công việc giữa tab Ảnh và tab Video.
- [ ] **PENDING**: Thêm các bảng/trường mới nếu cần thiết khi bổ sung tính năng (ví dụ: lưu cấu hình tool, lưu history phiên).

### 5. Giao diện & Xử lý - Tab "Flow Video" (Flow Video)
- [x] **DONE**: Khởi tạo tab Flow Video mô phỏng tương tự cấu trúc tab Flow Ảnh nhưng được tùy biến riêng cho Video.
- [x] **DONE**: Cấu hình Tỷ lệ video chỉ có 2 option `16:9 Ngang` và `9:16 Dọc`.
- [x] **DONE**: Tích hợp 4 model tạo video mới kèm theo hiển thị số lượng credit cụ thể: Veo 3.1 - Lite [10 Credit], Omni Flash 10s [15 Credit], Veo3.1 - Fast [20 Credit], Veo3.1 - Quality [100 Credit].
- [x] **DONE**: Phân vùng độc lập DB query và file cấu hình (`config_video.json`) không để lẫn lộn với tab Ảnh.
- [x] **DONE**: Tích hợp kịch bản Playwright tự động hóa tạo video trên Google Labs Flow.

### 6. Giao diện & Xử lý - Tab "Gemini"
- [x] **VERIFY**: Tab Gemini nằm giữa Flow Video và Cài đặt hệ thống, đồng bộ màu sắc/layout hiện tại.
- [x] **VERIFY**: Master Prompt và cốt truyện hỗ trợ nhập text hoặc nạp từ một file; bỏ tính năng thêm batch thủ công.
- [x] **VERIFY**: Chọn nhiều quốc gia bằng checkbox và tạo một batch/file TXT tương ứng cho mỗi quốc gia.
- [x] **VERIFY**: Danh sách Gemini gồm thêm Brazil, Nga và A_Rap_Xe_Ut.
- [x] **VERIFY**: Log chẩn đoán chi tiết cho UI queue, round-robin account, worker Chrome/Profile và từng bước web Gemini/selector/timeout/marker.
- [x] **VERIFY**: Chia cửa sổ Chrome Gemini thành các cột bằng nhau theo số phiên chạy đồng thời, giữ slot ổn định khi chuyển batch.
- [x] **VERIFY**: Gemini dùng profile bền vững và giữ nguyên cookie, lịch sử, session khi đóng; không áp dụng cơ chế xóa profile theo-task của Flow Ảnh/Video.
- [x] **VERIFY**: Cấu hình số lần gửi `1` tối đa và marker hoàn thành mặc định `[[DONE]]`; không có marker khi hết giới hạn sẽ FAILED.
- [x] **VERIFY**: Thay `[Ngôn ngữ]` trong Master Prompt bằng tên ngôn ngữ tương ứng của batch, không phân biệt hoa/thường.
- [x] **VERIFY**: Giữ mã quốc gia nội bộ ổn định nhưng dùng tên có dấu cho prompt, giao diện và file kết quả (ví dụ `Duc` → `Đức.txt`).
- [x] **VERIFY**: Queue đa luồng với round-robin toàn bộ account đang bật, không dùng trùng Chrome Profile đồng thời.
- [x] **VERIFY**: Hiển thị Batch, Account, Part hiện tại/Tổng Part và PENDING/RUNNING/SUCCESS/FAILED.
- [x] **VERIFY**: Chạy ngay, Tạm dừng/Tiếp tục, Dừng và Chạy lại lỗi.
