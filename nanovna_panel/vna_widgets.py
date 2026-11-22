"""Painted front-panel widgets for the VNA panel (knob, power bar, ports, split handle)."""
import math

from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, pyqtSignal, QSize, QEvent
from PyQt6.QtGui import (QPainter, QColor, QPen, QRadialGradient, QLinearGradient,
                         QFont, QPolygonF, QPainterPath)
from PyQt6.QtWidgets import QWidget, QAbstractButton, QPushButton, QFrame


class PanelButton(QPushButton):
    def __init__(self, text, style="key", parent=None, w=None, h=None):
        super().__init__(text, parent)
        self.setObjectName(style)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if w:
            self.setFixedWidth(w)
        if h:
            self.setFixedHeight(h)


class ClickFrame(QFrame):
    clicked = pyqtSignal()

    def mousePressEvent(self, e):
        self.clicked.emit()


class Knob(QWidget):
    """Rotary encoder with curved direction arrows. Hover + mouse wheel turns it."""
    turned = pyqtSignal(int)
    DETENT = 15.0

    def __init__(self, parent=None, size=160):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Hover and use the mouse wheel (or drag) to turn")
        self.angle = 0.0
        self.hover = False
        self._acc = 0
        self._drag = None
        self._dacc = 0.0

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
        self._acc += e.angleDelta().y() or e.angleDelta().x()
        s = int(self._acc / 120)
        if s:
            self._acc -= s * 120
            self._step(s)
        e.accept()

    def _ang(self, p):
        return math.degrees(math.atan2(p.y() - self.height() / 2, p.x() - self.width() / 2))

    def mousePressEvent(self, e):
        self._drag = self._ang(e.position())
        self._dacc = 0.0
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        a = self._ang(e.position())
        d = (a - self._drag + 180) % 360 - 180
        self._drag = a
        self._dacc += d
        s = int(self._dacc / 20)
        if s:
            self._dacc -= s * 20
            self._step(s)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        R = min(self.width(), self.height()) / 2 - 3

        # curved arrows (left = CCW, right = CW)
        p.setPen(QPen(QColor("#9aa3ad"), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        rr = R - 4
        box = QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr)
        p.drawArc(box, 150 * 16, 60 * 16)
        p.drawArc(box, -30 * 16, 60 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#9aa3ad"))
        for ang, sgn in ((210, 1), (-30, -1)):
            a = math.radians(ang)
            tip = c + QPointF(rr * math.cos(a), -rr * math.sin(a))
            t = QPointF(-math.sin(a), -math.cos(a)) * sgn          # tangent direction
            nrm = QPointF(math.cos(a), -math.sin(a))
            p.drawPolygon(QPolygonF([tip + t * 10, tip - nrm * 9, tip + nrm * 9]))

        rk = R * 0.80
        if self.hover:
            p.setPen(QPen(QColor(40, 120, 230, 210), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, rk + 4, rk + 4)
        sk = QRadialGradient(c - QPointF(rk * .3, rk * .3), rk * 1.6)
        sk.setColorAt(0, QColor("#ffffff"))
        sk.setColorAt(1, QColor("#b3b8bf"))
        p.setPen(QPen(QColor("#8f969f"), 1))
        p.setBrush(sk)
        p.drawEllipse(c, rk, rk)
        p.save()
        p.translate(c)
        p.rotate(self.angle)
        p.setPen(QPen(QColor(110, 116, 126, 170), 1.3))
        for _ in range(16):
            p.drawLine(QPointF(0, -rk + 2), QPointF(0, -rk * 0.84))
            p.rotate(22.5)
        p.restore()
        rc = rk * 0.80
        cg = QRadialGradient(c - QPointF(rc * .35, rc * .35), rc * 1.5)
        cg.setColorAt(0, QColor("#ffffff"))
        cg.setColorAt(0.6, QColor("#eceef1"))
        cg.setColorAt(1, QColor("#c3c8cf"))
        p.setPen(QPen(QColor("#b0b6be"), 1))
        p.setBrush(cg)
        p.drawEllipse(c, rc, rc)
        a = math.radians(self.angle - 90)
        dp = c + QPointF(math.cos(a) * rc * 0.62, math.sin(a) * rc * 0.62)
        dg = QRadialGradient(dp + QPointF(2, 2), rc * 0.17)
        dg.setColorAt(0, QColor("#f4f5f7"))
        dg.setColorAt(1, QColor("#a7adb6"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dg)
        p.drawEllipse(dp, rc * 0.13, rc * 0.13)
        p.end()


class DPadCenter(QWidget):
    """Yellow ring behind the arrow keys."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(17, 17)
        p.setPen(QPen(QColor("#c9a82a"), 4))
        g = QRadialGradient(c, 15)
        g.setColorAt(0, QColor("#6d7684"))
        g.setColorAt(1, QColor("#3f4752"))
        p.setBrush(g)
        p.drawEllipse(c, 13, 13)
        p.end()


class PowerBar(QAbstractButton):
    """Tall power key with a green light bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(46, 76)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Connect / disconnect")

    def sizeHint(self):
        return QSize(46, 76)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, QColor("#f7f7f7"))
        g.setColorAt(1, QColor("#c9ced5") if not self.isDown() else QColor("#aeb4bc"))
        p.setPen(QPen(QColor("#7f8894"), 1.5))
        p.setBrush(g)
        p.drawRoundedRect(r, 10, 10)
        bar = QRectF(r.center().x() - 5, r.top() + 14, 10, r.height() - 28)
        if self.isChecked():
            glow = QRadialGradient(bar.center(), 26)
            glow.setColorAt(0, QColor(60, 255, 90, 110))
            glow.setColorAt(1, QColor(60, 255, 90, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(bar.center(), 26, 30)
            p.setBrush(QColor("#2ee05a"))
        else:
            p.setBrush(QColor("#8a929c"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(bar, 5, 5)
        p.end()


class PortConnector(QWidget):
    """N-type style port with coloured number tag."""

    def __init__(self, num, color, sub, parent=None):
        super().__init__(parent)
        self.num = str(num)
        self.color = QColor(color)
        self.sub = sub
        self.setFixedSize(190, 104)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(110, 50)
        # hex boss
        poly = QPolygonF([c + QPointF(46 * math.cos(math.radians(60 * i)), 46 * math.sin(math.radians(60 * i)))
                          for i in range(6)])
        g = QLinearGradient(c - QPointF(40, 40), c + QPointF(40, 40))
        g.setColorAt(0, QColor("#f2f3f5"))
        g.setColorAt(1, QColor("#aab0b8"))
        p.setPen(QPen(QColor("#8d949d"), 1))
        p.setBrush(g)
        p.drawPolygon(poly)
        for r, col in ((32, "#5d636b"), (29, None), (21, "#2b2b2b"), (13, "#f2efe6")):
            if col is None:
                rg = QRadialGradient(c - QPointF(8, 8), 34)
                rg.setColorAt(0, QColor("#ffffff"))
                rg.setColorAt(1, QColor("#8d939a"))
                p.setBrush(rg)
            else:
                p.setBrush(QColor(col))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(c, r, r)
        gold = QRadialGradient(c, 4)
        gold.setColorAt(0, QColor("#ffe9a0"))
        gold.setColorAt(1, QColor("#b8901d"))
        p.setBrush(gold)
        p.drawEllipse(c, 3.5, 3.5)
        # number tag
        tag = QPainterPath()
        tag.addRoundedRect(QRectF(44, 0, 22, 30), 3, 3)
        p.fillPath(tag, self.color)
        p.setPen(QColor("#1a1a1a"))
        p.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        p.drawText(QRectF(44, 0, 22, 30), Qt.AlignmentFlag.AlignCenter, self.num)
        # label + warning
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QColor("#3c4a5e"))
        p.drawText(QRectF(0, 74, 64, 28), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.sub)
        tx, ty = 6, 82
        p.setPen(QPen(QColor("#222"), 1))
        p.setBrush(QColor("#f5c400"))
        p.drawPolygon(QPolygonF([QPointF(tx, ty + 12), QPointF(tx + 14, ty + 12), QPointF(tx + 7, ty)]))
        p.end()


class SplitHandle(QWidget):
    """Camouflaged divider between the trace screen and the on-screen menu.

    It continues the dark bezel and the screen (grey frame line, black inside), so only
    its blue arrow stands out: ▶ = click (on the arrow or anywhere on the rail) to fold
    the menu and the control panel (the traces then fill the window), ◀ = click to bring
    them back.
    """
    clicked = pyqtSignal()

    BEZEL = QColor("#1d1f23")     # #screenBezel background (QSS)
    BEZEL_R = 10                  # #screenBezel border radius (QSS)
    FRAME = QColor("#4b5563")     # #screen border colour (QSS)
    FRAME_W = 2                   # #screen border width (QSS)
    BAND = QColor("#000000")      # #screen background (QSS)
    ARROW = QColor("#3b7ddd")     # the panel's own blue (key hover border)

    def __init__(self, parent=None, width=8):
        super().__init__(parent)
        self._w = width
        self.setFixedWidth(width)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # keys keep going to the main window
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapsed = False
        self._pressed = False
        self._bezel = None      # bezel half on the left of the rail
        self._screen = None     # black screen frame inside it
        self._edges = None      # overlay that hides the cut on the neighbours' side
        self._update_tip()

    def sizeHint(self):
        return QSize(self._w, 200)

    def _neighbours(self):
        """The bezel half just left of the rail (#screenBezel) and the screen frame
        inside it (#screen); found once, then followed when they move or resize."""
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

    def zones(self):
        """(bezel top, bezel height, screen top, screen height) in rail coordinates,
        or None when folded (then only the arrow is drawn)."""
        if self.collapsed:
            return None
        bezel, screen = self._neighbours()
        if bezel is None or screen is None or screen.height() <= 0:
            return None
        win = self.window()
        y0 = self.mapTo(win, QPoint(0, 0)).y()
        return (bezel.mapTo(win, QPoint(0, 0)).y() - y0, bezel.height(),
                screen.mapTo(win, QPoint(0, 0)).y() - y0, screen.height())

    # the overlay covers BEZEL_R px on each side of the rail, above the two bezel halves
    def _sync_edges(self):
        parent = self.parentWidget()
        if parent is None:
            return
        if self._edges is None or self._edges.parentWidget() is not parent:
            self._edges = _RailEdges(self, parent)
        r = self.BEZEL_R
        self._edges.setGeometry(self.geometry().adjusted(-r, 0, r, 0))
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
        w = self.width()
        z = self.zones()
        if z is not None:
            btop, bh, stop, sh = z
            fw = self.FRAME_W
            p.fillRect(QRectF(0, btop, w, bh), self.BEZEL)                  # bezel between the halves
            p.fillRect(QRectF(0, stop, w, sh), self.FRAME)                  # screen frame line (top/bottom)
            p.fillRect(QRectF(0, stop + fw, w, sh - 2 * fw), self.BAND)     # black screen inside

        # blue arrow inside the rail, vertically centred (7 x 12 px in an 8 px rail)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
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
    """Overlay of SplitHandle, above the two bezel halves: squares their rounded corners
    at the cut and blackens the screen frame line on each side of the rail, so the bezel
    and the screen look continuous. Paints nothing over the rail itself; ignores the mouse."""

    def __init__(self, rail, parent):
        super().__init__(parent)
        self.rail = rail
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, e):
        z = self.rail.zones()                 # same y origin as the rail
        if z is None:
            return
        btop, bh, stop, sh = z
        R, w, fw = SplitHandle.BEZEL_R, self.rail.width(), SplitHandle.FRAME_W
        p = QPainter(self)
        for x0 in (0, R + w):                 # left strip, right strip
            p.fillRect(QRectF(x0, btop, R, R), SplitHandle.BEZEL)              # top corner
            p.fillRect(QRectF(x0, btop + bh - R, R, R), SplitHandle.BEZEL)     # bottom corner
        p.fillRect(QRectF(R - fw, stop + fw, fw, sh - 2 * fw), SplitHandle.BAND)   # left frame line
        p.fillRect(QRectF(R + w, stop + fw, fw, sh - 2 * fw), SplitHandle.BAND)    # right frame line
        p.end()
