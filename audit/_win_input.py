"""OS-level keyboard injection for Windows (SendInput).

Why this exists
---------------
Playwright's ``page.keyboard.press()`` delivers keystrokes over CDP,
straight into Blink's input pipeline. That is fine for anything the
*browser* handles — Tab moves focus, which fires a real accessibility
event that NVDA picks up through IAccessible2/UIA regardless of how the
key was injected.

It is **not** fine for anything NVDA itself handles. NVDA installs a
low-level Windows keyboard hook (``WH_KEYBOARD_LL``) and intercepts keys
before the focused application sees them. Browse-mode navigation — Up,
Down, Home, H for next heading — is implemented entirely in that hook.
CDP-injected keys never traverse the hook chain, so NVDA never learns a
key was pressed: Blink just scrolls the viewport and NVDA stays silent.

``SendInput`` posts into the OS input queue, which *does* run the hook
chain, so NVDA sees these keys exactly as it sees a human's.

Caveats
-------
- Input goes to whatever window is **foreground**, not to a specific
  page. The caller must ensure the browser window is focused; this is
  already a standing requirement of the NVDA worker (NVDA only reads the
  focused window), but it means no other app may steal focus mid-walk.
- Windows only. ``available()`` reports whether injection is usable, and
  every entry point degrades to a no-op returning False elsewhere so
  callers can fall back to CDP.
"""

from __future__ import annotations

import ctypes
import logging
import platform

log = logging.getLogger(__name__)

# Virtual-key codes we need for browse-mode navigation.
VK_HOME = 0x24
VK_END = 0x23
VK_UP = 0x26
VK_DOWN = 0x28
VK_LEFT = 0x25
VK_RIGHT = 0x27
VK_TAB = 0x09

_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002

# Navigation keys live on the "gray" pad, which Windows distinguishes
# from their numpad twins via the extended-key flag. NVDA's hook reads
# that flag; without it a Down-arrow can be taken for numpad-2.
_EXTENDED_KEYS = frozenset(
    {VK_HOME, VK_END, VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT}
)

_IS_WINDOWS = platform.system() == "Windows"

if _IS_WINDOWS:  # pragma: no cover - Windows-only structures
    from ctypes import wintypes

    _ULONG_PTR = (
        ctypes.c_ulonglong
        if ctypes.sizeof(ctypes.c_void_p) == 8
        else ctypes.c_ulong
    )

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            # All three members must be declared so the union is sized
            # against the largest (MOUSEINPUT) — a KEYBDINPUT-only union
            # is too small and SendInput rejects the struct size.
            _fields_ = [
                ("ki", _KEYBDINPUT),
                ("mi", _MOUSEINPUT),
                ("hi", _HARDWAREINPUT),
            ]

        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (
        wintypes.UINT,
        ctypes.POINTER(_INPUT),
        ctypes.c_int,
    )
    _user32.SendInput.restype = wintypes.UINT


def available() -> bool:
    """True when OS-level key injection can be used on this host."""
    return _IS_WINDOWS


def send_key(vk: int) -> bool:
    """Press and release one virtual key through the OS input queue.

    Returns True when both events were accepted. A False return means
    the caller should fall back to CDP and treat any resulting NVDA
    silence as "not evaluated" rather than as a finding.
    """
    if not _IS_WINDOWS:
        return False

    flags = _KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED_KEYS else 0
    down = _INPUT(type=_INPUT_KEYBOARD, ki=_KEYBDINPUT(wVk=vk, dwFlags=flags))
    up = _INPUT(
        type=_INPUT_KEYBOARD,
        ki=_KEYBDINPUT(wVk=vk, dwFlags=flags | _KEYEVENTF_KEYUP),
    )
    events = (_INPUT * 2)(down, up)

    sent = _user32.SendInput(2, events, ctypes.sizeof(_INPUT))
    if sent != 2:
        # Most commonly UIPI: a process at a higher integrity level owns
        # the foreground window and blocks synthetic input into it.
        log.warning(
            "SendInput delivered %d/2 events for vk=0x%02X (winerror=%d)",
            sent, vk, ctypes.get_last_error(),
        )
        return False
    return True
