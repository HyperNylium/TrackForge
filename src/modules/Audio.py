from ..types.profile import Profile
from ..types.track import SourceAudioTrack
from .EOS import build_eos_filter
from .Vars import (
    CHANNELS_BY_LAYOUT, LAYOUT_NAMES,
    layout_token
)


# bitrate per output layout for the lossy codecs that do not pick a good multichannel default.
_AAC_BITRATES = {1: "96k", 2: "192k", 6: "384k", 8: "512k"}
_OPUS_BITRATES = {1: "96k", 2: "128k", 6: "320k", 8: "448k"}

# nice codec names to show in output track titles.
_CODEC_TITLES = {
    "AAC": "AAC",
    "AC3": "AC3",
    "EAC3": "EAC3",
    "DTS": "DTS",
    "OPUS": "Opus",
    "FLAC": "FLAC",
    "WAV": "PCM",
}


def output_channels(source_channels: int, profile: Profile) -> int:
    """Work out how many channels the output track should have."""

    if profile.channels is not None:
        wanted = CHANNELS_BY_LAYOUT[profile.channels]
    else:
        wanted = source_channels

    # never make more channels than the source actually has.
    chosen = min(source_channels, wanted)

    # these codecs top out at 5.1.
    if profile.codec in ("AC3", "EAC3", "DTS"):
        chosen = min(6, chosen)

    return chosen


def output_title(profile: Profile, out_channels: int) -> str:
    """Build the output track title."""

    layout_label = layout_token(out_channels)

    if profile.transformation == "EOS":
        return f"Even-Out-Sound {layout_label}"

    if profile.transformation == "EOS+":
        return f"Even-Out-Sound+ {layout_label}"

    return f"{_CODEC_TITLES[profile.codec]} {layout_label}"


def codec_flags(codec: str, out_channels: int) -> list[str]:
    """The ffmpeg codec options for one output track."""

    match codec:
        case "AAC":
            return ["-c:a", "aac", "-b:a", _AAC_BITRATES.get(out_channels, "192k")]
        case "AC3":
            return ["-c:a", "ac3"]
        case "EAC3":
            return ["-c:a", "eac3"]
        case "DTS":
            # ffmpeg's built-in DTS encoder is experimental and lower quality.
            return ["-c:a", "dca", "-strict", "-2"]
        case "OPUS":
            return ["-c:a", "libopus", "-b:a", _OPUS_BITRATES.get(out_channels, "128k")]
        case "FLAC":
            return ["-c:a", "flac"]
        case "WAV":
            return ["-c:a", "pcm_s16le"]
        case "ORIG":
            return ["-c:a", "copy"]
        case _:
            raise ValueError(f"Unknown codec: {codec}")


def encode_command(ffmpeg: str, input_path: str, source: SourceAudioTrack, profile: Profile, out_path: str) -> list[str]:
    """Build the ffmpeg command that turns one source audio track into one output."""

    out_channels = output_channels(source.channels, profile)
    base = [ffmpeg, "-y", "-i", input_path]

    if profile.transformation is not None:
        target_layout = LAYOUT_NAMES.get(out_channels)
        eos_filter = build_eos_filter(
            profile.transformation, source.channels, target_layout,
            in_label=f"0:a:{source.index}", out_label="eos",
        )
        command = [*base, "-filter_complex", eos_filter, "-map", "[eos]"]
        command += codec_flags(profile.codec, out_channels)
    elif profile.codec == "ORIG":
        command = [*base, "-map", f"0:a:{source.index}", "-c:a", "copy"]
    else:
        command = [*base, "-map", f"0:a:{source.index}"]
        command += codec_flags(profile.codec, out_channels)
        # a plain conversion only needs -ac when it actually cuts the channel count.
        if out_channels < source.channels:
            command += ["-ac", str(out_channels)]

    # flags to report progress in a machine-readable way
    command += ["-nostats", "-progress", "pipe:1"]

    command.append(out_path)
    return command
