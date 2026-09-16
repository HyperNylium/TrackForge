import os
import sys
import time
import glob
import shutil
import tomllib
import zipfile
import tarfile
import argparse
import tempfile
import urllib.error
import urllib.request


FFMPEG_BASE_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
FFMPEG_ASSETS = {
    ("linux", "x64"): "ffmpeg-master-latest-linux64-gpl.tar.xz",
    ("linux", "arm64"): "ffmpeg-master-latest-linuxarm64-gpl.tar.xz",
    ("windows", "x64"): "ffmpeg-master-latest-win64-gpl.zip",
}
MKVTOOLNIX_URL = "https://mkvtoolnix.download/windows/releases/102.0/mkvtoolnix-64-bit-102.0.zip"  # TODO: update this every once in a while

# files copied into every release package alongside the binary.
EXTRA_FILES = ("LICENSE", "NOTICE", "README.md")


def _run() -> int:
    from src.cli import main

    return main()


def _find_file(folder: str, filename: str) -> str | None:
    """Return the first file under the folder with this name, or None."""

    for root, _dirs, names in os.walk(folder):
        if filename in names:
            return os.path.join(root, filename)

    return None


def _install(src: str, dest: str, is_windows: bool) -> None:
    """Copy a tool into static/ and make it runnable on Linux and mac."""

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)

    if not is_windows:
        os.chmod(dest, 0o755)


def _get_platform() -> tuple[str, str]:
    """Return the (os, arch) names for the machine we are building on."""

    import platform

    system = platform.system()
    machine = platform.machine().lower()

    match system:
        case "Linux":
            os_name = "linux"
        case "Windows":
            os_name = "windows"
        case "Darwin":
            os_name = "macos"
        case _:
            os_name = system.lower()

    match machine:
        case "aarch64" | "arm64":
            arch = "arm64"
        case "x86_64" | "amd64":
            arch = "x64"
        case _:
            arch = machine

    return os_name, arch


def _build_binary(project_root: str) -> None:
    """Build the onefile executable with PyInstaller into dist/."""

    import importlib

    data_sep = os.pathsep
    pyproject = os.path.join(project_root, "pyproject.toml")

    pyinstaller = importlib.import_module("PyInstaller.__main__")
    pyinstaller.run([
        "--onefile",
        "--name", "trackforge",
        "--paths", project_root,
        "--add-data", f"{pyproject}{data_sep}.",
        "--collect-submodules", "src",
        "--exclude-module", "PyInstaller",
        os.path.abspath(__file__),
    ])


def _download(url: str, dest: str, attempts: int = 5) -> None:
    """Download url to dest and try a few more times if it fails."""

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
    }

    request = urllib.request.Request(url, headers=headers)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response, open(dest, "wb") as out_file:
                shutil.copyfileobj(response, out_file)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            last_error = error
            print(f"  download attempt {attempt}/{attempts} failed: {error}")
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 10))

    raise RuntimeError(f"failed to download {url}: {last_error}")


def _extract(archive: str, dest_dir: str) -> None:
    """Extract a .zip or .tar.xz archive into dest_dir."""

    name = archive.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zip_file:
            zip_file.extractall(dest_dir)

    elif name.endswith((".tar.xz", ".txz")):
        with tarfile.open(archive, "r:xz") as tar_file:
            tar_file.extractall(dest_dir, filter="data")

    else:
        raise RuntimeError(f"unknown archive type: {archive}")


def _stage_ffmpeg(static_dir: str, os_name: str, arch: str, exe: str, refresh: bool) -> None:
    """Make sure ffmpeg and ffprobe are in static/. Download them if they are missing."""

    needed: list[str] = []
    for tool in ("ffmpeg", "ffprobe"):
        cached = os.path.join(static_dir, tool, tool + exe)
        if refresh or not os.path.isfile(cached):
            needed.append(tool)

    if not needed:
        print("ffmpeg + ffprobe already present, skipping download")
        return

    asset = FFMPEG_ASSETS.get((os_name, arch))
    if asset is None:
        missing = ", ".join(needed)
        raise RuntimeError(f"no ffmpeg download for {os_name}/{arch}. place {missing} in static/<tool>/ yourself.")

    url = f"{FFMPEG_BASE_URL}/{asset}"
    with tempfile.TemporaryDirectory() as temp_dir:
        archive = os.path.join(temp_dir, asset)
        print(f"downloading ffmpeg from {url}")

        _download(url, archive)
        _extract(archive, temp_dir)

        for tool in needed:
            src = _find_file(temp_dir, tool + exe)
            if src is None:
                raise RuntimeError(f"{tool + exe} not found in {asset}")

            dest = os.path.join(static_dir, tool, tool + exe)
            _install(src, dest, os_name == "windows")

            print(f"staged {tool} -> {dest}")


def _stage_mkvmerge(static_dir: str, exe: str, refresh: bool) -> None:
    """Make sure mkvmerge is in static/ on Windows. Download it if it is missing."""

    dest = os.path.join(static_dir, "mkvmerge", "mkvmerge" + exe)
    if not refresh and os.path.isfile(dest):
        print("mkvmerge already present, skipping download")
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        archive = os.path.join(temp_dir, "mkvtoolnix.zip")
        print(f"downloading mkvmerge from {MKVTOOLNIX_URL}")

        _download(MKVTOOLNIX_URL, archive)
        _extract(archive, temp_dir)

        src = _find_file(temp_dir, "mkvmerge" + exe)
        if src is None:
            raise RuntimeError("mkvmerge.exe not found in the mkvtoolnix archive")

        _install(src, dest, is_windows=True)
        print(f"staged mkvmerge -> {dest}")


def _assemble(project_root: str, version: str, os_name: str, exe: str, tools: list[str]) -> str:
    """Copy the binary, tools, and license files into a fresh release folder."""

    is_windows = os_name == "windows"
    release_dir = os.path.join(project_root, f"release-v{version}")
    if os.path.exists(release_dir):
        shutil.rmtree(release_dir)

    os.makedirs(release_dir)

    binary = os.path.join(project_root, "dist", "trackforge" + exe)
    if not os.path.isfile(binary):
        raise RuntimeError(f"built binary not found at {binary}")

    dest_binary = os.path.join(release_dir, "trackforge" + exe)
    shutil.copy2(binary, dest_binary)

    if not is_windows:
        os.chmod(dest_binary, 0o755)

    for tool in tools:
        src = os.path.join(project_root, "static", tool, tool + exe)
        dest = os.path.join(release_dir, "static", tool, tool + exe)

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)

        if not is_windows:
            os.chmod(dest, 0o755)

    for extra in EXTRA_FILES:
        src = os.path.join(project_root, extra)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(release_dir, extra))

    return release_dir


def _cleanup(project_root: str, include_release: bool) -> None:
    """Delete the PyInstaller leftovers. Also delete the release folders when include_release is set."""

    spec_file = os.path.join(project_root, "trackforge.spec")
    shutil.rmtree(os.path.join(project_root, "build"), ignore_errors=True)
    shutil.rmtree(os.path.join(project_root, "dist"), ignore_errors=True)
    if os.path.isfile(spec_file):
        os.remove(spec_file)

    if include_release:
        for release_dir in glob.glob(os.path.join(project_root, "release-v*")):
            shutil.rmtree(release_dir, ignore_errors=True)


def _build() -> int:
    parser = argparse.ArgumentParser(description="Build TrackForge and assemble an unzipped release-v<version> folder.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true", help="only re-download the bundled tools into static/ and exit.")
    mode.add_argument("--cleanup", action="store_true", help="only delete the build/, dist/, .spec, and release-v* leftovers and exit.")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.abspath(__file__))

    if args.cleanup:
        _cleanup(project_root, include_release=True)
        print("removed build artifacts")
        return 0

    os_name, arch = _get_platform()
    exe = ".exe" if os_name == "windows" else ""
    static_dir = os.path.join(project_root, "static")

    if args.refresh:
        _stage_ffmpeg(static_dir, os_name, arch, exe, refresh=True)
        if os_name == "windows":
            _stage_mkvmerge(static_dir, exe, refresh=True)
        return 0

    with open(os.path.join(project_root, "pyproject.toml"), "rb") as pyproject_file:
        version = str(tomllib.load(pyproject_file)["project"]["version"])

    # mkvmerge is only bundled on Windows
    tools = ["ffmpeg", "ffprobe"]
    if os_name == "windows":
        tools.append("mkvmerge")

    print(f"building trackforge {version} for {os_name}/{arch}")
    _build_binary(project_root)

    _stage_ffmpeg(static_dir, os_name, arch, exe, refresh=False)
    if os_name == "windows":
        _stage_mkvmerge(static_dir, exe, refresh=False)

    release_dir = _assemble(project_root, version, os_name, exe, tools)
    _cleanup(project_root, include_release=False)

    print(f"\nrelease staged at: {release_dir}")
    return 0


if getattr(sys, "frozen", False):
    sys.exit(_run())


if __name__ == "__main__":
    sys.exit(_build())
