from pydantic import BaseModel, ConfigDict, field_validator

from ..modules.Vars import CODECS, CHANNEL_LAYOUTS, TRANSFORMATIONS


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None, "EOS", or "EOS+".
    transformation: str | None = None

    # one of the names in CODECS.
    # EOS and EOS+ set this to "AC3" by default.
    codec: str

    # one of the names in CHANNEL_LAYOUTS, or None to keep the source layout.
    channels: str | None = None

    @field_validator("transformation")
    @classmethod
    def _check_transformation(cls, value: str | None) -> str | None:
        if value is not None and value not in TRANSFORMATIONS:
            raise ValueError(f"Unknown transformation: {value}")
        return value

    @field_validator("codec")
    @classmethod
    def _check_codec(cls, value: str) -> str:
        if value not in CODECS:
            allowed = ", ".join(sorted(CODECS))
            raise ValueError(f"Unknown codec '{value}'. Allowed: {allowed}")
        return value

    @field_validator("channels")
    @classmethod
    def _check_channels(cls, value: str | None) -> str | None:
        if value is not None and value not in CHANNEL_LAYOUTS:
            allowed = ", ".join(sorted(CHANNEL_LAYOUTS))
            raise ValueError(f"Unknown channel layout '{value}'. Allowed: {allowed}")
        return value

    @classmethod
    def parse(cls, text: str) -> "Profile":
        """Parse one profile like 'EOS-EAC3:5.1' into a Profile."""

        token = text.strip().upper()
        if not token:
            raise ValueError("Empty profile item")

        channels = None
        if ":" in token:
            left, channels = token.split(":", 1)
        else:
            left = token

        transformation = None
        remainder = left
        if left.startswith("EOS+"):
            transformation = "EOS+"
            remainder = left[len("EOS+"):]
        elif left.startswith("EOS"):
            transformation = "EOS"
            remainder = left[len("EOS"):]

        if transformation is not None:
            # after EOS or EOS+, the rest is empty (use the default codec) or "-CODEC".
            # anything else is a typo like "EOSX".
            if remainder == "":
                codec = "AC3"
            elif remainder.startswith("-"):
                codec = remainder[1:]
            else:
                raise ValueError(f"Could not parse profile item: {text}")
        else:
            codec = remainder

        if codec == "PCM":
            codec = "WAV"

        if transformation is not None and codec == "ORIG":
            raise ValueError(f"'{text}': ORIG cannot be combined with a transformation")

        return cls(
            transformation=transformation,
            codec=codec,
            channels=channels
        )
