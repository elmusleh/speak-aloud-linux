#!/usr/bin/env python3
"""Get selected text from the focused widget via AT-SPI accessibility API.

Usage: get-selected-text.py
Prints the currently selected text to stdout (no trailing newline).
Exits 0 on success, 1 on no selection found, 2 on timeout/error.

This bypasses the clipboard and X11 selection, working with Flatpak apps
like OnlyOffice where xdotool cannot inject synthetic keystrokes.
"""
import signal
import sys

def _timeout_handler(signum, frame):
    sys.exit(2)

# Set a 3-second safety timeout so this script can never hang the caller.
signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(3)

try:
    import pyatspi
except ImportError:
    sys.exit(2)


def _find_selected_text(obj, depth=0):
    """Recursively search for a widget with text selections."""
    if depth > 40:
        return None
    try:
        state = obj.getState()
        # Focused or active widgets are most likely to have the user's selection
        if state.contains(pyatspi.STATE_FOCUSED) or state.contains(pyatspi.STATE_ACTIVE):
            try:
                text_iface = obj.queryText()
                n = text_iface.getNSelections()
                if n > 0:
                    start, end = text_iface.getSelection(0)
                    return text_iface.getText(start, end)
            except Exception:
                pass
    except Exception:
        pass

    try:
        for i in range(obj.getChildCount()):
            result = _find_selected_text(obj.getChildAtIndex(i), depth + 1)
            if result is not None:
                return result
    except Exception:
        pass
    return None


def main():
    desktop = pyatspi.Registry.getDesktop(0)
    for i in range(desktop.getChildCount()):
        result = _find_selected_text(desktop.getChildAtIndex(i))
        if result is not None:
            sys.stdout.write(result)
            sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
