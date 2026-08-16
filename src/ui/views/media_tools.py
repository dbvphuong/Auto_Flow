import logging
import os
import re
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.system_config import load_system_config, save_system_config


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
TWO_K_SUFFIX_PATTERN = re.compile(r"_2k$", re.IGNORECASE)
UPSCALE_PILLOW = "pillow_lanczos"
UPSCALE_OPENCV = "opencv_lanczos4"
UPSCALE_METHODS = {
    "Pillow (Chậm)": UPSCALE_PILLOW,
    "OpenCV (Nhanh)": UPSCALE_OPENCV,
}


def is_2k_name(path):
    return bool(TWO_K_SUFFIX_PATTERN.search(Path(path).stem))


def two_k_path(path):
    path = Path(path)
    return path.with_name(f"{path.stem}_2K{path.suffix}")


def is_valid_upscaled_pair(source, destination):
    try:
        with Image.open(source) as original, Image.open(destination) as upscaled:
            return upscaled.size == (original.width * 2, original.height * 2)
    except (OSError, ValueError):
        return False


def scan_images(folder, recursive=False):
    root = Path(folder)
    iterator = root.rglob("*") if recursive else root.iterdir()
    return sorted(
        (
            path for path in iterator
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            and ".processing." not in path.name.lower()
            and "_backup_before_edit" not in path.parts
        ),
        key=lambda path: str(path).lower(),
    )


def _atomic_save_pillow(image, destination, source_info=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_info = source_info or {}
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{destination.stem}.processing.",
        suffix=destination.suffix,
        dir=destination.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    handle.close()
    try:
        suffix = destination.suffix.lower()
        save_kwargs = {}
        if source_info.get("icc_profile"):
            save_kwargs["icc_profile"] = source_info["icc_profile"]
        if source_info.get("exif"):
            save_kwargs["exif"] = source_info["exif"]
        if suffix in {".jpg", ".jpeg"}:
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            save_kwargs.update({"quality": 95, "subsampling": 0, "optimize": True})
            image.save(temp_path, format="JPEG", **save_kwargs)
        elif suffix == ".png":
            image.save(temp_path, format="PNG", optimize=True, **save_kwargs)
        elif suffix == ".webp":
            image.save(temp_path, format="WEBP", quality=95, method=6, **save_kwargs)
        else:
            raise ValueError(f"Định dạng ảnh không được hỗ trợ: {suffix}")

        with Image.open(temp_path) as verification:
            verification.verify()
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _source_image_info(opened):
    exif = opened.getexif()
    # ImageOps.exif_transpose đã xoay pixel thật; đặt Orientation về chuẩn để
    # phần mềm xem ảnh không xoay kết quả thêm một lần nữa.
    if exif and 274 in exif:
        exif[274] = 1
    return {
        "icc_profile": opened.info.get("icc_profile"),
        "exif": exif.tobytes() if exif else None,
    }


def upscale_lanczos(source, destination):
    with Image.open(source) as opened:
        source_info = _source_image_info(opened)
        image = ImageOps.exif_transpose(opened)
        original_size = image.size
        target_size = (original_size[0] * 2, original_size[1] * 2)
        upscaled = image.resize(target_size, Image.Resampling.LANCZOS)
        _atomic_save_pillow(upscaled, destination, source_info)
    return original_size, target_size


def upscale_opencv_lanczos4(source, destination):
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu OpenCV. Hãy chạy: pip install opencv-python-headless"
        ) from exc

    with Image.open(source) as opened:
        source_info = _source_image_info(opened)
        image = ImageOps.exif_transpose(opened)
        if image.mode not in ("L", "LA", "RGB", "RGBA"):
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        original_size = image.size
        target_size = (original_size[0] * 2, original_size[1] * 2)
        upscaled_array = cv2.resize(
            np.asarray(image), target_size, interpolation=cv2.INTER_LANCZOS4
        )
        _atomic_save_pillow(Image.fromarray(upscaled_array, mode=image.mode), destination, source_info)
    return original_size, target_size


def upscale_image(source, destination, method=UPSCALE_PILLOW):
    if method == UPSCALE_PILLOW:
        return upscale_lanczos(source, destination)
    if method == UPSCALE_OPENCV:
        return upscale_opencv_lanczos4(source, destination)
    raise ValueError(f"Thuật toán upscale không hợp lệ: {method}")


def _star_template(cv2, size):
    size = max(15, int(size) | 1)
    center = (size - 1) / 2
    radius = center * 0.88
    inner = radius * 0.22
    points = np.array([
        [round(center), round(center - radius)],
        [round(center + inner), round(center - inner)],
        [round(center + radius), round(center)],
        [round(center + inner), round(center + inner)],
        [round(center), round(center + radius)],
        [round(center - inner), round(center + inner)],
        [round(center - radius), round(center)],
        [round(center - inner), round(center - inner)],
    ], dtype=np.int32)
    template = np.zeros((size, size), dtype=np.uint8)
    cv2.fillPoly(template, [points], 255)
    return cv2.GaussianBlur(template, (0, 0), max(0.8, size / 55))


def _logo_specs(short_edge):
    # Flow giữ logo khoảng 61 px ở ảnh 1376x768 và ảnh 2K tải trực tiếp.
    # Ảnh được Lanczos x2 có cả logo và khoảng cách mép tăng đúng hai lần.
    if short_edge < 1100:
        return [(0.079, 0.127)]
    return [(0.040, 0.073), (0.079, 0.127)]


def detect_flow_logo(rgb_image, minimum_confidence=0.55):
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu OpenCV. Hãy chạy: pip install opencv-python-headless"
        ) from exc

    gray = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    short_edge = min(width, height)
    best = None

    for size_ratio, margin_ratio in _logo_specs(short_edge):
        nominal_size = max(25, round(short_edge * size_ratio))
        margin = short_edge * margin_ratio
        expected_center_x = width - margin
        expected_center_y = height - margin
        search_margin = max(12, round(nominal_size * 0.35))
        x0 = max(0, round(expected_center_x - nominal_size / 2 - search_margin))
        x1 = min(width, round(expected_center_x + nominal_size / 2 + search_margin))
        y0 = max(0, round(expected_center_y - nominal_size / 2 - search_margin))
        y1 = min(height, round(expected_center_y + nominal_size / 2 + search_margin))
        roi = gray[y0:y1, x0:x1]

        start_size = max(15, round(nominal_size * 0.78)) | 1
        end_size = max(start_size, round(nominal_size * 1.22) | 1)
        for template_size in range(start_size, end_size + 1, 2):
            if template_size >= min(roi.shape):
                continue
            template = _star_template(cv2, template_size)
            matches = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
            _, confidence, _, location = cv2.minMaxLoc(matches)
            candidate = {
                "confidence": float(confidence),
                "location": (x0 + location[0], y0 + location[1]),
                "matched_size": template_size,
                "center": (round(expected_center_x), round(expected_center_y)),
                "mask_size": nominal_size,
            }
            if best is None or candidate["confidence"] > best["confidence"]:
                best = candidate

    if best is None or best["confidence"] < minimum_confidence:
        return None
    # Logo là một mảng xám phẳng phủ lên nền. Kiểm tra thêm độ tương phản tại
    # đúng vị trí chuẩn để tránh nhận nhầm ngôi sao/chi tiết tự nhiên gần góc.
    validation_mask = _build_logo_mask(cv2, rgb_image.shape, best)
    erosion_size = max(5, round(best["mask_size"] * 0.24)) | 1
    ring_size = max(7, round(best["mask_size"] * 0.40)) | 1
    core = cv2.erode(
        validation_mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erosion_size, erosion_size)),
    )
    ring = cv2.dilate(
        validation_mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ring_size, ring_size)),
    )
    ring = cv2.subtract(ring, validation_mask)
    core_values = gray[core > 0]
    ring_values = gray[ring > 0]
    if not core_values.size or not ring_values.size:
        return None
    best["contrast"] = abs(float(core_values.mean()) - float(ring_values.mean()))
    if best["contrast"] < 10.0:
        return None
    return best


def _build_logo_mask(cv2, image_shape, detection):
    height, width = image_shape[:2]
    center_x, center_y = detection["center"]
    size = detection["mask_size"]
    radius = size * 0.52
    inner = radius * 0.25
    points = np.array([
        [round(center_x), round(center_y - radius)],
        [round(center_x + inner), round(center_y - inner)],
        [round(center_x + radius), round(center_y)],
        [round(center_x + inner), round(center_y + inner)],
        [round(center_x), round(center_y + radius)],
        [round(center_x - inner), round(center_y + inner)],
        [round(center_x - radius), round(center_y)],
        [round(center_x - inner), round(center_y - inner)],
    ], dtype=np.int32)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [points], 255)
    dilation = max(5, round(size * 0.22))
    if dilation % 2 == 0:
        dilation += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilation, dilation))
    return cv2.dilate(mask, kernel, iterations=1)


def remove_flow_logo(path):
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu OpenCV. Hãy chạy: pip install opencv-python-headless"
        ) from exc

    with Image.open(path) as opened:
        source_info = _source_image_info(opened)
        image = ImageOps.exif_transpose(opened).convert("RGB")
        rgb = np.asarray(image)

    detection = detect_flow_logo(rgb)
    if detection is None:
        return False, 0.0

    mask = _build_logo_mask(cv2, rgb.shape, detection)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    radius = max(3, round(detection["mask_size"] * 0.10))
    restored = cv2.inpaint(bgr, mask, radius, cv2.INPAINT_TELEA)
    restored_rgb = cv2.cvtColor(restored, cv2.COLOR_BGR2RGB)
    _atomic_save_pillow(Image.fromarray(restored_rgb), path, source_info)
    return True, detection["confidence"]


class MediaProcessingWorker(QThread):
    progress = pyqtSignal(int, int)
    processing_finished = pyqtSignal(dict, bool)
    fatal_error = pyqtSignal(str)

    def __init__(self, folder, upscale, remove_logo, recursive, upscale_method=UPSCALE_PILLOW):
        super().__init__()
        self.folder = Path(folder)
        self.upscale = upscale
        self.remove_logo = remove_logo
        self.recursive = recursive
        self.upscale_method = upscale_method
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        try:
            images = scan_images(self.folder, self.recursive)
            counts = {"total": len(images), "success": 0, "skipped": 0, "error": 0}
            for index, source in enumerate(images, start=1):
                if self._stopped:
                    break
                action, status, detail = self._process_file(source)
                counts[status.lower()] += 1
                self.progress.emit(index, len(images))

            self.processing_finished.emit(counts, self._stopped)
        except Exception as exc:
            logging.exception("[Media Tools] Lỗi xử lý folder")
            self.fatal_error.emit(str(exc))

    def _process_file(self, source):
        actions = []
        notes = []
        changed = False
        current_path = source
        source_is_2k = is_2k_name(source)
        delete_source_after_success = False

        try:
            if self.upscale:
                if source_is_2k:
                    notes.append("Đã có hậu tố _2K, bỏ qua upscale")
                else:
                    destination = two_k_path(source)
                    if destination.exists():
                        if not is_valid_upscaled_pair(source, destination):
                            raise RuntimeError(
                                f"{destination.name} đã tồn tại nhưng không đúng kích thước 2×; giữ lại ảnh gốc."
                            )
                        notes.append(f"Đã tồn tại {destination.name}, bỏ qua upscale")
                        current_path = destination
                        delete_source_after_success = True
                    else:
                        old_size, new_size = upscale_image(
                            source, destination, self.upscale_method
                        )
                        method_label = next(
                            (label for label, value in UPSCALE_METHODS.items()
                             if value == self.upscale_method),
                            self.upscale_method,
                        )
                        actions.append(f"Upscale 2× ({method_label})")
                        notes.append(
                            f"{old_size[0]}×{old_size[1]} → {new_size[0]}×{new_size[1]}"
                        )
                        current_path = destination
                        delete_source_after_success = True
                        changed = True

            if self.remove_logo and current_path is not None:
                removed, confidence = remove_flow_logo(current_path)
                if removed:
                    actions.append("Xóa logo")
                    notes.append(f"Nhận diện logo {confidence:.0%}")
                    changed = True
                else:
                    notes.append("Không phát hiện logo đủ tin cậy")

            if delete_source_after_success:
                source.unlink()
                actions.append("Xóa ảnh gốc")
                notes.append(f"Đã xóa {source.name}")
                changed = True

            if changed:
                return ", ".join(actions), "SUCCESS", "; ".join(notes)
            return ", ".join(actions) or "—", "SKIPPED", "; ".join(notes) or "Không cần xử lý"
        except Exception as exc:
            return ", ".join(actions) or "Đang xử lý", "ERROR", str(exc)

class MediaToolsView(QWidget):
    STATUS_LABELS = {
        "SUCCESS": "THÀNH CÔNG",
        "SKIPPED": "BỎ QUA",
        "ERROR": "LỖI",
    }
    STATUS_COLORS = {
        "SUCCESS": "#22c55e",
        "SKIPPED": "#f59e0b",
        "ERROR": "#ef4444",
    }

    def __init__(self):
        super().__init__()
        self.worker = None
        self._build_ui()
        self._load_settings()
        self._connect_setting_signals()

    @property
    def is_processing(self):
        return bool(self.worker and self.worker.isRunning())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        banner = QLabel("🛠 SỬA ẢNH / VIDEO")
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #0f766e,"
            "stop:0.5 #2563eb,stop:1 #7c3aed);color:white;font-size:20px;"
            "font-weight:900;padding:14px;border-radius:8px;letter-spacing:2px;"
        )
        layout.addWidget(banner)

        input_group = QGroupBox("Folder cần xử lý")
        input_layout = QHBoxLayout(input_group)
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("Nhập đường dẫn hoặc chọn folder chứa ảnh...")
        self.btn_browse = QPushButton("📁 Chọn folder")
        input_layout.addWidget(self.folder_input, 1)
        input_layout.addWidget(self.btn_browse)
        layout.addWidget(input_group)

        options_group = QGroupBox("Tính năng xử lý — nếu chọn cả hai sẽ upscale trước, xóa logo sau")
        options_layout = QHBoxLayout(options_group)
        self.chk_upscale = QCheckBox("Tăng chất lượng ảnh 2×")
        self.combo_upscale_method = QComboBox()
        for label, value in UPSCALE_METHODS.items():
            self.combo_upscale_method.addItem(label, value)
        self.chk_remove_logo = QCheckBox("Xóa logo Flow / Banana 2 bằng OpenCV Telea")
        self.chk_recursive = QCheckBox("Bao gồm folder con")
        options_layout.addWidget(self.chk_upscale)
        options_layout.addWidget(self.combo_upscale_method)
        options_layout.addWidget(self.chk_remove_logo)
        options_layout.addStretch()
        options_layout.addWidget(self.chk_recursive)
        layout.addWidget(options_group)

        controls = QHBoxLayout()
        self.btn_start = QPushButton("▶ BẮT ĐẦU XỬ LÝ")
        self.btn_start.setStyleSheet(self._button_style("#16a34a", "#22c55e"))
        self.btn_stop = QPushButton("■ DỪNG")
        self.btn_stop.setStyleSheet(self._button_style("#dc2626", "#ef4444"))
        self.btn_stop.setEnabled(False)
        controls.addWidget(self.btn_start)
        controls.addWidget(self.btn_stop)
        controls.addStretch()
        layout.addLayout(controls)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Chưa chạy")
        layout.addWidget(self.progress_bar)

        self.result_group = QGroupBox("Kết quả")
        result_layout = QVBoxLayout(self.result_group)
        self.lbl_result_status = QLabel("Đã chạy xong")
        self.lbl_result_status.setStyleSheet("color:#22c55e;font-size:16px;font-weight:800;")
        result_layout.addWidget(self.lbl_result_status)
        summary = QHBoxLayout()
        self.lbl_total = self._summary_label("Tổng: 0", "#60a5fa")
        self.lbl_success = self._summary_label("Thành công: 0", "#22c55e")
        self.lbl_skipped = self._summary_label("Bỏ qua: 0", "#f59e0b")
        self.lbl_error = self._summary_label("Lỗi: 0", "#ef4444")
        for label in (self.lbl_total, self.lbl_success, self.lbl_skipped, self.lbl_error):
            summary.addWidget(label)
        summary.addStretch()
        result_layout.addLayout(summary)
        self.result_group.setVisible(False)
        layout.addWidget(self.result_group)
        layout.addStretch(1)

        self.btn_browse.clicked.connect(self._browse_folder)
        self.btn_start.clicked.connect(self.start_processing)
        self.btn_stop.clicked.connect(self.stop_processing)
        self.chk_upscale.toggled.connect(self.combo_upscale_method.setEnabled)

    def _load_settings(self):
        settings = load_system_config().get("media_tools", {})
        if not isinstance(settings, dict):
            settings = {}
        self.folder_input.setText(str(settings.get("folder", "")))
        self.chk_upscale.setChecked(bool(settings.get("upscale", False)))
        method = settings.get("upscale_method", UPSCALE_PILLOW)
        method_index = self.combo_upscale_method.findData(method)
        self.combo_upscale_method.setCurrentIndex(max(0, method_index))
        self.chk_remove_logo.setChecked(bool(settings.get("remove_logo", False)))
        self.chk_recursive.setChecked(bool(settings.get("recursive", False)))
        self.combo_upscale_method.setEnabled(self.chk_upscale.isChecked())

    def _connect_setting_signals(self):
        self.folder_input.textChanged.connect(self._save_settings)
        self.chk_upscale.toggled.connect(self._save_settings)
        self.combo_upscale_method.currentIndexChanged.connect(self._save_settings)
        self.chk_remove_logo.toggled.connect(self._save_settings)
        self.chk_recursive.toggled.connect(self._save_settings)

    def _save_settings(self, *args):
        try:
            save_system_config({
                "media_tools": {
                    "folder": self.folder_input.text().strip(),
                    "upscale": self.chk_upscale.isChecked(),
                    "upscale_method": self.combo_upscale_method.currentData(),
                    "remove_logo": self.chk_remove_logo.isChecked(),
                    "recursive": self.chk_recursive.isChecked(),
                }
            })
        except Exception as exc:
            logging.warning("[Media Tools] Không thể lưu thiết lập: %s", exc)

    @staticmethod
    def _button_style(color, hover):
        return (
            f"QPushButton{{background:{color};color:white;font-weight:bold;padding:10px;"
            f"border:none;border-radius:5px;}}QPushButton:hover{{background:{hover};}}"
            "QPushButton:disabled{background:#4b5563;color:#9ca3af;}"
        )

    @staticmethod
    def _summary_label(text, color):
        label = QLabel(text)
        label.setStyleSheet(
            f"color:{color};font-weight:bold;padding:8px 12px;"
            "background:#252536;border-radius:5px;"
        )
        return label

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Chọn folder cần xử lý", self.folder_input.text().strip()
        )
        if folder:
            self.folder_input.setText(folder)

    def start_processing(self):
        folder = self.folder_input.text().strip()
        if not folder or not Path(folder).is_dir():
            self._show_inline_error("Folder không hợp lệ. Hãy nhập hoặc chọn một folder tồn tại.")
            return
        if not self.chk_upscale.isChecked() and not self.chk_remove_logo.isChecked():
            self._show_inline_error("Hãy chọn ít nhất một tính năng xử lý.")
            return
        if self.is_processing:
            return

        images = scan_images(folder, self.chk_recursive.isChecked())
        if not images:
            self._show_inline_error("Không tìm thấy file JPG, JPEG, PNG hoặc WEBP trong folder.")
            return

        self.result_group.setVisible(False)
        self.progress_bar.setRange(0, len(images))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"Đang xử lý: 0/{len(images)}")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

        self.worker = MediaProcessingWorker(
            folder=folder,
            upscale=self.chk_upscale.isChecked(),
            remove_logo=self.chk_remove_logo.isChecked(),
            recursive=self.chk_recursive.isChecked(),
            upscale_method=self.combo_upscale_method.currentData(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.processing_finished.connect(self._on_finished)
        self.worker.fatal_error.connect(self._on_fatal_error)
        self.worker.start()

    def stop_processing(self):
        if self.is_processing:
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.progress_bar.setFormat("Đang dừng sau file hiện tại...")

    def _on_progress(self, current, total):
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(current)
        self.progress_bar.setFormat(f"Đang xử lý: {current}/{total}")

    def _on_finished(self, counts, stopped):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._update_summary(counts)
        processed = counts["success"] + counts["skipped"] + counts["error"]
        self.progress_bar.setValue(processed)
        self.progress_bar.setFormat(
            f"{'Đã dừng' if stopped else 'Hoàn tất'}: {processed}/{counts['total']}"
        )
        self.lbl_result_status.setText("Đã dừng" if stopped else "Đã chạy xong")
        self.lbl_result_status.setStyleSheet(
            f"color:{'#f59e0b' if stopped else '#22c55e'};font-size:16px;font-weight:800;"
        )
        self.result_group.setVisible(True)

    def _on_fatal_error(self, message):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setFormat("Dừng do lỗi")
        self._show_inline_error(f"Không thể xử lý: {message}")

    def _update_summary(self, counts):
        self.lbl_total.setText(f"Tổng: {counts['total']}")
        self.lbl_success.setText(f"Thành công: {counts['success']}")
        self.lbl_skipped.setText(f"Bỏ qua: {counts['skipped']}")
        self.lbl_error.setText(f"Lỗi: {counts['error']}")

    def _show_inline_error(self, message):
        self.lbl_result_status.setText(message)
        self.lbl_result_status.setStyleSheet("color:#ef4444;font-size:15px;font-weight:800;")
        self.result_group.setVisible(True)

    def shutdown_tasks(self):
        self._save_settings()
        if self.is_processing:
            self.worker.stop()
            self.worker.wait(10000)
