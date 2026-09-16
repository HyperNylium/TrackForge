from ..types.track import PlannedAudio
from .Vars import TrackForgeError


# maps the ffprobe/ffmpeg flag names to the matching mkvmerge track-flag options.
# the default flag is handled on its own with --default-track-flag.
MKVMERGE_DISPOSITION_FLAGS = {
    "forced": "--forced-display-flag",
    "comment": "--commentary-flag",
    "hearing_impaired": "--hearing-impaired-flag",
    "visual_impaired": "--visual-impaired-flag",
    "original": "--original-flag",
}


def get_muxer(muxer: str, output_format: str, mkvmerge_available: bool) -> str:
    """Decide which muxer to use, based on the user's choice and the container."""

    match muxer:
        case "ffmpeg":
            return "ffmpeg"
        case "mkvmerge":
            if output_format == "mp4":
                raise TrackForgeError("mkvmerge cannot write mp4.\nUse --muxer ffmpeg.")
            if not mkvmerge_available:
                raise TrackForgeError("--muxer mkvmerge was requested but mkvmerge was not found.")
            return "mkvmerge"
        case "auto":
            if output_format == "mp4":
                return "ffmpeg"
            return "mkvmerge" if mkvmerge_available else "ffmpeg"
        case _:
            raise TrackForgeError(f"Unknown muxer: {muxer}")


def ffmpeg_mux_command(ffmpeg: str, original: str, audios: list[PlannedAudio], output_format: str, out_path: str) -> list[str]:
    """Mux the kept original streams plus the new audio temp files with ffmpeg."""

    command = [ffmpeg, "-y", "-i", original]
    for audio in audios:
        command += ["-i", audio.path]

    if output_format == "mkv":
        # take every original stream except its audio then add the new audio.
        command += ["-map", "0", "-map", "-0:a"]
    else:
        # mp4 cannot hold most subtitle or attachment types so we keep only the video.
        # chapters are added below.
        # uppercase V picks real video streams only, leaving out attached pictures or cover art.
        command += ["-map", "0:V?"]

    for input_index in range(len(audios)):
        command += ["-map", f"{input_index + 1}:a"]

    command += ["-c", "copy"]

    if output_format == "mp4":
        command += ["-map_chapters", "0"]

    for out_index, audio in enumerate(audios):
        command += [f"-metadata:s:a:{out_index}", f"language={audio.language}"]
        if audio.title:
            command += [f"-metadata:s:a:{out_index}", f"title={audio.title}"]
        flags = (["default"] if audio.is_default else []) + audio.dispositions
        command += [f"-disposition:a:{out_index}", "+".join(flags) if flags else "0"]

    if output_format == "mkv":
        command += ["-f", "matroska"]
    else:
        command += ["-f", "mp4"]

    command.append(out_path)
    return command


def mkvmerge_mux_command(mkvmerge: str, original: str, audios: list[PlannedAudio], out_path: str) -> list[str]:
    """Mux with mkvmerge. The original keeps its video, subs, attachments and chapters."""

    command = [mkvmerge, "-o", out_path, "--no-audio", original]
    for audio in audios:
        command += ["--language", f"0:{audio.language}"]
        if audio.title:
            command += ["--track-name", f"0:{audio.title}"]

        command += ["--default-track-flag", f"0:{'yes' if audio.is_default else 'no'}"]

        for flag_name in audio.dispositions:
            command += [MKVMERGE_DISPOSITION_FLAGS[flag_name], "0:yes"]
        command.append(audio.path)

    return command
