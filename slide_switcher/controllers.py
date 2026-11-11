from __future__ import annotations

import importlib
from pathlib import Path
import sys
import threading
from typing import Protocol


class SlideController(Protocol):
    """1-based indices; read the actual presentation before every mutation."""
    count: int
    requires_presenter: bool
    def current_slide(self) -> int: ...
    def goto(self, slide: int) -> int: ...
    def advance_if_current(self, expected: int) -> bool: ...


class BrowserController:
    requires_presenter = True

    def __init__(self, count: int):
        self.count, self._slide = count, 1
        self._lock = threading.RLock()

    def current_slide(self):
        with self._lock:
            return self._slide

    def goto(self, slide):
        with self._lock:
            if type(slide) is not int or not 1 <= slide <= self.count:
                raise ValueError("Slide number is outside the presentation.")
            self._slide = slide
            return slide

    def advance_if_current(self, expected):
        with self._lock:
            if self._slide != expected or expected >= self.count:
                return False
            self._slide += 1
            return True


class PowerPointController:
    """Windows desktop adapter using the documented PowerPoint COM object model.

    Requires PowerPoint + pywin32. Explicit goto avoids consuming animations
    instead of changing slides. Not a claim of native PowerPoint support on macOS.
    """
    requires_presenter = False

    def __init__(self, source: Path, count: int):
        if sys.platform != "win32":
            raise RuntimeError("Native PowerPoint control requires Windows, PowerPoint, and pywin32. Use Browser presenter on this Mac.")
        self.source, self.count = source.resolve(), count
        self._lock = threading.RLock()
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            app = win32com.client.Dispatch("PowerPoint.Application")
            app.Visible = True
            presentation = app.Presentations.Open(str(self.source), ReadOnly=True, WithWindow=True)
            presentation.SlideShowSettings.Run()
        finally:
            pythoncom.CoUninitialize()

    def _operation(self, target=None, expected=None):
        import pythoncom
        import win32com.client
        with self._lock:
            pythoncom.CoInitialize()
            try:
                app = win32com.client.GetActiveObject("PowerPoint.Application")
                view = None
                for i in range(1, app.SlideShowWindows.Count + 1):
                    window = app.SlideShowWindows(i)
                    if Path(window.Presentation.FullName).resolve() == self.source:
                        view = window.View
                        break
                if view is None:
                    raise RuntimeError("The selected PowerPoint slideshow is not running.")
                current = int(view.Slide.SlideIndex)
                if expected is not None and current != expected:
                    return False
                if target is not None:
                    view.GotoSlide(target)
                    current = int(view.Slide.SlideIndex)
                return current if expected is None else current == target
            finally:
                pythoncom.CoUninitialize()

    def current_slide(self):
        return self._operation()

    def goto(self, slide):
        if type(slide) is not int or not 1 <= slide <= self.count:
            raise ValueError("Invalid slide number.")
        return self._operation(target=slide)

    def advance_if_current(self, expected):
        return expected < self.count and self._operation(target=expected + 1, expected=expected)


def load_plugin(spec: str, deck):
    """Optional adapter for an existing controller: module:factory(deck) -> protocol."""
    module, name = spec.rsplit(":", 1)
    controller = getattr(importlib.import_module(module), name)(deck)
    for attr in ("count", "requires_presenter", "current_slide", "goto", "advance_if_current"):
        if not hasattr(controller, attr):
            raise ValueError(f"Controller plugin is missing {attr}.")
    if controller.count != len(deck.slides):
        raise ValueError("Controller slide count differs from the uploaded deck.")
    return controller
