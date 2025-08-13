import os
import sys
import subprocess
import shutil
import tempfile
import zipfile
import urllib.request

PACKAGES = [
    "attrs==25.3.0",
    "certifi==2025.8.3",
    "cffi==1.17.1",
    "charset-normalizer==3.4.2",
    "decorator==4.4.2",
    "exporter==0.0.4",
    "fastjsonschema==2.21.1",
    "glcontext==3.0.0",
    "idna==3.10",
    "imageio==2.37.0",
    "imageio-ffmpeg==0.6.0",
    "imgkit==1.2.3",
    "jsonschema==4.25.0",
    "jsonschema-specifications==2025.4.1",
    "jupyter_core==5.8.1",
    "moderngl==5.12.0",
    "moviepy==1.0.3",
    "nbformat==5.10.4",
    "numpy==2.3.2",
    "pillow==11.3.0",
    "platformdirs==4.3.8",
    "proglog==0.1.12",
    "pycparser==2.22",
    "Pygments==2.19.2",
    "PySide6==6.9.1",
    "PySide6_Addons==6.9.1",
    "PySide6_Essentials==6.9.1",
    "referencing==0.36.2",
    "requests==2.32.4",
    "rpds-py==0.27.0",
    "scipy==1.16.1",
    "setuptools==80.9.0",
    "shiboken6==6.9.1",
    "six==1.17.0",
    "sounddevice==0.5.2",
    "soundfile==0.13.1",
    "tqdm==4.67.1",
    "traitlets==5.14.3",
    "urllib3==2.5.0",
]

def run(cmd, check=True, capture=False, shell=False):
    kwargs = {}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
        kwargs["text"] = True
    print(">", " ".join(cmd) if not shell else cmd)
    proc = subprocess.run(cmd, check=False, shell=shell, **kwargs)
    if check and proc.returncode != 0:
        output = proc.stdout if capture else ""
        raise RuntimeError(f"Command failed ({proc.returncode}): {cmd}\n{output}")
    return proc

def pip_install(packages):
    print("Installing Python dependencies...")
    run([sys.executable, "-m", "pip", "install", *packages])

def _which_ffmpeg():
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

def _append_to_user_path(dir_path):
    # Append to user PATH using setx (user profile). Avoid duplicates.
    dir_path = os.path.normpath(dir_path)
    current = os.environ.get("PATH", "")
    parts = [p.strip() for p in current.split(os.pathsep) if p.strip()]
    if dir_path not in parts:
        new_path = os.pathsep.join(parts + [dir_path])
        # setx persists to user env (no admin). Beware very long PATH.
        run('setx PATH "{}"'.format(new_path), check=True, shell=True)
        os.environ["PATH"] = new_path  # update current process too
    else:
        print("PATH already contains:", dir_path)

def _set_aurora_ffmpeg(ffmpeg_exe):
    os.environ["AURORA_FFMPEG"] = ffmpeg_exe
    run(f'setx AURORA_FFMPEG "{ffmpeg_exe}"', check=False, shell=True)

# -------------------- Installers --------------------

def install_ffmpeg_windows():
    existing = _which_ffmpeg()
    if existing:
        print("ffmpeg already available on PATH:", existing)
        _set_aurora_ffmpeg(existing)
        return

    # 1) Try winget
    try:
        proc = run(["winget", "--version"], check=False, capture=True)
        if proc.returncode == 0:
            candidates = [
                ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--accept-package-agreements", "--accept-source-agreements"],
                ["winget", "install", "--id", "BtbN.FFmpeg", "-e", "--accept-package-agreements", "--accept-source-agreements"],
                ["winget", "install", "ffmpeg", "--accept-package-agreements", "--accept-source-agreements"],
            ]
            for cmd in candidates:
                try:
                    run(cmd, check=True)
                    ff = _which_ffmpeg()
                    if ff:
                        print("Installed ffmpeg via winget.")
                        _set_aurora_ffmpeg(ff)
                        return
                except Exception as e:
                    print("winget attempt failed:", e)
    except Exception as e:
        print("winget not available or failed:", e)

    # 2) Manual download & install (user-level, no admin)
    print("Falling back to manual download of FFmpeg...")
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    with tempfile.TemporaryDirectory() as td:
        zip_path = os.path.join(td, "ffmpeg.zip")
        print("Downloading:", url)
        urllib.request.urlretrieve(url, zip_path)
        dest_root = os.path.join(os.path.expanduser("~"), "AppData", "Local", "ffmpeg")
        os.makedirs(dest_root, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(dest_root)
        # Find the extracted bin folder
        bin_dir = None
        for root, dirs, files in os.walk(dest_root):
            lower = [f.lower() for f in files]
            if "ffmpeg.exe" in lower and os.path.basename(root).lower() == "bin":
                bin_dir = root
                break
        if not bin_dir:
            for root, dirs, files in os.walk(dest_root):
                if "ffmpeg.exe" in [f.lower() for f in files]:
                    bin_dir = root
                    break
        if not bin_dir:
            raise RuntimeError("Failed to locate ffmpeg.exe after extraction.")
        print("Installing to user path:", bin_dir)
        _append_to_user_path(bin_dir)
        ffmpeg_exe = os.path.join(bin_dir, "ffmpeg.exe")
        if not os.path.exists(ffmpeg_exe):
            raise RuntimeError("ffmpeg.exe not found after install.")
        _set_aurora_ffmpeg(ffmpeg_exe)
        print("FFmpeg installed and PATH updated.")

def install_ffmpeg_macOS_Linux():
    existing = _which_ffmpeg()
    if existing:
        print("ffmpeg already available on PATH:", existing)
        return

    if sys.platform.startswith("darwin"):
        print("macOS detected: installing FFmpeg via Homebrew...")
        if shutil.which("brew"):
            run(["brew", "install", "ffmpeg"])
        else:
            raise RuntimeError("Homebrew not found. Install it from https://brew.sh and re-run this installer.")
        # verify
        if not _which_ffmpeg():
            raise RuntimeError("FFmpeg installation appears to have failed on macOS.")
        print("FFmpeg installed successfully via Homebrew.")
        return

    if sys.platform.startswith("linux"):
        print("Linux detected: please install FFmpeg via your package manager, e.g.:")
        print("  sudo apt install ffmpeg      # Debian/Ubuntu")
        print("  sudo pacman -S ffmpeg        # Arch")
        print("  sudo dnf install ffmpeg      # Fedora")
        return

    raise RuntimeError(f"Unsupported platform: {sys.platform}")

# -------------------- Main --------------------

def main():
    pip_install(PACKAGES)
    if os.name == "nt":
        print("Windows detected: ensuring FFmpeg is installed and on PATH...")
        install_ffmpeg_windows()
    else:
        print("Non-Windows OS detected: ensuring FFmpeg is installed or providing guidance...")
        install_ffmpeg_macOS_Linux()

if __name__ == "__main__":
    main()
