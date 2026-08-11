import os
import logging
from PyQt6.QtCore import QRunnable, QObject, pyqtSignal, Qt
from PyQt6.QtGui import QImage, QImageReader

class ImageLoaderSignals(QObject):
    loaded = pyqtSignal(str, QImage)
    failed = pyqtSignal(str)

class ImageLoaderRunnable(QRunnable):
    def __init__(self, path, width=64, height=46):
        super().__init__()
        self.path = path
        self.width = width
        self.height = height
        self.signals = ImageLoaderSignals()

    def run(self):
        try:
            if not self.path or not os.path.exists(self.path):
                self.signals.failed.emit(self.path or "")
                return
            
            # Use QImageReader to scale the image while reading.
            # This is extremely fast and avoids loading large high-resolution images into memory.
            reader = QImageReader(self.path)
            if reader.canRead():
                orig_size = reader.size()
                if orig_size.isValid():
                    scaled_size = orig_size.scaled(self.width, self.height, Qt.AspectRatioMode.KeepAspectRatio)
                    reader.setScaledSize(scaled_size)
                image = reader.read()
                if not image.isNull():
                    self.signals.loaded.emit(self.path, image)
                    return
            
            self.signals.failed.emit(self.path)
        except Exception as e:
            logging.error(f"[ImageLoader] Error loading {self.path}: {e}")
            self.signals.failed.emit(self.path)
