from pydantic import BaseModel, ConfigDict, Field

from .profile import Profile
from ..modules.Vars import (
    NATURE_DISPOSITIONS,
    parse_float, parse_timestamp
)


class SourceAudioTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # the audio track number, counting only audio tracks and starting at 0.
    # this is what --info prints, what --track selects, and what ffmpeg's 0:a:<index> map uses.
    index: int

    # lowercase ffprobe codec name. for example, "aac" or "eac3".
    codec: str

    # number of audio channels in the source track.
    channels: int

    # the channel layout ffprobe reported, or None when it did not report one.
    layout: str | None = None

    # the language tag from the source, or "und" when it is missing.
    language: str = "und"

    # source track title, or None when it has none.
    title: str | None = None

    # whether the source marks this as the default track.
    is_default: bool = False

    # other source track flags to copy onto the new tracks (forced, commentary, and so on).
    # the default flag is handled on its own above.
    dispositions: list[str] = Field(default_factory=list)

    # track length in seconds, or None when ffprobe reported no usable duration.
    # a None duration means that encode's bar shows no percent or eta.
    duration: float | None = None

    @classmethod
    def from_stream(cls, stream: dict, index: int, format_duration: float | None = None) -> "SourceAudioTrack":
        """Build a SourceAudioTrack from one ffprobe audio stream dict."""

        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        dispositions: list[str] = []
        for name in NATURE_DISPOSITIONS:
            if disposition.get(name):
                dispositions.append(name)

        duration = parse_float(stream.get("duration")) or parse_timestamp(tags.get("DURATION")) or format_duration

        return cls(
            index=index,
            codec=str(stream.get("codec_name", "")).lower(),
            channels=int(stream.get("channels", 0)),
            layout=stream.get("channel_layout"),
            language=tags.get("language", "und"),
            title=tags.get("title"),
            is_default=bool(disposition.get("default", 0)),
            dispositions=dispositions,
            duration=duration,
        )


class PlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # the source track this output is made from.
    source: SourceAudioTrack

    # the profile that makes this output.
    # a plain ORIG profile means just copy the track.
    profile: Profile

    # output track title, or None to keep the source title.
    title: str | None = None

    # whether this output track gets the default flag.
    is_default: bool = False

    # extra track flags (forced, commentary, and so on) for this output.
    dispositions: list[str] = Field(default_factory=list)


class PlannedAudio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # path to the encoded temp audio file for this output track.
    path: str

    # language tag to set on the finished track.
    language: str

    # track title to set, or None to leave it blank.
    title: str | None = None

    # whether this output gets the default flag.
    is_default: bool = False

    # extra track flags to set on this output.
    dispositions: list[str] = Field(default_factory=list)
