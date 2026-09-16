import os
import sys
import shutil
import logging
import tomllib


log = logging.getLogger("trackforge")


class TrackForgeError(Exception):
    """Raised for any expected, user-facing failure. cli.py turns it into a clean exit."""


# output codecs we know how to make.
# ORIG means copy the source track as-is.
# PCM is accepted as another name for WAV and swapped to WAV in the profile parser.
CODECS = {"AAC", "AC3", "EAC3", "DTS", "OPUS", "FLAC", "WAV", "ORIG"}

TRANSFORMATIONS = {"EOS", "EOS+"}

# the channel options a profile can ask for. left out means keep the source layout.
CHANNEL_LAYOUTS = {"1.0", "2.0", "5.1", "7.1"}

# mp4 can only carry these output codecs.
MP4_CODECS = {"AAC", "AC3", "EAC3"}

# ffprobe codec names that are safe to copy straight into mp4 (used for ORIG + mp4).
MP4_SAFE_SOURCE_CODECS = {"aac", "ac3", "eac3"}

CONTAINER_EXTS = {".mkv", ".mp4"}

CHANNELS_BY_LAYOUT = {"1.0": 1, "2.0": 2, "5.1": 6, "7.1": 8}

# channel count to the layout name shown in titles
LAYOUT_NAMES = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}

# source track flags we copy onto the new tracks, besides the default flag.
# the default flag is handled on its own, only the first output of a default source keeps it.
# these are the names ffprobe and ffmpeg use for these flags.
NATURE_DISPOSITIONS = ("forced", "comment", "hearing_impaired", "visual_impaired", "original")


def _project_root() -> str:
    # Vars.py lives at <root>/src/modules/Vars.py, so three parents up is the repo root.
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _static_dir() -> str:
    # an explicit override points straight at the tools directory.
    override = os.getenv("TRACKFORGE_STATIC_DIR")
    if override:
        return override

    # when compiled the binaries sit in a static dir next to the executable.
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "static")

    # in a normal checkout they sit in the project's own static dir.
    return os.path.join(_project_root(), "static")


def _read_app_version() -> str:
    # when frozen, pyproject.toml is packed in with the code (sys._MEIPASS), not the project root.
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = _project_root()

    try:
        with open(os.path.join(base, "pyproject.toml"), "rb") as pyproject_file:
            return str(tomllib.load(pyproject_file)["project"]["version"])
    except Exception:
        return "unknown"


STATIC_DIR = _static_dir()
APP_VERSION = _read_app_version()


def layout_token(channels: int) -> str:
    """Turn a channel count into the layout label shown in titles and the tree."""

    return LAYOUT_NAMES.get(channels, f"{channels}ch")


def get_binary(tool: str, required: bool = False) -> str | None:
    """Find a tool. Look in static/<tool>/ first, then fall back to PATH. Raise when required but not found."""

    exe = tool + (".exe" if os.name == "nt" else "")
    candidate = os.path.join(STATIC_DIR, tool, exe)
    if os.path.isfile(candidate) and (os.name == "nt" or os.access(candidate, os.X_OK)):
        return candidate

    found = shutil.which(exe)
    if found is None and exe != tool:
        found = shutil.which(tool)

    if required and found is None:
        raise TrackForgeError(f"Required tool '{tool}' was not found in static/{tool}/ or on PATH.")

    return found


def binary_source(path: str) -> str:
    """Say whether a resolved tool path came from the bundled static dir or PATH."""

    prefix = os.path.abspath(STATIC_DIR) + os.sep
    if os.path.abspath(path).startswith(prefix):
        return "bundled"
    return "PATH"


def parse_float(value: object) -> float | None:
    """Turn a value into a float, or None when it does not parse."""

    if value is None:
        return None

    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: object) -> float | None:
    """Turn 'HH:MM:SS.fff' into seconds, or None when it does not parse."""

    if value is None:
        return None

    parts = str(value).split(":")
    if len(parts) != 3:
        return None

    hours = parse_float(parts[0])
    minutes = parse_float(parts[1])
    seconds = parse_float(parts[2])
    if hours is None or minutes is None or seconds is None:
        return None

    return hours * 3600 + minutes * 60 + seconds


def configure_logging(verbose: bool) -> None:
    """Send every log line to stdout so all program output shares one stream."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    log.handlers.clear()
    log.addHandler(handler)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
