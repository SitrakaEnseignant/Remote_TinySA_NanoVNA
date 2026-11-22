"""Headless stand-ins for PyQt6 (QtCore / QtGui / QtWidgets) and pyqtgraph, so the REAL vna_panel.py
code runs without a display.  Widgets are inert; signals are queued and delivered by pump() on the
calling thread, like Qt's queued connections (sender() is set during delivery).
Dialogs answer from DIALOG: {"save": path, "open": path, "int": n, "text": str}.
"""
import collections
import sys
import threading
import types

EVENTS = collections.deque()
SENDER = [None]
DIALOG = {"save": "", "open": "", "int": 0, "text": ""}


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Dummy()


class Dummy(metaclass=_Meta):
    """Accepts any constructor, attribute, call, operator; returns more Dummies."""
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        d = Dummy()
        object.__setattr__(self, name, d)
        return d

    def __call__(self, *a, **k):
        return Dummy()

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return True

    def __int__(self):
        return 0

    def __index__(self):
        return 0

    def __float__(self):
        return 0.0

    def __getitem__(self, k):
        return Dummy()

    def __setitem__(self, k, v):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _op(self, *a):
        return Dummy()

    __or__ = __ror__ = __and__ = __rand__ = __xor__ = __add__ = __radd__ = __sub__ = __rsub__ = _op
    __mul__ = __rmul__ = __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = __neg__ = _op
    __mod__ = __rmod__ = __invert__ = __pos__ = __abs__ = _op

    def __lt__(self, o):
        return False

    __le__ = __gt__ = __ge__ = __lt__

    def __eq__(self, o):
        return self is o

    def __hash__(self):
        return id(self)


class _Sig:
    def __init__(self, owner):
        self.owner, self.cbs = owner, []

    def connect(self, cb):
        self.cbs.append(cb)

    def disconnect(self, *a):
        self.cbs.clear()

    def emit(self, *a):
        EVENTS.append((self.owner, list(self.cbs), a))


class pyqtSignal:
    def __init__(self, *t):
        pass

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        d = obj.__dict__
        if self.name not in d:
            d[self.name] = _Sig(obj)
        return d[self.name]


def pump():
    """Deliver queued signals; returns how many were delivered."""
    n = 0
    while EVENTS:
        owner, cbs, a = EVENTS.popleft()
        SENDER[0] = owner
        try:
            for cb in cbs:
                cb(*a)
        finally:
            SENDER[0] = None
        n += 1
    return n


class QThread:
    def __init__(self, *a):
        self._t = None

    def start(self):
        self._t = threading.Thread(target=self.run, daemon=True)
        self._t.start()

    def wait(self, ms=None):
        if self._t:
            self._t.join(ms / 1000 if ms else None)
        return True

    def isRunning(self):
        return bool(self._t and self._t.is_alive())

    def deleteLater(self):
        pass


class QObjectBase(Dummy):
    def sender(self):
        return SENDER[0]


class QFileDialog(Dummy):
    @staticmethod
    def getSaveFileName(*a, **k):
        return DIALOG["save"], ""

    @staticmethod
    def getOpenFileName(*a, **k):
        return DIALOG["open"], ""


class QInputDialog(Dummy):
    @staticmethod
    def getInt(*a, **k):
        return DIALOG["int"], True

    @staticmethod
    def getText(*a, **k):
        return DIALOG["text"], True


class QMessageBox(Dummy):
    @staticmethod
    def information(*a, **k):
        return None

    @staticmethod
    def warning(*a, **k):
        return None


def _module(name, explicit):
    m = types.ModuleType(name)
    m.__dict__.update(explicit)
    cache = {}

    def __getattr__(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        if attr not in cache:
            cache[attr] = type(attr, (QObjectBase,), {})
        return cache[attr]
    m.__getattr__ = __getattr__
    return m


def install():
    qtcore = _module("PyQt6.QtCore", dict(pyqtSignal=pyqtSignal, QThread=QThread))
    qtgui = _module("PyQt6.QtGui", {})
    qtwidgets = _module("PyQt6.QtWidgets", dict(QFileDialog=QFileDialog, QInputDialog=QInputDialog,
                                                 QMessageBox=QMessageBox))
    pyqt6 = types.ModuleType("PyQt6")
    pyqt6.QtCore, pyqt6.QtGui, pyqt6.QtWidgets = qtcore, qtgui, qtwidgets
    pg = _module("pyqtgraph", {})
    sys.modules.update({"PyQt6": pyqt6, "PyQt6.QtCore": qtcore, "PyQt6.QtGui": qtgui,
                        "PyQt6.QtWidgets": qtwidgets, "pyqtgraph": pg})
