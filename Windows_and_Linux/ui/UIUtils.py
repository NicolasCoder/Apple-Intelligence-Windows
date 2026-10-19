import os
import sys

from PySide6 import QtGui, QtCore, QtWidgets
from PySide6.QtGui import QImage, QPixmap

import darkdetect
colorMode = 'dark' if darkdetect.isDark() else 'light'

class UIUtils:
    @classmethod
    def clear_layout(cls, layout):
        """
        Clear the layout of all widgets.
        """
        while ((child := layout.takeAt(0)) != None):
            #If the child is a layout, delete it
            if child.layout():
                cls.clear_layout(child.layout())
                child.layout().deleteLater()
            else:
                child.widget().deleteLater()

    @classmethod
    def resize_and_round_image(cls, image, image_size = 100, rounding_amount = 50):
        image = image.scaledToWidth(image_size)
        clipPath = QtGui.QPainterPath()
        clipPath.addRoundedRect(0, 0, image_size, image_size, rounding_amount, rounding_amount)
        target = QImage(image_size, image_size, QImage.Format_ARGB32)
        target.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(target)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setClipPath(clipPath)
        painter.drawImage(0, 0, image)
        painter.end()
        targetPixmap = QPixmap.fromImage(target)
        return targetPixmap

    @classmethod
    def setup_window_and_layout(cls, base: QtWidgets.QWidget):
        # Set the window icon
        icon_path = os.path.join(os.path.dirname(sys.argv[0]), 'icons', 'app_icon.png')
        if os.path.exists(icon_path): base.setWindowIcon(QtGui.QIcon(icon_path))
        main_layout = QtWidgets.QVBoxLayout(base)
        main_layout.setContentsMargins(0, 0, 0, 0)
        base.background = ThemeBackground(base, 'gradient')
        main_layout.addWidget(base.background)


class ThemeBackground(QtWidgets.QWidget):
    """
    A custom widget that creates an Apple Intelligence glassmorphic background
    for the application based on the selected theme.
    """
    def __init__(self, parent=None, theme='gradient', is_popup=False, border_radius=20):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.theme = theme
        self.is_popup = is_popup
        self.border_radius = border_radius if border_radius else 20

    def paintEvent(self, event):
        """
        Override the paint event to draw an Apple Intelligence style surface:
        deep frosted glass with a subtle mesh-like ambient gradient and a
        delicate luminous specular border.
        """
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = QtCore.QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        radius = float(self.border_radius)

        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        if self.theme == 'gradient':
            # Base frosted surface
            if colorMode == 'dark':
                base_grad = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
                base_grad.setColorAt(0.0, QtGui.QColor(28, 28, 36, 245))
                base_grad.setColorAt(0.5, QtGui.QColor(22, 22, 29, 248))
                base_grad.setColorAt(1.0, QtGui.QColor(32, 24, 38, 245))
                painter.fillPath(path, QtGui.QBrush(base_grad))

                # Subtle Apple Intelligence ambient glow in top-left & bottom-right
                glow1 = QtGui.QRadialGradient(rect.left() + rect.width() * 0.15, rect.top() + rect.height() * 0.1, rect.width() * 0.7)
                glow1.setColorAt(0.0, QtGui.QColor(120, 90, 255, 38))
                glow1.setColorAt(1.0, QtGui.QColor(120, 90, 255, 0))
                painter.fillPath(path, QtGui.QBrush(glow1))

                glow2 = QtGui.QRadialGradient(rect.right() - rect.width() * 0.15, rect.bottom() - rect.height() * 0.1, rect.width() * 0.7)
                glow2.setColorAt(0.0, QtGui.QColor(255, 95, 160, 32))
                glow2.setColorAt(1.0, QtGui.QColor(255, 95, 160, 0))
                painter.fillPath(path, QtGui.QBrush(glow2))
            else:
                base_grad = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
                base_grad.setColorAt(0.0, QtGui.QColor(248, 246, 255, 246))
                base_grad.setColorAt(0.5, QtGui.QColor(252, 252, 254, 248))
                base_grad.setColorAt(1.0, QtGui.QColor(255, 245, 250, 246))
                painter.fillPath(path, QtGui.QBrush(base_grad))

                glow1 = QtGui.QRadialGradient(rect.left() + rect.width() * 0.2, rect.top(), rect.width() * 0.7)
                glow1.setColorAt(0.0, QtGui.QColor(145, 110, 255, 30))
                glow1.setColorAt(1.0, QtGui.QColor(145, 110, 255, 0))
                painter.fillPath(path, QtGui.QBrush(glow1))

            # Apple Intelligence luminous iridescent border
            border_grad = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
            if colorMode == 'dark':
                border_grad.setColorAt(0.0, QtGui.QColor(168, 130, 255, 140))
                border_grad.setColorAt(0.35, QtGui.QColor(94, 175, 255, 100))
                border_grad.setColorAt(0.7, QtGui.QColor(255, 110, 180, 110))
                border_grad.setColorAt(1.0, QtGui.QColor(255, 165, 100, 130))
            else:
                border_grad.setColorAt(0.0, QtGui.QColor(150, 110, 255, 120))
                border_grad.setColorAt(0.5, QtGui.QColor(90, 165, 255, 90))
                border_grad.setColorAt(1.0, QtGui.QColor(245, 115, 175, 120))

            pen = QtGui.QPen(QtGui.QBrush(border_grad), 1.3)
            painter.setPen(pen)
            painter.drawPath(path)
        else:
            if colorMode == 'dark':
                color = QtGui.QColor(28, 28, 30, 248)
                border_color = QtGui.QColor(255, 255, 255, 36)
            else:
                color = QtGui.QColor(245, 245, 247, 248)
                border_color = QtGui.QColor(0, 0, 0, 28)
            painter.fillPath(path, QtGui.QBrush(color))
            pen = QtGui.QPen(border_color, 1.0)
            painter.setPen(pen)
            painter.drawPath(path)

