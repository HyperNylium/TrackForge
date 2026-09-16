import os
import json
import uuid
import shutil
import tempfile
import threading
import subprocess
from functools import partial
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait

from rich.console import Console

from ..types.job import Job
from ..types.profile import Profile
from ..types.track import SourceAudioTrack, PlanItem, PlannedAudio
from .Audio import encode_command, output_channels, output_title
from .Mux import get_muxer, ffmpeg_mux_command, mkvmerge_mux_command
from .Progress import (
    Reporter, FileReporter,
    make_reporter
)
from .Vars import (
    TrackForgeError,
    log,
    get_binary,
    layout_token,
    parse_float,
    parse_timestamp,
    binary_source,
)


def ffprobe(ffprobe_path: str, input_path: str) -> list[SourceAudioTrack]:
    """Probe the input for its audio tracks in file order, or error when it has none."""

    timeout = 180
    cmd = [ffprobe_path, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", input_path]
    log.debug(f"Running ffprobe: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as timeout_error:
        raise TrackForgeError(f"ffprobe timed out on {input_path}") from timeout_error

    if result.returncode != 0:
        raise TrackForgeError(f"ffprobe failed on {input_path}: {result.stderr.strip()}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as decode_error:
        raise TrackForgeError(f"Could not parse ffprobe output for {input_path}") from decode_error

    streams = data.get("streams") or []
    if not isinstance(streams, list):
        raise TrackForgeError(f"ffprobe returned no usable streams for {input_path}")

    # the whole file duration is the last-resort fallback when a track has none of its own.
    fmt = data.get("format") or {}
    format_duration = parse_float(fmt.get("duration"))

    tracks: list[SourceAudioTrack] = []
    audio_index = 0
    for stream in streams:
        if stream.get("codec_type") != "audio":
            continue
        tracks.append(SourceAudioTrack.from_stream(stream, audio_index, format_duration))
        audio_index += 1

    if not tracks:
        raise TrackForgeError(f"No audio tracks found in {input_path}")

    return tracks


def build_plan(tracks: list[SourceAudioTrack], job: Job) -> list[PlanItem]:
    """Turn the probed tracks plus the job into one ordered list of outputs."""

    plan: list[PlanItem] = []
    for track in tracks:
        # with --track, the tracks that are not the target are copied as-is.
        if job.target_index is not None and track.index != job.target_index:
            plan.append(PlanItem(
                source=track,
                profile=Profile(transformation=None, codec="ORIG", channels=None),
                title=track.title,
                is_default=track.is_default,
                dispositions=track.dispositions,
            ))
            continue

        for position, profile in enumerate(job.profiles):
            is_default = track.is_default and position == 0
            if profile.transformation is None and profile.codec == "ORIG":
                title = track.title
            else:
                out_channels = output_channels(track.channels, profile)
                title = output_title(profile, out_channels)

            plan.append(PlanItem(
                source=track,
                profile=profile,
                title=title,
                is_default=is_default,
                dispositions=track.dispositions,
            ))

    return plan


def print_audio_info(input_path: str, tracks: list[SourceAudioTrack]) -> None:
    """Write the input's audio tracks to stdout, one per line."""

    print(f"Audio tracks in {input_path}:")
    print(f"{'idx':>3}  {'lang':<4}  {'codec':<8}  {'channels':<10}  {'default':<3}  title")
    for track in tracks:
        layout = layout_token(track.channels)
        channels = f"{track.channels} ({layout})"
        default = "*" if track.is_default else ""
        title = track.title or ""
        print(f"{track.index:>3}  {track.language:<4}  {track.codec:<8}  {channels:<10}  {default:<3}  {title}")


def _progress_out_time(fields: dict[str, str]) -> float | None:
    """Read the encoded position in seconds from one ffmpeg -progress block."""

    # prefer the microsecond field.
    # do not trust out_time_ms because some builds put microseconds there too.
    micros = fields.get("out_time_us")
    if micros is not None and micros != "N/A":
        try:
            return int(micros) / 1_000_000
        except ValueError:
            pass

    # fall back to the HH:MM:SS.ffffff text field.
    text = fields.get("out_time")
    if text is not None and text != "N/A":
        return parse_timestamp(text)

    return None


def _terminate(process: subprocess.Popen) -> None:
    """Stop one ffmpeg process, ignoring it when it has already exited."""

    try:
        process.terminate()
    except (ProcessLookupError, OSError):
        pass


class _EncodeGroup:
    """Holds the running ffmpeg processes so a failure or a ctrl+c can stop them all."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()
        self._children: set[_EncodeGroup] = set()
        self._aborted = False

    def register(self, process: subprocess.Popen) -> None:
        with self._lock:
            # a process that starts after an abort is stopped right away.
            if self._aborted:
                _terminate(process)

            self._processes.add(process)

    def unregister(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.discard(process)

    def add(self, child: "_EncodeGroup") -> None:
        with self._lock:
            # a file that starts after the batch abort is aborted right away.
            if self._aborted:
                child.abort()

            self._children.add(child)

    def remove(self, child: "_EncodeGroup") -> None:
        with self._lock:
            self._children.discard(child)

    def abort(self) -> None:
        with self._lock:
            self._aborted = True
            for process in self._processes:
                _terminate(process)

            for child in self._children:
                child.abort()


def _encode_streaming(command: list[str], on_progress: Callable[[float, float | None], None], file_group: _EncodeGroup) -> None:
    """Run one ffmpeg encode and stream its -progress output to on_progress."""

    log.debug(f"Running: {' '.join(command)}")

    # stderr goes to a temp file so a full stderr pipe can never deadlock the stdout read.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_file, text=True)
        file_group.register(process)

        try:
            fields: dict[str, str] = {}
            for raw_line in process.stdout:
                line = raw_line.strip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                fields[key] = value.strip()
                # each block ends with a progress= line, so report what that block held.
                if key == "progress":
                    out_time = _progress_out_time(fields)
                    if out_time is not None:
                        # read ffmpeg's speed field like "1.23x", or None when it is missing.
                        speed_field = fields.get("speed")
                        if speed_field is None or speed_field == "N/A":
                            speed = None
                        else:
                            speed = parse_float(speed_field.rstrip("x").strip())

                        on_progress(out_time, speed)
                    fields = {}

            process.wait()
        finally:
            file_group.unregister(process)

        if process.returncode != 0:
            stderr_file.seek(0)
            stderr_text = stderr_file.read().strip()
            raise TrackForgeError(f"Command failed ({os.path.basename(command[0])}): {stderr_text}")


def _encode_one(index: int, ffmpeg: str, input_path: str, item: PlanItem, temp_dir: str, reporter: FileReporter, file_group: _EncodeGroup) -> PlannedAudio:
    """Encode or copy one planned output track into its own temp file."""

    out_path = os.path.join(temp_dir, f"{uuid.uuid4()}.mka")
    command = encode_command(ffmpeg, input_path, item.source, item.profile, out_path)

    reporter.item_started(index)
    try:
        _encode_streaming(command, partial(reporter.item_progress, index), file_group)
    except Exception:
        reporter.item_failed(index)
        raise

    reporter.item_done(index)

    return PlannedAudio(
        path=out_path,
        language=item.source.language,
        title=item.title,
        is_default=item.is_default,
        dispositions=item.dispositions,
    )


def encode_plan(ffmpeg: str, input_path: str, plan: list[PlanItem], temp_dir: str, workers: int, reporter: FileReporter, abort_group: _EncodeGroup) -> list[PlannedAudio]:
    """Encode or copy each planned output track into its own temp file."""

    max_workers = min(workers, len(plan)) or 1
    file_group = _EncodeGroup()
    abort_group.add(file_group)
    results: list[PlannedAudio] = []
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: list[Future[PlannedAudio]] = []
            for index, item in enumerate(plan):
                futures.append(pool.submit(_encode_one, index, ffmpeg, input_path, item, temp_dir, reporter, file_group))

            try:
                for future in futures:
                    results.append(future.result())
            except BaseException:
                # a failure or a ctrl+c stops the running encodes and drops the pending ones.
                file_group.abort()
                for future in futures:
                    future.cancel()

                # wait for the workers to notice the killed processes so none outlive us.
                wait(futures)
                raise
    finally:
        abort_group.remove(file_group)

    return results


def _report_outcomes(outcomes: list[tuple[bool, str, str]], batch: bool) -> int:
    """Log the batch summary or the single file's error, and return the exit code."""

    failures: list[tuple[str, str]] = []
    for ok, output, error in outcomes:
        if not ok:
            failures.append((output, error))

    if batch:
        log.info(f"Processed {len(outcomes) - len(failures)} of {len(outcomes)} files")
        for output, error in failures:
            log.error(f"failed: {os.path.basename(output)}: {error}")
    elif failures:
        log.error(failures[0][1])

    return 2 if failures else 0


def run_info(job: Job, simple: bool) -> int:
    """Probe the input and print its audio tracks."""

    console = Console(log_path=False)
    reporter = make_reporter(simple, console, batch=False)
    ffprobe_path = get_binary("ffprobe", required=True)

    # rich keeps these quiet since --info never opens the live region but simple logs them.
    reporter.note(f"ffprobe: {ffprobe_path} ({binary_source(ffprobe_path)})")
    tracks = ffprobe(ffprobe_path, job.input)
    reporter.note(f"Probed {len(tracks)} audio tracks")

    print_audio_info(job.input, tracks)
    return 0


def run_one(job: Job, reporter: Reporter, ffprobe_path: str, ffmpeg: str, mkvmerge: str | None, abort_group: _EncodeGroup) -> tuple[bool, str, str]:
    """Process one file end to end and capture its outcome so the batch can carry on."""

    file_reporter = reporter.add_file(job.input, job.output)
    try:
        tracks = ffprobe(ffprobe_path, job.input)
        job.validate_against_tracks(tracks)

        chosen_muxer = get_muxer(job.muxer, job.output_format, mkvmerge is not None)
        plan = build_plan(tracks, job)
        file_reporter.start(plan)

        temp_dir = tempfile.mkdtemp(prefix="trackforge_")
        temp_output = job.output + ".tmp"
        try:
            audios = encode_plan(ffmpeg, job.input, plan, temp_dir, job.workers, file_reporter, abort_group)

            # the mirrored output may sit in a subfolder that does not exist yet.
            os.makedirs(os.path.dirname(job.output) or ".", exist_ok=True)

            file_reporter.mux_started()
            file_reporter.note(f"Muxing {job.output_format} output with {chosen_muxer}")
            if chosen_muxer == "mkvmerge":
                command = mkvmerge_mux_command(mkvmerge, job.input, audios, temp_output)
            else:
                command = ffmpeg_mux_command(ffmpeg, job.input, audios, job.output_format, temp_output)

            log.debug(f"Running: {' '.join(command)}")
            result = subprocess.run(command, capture_output=True, text=True)

            # account for mkvmerge outputting errors on stdout and using exit code 1 for warnings
            if chosen_muxer == "mkvmerge":
                mux_failed = result.returncode >= 2
            else:
                mux_failed = result.returncode != 0

            if mux_failed:
                message = result.stderr.strip() or result.stdout.strip()
                raise TrackForgeError(f"Command failed ({os.path.basename(command[0])}): {message}")

            os.replace(temp_output, job.output)
            file_reporter.mux_done()
            file_reporter.note(f"Wrote {job.output}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if os.path.exists(temp_output):
                os.remove(temp_output)
    except Exception as error:
        return (False, job.output, str(error))

    return (True, job.output, "")


def run_batch(jobs: list[Job], simple: bool, file_workers: int) -> int:
    """Process every job, up to file_workers at once, and report the outcome."""

    console = Console(log_path=False)
    reporter = make_reporter(simple, console, batch=len(jobs) > 1)

    ffprobe_path = get_binary("ffprobe", required=True)
    ffmpeg = get_binary("ffmpeg", required=True)
    mkvmerge = get_binary("mkvmerge")

    abort_group = _EncodeGroup()
    outcomes: list[tuple[bool, str, str]] = []
    with reporter:
        binaries = [
            "Found binaries:",
            f"ffprobe: {ffprobe_path} ({binary_source(ffprobe_path)})",
            f"ffmpeg: {ffmpeg} ({binary_source(ffmpeg)})",
        ]
        if mkvmerge is not None:
            binaries.append(f"mkvmerge: {mkvmerge} ({binary_source(mkvmerge)})")
        reporter.note("\n".join(binaries))

        max_workers = min(file_workers, len(jobs)) or 1
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: list[Future[tuple[bool, str, str]]] = []
            for job in jobs:
                futures.append(pool.submit(run_one, job, reporter, ffprobe_path, ffmpeg, mkvmerge, abort_group))

            try:
                for future in futures:
                    outcomes.append(future.result())
            except BaseException:
                # ctrl+c stops every running file and drops the pending ones.
                abort_group.abort()
                for future in futures:
                    future.cancel()

                wait(futures)
                raise

    return _report_outcomes(outcomes, len(jobs) > 1)
