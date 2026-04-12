"""macOS LaunchAgent 등록/해제를 위한 유틸리티."""

import plistlib
import shutil
import subprocess
from pathlib import Path


LABEL = "com.dysim.mycron"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _find_mycron_bin() -> str:
    path = shutil.which("mycron")
    if path:
        return path
    raise FileNotFoundError(
        "mycron 바이너리를 찾을 수 없습니다. pipx install 후 다시 시도하세요."
    )


def _build_plist(bin_path: str) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [bin_path, "start", "--foreground"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(Path.home() / ".mycron" / "launchd-stdout.log"),
        "StandardErrorPath": str(Path.home() / ".mycron" / "launchd-stderr.log"),
    }


def install() -> None:
    if PLIST_PATH.exists():
        print(f"이미 설치되어 있습니다: {PLIST_PATH}")
        print("재설치하려면 먼저 'mycron uninstall'을 실행하세요.")
        return

    bin_path = _find_mycron_bin()
    plist = _build_plist(bin_path)

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)

    subprocess.run(["launchctl", "load", str(PLIST_PATH)], check=True)
    print(f"LaunchAgent 등록 완료: {PLIST_PATH}")
    print(f"바이너리: {bin_path}")
    print("시스템 재시작 후에도 자동으로 실행됩니다.")


def uninstall() -> None:
    if not PLIST_PATH.exists():
        print("설치된 LaunchAgent가 없습니다.")
        return

    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], check=True)
    PLIST_PATH.unlink()
    print(f"LaunchAgent 해제 완료: {PLIST_PATH}")
