import os
import time
import threading
from dataclasses import field, dataclass

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.progress_bar import ProgressBar

from ..types.track import PlanItem
from .Audio import output_channels
from .Vars import log, layout_token


# how wide each per-encode bar is drawn, in characters.
BAR_WIDTH = 15

# how many characters wide the file name column is before it truncates.
NAME_WIDTH = 38

# the per-track progress ramp, filling as a track encodes.
_RUNNING_RAMP = "○◔◑◕"


@dataclass
class _ItemState:
    # short label shown on the row, like "EOS 2.0".
    label: str

    # track length in seconds, or None when the length is unknown.
    total: float | None

    # seconds of audio encoded so far.
    completed: float = 0.0

    # last reported encode speed multiplier, or None when unknown.
    speed: float | None = None

    # one of pending, running, done, failed.
    status: str = "pending"

    # wall clock start time, used to show elapsed time when there is no duration.
    started_at: float | None = None


@dataclass
class _FileState:
    # the input path being processed and where its result is written.
    input: str
    output: str

    # one _ItemState per planned output track for this file.
    states: list[_ItemState] = field(default_factory=list)

    # source track groupings: each is a source description plus the indices of its output tracks.
    groups: list[tuple[str, list[int]]] = field(default_factory=list)

    # one of pending, running, done for this file's mux step.
    mux_status: str = "pending"


def _item_label(item: PlanItem) -> str:
    """Short label for one planned output, like 'EOS 2.0' or 'AAC 5.1'."""

    out_channels = output_channels(item.source.channels, item.profile)
    base = item.profile.transformation or item.profile.codec

    return f"{base} {layout_token(out_channels)}"


def _track_fraction(state: _ItemState) -> float:
    """How far one output track has encoded, from 0.0 to 1.0."""

    match state.status:
        case "done":
            return 1.0
        case "running":
            if state.total is None or state.total <= 0:
                return 0.0
            return min(1.0, max(0.0, state.completed / state.total))
        case _:
            # pending and failed have made no progress.
            return 0.0


def _track_glyph(state: _ItemState) -> Text:
    """One filling dot for one output track, coloured by how it is doing."""

    match state.status:
        case "done":
            return Text("●", style="green")
        case "failed":
            return Text("✗", style="red")
        case "pending":
            return Text("○", style="dim")
        case _:
            # a running track fills through the ramp but never reaches the full circle,
            # which is reserved for done so the two never look the same.
            step = min(len(_RUNNING_RAMP) - 1, int(_track_fraction(state) * len(_RUNNING_RAMP)))
            return Text(_RUNNING_RAMP[step], style="yellow")


def _file_dots(state: _FileState) -> Text:
    """The row of per-track dots."""

    dots = Text()
    for position, (_description, indices) in enumerate(state.groups):
        if position > 0:
            dots.append(" │ ", style="dim")

        for place, index in enumerate(indices):
            if place > 0:
                dots.append(" ")
            dots.append_text(_track_glyph(state.states[index]))

    return dots


def _file_fraction(state: _FileState) -> float:
    """Overall encode progress for a file."""

    if not state.states:
        return 0.0

    total = 0.0
    for item in state.states:
        total += _track_fraction(item)

    return total / len(state.states)


def _file_row(state: _FileState) -> tuple[ProgressBar, object, Text]:
    """The bar, the middle percent/phase cell, and the dots for one file's row."""

    # before ffprobe has filled the plan there is nothing to measure yet.
    if not state.states:
        return ProgressBar(total=100, completed=0, width=BAR_WIDTH), Text("·", style="dim"), Text()

    # muxing happens after every track is encoded so the bar is full and the dots are all done.
    if state.mux_status == "running":
        return ProgressBar(total=100, completed=100, width=BAR_WIDTH), "muxing...", _file_dots(state)

    fraction = _file_fraction(state)
    bar = ProgressBar(total=100, completed=fraction * 100, width=BAR_WIDTH)
    return bar, f"{int(fraction * 100)}%", _file_dots(state)


class FileReporter:
    """Per-file progress handle. The encode and mux code drives one of these."""

    def start(self, plan: list[PlanItem]) -> None:
        raise NotImplementedError

    def item_started(self, index: int) -> None:
        raise NotImplementedError

    def item_progress(self, index: int, out_time: float, speed: float | None) -> None:
        raise NotImplementedError

    def item_done(self, index: int) -> None:
        raise NotImplementedError

    def item_failed(self, index: int) -> None:
        raise NotImplementedError

    def mux_started(self) -> None:
        raise NotImplementedError

    def mux_done(self) -> None:
        raise NotImplementedError

    def note(self, message: str) -> None:
        raise NotImplementedError

    def finish(self, ok: bool) -> None:
        raise NotImplementedError


class Reporter:
    """Owns the whole display and hands out one FileReporter per input file."""

    def __enter__(self) -> "Reporter":
        raise NotImplementedError

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        raise NotImplementedError

    def note(self, message: str) -> None:
        """Show a batch level milestone line, like the found binaries."""

        raise NotImplementedError

    def add_file(self, input_path: str, output_path: str) -> FileReporter:
        """Register one input file and return its own progress handle."""

        raise NotImplementedError


class RichFileReporter(FileReporter):
    """One file's progress, held as shared state that the Live thread renders."""

    def __init__(self, reporter: "RichReporter", console: Console, lock: threading.Lock, state: _FileState) -> None:
        self._reporter = reporter
        self._console = console
        self._lock = lock
        self._state = state

    def start(self, plan: list[PlanItem]) -> None:
        with self._lock:
            states: list[_ItemState] = []
            grouped: dict[int, list[int]] = {}
            for index, item in enumerate(plan):
                states.append(_ItemState(label=_item_label(item), total=item.source.duration))
                if item.source.index not in grouped:
                    grouped[item.source.index] = []
                grouped[item.source.index].append(index)

            groups: list[tuple[str, list[int]]] = []
            for indices in grouped.values():
                source = plan[indices[0]].source
                description = f"[{source.index}] {source.language} {source.codec} {layout_token(source.channels)}"
                groups.append((description, indices))

            self._state.states = states
            self._state.groups = groups

    def item_started(self, index: int) -> None:
        with self._lock:
            state = self._state.states[index]
            state.status = "running"
            state.started_at = time.monotonic()

    def item_progress(self, index: int, out_time: float, speed: float | None) -> None:
        with self._lock:
            state = self._state.states[index]
            state.completed = out_time
            if speed is not None:
                state.speed = speed

    def item_done(self, index: int) -> None:
        with self._lock:
            state = self._state.states[index]
            state.status = "done"
            if state.total is not None:
                state.completed = state.total

    def item_failed(self, index: int) -> None:
        with self._lock:
            self._state.states[index].status = "failed"

    def mux_started(self) -> None:
        with self._lock:
            self._state.mux_status = "running"

    def mux_done(self) -> None:
        with self._lock:
            self._state.mux_status = "done"

    def note(self, message: str) -> None:
        self._console.log(message)

    def finish(self, ok: bool) -> None:
        # drop the file from the live region so it stays bounded to the files still encoding.
        self._reporter.retire_file(self._state, ok)


class RichReporter(Reporter):
    """A compact live region: a header plus one row per file still being processed."""

    def __init__(self, console: Console, total: int) -> None:
        self._console = console
        self._total = total
        self._completed = 0
        self._failed = 0
        self._lock = threading.Lock()
        self._active = False
        self._files: list[_FileState] = []
        self._live = Live(console=console, get_renderable=self._render, refresh_per_second=10)

    def __enter__(self) -> "RichReporter":
        self._active = True
        self._live.start()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        self._live.stop()
        self._active = False
        return False

    def note(self, message: str) -> None:
        """Print a batch milestone above the live region while it is running."""

        if self._active:
            self._console.log(message)

    def add_file(self, input_path: str, output_path: str) -> FileReporter:
        with self._lock:
            state = _FileState(input=input_path, output=output_path)
            self._files.append(state)
            return RichFileReporter(self, self._console, self._lock, state)

    def retire_file(self, state: _FileState, ok: bool) -> None:
        """Remove a finished file from the live region."""

        with self._lock:
            if state in self._files:
                self._files.remove(state)
            if ok:
                self._completed += 1
            else:
                self._failed += 1

        if not ok:
            self._console.log(Text(f"failed: {os.path.basename(state.output)}", style="red"))

    def _header(self) -> Text:
        """Build the header line for the live region."""

        header = Text()
        header.append("TrackForge", style="bold")
        header.append("  ·  ", style="dim")
        header.append(f"{self._completed}/{self._total} done")
        header.append("  ·  ", style="dim")
        header.append(f"{len(self._files)} running")

        if self._failed:
            header.append("  ·  ", style="dim")
            header.append(f"{self._failed} failed", style="red")

        return header

    def _render(self) -> Group | Text:
        """Rebuild the live region: the header, then one row per file still processing."""

        with self._lock:
            header = self._header()
            if not self._files:
                return header

            table = Table.grid(padding=(0, 1))
            table.add_column(width=NAME_WIDTH, no_wrap=True, overflow="ellipsis")
            table.add_column(width=BAR_WIDTH, no_wrap=True)
            table.add_column(width=9, justify="right", no_wrap=True)
            table.add_column(no_wrap=True)

            for state in self._files:
                bar, middle, dots = _file_row(state)
                table.add_row(os.path.basename(state.output), bar, middle, dots)

            return Group(header, Text(""), table)


class SimpleFileReporter(FileReporter):
    """One file's progress as plain log lines. Used for --simple and -v."""

    def __init__(self, tag: str) -> None:
        self._tag = tag
        self._labels: list[str] = []
        self._total = 0

    def start(self, plan: list[PlanItem]) -> None:
        self._total = len(plan)
        self._labels = []
        for item in plan:
            self._labels.append(_item_label(item))

    def item_started(self, index: int) -> None:
        log.debug(f"{self._tag}[{index + 1}/{self._total}] {self._labels[index]} start")

    def item_progress(self, index: int, out_time: float, speed: float | None) -> None:
        """Simple output has no live bar so progress is ignored."""

    def item_done(self, index: int) -> None:
        log.info(f"{self._tag}[{index + 1}/{self._total}] {self._labels[index]} ... done")

    def item_failed(self, index: int) -> None:
        log.error(f"{self._tag}[{index + 1}/{self._total}] {self._labels[index]} failed")

    def mux_started(self) -> None:
        """The muxing milestone is written through note, not here."""

    def mux_done(self) -> None:
        """Simple output has no mux spinner to stop."""

    def note(self, message: str) -> None:
        log.info(f"{self._tag}{message}")

    def finish(self, ok: bool) -> None:
        """Simple output has no live region to retire a file from."""


class SimpleReporter(Reporter):
    """Plain stdout log lines instead of a live region. Used for --simple and -v."""

    def __init__(self, batch: bool) -> None:
        self._batch = batch

    def __enter__(self) -> "SimpleReporter":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        return False

    def note(self, message: str) -> None:
        log.info(message)

    def add_file(self, input_path: str, output_path: str) -> FileReporter:
        # in a batch, tag each line with the file so concurrent output stays readable.
        tag = f"[{os.path.basename(output_path)}] " if self._batch else ""
        return SimpleFileReporter(tag)


def make_reporter(simple: bool, console: Console, batch: bool, total: int = 1) -> Reporter:
    """Pick the reporter for this run. Non-TTY is handled by RichReporter itself."""

    if simple:
        return SimpleReporter(batch)
    return RichReporter(console, total)
