from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)


class TaskPromptEditDialog(QDialog):
    """Edit one task prompt and explicitly save or cancel the change."""

    def __init__(self, prompt, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chỉnh sửa Prompt")
        self.resize(680, 360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Nội dung prompt:"))

        self.input_prompt = QPlainTextEdit(prompt or "")
        self.input_prompt.setPlaceholderText("Nhập prompt...")
        layout.addWidget(self.input_prompt, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.btn_save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.btn_save.setText("Lưu")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Hủy")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.input_prompt.textChanged.connect(self._update_save_state)
        layout.addWidget(self.buttons)

        self._update_save_state()
        self.input_prompt.setFocus()
        self.input_prompt.selectAll()

    def _update_save_state(self):
        self.btn_save.setEnabled(bool(self.prompt()))

    def prompt(self):
        return self.input_prompt.toPlainText().strip()


class PromptCellWidget(QWidget):
    """Read-only prompt text with a compact edit action at the right edge."""

    def __init__(self, task_id, prompt, edit_callback, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 2, 4, 2)
        layout.setSpacing(6)

        self.lbl_prompt = QLabel(prompt or "")
        self.lbl_prompt.setWordWrap(True)
        self.lbl_prompt.setToolTip(prompt or "")
        self.lbl_prompt.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.lbl_prompt, 1)

        self.btn_edit = QPushButton("✎")
        self.btn_edit.setFixedSize(27, 27)
        self.btn_edit.setToolTip("Chỉnh sửa prompt")
        self.btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit.setStyleSheet(
            "QPushButton { border: none; border-radius: 4px; font-size: 17px; "
            "padding: 0; background: transparent; }"
            "QPushButton:hover { background: rgba(139, 92, 246, 0.22); }"
        )
        self.btn_edit.clicked.connect(lambda: edit_callback(task_id))
        layout.addWidget(self.btn_edit, 0, Qt.AlignmentFlag.AlignRight)
