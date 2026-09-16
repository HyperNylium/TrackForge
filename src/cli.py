import os
import sys
import argparse

from pydantic import ValidationError

from .types.job import Job
from .types.profile import Profile
from .modules.Process import run_batch, run_info
from .modules.Vars import (
    TrackForgeError,
    log,
    CONTAINER_EXTS, APP_VERSION,
    configure_logging
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="trackforge",
        description="Re-encode and downmix the audio tracks of a media file.",
    )
    parser.add_argument("input", help="Input .mkv or .mp4 file, or a folder of them")
    parser.add_argument("output", nargs="?", help="Output file, or output folder for a folder input")
    parser.add_argument(
        "profiles", nargs="?",
        help='Comma separated profiles. For example, "ORIG, AAC:2.0, EOS:2.0"',
    )
    parser.add_argument(
        "--info", action="store_true",
        help="Print the input's audio tracks and exit",
    )
    parser.add_argument(
        "--track", type=int, default=None, metavar="INDEX",
        help="Only process the audio track at this index (from --info)",
    )
    parser.add_argument(
        "--muxer", choices=("auto", "ffmpeg", "mkvmerge"), default="auto",
        help="Muxer to use (default: auto)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite the output if it already exists",
    )
    parser.add_argument(
        "--workers", type=int, default=1, metavar="N",
        help="Encode up to N audio tracks at once within a file (default: 1)",
    )
    parser.add_argument(
        "--file-workers", type=int, default=1, metavar="M",
        help="Process up to M files at once when the input is a folder (default: 1)",
    )
    parser.add_argument(
        "--simple", action="store_true",
        help="Plain line output instead of the live progress display",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging (implies --simple)")
    parser.add_argument("--version", action="version", version=APP_VERSION)

    return parser.parse_args(argv)


def _parse_profiles(text: str | None) -> list[Profile]:
    """Parse the comma separated profiles argument into Profile objects."""

    profiles: list[Profile] = []
    if text:
        for item in text.split(","):
            if item.strip():
                profiles.append(Profile.parse(item))

    return profiles


def _media_files(folder: str) -> list[str]:
    """Every .mkv or .mp4 file under the folder."""

    found: list[str] = []
    for root, _dirs, names in os.walk(folder):
        for name in names:
            if os.path.splitext(name)[1].lower() in CONTAINER_EXTS:
                found.append(os.path.join(root, name))

    found.sort()
    return found


def build_job(args: argparse.Namespace) -> Job:
    """Turn parsed args into a single-file Job."""

    return Job(
        input=args.input,
        output=args.output,
        profiles=_parse_profiles(args.profiles),
        target_index=args.track,
        muxer=args.muxer,
        overwrite=args.overwrite,
        info=args.info,
        workers=args.workers,
    )


def build_jobs(args: argparse.Namespace) -> list[Job]:
    """Turn parsed args into one Job per input file. A folder expands recursively."""

    if not os.path.isdir(args.input):
        return [build_job(args)]

    if not args.output:
        raise ValueError("An output folder is required when the input is a folder")

    if args.track is not None:
        raise ValueError("--track cannot be used with a folder input")

    profiles = _parse_profiles(args.profiles)
    if not profiles:
        raise ValueError("At least one profile is required unless --info is used")

    inputs = _media_files(args.input)
    if not inputs:
        raise ValueError(f"No .mkv or .mp4 files found in {args.input}")

    jobs: list[Job] = []
    for input_path in inputs:
        output_path = os.path.join(args.output, os.path.relpath(input_path, args.input))
        jobs.append(Job(
            input=input_path,
            output=output_path,
            profiles=profiles,
            muxer=args.muxer,
            overwrite=args.overwrite,
            info=False,
            workers=args.workers,
        ))

    return jobs


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    # when verbose logging is enabled switch to simple output
    simple = args.simple or args.verbose

    try:
        if args.info:
            if os.path.isdir(args.input):
                raise ValueError("--info cannot be used with a folder input")
            return run_info(build_job(args), simple)

        jobs = build_jobs(args)
        return run_batch(jobs, simple, args.file_workers)
    except ValidationError as error:
        # pydantic wraps our own messages so show just those without its extraness
        for detail in error.errors():
            log.error(detail["msg"].removeprefix("Value error, "))
        return 2
    except (TrackForgeError, ValueError) as error:
        log.error(f"{error}")
        return 2
    except KeyboardInterrupt:
        # run_batch already stopped the encodes and cleaned the temp dirs on the way up.
        log.error("Interrupted")
        return 130
    except Exception as error:
        log.error(f"Unexpected error: {error}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
