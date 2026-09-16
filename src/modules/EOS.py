# EOS / EOS+ (Even-Out-Sound) audio DSP.
#
# The compand dynamic-range curve and the pan/downmix coefficient filters in this
# file are copied from mkv-auto by philiptn (https://github.com/philiptn/mkv-auto),
# a GPLv3 project. They are used here with the author's permission, granted on
# 2026-09-12 in mkv-auto issue #44, on the condition that this project is GPLv3 and
# credits the original author. See the repository NOTICE and LICENSE files.
#
# Only the copied DSP lives here. Codec choice and plain channel downmixing are our
# own work and live in Audio.py.


COMPAND_FILTER = (
    "compand=attacks=0:decays=0.3:soft-knee=6:points=-110.00/-110.00|-101.11/-101.15|"
    "-93.93/-93.97|-83.52/-84.05|-74.59/-74.81|-65.18/-65.23|-52.29/-51.54|-42.14/-39.32|"
    "-34.35/-27.25|-31.43/-22.64|-27.54/-18.38|-24.29/-15.90|-20.07/-13.77|-13.58/-10.18|"
    "-5.15/-8.04|2.64/-6.96|10.76/-5.36|20.17/-4.29:gain=0"
)


def get_pan_filter_eos(source_channels, layout):
    if layout in ("5.1", "5.1(side)"):
        return (
            "pan=5.1|"
            "FL=0.5*FL|"
            "FR=0.5*FR|"
            "FC=0.6*FC|"
            "LFE=0.3*LFE|"
            "BL=0.3*BL|"
            "BR=0.3*BR"
        )

    elif layout == "7.1":
        return (
            "pan=7.1|"
            "FL=0.5*FL|"
            "FR=0.5*FR|"
            "FC=0.6*FC|"
            "LFE=0.3*LFE|"
            "BL=0.3*BL|"
            "BR=0.3*BR|"
            "SL=0.3*SL|"
            "SR=0.3*SR"
        )

    elif layout == "Stereo":
        if source_channels > 2:
            return (
                "pan=stereo|"
                "FL=0.5*FL+0.6*FC+0.3*BL+0.3*SL+0.3*LFE|"
                "FR=0.5*FR+0.6*FC+0.3*BR+0.3*SR+0.3*LFE"
            )
        else:
            return (
                "pan=stereo|"
                "FL=0.7*FL|"
                "FR=0.7*FR"
            )

    elif layout == "Mono":
        if source_channels > 2:
            return "pan=mono|FC=0.5*FL+0.5*FR+0.6*FC"
        elif source_channels == 2:
            return "pan=mono|FC=0.7*FL+0.7*FR"
        else:
            return "pan=mono|FC=0.7*FC"

    else:
        return None


def get_pan_filter_eos_plus(source_channels, layout):
    if layout in ("5.1", "5.1(side)"):
        return (
            "pan=5.1|"
            "FL=0.3*FL|"
            "FR=0.3*FR|"
            "FC=0.7*FC|"
            "LFE=0.1*LFE|"
            "BL=0.1*BL|"
            "BR=0.1*BR"
        )

    elif layout == "7.1":
        return (
            "pan=7.1|"
            "FL=0.3*FL|"
            "FR=0.3*FR|"
            "FC=0.7*FC|"
            "LFE=0.1*LFE|"
            "BL=0.1*BL|"
            "BR=0.1*BR|"
            "SL=0.1*SL|"
            "SR=0.1*SR"
        )

    elif layout == "Stereo":
        if source_channels > 2:
            return (
                "pan=stereo|"
                "FL=0.3*FL+0.7*FC+0.1*BL+0.1*SL+0.1*LFE|"
                "FR=0.3*FR+0.7*FC+0.1*BR+0.1*SR+0.1*LFE"
            )
        else:
            return (
                "pan=stereo|"
                "FL=0.7*FL|"
                "FR=0.7*FR"
            )

    elif layout == "Mono":
        if source_channels > 2:
            return "pan=mono|FC=0.3*FL+0.3*FR+0.7*FC"
        elif source_channels == 2:
            return "pan=mono|FC=0.7*FL+0.7*FR"
        else:
            return "pan=mono|FC=0.7*FC"

    else:
        return None


def build_eos_filter(transformation, source_channels, target_layout, in_label, out_label):
    """Build the ffmpeg filter_complex string for one EOS/EOS+ output track."""

    volume_prefix = "volume=0.8," if source_channels <= 2 else ""

    if transformation == "EOS+":
        pan_filter = get_pan_filter_eos_plus(source_channels, target_layout)
    else:
        pan_filter = get_pan_filter_eos(source_channels, target_layout)

    body = f"{volume_prefix}{COMPAND_FILTER}"
    if pan_filter:
        body = f"{body},{pan_filter}"

    return f"[{in_label}]{body}[{out_label}]"
