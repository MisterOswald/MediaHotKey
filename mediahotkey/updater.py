"""
Self-updater: pull the latest MediaHotKey from GitHub and replace the app
files in place.

Strategy (source installs, i.e. running the .py/.pyw):
  * "latest" = the HEAD commit sha of the repo's default branch (GitHub API).
  * "current" = the sha recorded the last time we updated (stored next to the
    app). On first run we record the remote sha and assume we're current.
  * Updating downloads the branch zip from codeload.github.com, extracts it,
    and copies the files over the install folder. User data lives in
    %APPDATA%\\MediaHotKey (not the repo folder), so it's never touched.

Frozen .exe builds can't rewrite their own running binary, so auto-update is
reported as unsupported there (rebuild with build_exe.bat instead).
"""

import os
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
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
UA = "MediaHotKey-Updater"

# Never overwrite/copy these (version control + local-only state).
SKIP_TOP = {".git", ".github", ".mediahotkey_update.json", "config.json",
            ".spotify_token_cache"}


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


def check():
    """Return {available, remote, current, first_run?, error?}."""
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


def apply_update(progress=lambda m: None):
    """Download + install the latest version. Returns (ok, message)."""
    if is_frozen():
        return False, ("Auto-update isn't supported for the .exe build — "
                       "rebuild with build_exe.bat to update.")
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


def relaunch():
    """Start a fresh copy of the app and return (the caller should then quit)."""
    try:
        if is_frozen():
            subprocess.Popen([sys.executable], cwd=install_dir(), close_fds=True)
        else:
            run_py = os.path.join(install_dir(), "run.py")
            subprocess.Popen([sys.executable, run_py], cwd=install_dir(),
                             close_fds=True)
        return True
    except Exception:  # noqa: BLE001
        return False
