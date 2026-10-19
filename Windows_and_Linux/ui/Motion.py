from PySide6 import QtCore, QtGui, QtWidgets

from ui.UIUtils import colorMode


class GlowButton(QtWidgets.QPushButton):
    """
    QPushButton that keeps its stylesheet look but adds smooth, animated
    hover (luminous overlay) and press (soft dimming) feedback, like native
    Apple controls. Qt stylesheets can't animate, so we paint the overlay.
    """

    def __init__(self, *args, radius=10, glow=0.15, **kwargs):
        super().__init__(*args, **kwargs)
        self._radius = radius
        self._glow = glow
        self._hover = 0.0
        self._press = 0.0
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))

        self._hover_anim = QtCore.QVariantAnimation(self)
        self._hover_anim.setDuration(170)
        self._hover_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_value)

        self._press_anim = QtCore.QVariantAnimation(self)
        self._press_anim.setDuration(110)
        self._press_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self._press_anim.valueChanged.connect(self._on_press_value)

    def _on_hover_value(self, v):
        self._hover = float(v)
        self.update()

    def _on_press_value(self, v):
        self._press = float(v)
        self.update()

    def _animate(self, anim, current, target):
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.start()

    def enterEvent(self, event):
        self._animate(self._hover_anim, self._hover, 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate(self._hover_anim, self._hover, 0.0)
        self._animate(self._press_anim, self._press, 0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._animate(self._press_anim, self._press, 1.0)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._animate(self._press_anim, self._press, 0.0)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isEnabled() or (self._hover <= 0.001 and self._press <= 0.001):
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(self._radius, rect.height() / 2.0)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        if self._hover > 0.001:
            base = 255 if colorMode == 'dark' else 0
            a = int(255 * self._glow * self._hover * (1.0 if colorMode == 'dark' else 0.6))
            p.setBrush(QtGui.QColor(base, base, base, a))
            p.drawRoundedRect(rect, radius, radius)
        if self._press > 0.001:
            p.setBrush(QtGui.QColor(0, 0, 0, int(46 * self._press)))
            p.drawRoundedRect(rect, radius, radius)
        p.end()


def fade_in(widget, duration=220):
    """Fade a top-level widget in from transparent with an ease-out curve."""
    widget.setWindowOpacity(0.0)
    anim = QtCore.QPropertyAnimation(widget, b"windowOpacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
    anim.start()
    widget._fade_anim = anim
    return anim


def crossfade_stack(owner, stack, index, on_changed=None):
    """
    Switch a QStackedWidget page with a quick fade-out / fade-in instead of a hard cut.
    `owner` keeps references to the animations (must be a QObject).
    """
    if stack.currentIndex() == index:
        return
    if not stack.isVisible():
        stack.setCurrentIndex(index)
        if on_changed:
            on_changed()
        return

    effect = QtWidgets.QGraphicsOpacityEffect(stack)
    effect.setOpacity(1.0)
    stack.setGraphicsEffect(effect)

    fade_out = QtCore.QPropertyAnimation(effect, b"opacity", owner)
    fade_out.setDuration(110)
    fade_out.setStartValue(1.0)
    fade_out.setEndValue(0.0)
    fade_out.setEasingCurve(QtCore.QEasingCurve.Type.InCubic)

    def _swap():
        stack.setCurrentIndex(index)
        if on_changed:
            on_changed()
        fade_in_anim = QtCore.QPropertyAnimation(effect, b"opacity", owner)
        fade_in_anim.setDuration(180)
        fade_in_anim.setStartValue(0.0)
        fade_in_anim.setEndValue(1.0)
        fade_in_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        fade_in_anim.finished.connect(lambda: stack.setGraphicsEffect(None))
        fade_in_anim.start()
        owner._page_fade_in = fade_in_anim

    fade_out.finished.connect(_swap)
    fade_out.start()
    owner._page_fade_out = fade_out

