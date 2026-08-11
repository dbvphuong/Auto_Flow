import os
import logging
import threading
import time
from datetime import datetime

class FlushFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

def clean_old_logs(logs_dir, days=7):
    """
    Xóa các file log cũ hơn `days` ngày trong thư mục `logs_dir`.
    Chạy trong một thread riêng biệt để không ảnh hưởng đến thời gian khởi động của ứng dụng.
    """
    try:
        if not os.path.exists(logs_dir):
            return
        
        now = time.time()
        cutoff = now - (days * 24 * 60 * 60)
        
        deleted_count = 0
        for filename in os.listdir(logs_dir):
            if filename.startswith("session_") and filename.endswith(".log"):
                file_path = os.path.join(logs_dir, filename)
                try:
                    if os.path.isfile(file_path) and os.path.getmtime(file_path) < cutoff:
                        os.remove(file_path)
                        deleted_count += 1
                except Exception as e:
                    print(f"Lỗi khi xóa file log {filename}: {e}")
                    
        if deleted_count > 0:
            logging.info(f"Đã tự động dọn dẹp {deleted_count} file log cũ (> {days} ngày).")
    except Exception as e:
        print(f"Lỗi trong quá trình dọn dẹp log: {e}")

def setup_logger():
    # Thư mục gốc dự án (cha của src)
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(src_dir)
    logs_dir = os.path.join(project_root, "logs")
    
    # Tạo thư mục logs nếu chưa tồn tại
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
        
    # Tạo tên file log theo phiên chạy YYYYMMDD_HHMMSS
    session_time = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = os.path.join(logs_dir, f"session_{session_time}.log")
    
    # Cấu hình logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s",
        handlers=[
            FlushFileHandler(log_file_path, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )
    
    logging.info("=== BẮT ĐẦU PHIÊN CHẠY MỚI ===")
    logging.info(f"Đường dẫn file log: {log_file_path}")
    
    # Khởi động thread dọn dẹp log cũ
    threading.Thread(target=clean_old_logs, args=(logs_dir, 7), daemon=True).start()
    
    return log_file_path
