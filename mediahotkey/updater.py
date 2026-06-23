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
    swap happens on restart (see relaunch). Only proceeds if the latest release
    is actually newer than what's running."""
    global _pending_exe
    from . import __version__
    try:
        rel = latest_release()
    except Exception as exc:  # noqa: BLE001
        return False, f"Couldn't reach GitHub: {exc}"
    tag = rel.get("tag", "")
    if _ver_tuple(tag) <= _ver_tuple(__version__):
        return False, f"You're already on the latest version ({__version__})."
    url = rel.get("assets", {}).get("MediaHotKey.exe") or EXE_DOWNLOAD

    cur = sys.executable
    new = cur + ".new"
    progress(f"Downloading {tag}…")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=600) as r, open(new, "wb") as fh:
            shutil.copyfileobj(r, fh)
    except Exception as exc:  # noqa: BLE001
        return False, f"Download failed: {exc}"
    if os.path.getsize(new) < 1_000_000:   # a real exe is tens of MB
        try:
            os.remove(new)
        except OSError:
            pass
        return False, "Couldn't get the new .exe — try again in a minute."
    _pending_exe = (new, cur)
    return True, f"Update to {tag} downloaded. Click Restart to apply."


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


def cleanup_stale():
    """Remove leftovers from older/failed update attempts (called at startup)."""
    if not is_frozen():
        return
    cur = sys.executable
    for suffix in (".old", ".new", ".update.bat"):
        p = cur + suffix
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _child_env():
    """Environment for relaunching a frozen build. PyInstaller's onefile
    bootloader passes _MEIPASS2 (and friends) to child processes so they reuse
    the parent's extracted temp dir — but the parent deletes that dir on exit,
    leaving the child with missing files (404) and a 'failed to remove temp
    directory' warning. Strip them so the new exe extracts its own copy."""
    env = os.environ.copy()
    for k in ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE",
              "_PYIBoot_SPLASH", "_MEIPASS"):
        env.pop(k, None)
    return env


def _swap_and_launch(new, cur):
    """Swap a freshly downloaded exe in WITHOUT a helper batch.

    Windows won't let you delete/overwrite a running .exe, but it *does* let you
    rename it. So: move the running exe aside (.old), move the new exe into its
    place, then launch it. The .old file is cleaned up on the next start."""
    old = cur + ".old"
    try:
        if os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass
        os.rename(cur, old)          # allowed even while running
    except Exception:  # noqa: BLE001
        return False
    try:
        os.replace(new, cur)         # put the new exe at the original path
    except Exception:  # noqa: BLE001
        try:
            os.rename(old, cur)      # roll back
        except OSError:
            pass
        return False
    try:
        subprocess.Popen([cur], cwd=os.path.dirname(cur), close_fds=True,
                         env=_child_env())
    except Exception:  # noqa: BLE001
        return False
    return True


def relaunch():
    """Start a fresh copy of the app (the caller should then quit). For a frozen
    build with a downloaded update pending, swap the exe in first."""
    try:
        if is_frozen():
            if _pending_exe:
                return _swap_and_launch(*_pending_exe)
            subprocess.Popen([sys.executable], cwd=install_dir(), close_fds=True,
                             env=_child_env())
        else:
            run_py = os.path.join(install_dir(), "run.py")
            subprocess.Popen([sys.executable, run_py], cwd=install_dir(),
                             close_fds=True)
        return True
    except Exception:  # noqa: BLE001
        return False
