"""Compact, persistent-option controls embedded in the motion preview menu."""
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QStyle, QToolButton, QWidget,
)


class AstraControls(QWidget):
    styleChanged = Signal(str)
    periodsChanged = Signal(float, float)
    resetRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("astraControls")
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 6, 12, 10)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)
        self.style_box = QComboBox(self)
        self.style_box.setObjectName("astraStyle")
        self.style_box.addItem("\u67d4\u5149\u65e7\u7248", "classic")
        self.style_box.addItem("\u661f\u94fe\u65b0\u7248", "linked")
        self.style_box.setMinimumWidth(132)
        self.minimum = QDoubleSpinBox(self)
        self.maximum = QDoubleSpinBox(self)
        for widget, name in ((self.minimum, "astraMinimum"), (self.maximum, "astraMaximum")):
            widget.setObjectName(name)
            widget.setRange(1, 120)
            widget.setDecimals(1)
            widget.setSingleStep(0.5)
            widget.setSuffix(" \u79d2")
            widget.setKeyboardTracking(False)
            widget.setMinimumWidth(132)
        for row, (text, widget) in enumerate((
            ("\u52a8\u6548\u7248\u672c", self.style_box),
            ("\u6700\u77ed\u5468\u671f", self.minimum),
            ("\u6700\u957f\u5468\u671f", self.maximum),
        )):
            label = QLabel(text, self)
            label.setBuddy(widget)
            widget.setAccessibleName(text)
            layout.addWidget(label, row, 0)
            layout.addWidget(widget, row, 1)
        self.reset_button = QToolButton(self)
        self.reset_button.setIcon(QIcon.fromTheme(
            "edit-undo", self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)))
        self.reset_button.setToolTip("\u6062\u590d\u6b64\u7248\u672c\u9ed8\u8ba4\u5468\u671f")
        self.reset_button.setAccessibleName(self.reset_button.toolTip())
        self.reset_button.setFixedSize(28, 28)
        layout.addWidget(self.reset_button, 0, 2)
        self.style_box.currentIndexChanged.connect(
            lambda _index: self.styleChanged.emit(self.style_box.currentData()))
        self.minimum.valueChanged.connect(lambda _value: self._period_changed(True))
        self.maximum.valueChanged.connect(lambda _value: self._period_changed(False))
        self.reset_button.clicked.connect(self.resetRequested)

    def _period_changed(self, minimum_changed):
        if self.minimum.value() > self.maximum.value():
            other = self.maximum if minimum_changed else self.minimum
            with QSignalBlocker(other):
                other.setValue(self.minimum.value() if minimum_changed else self.maximum.value())
        self.periodsChanged.emit(self.minimum.value(), self.maximum.value())

    def set_options(self, options):
        with (QSignalBlocker(self.style_box), QSignalBlocker(self.minimum),
              QSignalBlocker(self.maximum)):
            self.style_box.setCurrentIndex(self.style_box.findData(options.style))
            self.minimum.setValue(options.minimum)
            self.maximum.setValue(options.maximum)
