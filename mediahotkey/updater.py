"""
Self-updater.

Source installs (running the .py/.pyw):
  * "latest" = HEAD commit sha of the default branch (GitHub API).
  * Updating downloads the branch zip from codeload, extracts it, and copies the
    files over the install folder. User data in %APPDATA% is never touched.

Frozen .exe builds:
  * GitHub Actions builds MediaHotKey.exe on each push and publishes it as a
    release asset (tag v<version>).
  * "latest" = the newest release's tag vs the running __version__.
  * Updating downloads the new MediaHotKey.exe next to the running one, then on
    restart a tiny detached batch waits for this process to exit, swaps the exe
    in, and relaunches (you can't overwrite a running .exe directly).
"""

import os
import re
import sys
import json
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request

REPO = "MisterOswald/MediaHotKey"
BRANCH = "main"
API_COMMITS = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
API_LATEST_RELEASE = f"https://api.github.com/repos/{REPO}/releases/latest"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
EXE_DOWNLOAD = f"https://github.com/{REPO}/releases/latest/download/MediaHotKey.exe"
UA = "MediaHotKey-Updater"

# Never overwrite/copy these (version control + local-only state).
SKIP_TOP = {".git", ".github", ".mediahotkey_update.json", "config.json",
            ".spotify_token_cache"}

_pending_exe = None  # (downloaded_path, current_exe_path) awaiting a restart swap


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def install_dir():
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _state_path():
    return os.path.join(install_dir(), ".mediahotkey_update.json")


def _load_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_state(sha):
    try:
        with open(_state_path(), "w", encoding="utf-8") as fh:
            json.dump({"sha": sha}, fh)
    except OSError:
        pass


def current_sha():
    return _load_state().get("sha")


def remote_sha(timeout=10):
    req = urllib.request.Request(
        API_COMMITS,
        headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("sha")


def _ver_tuple(s):
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def latest_release(timeout=10):
    req = urllib.request.Request(
        API_LATEST_RELEASE,
        headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    assets = {a.get("name"): a.get("browser_download_url")
              for a in data.get("assets", [])}
    return {"tag": data.get("tag_name") or data.get("name") or "", "assets": assets}


def check():
    """Return {available, remote, current, first_run?, error?}."""
    if is_frozen():
        from . import __version__
        try:
            rel = latest_release()
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": str(exc)}
        latest = rel.get("tag", "")
        available = _ver_tuple(latest) > _ver_tuple(__version__)
        return {"available": available, "remote": latest, "current": __version__}

    try:
        rsha = remote_sha()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}
    if not rsha:
        return {"available": False, "error": "no sha from GitHub"}
    csha = current_sha()
    if csha is None:
        _save_state(rsha)   # first run → assume up to date going forward
        return {"available": False, "remote": rsha, "current": rsha,
                "first_run": True}
    return {"available": rsha != csha, "remote": rsha, "current": csha}


def _copy_tree(src_root, dst_root):
    for dirpath, dirnames, filenames in os.walk(src_root):
        rel = os.path.relpath(dirpath, src_root)
        top = (rel.split(os.sep)[0] if rel != "." else "")
        if top in SKIP_TOP:
            dirnames[:] = []
            continue
        target_dir = dst_root if rel == "." else os.path.join(dst_root, rel)
        os.makedirs(target_dir, exist_ok=True)
        for name in filenames:
            if rel == "." and name in SKIP_TOP:
                continue
            shutil.copy2(os.path.join(dirpath, name), os.path.join(target_dir, name))


def _apply_update_frozen(progress):
    """Download the new MediaHotKey.exe next to the running one; the actual
    swap happens on restart (see relaunch)."""
    global _pending_exe
    cur = sys.executable
    new = cur + ".new"
    progress("Downloading the new MediaHotKey.exe…")
    try:
        req = urllib.request.Request(EXE_DOWNLOAD, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=600) as r, open(new, "wb") as fh:
            shutil.copyfileobj(r, fh)
    except Exception as exc:  # noqa: BLE001
        return False, f"Download failed: {exc}"
    if os.path.getsize(new) < 1_000_000:   # a real exe is tens of MB
        try:
            os.remove(new)
        except OSError:
            pass
        return False, ("Couldn't get the new .exe — has a release been published "
                       "yet? (GitHub Actions builds it on push.)")
    _pending_exe = (new, cur)
    return True, "Update downloaded. Click Restart to apply."


def apply_update(progress=lambda m: None):
    """Download + install the latest version. Returns (ok, message)."""
    if is_frozen():
        return _apply_update_frozen(progress)

    # The API (sha) may be rate-limited/blocked even when the zip host works,
    # so don't hard-depend on it — install from codeload regardless.
    try:
        rsha = remote_sha()
    except Exception:  # noqa: BLE001
        rsha = None

    tmp = tempfile.mkdtemp(prefix="mhk_update_")
    try:
        progress("Downloading latest version…")
        zip_path = os.path.join(tmp, "src.zip")
        req = urllib.request.Request(ZIP_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(zip_path, "wb") as fh:
            shutil.copyfileobj(r, fh)

        progress("Extracting…")
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        roots = [d for d in os.listdir(tmp)
                 if os.path.isdir(os.path.join(tmp, d)) and d != "__MACOSX"]
        if not roots:
            return False, "Downloaded archive looked empty."
        src_root = os.path.join(tmp, roots[0])

        progress("Installing files…")
        _copy_tree(src_root, install_dir())
        if rsha:
            _save_state(rsha)
        return True, "Update installed. Restart to apply."
    except Exception as exc:  # noqa: BLE001
        return False, f"Update failed: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _spawn_swap(new, cur):
    """Detached batch: wait for this process to exit, swap the exe, relaunch."""
    pid = os.getpid()
    bat = cur + ".update.bat"
    script = (
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /fi "PID eq {pid}" | find "{pid}" >nul\r\n'
        "if not errorlevel 1 (\r\n"
        "  ping 127.0.0.1 -n 2 >nul\r\n"
        "  goto wait\r\n"
        ")\r\n"
        f'move /y "{new}" "{cur}" >nul\r\n'
        f'start "" "{cur}"\r\n'
        'del "%~f0"\r\n'
    )
    with open(bat, "w", encoding="utf-8") as fh:
        fh.write(script)
    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008
    subprocess.Popen(["cmd", "/c", bat], close_fds=True,
                     creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS)
    return True


def relaunch():
    """Start a fresh copy of the app (the caller should then quit). For a frozen
    build with a downloaded update pending, hand off to the swap batch instead."""
    try:
        if is_frozen():
            if _pending_exe:
                return _spawn_swap(*_pending_exe)
            subprocess.Popen([sys.executable], cwd=install_dir(), close_fds=True)
        else:
            run_py = os.path.join(install_dir(), "run.py")
            subprocess.Popen([sys.executable, run_py], cwd=install_dir(),
                             close_fds=True)
        return True
    except Exception:  # noqa: BLE001
        return False
