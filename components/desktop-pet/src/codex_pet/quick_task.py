"""Draft locally, then hand off to the official composer (never auto-send)."""
from urllib.parse import urlencode

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout


def new_task_url(prompt: str = "") -> str:
    url = "codex://threads/new"
    if prompt.strip():
        url += "?" + urlencode({"prompt": prompt.strip()})
    # Keep the handoff comfortably below Windows command/URL transport limits.
    if len(url.encode("utf-8")) > 6000:
        raise ValueError("内容较长，请缩短后继续，或直接在 Codex 中输入。")
    return url


def open_new_task(prompt: str = "") -> bool:
    return QDesktopServices.openUrl(QUrl(new_task_url(prompt)))


class DraftEdit(QPlainTextEdit):
    continue_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.continue_requested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class QuickTaskDialog(QDialog):
    def __init__(self, opener=open_new_task):
        super().__init__()
        self._opener = opener
        self.setWindowTitle("桌宠 · 新任务")
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.resize(400, 240)
        layout = QVBoxLayout(self)
        self.hint = QLabel("写下想做的事，再到 Codex 选择项目并确认发送。")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.editor = DraftEdit()
        self.editor.setPlaceholderText("让 Codex 帮你做什么？\nEnter 继续 · Shift+Enter 换行 · Esc 收起")
        layout.addWidget(self.editor)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.PlainText)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.button = QPushButton("在 Codex 中继续 ↗")
        self.button.setAutoDefault(False)
        self.button.clicked.connect(self.handoff)
        self.editor.continue_requested.connect(self.handoff)
        layout.addWidget(self.button)

    def handoff(self):
        try:
            opened = self._opener(self.editor.toPlainText())
        except (ValueError, OSError) as exc:
            self.feedback.setText(str(exc))
            return
        if not opened:
            self.feedback.setText("未能打开 Codex；草稿已保留，请确认官方应用可启动。")
            return
        # OS acceptance is not proof of delivery: retain the draft until edited.
        self.feedback.setText("已请求打开 Codex 输入页；请在那里确认内容并发送。")
        self.hide()

    def present(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.editor.setFocus()
