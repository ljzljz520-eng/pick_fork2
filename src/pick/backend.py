import curses
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

# Raw key codes use the curses numbering; the blessed backend maps its
# sequences onto the same codes, so key translation can live in one place.
KEYS_ENTER = (curses.KEY_ENTER, ord("\n"), ord("\r"))
KEYS_UP = (curses.KEY_UP, ord("k"))
KEYS_DOWN = (curses.KEY_DOWN, ord("j"))
KEYS_SELECT = (curses.KEY_RIGHT, ord(" "))


class EventKind(Enum):
    """Backend independent input intent.

    Anything that is not a navigation/selection key arrives as ``KEY`` and
    carries the raw key code, so that host level policy (e.g. quit keys) can
    be decided outside the backend.
    """

    UP = "up"
    DOWN = "down"
    SELECT = "select"
    CONFIRM = "confirm"
    KEY = "key"


@dataclass(frozen=True)
class Event:
    """A single normalized input event.

    ``code`` carries the raw backend key code so that host level policy
    (e.g. configurable quit keys) can be decided outside the backend; it is
    ``None`` for synthetic events.
    """

    kind: EventKind
    code: Optional[int] = None

    @classmethod
    def up(cls) -> "Event":
        return cls(EventKind.UP)

    @classmethod
    def down(cls) -> "Event":
        return cls(EventKind.DOWN)

    @classmethod
    def select(cls) -> "Event":
        return cls(EventKind.SELECT)

    @classmethod
    def confirm(cls) -> "Event":
        return cls(EventKind.CONFIRM)

    @classmethod
    def key(cls, code: int) -> "Event":
        return cls(EventKind.KEY, code)


@dataclass(frozen=True)
class FrameSpan:
    """One string placed at an absolute terminal coordinate."""

    y: int
    x: int
    text: str
    max_width: int


@dataclass(frozen=True)
class Frame:
    """A backend independent rendering of one session state.

    A host can inspect or composite the spans itself instead of submitting
    the frame to a backend.
    """

    clear: bool
    spans: Tuple[FrameSpan, ...]


class Backend(ABC):
    """Abstract pick UI backend.

    The contract is split into three concerns:

    * terminal ownership: :meth:`setup` / :meth:`teardown`
    * raw input acquisition: :meth:`getch`, with normalized translation via
      :meth:`translate_key` / :meth:`read_event`
    * frame submission: :meth:`commit` (built on top of the low level
      :meth:`clear` / :meth:`addnstr` / :meth:`refresh` primitives)
    """

    # -- terminal ownership -------------------------------------------------

    @abstractmethod
    def setup(self) -> None: ...

    @abstractmethod
    def teardown(self) -> None: ...

    # -- raw input ----------------------------------------------------------

    @abstractmethod
    def getch(self) -> int: ...

    def translate_key(self, code: int) -> Event:
        """Translate a raw key code into a normalized :class:`Event`."""
        if code in KEYS_UP:
            return Event(EventKind.UP, code)
        if code in KEYS_DOWN:
            return Event(EventKind.DOWN, code)
        if code in KEYS_ENTER:
            return Event(EventKind.CONFIRM, code)
        if code in KEYS_SELECT:
            return Event(EventKind.SELECT, code)
        return Event(EventKind.KEY, code)

    def read_event(self) -> Event:
        """Block for one key press and return it as a normalized event."""
        return self.translate_key(self.getch())

    # -- frame submission ---------------------------------------------------

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def getmaxyx(self) -> Tuple[int, int]: ...

    @abstractmethod
    def addnstr(self, y: int, x: int, s: str, n: int) -> None: ...

    @abstractmethod
    def refresh(self) -> None: ...

    def commit(self, frame: Frame) -> None:
        """Submit a whole :class:`Frame` to the terminal."""
        if frame.clear:
            self.clear()
        for span in frame.spans:
            self.addnstr(span.y, span.x, span.text, span.max_width)
        self.refresh()
