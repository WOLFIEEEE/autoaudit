"""Tests for OS-level keyboard injection.

The point of audit/_win_input is that browse-mode keys must traverse
NVDA's low-level keyboard hook, which CDP-injected keys never do. These
tests verify the platform contract on every OS and, on Windows, that the
ctypes structures are actually well-formed.
"""

from __future__ import annotations

import ctypes
import platform

import pytest

from audit import _win_input

IS_WINDOWS = platform.system() == "Windows"


def test_available_matches_platform():
    assert _win_input.available() is IS_WINDOWS


@pytest.mark.skipif(IS_WINDOWS, reason="non-Windows fallback behaviour")
def test_send_key_is_a_safe_noop_off_windows():
    """Callers rely on a False return to fall back to CDP and then treat
    NVDA silence as "not evaluated" rather than as a finding."""
    assert _win_input.send_key(_win_input.VK_DOWN) is False
    assert _win_input.send_key(_win_input.VK_HOME) is False


def test_navigation_keys_are_marked_extended():
    """Windows distinguishes the gray navigation cluster from the numpad
    via the extended-key flag, and NVDA's hook reads it. Arrow keys sent
    without the flag can be taken for their numpad twins."""
    for vk in (
        _win_input.VK_UP,
        _win_input.VK_DOWN,
        _win_input.VK_LEFT,
        _win_input.VK_RIGHT,
        _win_input.VK_HOME,
        _win_input.VK_END,
    ):
        assert vk in _win_input._EXTENDED_KEYS

    # Tab is NOT an extended key — flagging it would be wrong.
    assert _win_input.VK_TAB not in _win_input._EXTENDED_KEYS


def test_virtual_key_codes_match_the_windows_api():
    # From WinUser.h. Wrong codes fail silently (NVDA hears a different
    # key), so pin them.
    assert _win_input.VK_TAB == 0x09
    assert _win_input.VK_END == 0x23
    assert _win_input.VK_HOME == 0x24
    assert _win_input.VK_LEFT == 0x25
    assert _win_input.VK_UP == 0x26
    assert _win_input.VK_RIGHT == 0x27
    assert _win_input.VK_DOWN == 0x28


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only ctypes structures")
def test_input_struct_is_sized_against_the_largest_union_member():
    """SendInput rejects a struct whose cbSize is wrong. A KEYBDINPUT-only
    union is smaller than MOUSEINPUT and silently breaks injection."""
    size = ctypes.sizeof(_win_input._INPUT)
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert size == expected, f"INPUT is {size} bytes, expected {expected}"
    assert ctypes.sizeof(_win_input._MOUSEINPUT) >= ctypes.sizeof(
        _win_input._KEYBDINPUT
    )
