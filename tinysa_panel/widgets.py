"""Custom painted front-panel widgets: rotary knob, power button, RF connector, split handle."""
import math

from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, pyqtSignal, QSize, QEvent
from PyQt6.QtGui import (QPainter, QColor, QPen, QBrush, QRadialGradient,
                         QLinearGradient, QFont, QPolygonF, QPainterPath)
from PyQt6.QtWidgets import QWidget, QAbstractButton, QPushButton, QFrame, QLabel


class PanelButton(QPushButton):
    """Front-panel key. Never takes keyboard focus so the window gets keys."""

    def __init__(self, text, style="key", parent=None, w=None, h=None):
        super().__init__(text, parent)
        self.setObjectName(style)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if w:
            self.setFixedWidth(w)
        if h:
            self.setFixedHeight(h)


class Knob(QWidget):
    """Rotary encoder. Hover + mouse wheel turns it; click-drag also turns it."""
    turned = pyqtSignal(int)

    DETENT = 15.0  # degrees per step

    def __init__(self, parent=None, size=150):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Hover and use the mouse wheel (or drag) to turn")
        self.angle = 0.0
        self.hover = False
        self._wheel_acc = 0
        self._drag_ang = None
        self._drag_acc = 0.0

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def _step(self, n):
        if n:
            self.angle = (self.angle + n * self.DETENT) % 360
            self.update()
            self.turned.emit(n)

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        self._wheel_acc += d
        steps = int(self._wheel_acc / 120)
        if steps:
            self._wheel_acc -= steps * 120
            self._step(steps)
        e.accept()

    def _ang(self, pos):
        c = QPointF(self.width() / 2, self.height() / 2)
        return math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))

    def mousePressEvent(self, e):
        self._drag_ang = self._ang(e.position())
        self._drag_acc = 0.0
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag_ang is None:
            return
        a = self._ang(e.position())
        d = a - self._drag_ang
        if d > 180:
            d -= 360
        elif d < -180:
            d += 360
        self._drag_ang = a
        self._drag_acc += d
        steps = int(self._drag_acc / 20)
        if steps:
            self._drag_acc -= steps * 20
            self._step(steps)

    def mouseReleaseEvent(self, e):
        self._drag_ang = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        R = min(self.width(), self.height()) / 2 - 3

        # recessed ring in panel
        g = QRadialGradient(c, R)
        g.setColorAt(0.80, QColor("#9fb0c6"))
        g.setColorAt(1.00, QColor("#c9d4e2"))
        p.setPen(QPen(QColor("#8a9bb3"), 1))
        p.setBrush(g)
        p.drawEllipse(c, R, R)

        if self.hover:
            p.setPen(QPen(QColor(40, 120, 230, 200), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, R - 1.5, R - 1.5)

        # knurled skirt
        r2 = R * 0.86
        sk = QRadialGradient(c - QPointF(r2 * .3, r2 * .3), r2 * 1.6)
        sk.setColorAt(0, QColor("#ffffff"))
        sk.setColorAt(1, QColor("#b9bec6"))
        p.setPen(QPen(QColor("#9aa1ab"), 1))
        p.setBrush(sk)
        p.drawEllipse(c, r2, r2)
        p.save()
        p.translate(c)
        p.rotate(self.angle)
        p.setPen(QPen(QColor(120, 126, 136, 150), 1.2))
        for _ in range(60):
            p.drawLine(QPointF(0, -r2 + 1), QPointF(0, -r2 * 0.90))
            p.rotate(6)
        p.restore()

        # cap
        r3 = R * 0.76
        cg = QRadialGradient(c - QPointF(r3 * .35, r3 * .35), r3 * 1.5)
        cg.setColorAt(0, QColor("#ffffff"))
        cg.setColorAt(0.55, QColor("#eceef1"))
        cg.setColorAt(1, QColor("#c3c8cf"))
        p.setPen(QPen(QColor("#b0b6be"), 1))
        p.setBrush(cg)
        p.drawEllipse(c, r3, r3)

        # finger dimple
        a = math.radians(self.angle - 90)
        dp = c + QPointF(math.cos(a) * r3 * 0.6, math.sin(a) * r3 * 0.6)
        dr = r3 * 0.15
        dg = QRadialGradient(dp + QPointF(dr * .3, dr * .3), dr * 1.2)
        dg.setColorAt(0, QColor("#f4f5f7"))
        dg.setColorAt(1, QColor("#a7adb6"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dg)
        p.drawEllipse(dp, dr, dr)
        p.end()


class PowerButton(QAbstractButton):
    """Square key with a power symbol; lit green when connected."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(64, 44)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Connect / disconnect")

    def sizeHint(self):
        return QSize(64, 44)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor("#fdfdfd"))
        g.setColorAt(1, QColor("#cfd4da") if not self.isDown() else QColor("#b9bfc7"))
        p.setPen(QPen(QColor("#8a94a3"), 1))
        p.setBrush(g)
        p.drawRoundedRect(r, 6, 6)
        col = QColor("#3cc93c") if self.isChecked() else QColor("#8e959e")
        c = r.center()
        rad = 11
        if self.isChecked():
            glow = QRadialGradient(c, 20)
            glow.setColorAt(0, QColor(80, 255, 80, 90))
            glow.setColorAt(1, QColor(80, 255, 80, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, 20, 20)
        pen = QPen(col, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), 120 * 16, 300 * 16)
        p.drawLine(QPointF(c.x(), c.y() - rad - 2), QPointF(c.x(), c.y() - 2))
        p.end()


class Connector(QWidget):
    """SMA-style bulkhead connector with warning-triangle label."""

    def __init__(self, title, sub, parent=None):
        super().__init__(parent)
        self.title = title
        self.sub = sub
        self.setFixedSize(200, 120)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, 38)
        # hex nut
        poly = QPolygonF([c + QPointF(30 * math.cos(math.radians(60 * i + 30)),
                                      30 * math.sin(math.radians(60 * i + 30))) for i in range(6)])
        g = QLinearGradient(c - QPointF(30, 30), c + QPointF(30, 30))
        g.setColorAt(0, QColor("#f4f4f4"))
        g.setColorAt(1, QColor("#7d838b"))
        p.setPen(QPen(QColor("#5d636b"), 1))
        p.setBrush(g)
        p.drawPolygon(poly)
        # body
        rg = QRadialGradient(c - QPointF(6, 6), 26)
        rg.setColorAt(0, QColor("#ffffff"))
        rg.setColorAt(1, QColor("#8f959c"))
        p.setBrush(rg)
        p.drawEllipse(c, 20, 20)
        p.setBrush(QColor("#2a2a2a"))
        p.drawEllipse(c, 12, 12)
        p.setBrush(QColor("#f5f2ea"))
        p.drawEllipse(c, 7, 7)
        gold = QRadialGradient(c, 3)
        gold.setColorAt(0, QColor("#ffe9a0"))
        gold.setColorAt(1, QColor("#c9a227"))
        p.setBrush(gold)
        p.drawEllipse(c, 2.5, 2.5)
        # labels
        p.setPen(QColor("#3c4a5e"))
        f = QFont("Segoe UI", 8)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 76, self.width() - 26, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.title)
        f.setBold(False)
        p.setFont(f)
        p.drawText(QRectF(0, 94, self.width() - 26, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.sub)
        # warning triangle
        tx, ty = self.width() - 20, 82
        tri = QPolygonF([QPointF(tx, ty), QPointF(tx + 16, ty), QPointF(tx + 8, ty - 14)])
        p.setPen(QPen(QColor("#222"), 1.2))
        p.setBrush(QColor("#f5c400"))
        p.drawPolygon(tri)
        p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        p.drawText(QRectF(tx, ty - 12, 16, 12), Qt.AlignmentFlag.AlignCenter, "!")
        p.end()


class ClickFrame(QFrame):
    clicked = pyqtSignal()

    def mousePressEvent(self, e):
        self.clicked.emit()


class SplitHandle(QWidget):
    """Camouflaged divider between the trace screen and the menu column / control panel.

    Where it touches the screen the rail is black like the screen; elsewhere nothing is
    painted (the window background shows through), so only its blue arrow stands out:
    ▶ = click (on the arrow or anywhere on the rail) to fold the right-hand side (the
    trace screen then fills the window and the rail moves to the right edge),
    ◀ = click to bring it back.
    """
    clicked = pyqtSignal()

    BAND = QColor("#000000")      # rail colour where it touches the screen
    ARROW = QColor("#3b7ddd")     # the panel's own blue (key hover border)

    def __init__(self, parent=None, width=8):
        super().__init__(parent)
        self._w = width
        self.setFixedWidth(width)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # keys keep going to the main window
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapsed = False
        self._pressed = False
        self._bezel = None      # left half of the screen bezel
        self._screen = None     # black screen frame inside it
        self._edges = None      # overlay that blackens the bezel edges next to the rail
        self._update_tip()

    def sizeHint(self):
        return QSize(self._w, 200)

    def _neighbours(self):
        """The bezel half just left of the rail (#screenBezel) and the black screen
        frame inside it (#screen); found once, then followed when they move or resize."""
        if self._bezel is None:
            win = self.window()
            x0 = self.mapTo(win, QPoint(0, 0)).x()
            for f in win.findChildren(QFrame, "screenBezel"):
                if f.isVisible() and f.mapTo(win, QPoint(f.width(), 0)).x() == x0:
                    scr = f.findChild(QFrame, "screen")
                    if scr is not None:
                        self._bezel, self._screen = f, scr
                        for w in (f, scr):
                            w.installEventFilter(self)      # repaint when they move or resize
                    break
        return self._bezel, self._screen

    def eventFilter(self, obj, e):
        if e.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._refresh()
        return False

    def _refresh(self):
        self.update()
        if self._edges is not None:
            self._edges.update()

    def screen_band(self):
        """(top, height) of the screen next to the rail, in rail coordinates, or None
        when folded (then only the arrow is drawn)."""
        if self.collapsed:
            return None
        bezel, screen = self._neighbours()
        if screen is None or screen.height() <= 0:
            return None
        win = self.window()
        top = screen.mapTo(win, QPoint(0, 0)).y() - self.mapTo(win, QPoint(0, 0)).y()
        return top, screen.height()

    # the overlay sits 1 px wider than the rail on each side, above the bezel halves
    def _sync_edges(self):
        parent = self.parentWidget()
        if parent is None:
            return
        if self._edges is None or self._edges.parentWidget() is not parent:
            self._edges = _RailEdges(self, parent)
        self._edges.setGeometry(self.geometry().adjusted(-1, 0, 1, 0))
        self._edges.show()
        self._edges.raise_()
        self._edges.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_edges()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_edges()

    def moveEvent(self, e):
        super().moveEvent(e)
        self._sync_edges()

    def set_collapsed(self, collapsed):
        self.collapsed = bool(collapsed)
        self._update_tip()
        self._refresh()

    def _update_tip(self):
        self.setToolTip("Show the menu and control panel" if self.collapsed
                        else "Hide the menu and control panel")

    def mousePressEvent(self, e):
        self._pressed = e.button() == Qt.MouseButton.LeftButton

    def mouseReleaseEvent(self, e):
        # click = press and release inside the rail
        if self._pressed and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        self._pressed = False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        # rail: black where it touches the screen, nothing painted elsewhere
        band = self.screen_band()
        if band is not None:
            p.fillRect(QRectF(0, band[0], self.width(), band[1]), self.BAND)

        # blue arrow inside the rail, vertically centred (7 x 12 px in an 8 px rail)
        c = r.center()
        hw = max(1.5, min(r.width() / 2, 3.5))   # half width of the arrow
        hh = hw * 1.7                            # half height of the arrow
        d = -1 if self.collapsed else 1     # ◀ when collapsed, ▶ otherwise
        tri = QPolygonF([QPointF(c.x() - d * hw, c.y() - hh),
                         QPointF(c.x() - d * hw, c.y() + hh),
                         QPointF(c.x() + d * hw, c.y())])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.ARROW)
        p.drawPolygon(tri)
        p.end()


class _RailEdges(QWidget):
    """Overlay of SplitHandle: blackens, at screen height only, the 1 px bezel edge on
    each side of the rail, so the cut blends into the screen. Ignores the mouse."""

    def __init__(self, rail, parent):
        super().__init__(parent)
        self.rail = rail
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, e):
        band = self.rail.screen_band()      # same y origin as the rail
        if band is None:
            return
        top, h = band
        p = QPainter(self)
        p.fillRect(QRectF(0, top, 1, h), SplitHandle.BAND)
        p.fillRect(QRectF(self.width() - 1, top, 1, h), SplitHandle.BAND)
        p.end()
