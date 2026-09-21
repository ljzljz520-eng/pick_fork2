import curses
import textwrap
from collections import namedtuple
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Container,
    Generic,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
)

import cadule

from .backend import (
    Backend,
    Event,
    EventKind,
    Frame,
    FrameSpan,
    KEYS_DOWN,
    KEYS_ENTER,
    KEYS_SELECT,
    KEYS_UP,
)
from .blessed_backend import BlessedBackend
from .curses_backend import CursesBackend

__all__ = [
    "Picker",
    "Session",
    "SessionState",
    "SessionResult",
    "pick",
    "Option",
    "Position",
    "Backend",
    "CursesBackend",
    "BlessedBackend",
    "Event",
    "EventKind",
    "Frame",
    "FrameSpan",
    "__call__",
    "SYMBOL_CIRCLE_FILLED",
    "SYMBOL_CIRCLE_EMPTY",
]


@dataclass
class Option:
    label: str
    value: Any = None
    description: Optional[str] = None
    enabled: bool = True


SYMBOL_CIRCLE_FILLED = "(x)"
SYMBOL_CIRCLE_EMPTY = "( )"

OPTION_T = TypeVar("OPTION_T", str, Option)
PICK_RETURN_T = Tuple[OPTION_T, int]

Position = namedtuple('Position', ['y', 'x'])


class SessionState(Enum):
    """Lifecycle state of a pick session."""

    PENDING = "pending"
    SELECTED = "selected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SessionResult(Generic[OPTION_T]):
    """Terminal (or pending) state of a session plus its selection.

    ``selection`` is set only when the state is ``SELECTED``; the legacy
    cancellation return values (``[]`` / ``(None, -1)``) can be obtained from
    :meth:`Session.cancel_return`.
    """

    state: SessionState
    selection: Optional[Union[List[PICK_RETURN_T], PICK_RETURN_T]]

    @property
    def pending(self) -> bool:
        return self.state is SessionState.PENDING

    @property
    def selected(self) -> bool:
        return self.state is SessionState.SELECTED

    @property
    def cancelled(self) -> bool:
        return self.state is SessionState.CANCELLED


def _validate_options(
    options: Sequence[OPTION_T],
    default_index: int,
    multiselect: bool,
    min_selection_count: int,
) -> None:
    if len(options) == 0:
        raise ValueError("options should not be an empty list")

    if default_index >= len(options):
        raise ValueError("default_index should be less than the length of options")

    if multiselect and min_selection_count > len(options):
        raise ValueError(
            "min_selection_count is bigger than the available options, you will not be able to make any selection"
        )

    if all(isinstance(option, Option) and not option.enabled for option in options):
        raise ValueError(
            "all given options are disabled, you must at least have one enabled option."
        )


@dataclass
class Session(Generic[OPTION_T]):
    """An explicit, non-blocking pick session state machine.

    Driven entirely by normalized events; it never acquires the terminal or
    blocks for input::

        session = picker.begin()
        while session.state is SessionState.PENDING:
            backend.commit(session.render(*backend.getmaxyx()))
            session.dispatch(backend.read_event())   # or a host-fed event
        if session.result().selected:
            ...

    :meth:`run_loop` is only a thin blocking adapter on top of this loop.
    """

    options: Sequence[OPTION_T]
    title: Optional[str] = None
    indicator: str = "*"
    default_index: int = 0
    multiselect: bool = False
    min_selection_count: int = 0
    position: Position = Position(0, 0)
    clear_screen: bool = True
    quit_keys: Optional[Union[Container[int], Iterable[int]]] = None

    index: int = field(init=False)
    selected_indexes: List[int] = field(init=False, default_factory=list)
    state: SessionState = field(init=False, default=SessionState.PENDING)

    def __post_init__(self) -> None:
        _validate_options(
            self.options, self.default_index, self.multiselect, self.min_selection_count
        )
        self.index = self.default_index
        option = self.options[self.index]
        if isinstance(option, Option) and not option.enabled:
            self.move_down()

    # -- state transitions --------------------------------------------------

    def move_up(self) -> None:
        while True:
            self.index -= 1
            if self.index < 0:
                self.index = len(self.options) - 1
            option = self.options[self.index]
            if not isinstance(option, Option) or option.enabled:
                break

    def move_down(self) -> None:
        while True:
            self.index += 1
            if self.index >= len(self.options):
                self.index = 0
            option = self.options[self.index]
            if not isinstance(option, Option) or option.enabled:
                break

    def mark_index(self) -> None:
        if self.multiselect:
            option = self.options[self.index]
            if isinstance(option, Option) and not option.enabled:
                return
            if self.index in self.selected_indexes:
                self.selected_indexes.remove(self.index)
            else:
                self.selected_indexes.append(self.index)

    def dispatch(self, event: Event) -> SessionState:
        """Apply exactly one normalized event and return the new state.

        Dispatched events are ignored once the session has reached a terminal
        state, so late events from a host event loop cannot revive a finished
        session.
        """
        if self.state is not SessionState.PENDING:
            return self.state

        if (
            event.code is not None
            and self.quit_keys is not None
            and event.code in self.quit_keys
        ):
            self.state = SessionState.CANCELLED
            return self.state

        if event.kind is EventKind.UP:
            self.move_up()
        elif event.kind is EventKind.DOWN:
            self.move_down()
        elif event.kind is EventKind.CONFIRM:
            if not (
                self.multiselect
                and len(self.selected_indexes) < self.min_selection_count
            ):
                self.state = SessionState.SELECTED
        elif event.kind is EventKind.SELECT and self.multiselect:
            self.mark_index()
        return self.state

    def result(self) -> SessionResult[OPTION_T]:
        selection = self.get_selected() if self.state is SessionState.SELECTED else None
        return SessionResult(self.state, selection)

    def cancel_return(self) -> Union[List[PICK_RETURN_T], Tuple[None, int]]:
        """Legacy return value used when the session is cancelled."""
        return [] if self.multiselect else (None, -1)

    def get_selected(self) -> Union[List[PICK_RETURN_T], PICK_RETURN_T]:
        """return the current selected option as a tuple: (option, index)
        or as a list of tuples (in case multiselect==True)
        """
        if self.multiselect:
            return_tuples = []
            for selected in self.selected_indexes:
                return_tuples.append((self.options[selected], selected))
            return return_tuples
        else:
            return self.options[self.index], self.index

    # -- pure view / frame generation --------------------------------------

    def get_title_lines(self, *, max_width: int = 80) -> List[str]:
        if not self.title:
            return []

        if "\n" in self.title:
            lines = self.title.split("\n")
        else:
            lines = textwrap.fill(self.title, max_width - 2, drop_whitespace=False).split("\n")
        return lines + [""]

    def get_option_lines(self) -> List[str]:
        lines: List[str] = []
        for index, option in enumerate(self.options):
            if index == self.index:
                prefix = self.indicator
            else:
                prefix = len(self.indicator) * " "

            if self.multiselect:
                symbol = (
                    SYMBOL_CIRCLE_FILLED
                    if index in self.selected_indexes
                    else SYMBOL_CIRCLE_EMPTY
                )
                prefix = f"{prefix} {symbol}"

            option_as_str = option.label if isinstance(option, Option) else option
            lines.append(f"{prefix} {option_as_str}")

        return lines

    def get_lines(self, *, max_width: int = 80) -> Tuple[List[str], int]:
        title_lines = self.get_title_lines(max_width=max_width)
        option_lines = self.get_option_lines()
        lines = title_lines + option_lines
        current_line = self.index + len(title_lines) + 1
        return lines, current_line

    def render(self, max_y: int, max_x: int) -> Frame:
        """Build the current frame for a viewport of ``(max_y, max_x)``.

        Pure computation: the host decides when to render and may inspect or
        composite the returned frame instead of submitting it to a backend.
        """
        y, x = self.position  # start point
        max_rows = max_y - y  # the max rows we can draw

        lines, current_line = self.get_lines(max_width=max_x)

        # calculate how many lines we should scroll, relative to the top
        scroll_top = 0
        if current_line > max_rows:
            scroll_top = current_line - max_rows

        lines_to_draw = lines[scroll_top : scroll_top + max_rows]

        description_present = False
        for option in self.options:
            if isinstance(option, Option) and option.description is not None:
                description_present = True
                break

        title_length = len(self.get_title_lines(max_width=max_x))

        spans: List[FrameSpan] = []
        for i, line in enumerate(lines_to_draw):
            if description_present and i > title_length:
                width = max_x // 2 - 2
            else:
                width = max_x - 2
            spans.append(FrameSpan(y, x, line, width))
            y += 1

        option = self.options[self.index]
        if isinstance(option, Option) and option.description is not None:
            description_lines = textwrap.fill(option.description, max_x // 2 - 2).split('\n')

            for i, line in enumerate(description_lines):
                spans.append(FrameSpan(i + title_length, max_x // 2, line, max_x - 2))

        return Frame(clear=self.clear_screen, spans=tuple(spans))

    # -- blocking adapter ---------------------------------------------------

    def draw(self, screen: Backend) -> None:
        """render and commit one frame (blocking adapter helper)"""
        screen.commit(self.render(*screen.getmaxyx()))

    def run_loop(
        self,
        screen: Backend,
        position: Optional[Position] = None,
    ) -> Union[List[PICK_RETURN_T], PICK_RETURN_T, Tuple[None, int]]:
        """Blocking adapter: render, wait for input and dispatch until done."""
        if position is not None:
            self.position = position
        while self.state is SessionState.PENDING:
            self.draw(screen)
            self.dispatch(screen.read_event())

        result = self.result()
        selection = result.selection
        if result.state is SessionState.SELECTED and selection is not None:
            return selection
        return self.cancel_return()


@dataclass
class Picker(Generic[OPTION_T]):
    options: Sequence[OPTION_T]
    title: Optional[str] = None
    indicator: str = "*"
    default_index: int = 0
    multiselect: bool = False
    min_selection_count: int = 0
    screen: Optional[curses.window] = None
    position: Position = Position(0, 0)
    clear_screen: bool = True
    quit_keys: Optional[Union[Container[int], Iterable[int]]] = None
    backend: Union[str, Backend] = "curses"

    _session: "Session[OPTION_T]" = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._session = self._new_session()

    def _new_session(self) -> Session[OPTION_T]:
        return Session(
            options=self.options,
            title=self.title,
            indicator=self.indicator,
            default_index=self.default_index,
            multiselect=self.multiselect,
            min_selection_count=self.min_selection_count,
            position=self.position,
            clear_screen=self.clear_screen,
            quit_keys=self.quit_keys,
        )

    # -- explicit session state machine API ---------------------------------

    def begin(self) -> Session[OPTION_T]:
        """Create a fresh session for this picker's configuration."""
        self._session = self._new_session()
        return self._session

    def dispatch(self, event: Event) -> SessionState:
        return self._session.dispatch(event)

    def render(self, max_y: int, max_x: int) -> Frame:
        return self._session.render(max_y, max_x)

    def result(self) -> SessionResult[OPTION_T]:
        return self._session.result()

    @property
    def state(self) -> SessionState:
        return self._session.state

    # -- legacy session proxy -----------------------------------------------

    @property
    def index(self) -> int:
        return self._session.index

    @index.setter
    def index(self, value: int) -> None:
        self._session.index = value

    @property
    def selected_indexes(self) -> List[int]:
        return self._session.selected_indexes

    def move_up(self) -> None:
        self._session.move_up()

    def move_down(self) -> None:
        self._session.move_down()

    def mark_index(self) -> None:
        self._session.mark_index()

    def get_selected(self) -> Union[List[PICK_RETURN_T], PICK_RETURN_T]:
        return self._session.get_selected()

    def get_title_lines(self, *, max_width: int = 80) -> List[str]:
        return self._session.get_title_lines(max_width=max_width)

    def get_option_lines(self) -> List[str]:
        return self._session.get_option_lines()

    def get_lines(self, *, max_width: int = 80) -> Tuple[List[str], int]:
        return self._session.get_lines(max_width=max_width)

    def draw(self, screen: Backend) -> None:
        self._session.draw(screen)

    def run_loop(
        self, screen: Backend, position: Position
    ) -> Union[List[PICK_RETURN_T], PICK_RETURN_T, Tuple[None, int]]:
        return self._session.run_loop(screen, position)

    def _resolve_backend(self) -> Backend:
        if isinstance(self.backend, Backend):
            return self.backend
        if self.backend == "curses":
            return CursesBackend(screen=self.screen)
        if self.backend == "blessed":
            return BlessedBackend()
        raise ValueError(
            f"Unknown backend: {self.backend!r}. "
            "Use 'curses', 'blessed', or a Backend instance."
        )

    def config_curses(self) -> None:
        try:
            # use the default colors of the terminal
            curses.use_default_colors()
            # hide the cursor
            curses.curs_set(0)
        except Exception:
            # Curses failed to initialize color support, eg. when TERM=vt100
            curses.initscr()

    def _start(self, screen: curses.window):
        self.config_curses()
        return self.begin().run_loop(CursesBackend(screen=screen), self.position)

    def start(self):
        session = self.begin()
        backend = self._resolve_backend()
        if isinstance(backend, CursesBackend) and backend._screen is not None:
            # Embedded in an existing curses application (backward-compatible)
            last_cur = curses.curs_set(0)
            ret = session.run_loop(backend, self.position)
            if last_cur:
                curses.curs_set(last_cur)
            return ret
        elif isinstance(backend, CursesBackend):
            # Standalone curses mode
            def _curses_main(screen: curses.window):
                backend._screen = screen
                backend.setup()
                return session.run_loop(backend, self.position)
            return curses.wrapper(_curses_main)
        else:
            # Other backends (e.g. blessed)
            backend.setup()
            try:
                return session.run_loop(backend, self.position)
            finally:
                backend.teardown()


def pick(
    options: Sequence[OPTION_T],
    title: Optional[str] = None,
    indicator: str = "*",
    default_index: int = 0,
    multiselect: bool = False,
    min_selection_count: int = 0,
    screen: Optional[curses.window] = None,
    position: Position = Position(0, 0),
    clear_screen: bool = True,
    quit_keys: Optional[Union[Container[int], Iterable[int]]] = None,
    backend: Union[str, Backend] = "curses",
):
    picker: Picker = Picker(
        options,
        title,
        indicator,
        default_index,
        multiselect,
        min_selection_count,
        screen,
        position,
        clear_screen,
        quit_keys,
        backend,
    )
    return picker.start()


@cadule
def __call__(
    options: Sequence[OPTION_T],
    title: Optional[str] = None,
    indicator: str = "*",
    default_index: int = 0,
    multiselect: bool = False,
    min_selection_count: int = 0,
    screen: Optional[curses.window] = None,
    position: Position = Position(0, 0),
    clear_screen: bool = True,
    quit_keys: Optional[Union[Container[int], Iterable[int]]] = None,
    backend: Union[str, Backend] = "curses",
):
    return pick(
        options,
        title,
        indicator,
        default_index,
        multiselect,
        min_selection_count,
        screen,
        position,
        clear_screen,
        quit_keys,
        backend,
    )
