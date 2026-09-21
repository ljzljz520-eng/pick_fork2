from typing import List, Tuple

from pick import (
    Event,
    EventKind,
    Frame,
    Picker,
    Option,
    Session,
    SessionState,
)
from pick.curses_backend import CursesBackend


class ScriptedBackend(CursesBackend):
    """Backend that replays scripted raw key codes and records frames.

    It never touches a real terminal, which lets us exercise the blocking
    adapter without curses.
    """

    def __init__(self, key_codes: List[int], size: Tuple[int, int] = (24, 80)) -> None:
        super().__init__(screen=None)
        self._codes = iter(key_codes)
        self._size = size
        self.frames: List[Frame] = []
        self.committed = 0

    def getmaxyx(self) -> Tuple[int, int]:
        return self._size

    def clear(self) -> None:
        pass

    def addnstr(self, y: int, x: int, s: str, n: int) -> None:
        pass

    def refresh(self) -> None:
        pass

    def getch(self) -> int:
        return next(self._codes)

    def commit(self, frame: Frame) -> None:
        self.frames.append(frame)
        self.committed += 1


def test_move_up_down():
    title = "Please choose an option: "
    options = ["option1", "option2", "option3"]
    picker = Picker(options, title)
    picker.move_up()
    assert picker.get_selected() == ("option3", 2)
    picker.move_down()
    picker.move_down()
    assert picker.get_selected() == ("option2", 1)


def test_default_index():
    title = "Please choose an option: "
    options = ["option1", "option2", "option3"]
    picker = Picker(options, title, default_index=1)
    assert picker.get_selected() == ("option2", 1)


def test_get_lines():
    title = "Please choose an option: "
    options = ["option1", "option2", "option3"]
    picker = Picker(options, title, indicator="*")
    lines, current_line = picker.get_lines()
    assert lines == [title, "", "* option1", "  option2", "  option3"]
    assert current_line == 3


def test_no_title():
    options = ["option1", "option2", "option3"]
    picker = Picker(options)
    _, current_line = picker.get_lines()
    assert current_line == 1


def test_pick_list_of_non_str_and_option():
    # More details: https://github.com/aisk/pick/issues/120
    options = [{"key1": "value1"}, {"key2": "value2"}]
    picker = Picker(options)  # type: ignore
    lines, _ = picker.get_lines()
    assert lines == ["* {'key1': 'value1'}", "  {'key2': 'value2'}"]


def test_multi_select():
    title = "Please choose an option: "
    options = ["option1", "option2", "option3"]
    picker = Picker(options, title, multiselect=True, min_selection_count=1)
    assert picker.get_selected() == []
    picker.mark_index()
    assert picker.get_selected() == [("option1", 0)]
    picker.move_down()
    picker.mark_index()
    assert picker.get_selected() == [("option1", 0), ("option2", 1)]


def test_option():
    options = [Option("option1", 101, "description1"), Option("option2", 102),
               Option("option3", description="description3"), Option("option4")]
    picker = Picker(options, multiselect=True)
    for _ in range(4):
        picker.mark_index()
        picker.move_down()
    selected_options = picker.get_selected()
    for option in selected_options:
        assert isinstance(option, tuple)
        assert isinstance(option[0], Option)
    option = selected_options[0]
    assert option[0].label == "option1"
    assert option[0].value == 101
    assert option[0].description == "description1"

def test_disabled_option():
    options = [Option("option1"), Option("option2", enabled=False), Option("option3")]
    picker = Picker(options)
    assert picker.get_selected() == (Option("option1"), 0)
    picker.move_down()
    assert picker.get_selected() == (Option("option3"), 2)


def test_mark_index_disabled_option():
    options = [Option("option1"), Option("option2", enabled=False), Option("option3")]
    picker = Picker(options, multiselect=True)
    picker.index = 1  # Point directly to disabled option2
    picker.mark_index()
    assert picker.get_selected() == []  # Disabled option should NOT be marked


# ---------------------------------------------------------------------------
# Explicit session state machine
# ---------------------------------------------------------------------------


def test_begin_returns_pending_session():
    picker = Picker(["a", "b", "c"], "title")
    session = picker.begin()
    assert isinstance(session, Session)
    assert session.state is SessionState.PENDING
    assert session.result().pending
    assert session.result().selection is None
    # begin() also becomes the picker's active session
    assert picker.state is SessionState.PENDING


def test_begin_resets_session_state():
    picker = Picker(["a", "b", "c"])
    picker.dispatch(Event.confirm())
    assert picker.state is SessionState.SELECTED
    session = picker.begin()
    assert session.state is SessionState.PENDING
    assert session.index == 0
    assert session.selected_indexes == []


def test_dispatch_navigation_and_confirm():
    session = Picker(["option1", "option2", "option3"]).begin()

    assert session.dispatch(Event.down()) is SessionState.PENDING
    assert session.index == 1
    assert session.dispatch(Event.down()) is SessionState.PENDING
    assert session.index == 2
    assert session.dispatch(Event.up()) is SessionState.PENDING
    assert session.index == 1

    assert session.dispatch(Event.confirm()) is SessionState.SELECTED
    result = session.result()
    assert result.selected
    assert result.selection == ("option2", 1)


def test_dispatch_multiselect_mark_and_select():
    session = Picker(
        ["option1", "option2", "option3"], multiselect=True
    ).begin()

    session.dispatch(Event.select())
    session.dispatch(Event.down())
    session.dispatch(Event.select())
    assert session.selected_indexes == [0, 1]
    assert session.dispatch(Event.confirm()) is SessionState.SELECTED
    assert session.result().selection == [("option1", 0), ("option2", 1)]


def test_dispatch_confirm_blocked_below_min_selection():
    session = Picker(
        ["option1", "option2"], multiselect=True, min_selection_count=2
    ).begin()
    session.dispatch(Event.select())
    assert session.dispatch(Event.confirm()) is SessionState.PENDING
    session.dispatch(Event.down())
    session.dispatch(Event.select())
    assert session.dispatch(Event.confirm()) is SessionState.SELECTED


def test_dispatch_quit_key_cancels_single_select():
    session = Picker(["a", "b"], quit_keys=[ord("q")]).begin()
    assert session.dispatch(Event.key(ord("q"))) is SessionState.CANCELLED
    result = session.result()
    assert result.cancelled
    assert result.selection is None
    assert session.cancel_return() == (None, -1)


def test_dispatch_quit_key_cancels_multiselect():
    session = Picker(
        ["a", "b"], multiselect=True, quit_keys=[ord("q")]
    ).begin()
    session.dispatch(Event.select())
    assert session.dispatch(Event.key(ord("q"))) is SessionState.CANCELLED
    assert session.cancel_return() == []


def test_dispatch_quit_check_precedes_movement():
    # 'k' is normally KEYS_UP, but as a quit key it must cancel
    session = Picker(["a", "b"], quit_keys=[ord("k")]).begin()
    assert session.dispatch(Event(EventKind.UP, ord("k"))) is SessionState.CANCELLED


def test_dispatch_ignored_after_terminal_state():
    session = Picker(["a", "b"]).begin()
    session.dispatch(Event.confirm())
    assert session.dispatch(Event.down()) is SessionState.SELECTED
    assert session.index == 0
    assert session.dispatch(Event.key(ord("q"))) is SessionState.SELECTED


def test_unrecognized_event_is_noop():
    session = Picker(["a", "b"]).begin()
    assert session.dispatch(Event.key(ord("z"))) is SessionState.PENDING
    assert session.index == 0


# ---------------------------------------------------------------------------
# Frame rendering (pure, no terminal)
# ---------------------------------------------------------------------------


def test_render_reflects_current_state():
    session = Picker(["option1", "option2", "option3"], "title").begin()

    frame = session.render(24, 80)
    assert frame.clear is True
    texts = [span.text for span in frame.spans]
    assert "* option1" in texts
    assert "  option2" in texts

    session.dispatch(Event.down())
    frame = session.render(24, 80)
    texts = [span.text for span in frame.spans]
    assert "  option1" in texts
    assert "* option2" in texts
    # coordinates are absolute starting at the session position (0, 0)
    assert frame.spans[texts.index("* option2")].y == texts.index("* option2")


def test_render_multiselect_symbols():
    session = Picker(["a", "b"], multiselect=True).begin()
    session.dispatch(Event.select())
    texts = [span.text for span in session.render(24, 80).spans]
    assert "* (x) a" in texts
    assert "  ( ) b" in texts


# ---------------------------------------------------------------------------
# Backend event translation
# ---------------------------------------------------------------------------


def test_backend_translate_key():
    backend = CursesBackend()
    assert backend.translate_key(ord("j")) == Event(EventKind.DOWN, ord("j"))
    assert backend.translate_key(ord("k")) == Event(EventKind.UP, ord("k"))
    assert backend.translate_key(ord("\n")) == Event(EventKind.CONFIRM, ord("\n"))
    assert backend.translate_key(ord(" ")) == Event(EventKind.SELECT, ord(" "))
    unknown = backend.translate_key(ord("z"))
    assert unknown.kind is EventKind.KEY
    assert unknown.code == ord("z")


# ---------------------------------------------------------------------------
# Blocking adapter (run_loop) on top of the state machine
# ---------------------------------------------------------------------------


def test_run_loop_adapter_selects_and_renders_each_turn():
    backend = ScriptedBackend([ord("j"), ord("j"), ord("\n")])
    session = Picker(["option1", "option2", "option3"]).begin()

    selected = session.run_loop(backend)

    assert selected == ("option3", 2)
    assert session.state is SessionState.SELECTED
    # one frame before each key press
    assert backend.committed == 3


def test_run_loop_adapter_cancel_returns_legacy_sentinels():
    backend = ScriptedBackend([ord("q")])
    session = Picker(["a", "b"], quit_keys=[ord("q")]).begin()
    assert session.run_loop(backend) == (None, -1)

    backend = ScriptedBackend([ord("q")])
    session = Picker(["a", "b"], multiselect=True, quit_keys=[ord("q")]).begin()
    assert session.run_loop(backend) == []


# ---------------------------------------------------------------------------
# Host-driven embedding: the host owns the loop, terminal and rendering
# ---------------------------------------------------------------------------


def test_host_driven_loop_with_backend_owned_frames():
    backend = ScriptedBackend([ord("j"), ord(" "), ord("\n")])
    session = Picker(["a", "b"], multiselect=True).begin()

    host_ticks = 0
    while session.state is SessionState.PENDING:
        host_ticks += 1  # host gets a chance to pump its own events each turn
        backend.commit(session.render(*backend.getmaxyx()))
        session.dispatch(backend.read_event())

    assert host_ticks == 3
    assert backend.committed == 3
    assert session.result().selected
    assert session.result().selection == [("b", 1)]


def test_host_feeds_synthetic_events_without_backend_input():
    session = Picker(["a", "b", "c"]).begin()
    # a host can drive the session with fully synthetic events, no getch at all
    session.dispatch(Event.down())
    session.dispatch(Event(EventKind.KEY, ord("z")))  # ignored
    session.dispatch(Event.confirm())
    assert session.result().selection == ("b", 1)
