import sys
from PyQt6.QtWidgets import QApplication

from common.logger import setup_logger
# Khởi tạo logger cho phiên làm việc mới
setup_logger()

from ui.main_window import MainWindow
from ui.theme import theme_stylesheet
from data.database import init_db
from core.system_config import load_system_config

def main():
    app = QApplication(sys.argv)
    
    # Initialize database
    init_db()
    
    # Apply the saved theme before constructing widgets to avoid a color flash.
    app.setStyleSheet(theme_stylesheet(load_system_config().get("theme")))
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
