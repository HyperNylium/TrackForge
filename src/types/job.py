import os

from pydantic import BaseModel, ConfigDict, model_validator

from .profile import Profile
from .track import SourceAudioTrack
from ..modules.Vars import (
    TrackForgeError,
    CONTAINER_EXTS, MP4_CODECS, MP4_SAFE_SOURCE_CODECS
)


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # path to the source .mkv or .mp4.
    input: str

    # path to write the result to, or None when --info is used.
    output: str | None = None

    # "mkv" or "mp4", set from the output file extension during validation.
    output_format: str | None = None

    # one profile for each output track we make, in the order given.
    profiles: list[Profile] = []

    # the one audio track to process, or None to process every track.
    target_index: int | None = None

    # "auto", "ffmpeg", or "mkvmerge".
    muxer: str = "auto"

    # allow writing over an existing output file.
    overwrite: bool = False

    # when True, just print the audio tracks and exit.
    info: bool = False

    # how many audio tracks to encode at once.
    workers: int = 1

    @model_validator(mode="after")
    def _cross_validate(self) -> "Job":
        input_ext = os.path.splitext(self.input)[1].lower()
        if input_ext not in CONTAINER_EXTS:
            raise ValueError(f"Input must be .mkv or .mp4, got '{input_ext or self.input}'")

        if not os.path.isfile(self.input):
            raise ValueError(f"Input file does not exist: {self.input}")

        if self.muxer not in ("auto", "ffmpeg", "mkvmerge"):
            raise ValueError(f"Unknown muxer '{self.muxer}'")

        if self.workers < 1:
            raise ValueError("--workers must be 1 or higher")

        # --info only needs the input.
        # skip the output and profile checks.
        if self.info:
            return self

        if not self.output:
            raise ValueError("An output path is required unless --info is used")

        if not self.profiles:
            raise ValueError("At least one profile is required unless --info is used")

        output_ext = os.path.splitext(self.output)[1].lower()
        if output_ext not in CONTAINER_EXTS:
            raise ValueError(f"Output must be .mkv or .mp4, got '{output_ext or self.output}'")

        self.output_format = output_ext.lstrip(".")
        if self.output_format == "mp4":
            for profile in self.profiles:
                # ORIG is checked later against the real source codec.
                if profile.codec == "ORIG":
                    continue

                if profile.codec not in MP4_CODECS:
                    allowed = ", ".join(sorted(MP4_CODECS))
                    raise ValueError(f"Codec '{profile.codec}' is not allowed in mp4 output (allowed: {allowed}).\nUse a .mkv output instead.")

        if self.muxer == "mkvmerge" and self.output_format == "mp4":
            raise ValueError("mkvmerge cannot write mp4.\nUse --muxer ffmpeg or a .mkv output.")

        if not self.overwrite and os.path.exists(self.output):
            raise ValueError(f"Output already exists (use --overwrite): {self.output}")

        if self.target_index is not None and self.target_index < 0:
            raise ValueError("--track index cannot be negative")

        return self

    def validate_against_tracks(self, tracks: list[SourceAudioTrack]) -> None:
        """Checks that need the probed source tracks. Runs before any encoding."""

        if self.target_index is not None:
            if not 0 <= self.target_index < len(tracks):
                raise TrackForgeError(f"--track {self.target_index} is out of range (file has {len(tracks)} audio track(s))")

        if self.output_format != "mp4":
            return

        # for mp4, every track we copy straight through must already be an mp4-safe codec.
        copied: list[SourceAudioTrack] = []
        if self.target_index is not None:
            processed: list[SourceAudioTrack] = []
            for track in tracks:
                if track.index == self.target_index:
                    processed.append(track)
                else:
                    copied.append(track)
        else:
            processed = tracks

        if any(profile.codec == "ORIG" for profile in self.profiles):
            copied += processed

        for track in copied:
            if track.codec not in MP4_SAFE_SOURCE_CODECS:
                raise TrackForgeError(f"Source track {track.index} ({track.codec}) would be copied into mp4, which cannot hold it.\nUse a .mkv output instead.")
