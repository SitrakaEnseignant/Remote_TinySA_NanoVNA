"""Minimal PyQt6.QtCore stand-in (QThread + pyqtSignal) so vna_device can be tested without Qt."""
import sys
import threading
import types


class _Sig:
    def __init__(self):
        self.cbs = []

    def connect(self, cb):
        self.cbs.append(cb)

    def emit(self, *a):
        for cb in list(self.cbs):
            cb(*a)


class pyqtSignal:
    def __init__(self, *t):
        pass

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        return self if obj is None else obj.__dict__.setdefault(self.name, _Sig())


class QThread:
    def start(self):
        self._t = threading.Thread(target=self.run, daemon=True)
        self._t.start()

    def wait(self, ms=None):
        self._t.join(ms / 1000 if ms else None)


def install_qt_stub():
    qtcore = types.ModuleType("PyQt6.QtCore")
    qtcore.QThread, qtcore.pyqtSignal = QThread, pyqtSignal
    pyqt6 = types.ModuleType("PyQt6")
    pyqt6.QtCore = qtcore
    sys.modules["PyQt6"], sys.modules["PyQt6.QtCore"] = pyqt6, qtcore
