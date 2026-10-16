"""Read a PNG image from the system clipboard when available."""

from __future__ import annotations

import base64
import re
import subprocess
import sys


def read_image() -> bytes | None:
    if sys.platform == "win32":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "Add-Type -AssemblyName System.Drawing; "
            "$image = [System.Windows.Forms.Clipboard]::GetImage(); "
            "if ($image) { $stream = New-Object System.IO.MemoryStream; "
            "$image.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png); "
            "[Convert]::ToBase64String($stream.ToArray()) }"
        )
        command = ["powershell", "-NoProfile", "-STA", "-Command", script]
    elif sys.platform == "darwin":
        command = ["osascript", "-e", "set theImage to the clipboard as «class PNGf»", "-e", "return theImage"]
    else:
        command = ["wl-paste", "--no-newline", "--type", "image/png"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode or not result.stdout:
        return None
    if sys.platform == "win32":
        try:
            return base64.b64decode(result.stdout.strip(), validate=True)
        except ValueError:
            return None
    if sys.platform == "darwin":
        match = re.search(rb"PNGf([0-9A-Fa-f]+)", result.stdout)
        return bytes.fromhex(match.group(1).decode()) if match else None
    return result.stdout
