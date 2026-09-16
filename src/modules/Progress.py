import os
import time
import threading
from dataclasses import field, dataclass

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich.progress_bar import ProgressBar

from ..types.track import PlanItem
from .Audio import output_channels
from .Vars import log, layout_token


# how wide each per-encode bar is drawn, in characters.
BAR_WIDTH = 15


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

    # source track groupings: each is a tree line plus the item indices under it.
    groups: list[tuple[str, list[int]]] = field(default_factory=list)

    # one of pending, running, done for this file's mux step.
    mux_status: str = "pending"


def _item_label(item: PlanItem) -> str:
    """Short label for one planned output, like 'EOS 2.0' or 'AAC 5.1'."""

    out_channels = output_channels(item.source.channels, item.profile)
    base = item.profile.transformation or item.profile.codec

    return f"{base} {layout_token(out_channels)}"


def _elapsed_note(state: _ItemState) -> str:
    """Elapsed time and speed for a running bar that has no known duration."""

    if state.started_at is None:
        return ""

    elapsed = int(time.monotonic() - state.started_at)
    if state.speed is not None and state.speed > 0:
        return f"{elapsed}s {state.speed:.1f}x"

    return f"{elapsed}s"


def _item_bar(state: _ItemState) -> ProgressBar:
    """Pick the progress bar for one output row based on its state."""

    match state.status:
        case "done":
            return ProgressBar(total=100, completed=100, width=BAR_WIDTH)
        case "pending" | "failed":
            return ProgressBar(total=100, completed=0, width=BAR_WIDTH)
        case _:
            # a running bar with no known duration pulses instead of filling.
            if state.total is None or state.total <= 0:
                return ProgressBar(total=None, width=BAR_WIDTH)

            completed = min(state.total, max(0.0, state.completed))
            return ProgressBar(total=state.total, completed=completed, width=BAR_WIDTH)


def _item_cells(state: _ItemState) -> tuple[str, object]:
    """Build the percent and note cells for one output row."""

    match state.status:
        case "pending":
            return "", ""
        case "failed":
            return "", Text("failed", style="red")
        case "done":
            if state.total is None or state.total <= 0:
                return "", "done"
            return "100%", "done"
        case _:
            # a running bar shows a percent and eta, or elapsed time when the duration is unknown.
            if state.total is None or state.total <= 0:
                return "", _elapsed_note(state)

            percent = int(min(1.0, state.completed / state.total) * 100)
            if state.speed is not None and state.speed > 0:
                remaining = max(0.0, state.total - state.completed)
                eta = int(remaining / state.speed)
                return f"{percent}%", f"~{eta}s"

            return f"{percent}%", "~?s"


def _render_item(state: _ItemState) -> Table:
    """Build one output row: label, bar, percent, and note."""

    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=10, no_wrap=True)
    grid.add_column(width=BAR_WIDTH, no_wrap=True)
    grid.add_column(width=4, justify="right", no_wrap=True)
    grid.add_column(no_wrap=True)
    percent, note = _item_cells(state)
    grid.add_row(state.label, _item_bar(state), percent, note)
    return grid


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

    def __init__(self, console: Console, lock: threading.Lock, state: _FileState) -> None:
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


class RichReporter(Reporter):
    """A live tree of every file's per-encode bars, rebuilt from shared state."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._lock = threading.Lock()
        self._active = False
        self._files: list[_FileState] = []
        self._spinner = Spinner("dots", text=" muxing")
        self._live = Live(console=console, get_renderable=self._render, refresh_per_second=5)

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
            return RichFileReporter(self._console, self._lock, state)

    def _render(self) -> Tree:
        """Rebuild the whole display from shared state."""

        with self._lock:
            if len(self._files) == 1:
                return self._file_tree(self._files[0], header=True)

            root = Tree(f"Processing {len(self._files)} files")
            for state in self._files:
                root.add(self._file_tree(state, header=False))
            return root

    def _file_tree(self, state: _FileState, header: bool) -> Tree:
        """Build one file's tree, either headed on its own or as a batch branch."""

        if header:
            tree = Tree(f"Processing: {state.input}  ->  {state.output}")
            container = tree.add(os.path.basename(state.output))
        else:
            tree = Tree(os.path.basename(state.output))
            container = tree

        for description, indices in state.groups:
            source_node = container.add(description)
            for index in indices:
                source_node.add(_render_item(state.states[index]))

        match state.mux_status:
            case "running":
                tree.add(self._spinner)
            case "done":
                tree.add(Text("mux done"))

        return tree


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


def make_reporter(simple: bool, console: Console, batch: bool) -> Reporter:
    """Pick the reporter for this run. Non-TTY is handled by RichReporter itself."""

    if simple:
        return SimpleReporter(batch)
    return RichReporter(console)
