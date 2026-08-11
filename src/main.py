import sys
from PyQt6.QtWidgets import QApplication
import qdarktheme

from common.logger import setup_logger
# Khởi tạo logger cho phiên làm việc mới
setup_logger()

from ui.main_window import MainWindow
from data.database import init_db

def main():
    app = QApplication(sys.argv)
    
    # Initialize database
    init_db()
    
    # Set dark theme
    app.setStyleSheet(qdarktheme.load_stylesheet("dark"))
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
