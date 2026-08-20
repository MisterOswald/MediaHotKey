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
import time
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
ZIP_ASSET = "MediaHotKey-win64.zip"   # the fast-launch folder build
UA = "MediaHotKey-Updater"

# Never overwrite/copy these (version control + local-only state).
SKIP_TOP = {".git", ".github", ".mediahotkey_update.json", "config.json",
            ".spotify_token_cache"}

_pending_exe = None  # (downloaded_path, current_exe_path) awaiting a restart swap
_pending_dir = None  # staged folder-build awaiting a restart swap


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def is_onedir():
    """True when running the fast-launch folder build (exe + _internal\\)."""
    return is_frozen() and os.path.isdir(
        os.path.join(os.path.dirname(sys.executable), "_internal"))


def app_home():
    """Per-user install location for the folder build."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "MediaHotKey", "app")


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


# ---------------------------------------------------------------- folder build
def _download(url, dest, timeout=600):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as fh:
        shutil.copyfileobj(r, fh)


def _find_app_root(directory):
    """The folder inside an extracted zip that holds MediaHotKey.exe — the
    directory itself, or its single subfolder (defensive against a zip with a
    wrapping top-level folder)."""
    if os.path.exists(os.path.join(directory, "MediaHotKey.exe")):
        return directory
    entries = [e for e in os.listdir(directory)
               if os.path.isdir(os.path.join(directory, e))]
    if len(entries) == 1:
        sub = os.path.join(directory, entries[0])
        if os.path.exists(os.path.join(sub, "MediaHotKey.exe")):
            return sub
    return None


def _fetch_zip_to(url, staging, progress):
    """Download + extract the folder-build zip into `staging`; return the app
    root inside it, or None."""
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    pkg = os.path.join(staging, "pkg.zip")
    progress("downloading the fast-launch folder version…")
    _download(url, pkg)
    progress("unpacking…")
    with zipfile.ZipFile(pkg) as z:
        z.extractall(staging)
    os.remove(pkg)
    return _find_app_root(staging)


def migrate_to_folder(progress=lambda m: None):
    """One-file exe → folder build, installed to app_home(). Requires the
    running version's release to carry the zip asset. Returns
    (ok, message, new_exe_path_or_None); the caller then launch_migrated()s."""
    from . import __version__
    if not is_frozen() or is_onedir():
        return False, "already the folder build", None
    try:
        rel = latest_release()
    except Exception as exc:  # noqa: BLE001
        return False, f"couldn't reach GitHub: {exc}", None
    if _ver_tuple(rel.get("tag", "")) != _ver_tuple(__version__):
        # Never migrate across versions — the normal exe update runs first.
        return False, "no folder build published for this exact version", None
    url = rel.get("assets", {}).get(ZIP_ASSET)
    if not url:
        return False, "this release has no folder-build asset", None
    home = app_home()
    staging = home + ".new"
    try:
        root = _fetch_zip_to(url, staging, progress)
        if root is None:
            shutil.rmtree(staging, ignore_errors=True)
            return False, "downloaded archive didn't contain MediaHotKey.exe", None
        old = home + ".old"
        shutil.rmtree(old, ignore_errors=True)
        if os.path.isdir(home):
            try:
                os.replace(home, old)
            except OSError:
                shutil.rmtree(home, ignore_errors=True)
        os.makedirs(os.path.dirname(home), exist_ok=True)
        os.replace(root, home)
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
        return True, "folder version installed", os.path.join(home, "MediaHotKey.exe")
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"migration failed: {exc}", None


def launch_migrated(new_exe):
    """Start the freshly installed folder build; it deletes this one-file exe
    once we've exited (MHK_REMOVE_OLD) and refreshes the desktop shortcut."""
    env = _child_env()
    env["MHK_REMOVE_OLD"] = sys.executable
    try:
        subprocess.Popen([new_exe], cwd=os.path.dirname(new_exe),
                         close_fds=True, env=env)
        return True
    except Exception:  # noqa: BLE001
        return False


def _apply_update_onedir(progress):
    """Folder-build self-update: stage the new version next to the install;
    the swap happens on restart via MHK_FINISH_UPDATE."""
    global _pending_dir
    from . import __version__
    try:
        rel = latest_release()
    except Exception as exc:  # noqa: BLE001
        return False, f"Couldn't reach GitHub: {exc}"
    tag = rel.get("tag", "")
    if _ver_tuple(tag) <= _ver_tuple(__version__):
        return False, f"You're already on the latest version ({__version__})."
    url = rel.get("assets", {}).get(ZIP_ASSET)
    if not url:
        return False, f"Release {tag} has no folder-build package yet."
    staging = os.path.join(install_dir(), "update_staging")
    progress(f"Downloading {tag}…")
    try:
        root = _fetch_zip_to(url, staging, progress)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"Download failed: {exc}"
    if root is None:
        shutil.rmtree(staging, ignore_errors=True)
        return False, "Downloaded package looked wrong — try again in a minute."
    _pending_dir = root
    return True, f"Update to {tag} downloaded. Click Restart to apply."


def finish_update_if_needed():
    """When started from update_staging with MHK_FINISH_UPDATE=<install dir>,
    copy ourselves over the (now-exited) old install and relaunch from there.
    Returns True when the caller must exit immediately."""
    target = os.environ.get("MHK_FINISH_UPDATE")
    if not target or not is_frozen():
        return False
    me = os.path.dirname(sys.executable)
    exe = os.path.join(target, "MediaHotKey.exe")
    # Wait for the old process to fully exit (its exe becomes writable).
    for _ in range(60):
        try:
            if not os.path.exists(exe):
                break
            with open(exe, "ab"):
                break
        except OSError:
            time.sleep(0.5)
    for _ in range(3):
        try:
            internal = os.path.join(target, "_internal")
            if os.path.isdir(internal):
                shutil.rmtree(internal)
            if os.path.exists(exe):
                os.remove(exe)
            break
        except OSError:
            time.sleep(1.0)
    # COPY (not move) — our own _internal is memory-mapped while we run, and
    # the staging leftovers are cleaned up by the next normal start.
    shutil.copytree(me, target, dirs_exist_ok=True)
    env = _child_env()
    subprocess.Popen([exe], cwd=target, close_fds=True, env=env)
    return True


def apply_update(progress=lambda m: None):
    """Download + install the latest version. Returns (ok, message)."""
    if is_frozen():
        if is_onedir():
            return _apply_update_onedir(progress)
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
    if is_onedir():
        shutil.rmtree(os.path.join(install_dir(), "update_staging"),
                      ignore_errors=True)
    for d in (app_home() + ".new", app_home() + ".old"):
        shutil.rmtree(d, ignore_errors=True)


def _child_env():
    """Environment for relaunching a frozen build. PyInstaller's onefile
    bootloader passes _MEIPASS2 (and friends) to child processes so they reuse
    the parent's extracted temp dir — but the parent deletes that dir on exit,
    leaving the child with missing files (404) and a 'failed to remove temp
    directory' warning. Strip them so the new exe extracts its own copy."""
    env = os.environ.copy()
    for k in ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE",
              "_PYIBoot_SPLASH", "_MEIPASS",
              "MHK_FINISH_UPDATE", "MHK_REMOVE_OLD"):
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
            if _pending_dir:
                exe = os.path.join(_pending_dir, "MediaHotKey.exe")
                env = _child_env()
                env["MHK_FINISH_UPDATE"] = install_dir()
                subprocess.Popen([exe], cwd=_pending_dir, close_fds=True, env=env)
                return True
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
