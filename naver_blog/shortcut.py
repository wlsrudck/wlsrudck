"""바탕화면에 '클립 올리기 (블로그 이름)' 바로가기를 만든다 (윈도우만, 이미 있으면 그대로).

프로그램 창을 열 때와 클립을 만들 때 부른다. 바로가기는 이 블로그 폴더 안의 '클립_올리기' 폴더를 연다.
"""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
UPLOAD = ROOT / "클립_올리기"


def ensure_clip_shortcut(blog_name: str = "") -> str:
    """만들었으면 바로가기 이름, 아니면 빈 문자열"""
    UPLOAD.mkdir(exist_ok=True)
    if os.name != "nt":
        return ""
    name = re.sub(r'[\\/:*?"<>|]', "", f"클립 올리기 ({blog_name or ROOT.name})")
    ps = ("$d=[Environment]::GetFolderPath('Desktop'); $p=Join-Path $d ($env:LNK_NAME + '.lnk');"
          "if (-not (Test-Path $p)) { $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
          "$s.TargetPath=$env:LNK_TARGET; $s.Save(); 'made' }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30,
                           env={**os.environ, "LNK_NAME": name, "LNK_TARGET": str(UPLOAD)},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return name if "made" in (r.stdout or "") else ""
    except Exception:
        return ""
