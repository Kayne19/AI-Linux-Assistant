import contextlib
import curses
import io
from pathlib import Path
import queue
import threading
import time
from orchestration.session_bootstrap import bootstrap_interactive_session


def wrap_text(text, width):
    lines = []
    for paragraph in text.splitlines() or [""]:
        while len(paragraph) > width:
            lines.append(paragraph[:width])
            paragraph = paragraph[width:]
        lines.append(paragraph)
    return lines


def extract_sources(retrieved_docs):
    sources = []
    for line in retrieved_docs.splitlines():
        if line.startswith("[Source:"):
            sources.append(line.strip())
    return sources


def format_toggle(name, enabled):
    state = "on" if enabled else "off"
    return f"{name}:{state}"


def format_state_name(state_name):
    return state_name.replace("_", " ").title()


def format_pane_name(pane_name):
    return pane_name.replace("_", " ").title()


def format_chat_log(chat_log):
    lines = []
    for role, msg in chat_log:
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {msg}")
        lines.append("-" * 110)
    return "\n".join(lines).rstrip() + "\n"


def format_debug_log(debug_log):
    return "\n".join(debug_log).rstrip() + "\n"


def save_tui_logs(chat_log, debug_log):
    logs_dir = Path(__file__).resolve().parent.parent / "tui_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    chat_text = format_chat_log(chat_log)
    debug_text = format_debug_log(debug_log)

    (logs_dir / "last_chat_log.txt").write_text(chat_text, encoding="utf-8")
    (logs_dir / "last_debug_log.txt").write_text(debug_text, encoding="utf-8")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    (logs_dir / f"chat_{timestamp}.txt").write_text(chat_text, encoding="utf-8")
    (logs_dir / f"debug_{timestamp}.txt").write_text(debug_text, encoding="utf-8")
    return logs_dir


def run_tui(stdscr, session_info):
    curses.curs_set(1)
    curses.start_color()
    curses.use_default_colors()
    stdscr.timeout(100)
    curses.init_pair(1, curses.COLOR_CYAN, -1)  # AI Generated for the TUI
    curses.init_pair(2, curses.COLOR_YELLOW, -1)  # AI Generated for the TUI
    curses.init_pair(3, curses.COLOR_GREEN, -1)  # AI Generated for the TUI
    curses.init_pair(4, curses.COLOR_MAGENTA, -1)
    user_attr = curses.color_pair(1) | curses.A_BOLD  # AI Generated for the TUI
    assistant_attr = curses.color_pair(2) | curses.A_BOLD  # AI Generated for the TUI
    separator_attr = curses.color_pair(3)  # AI Generated for the TUI
    status_attr = curses.color_pair(4) | curses.A_BOLD
    message_attr = curses.A_NORMAL  # AI Generated for the TUI

    router = session_info.build_router()
    event_queue = queue.Queue()
    chat_log = router.get_history()[:]
    debug_log = []
    input_buffer = ""
    chat_scroll_offset = 0
    debug_scroll_offset = 0
    active_pane = "chat"
    debug_pane = True
    toggle_query = True
    toggle_sources = True
    toggle_timing = True
    toggle_context = True
    status_text = "Idle"  # AI Generated for the TUI
    active_state = "Idle"
    active_tool = "-"
    loading = False  # AI Generated for the TUI
    turn_started_at = 0.0
    last_saved_logs_dir = None

    def on_state_change(state, turn):
        event_queue.put(("state", {"state": state.name}))

    def on_event(event_type, payload):
        event_queue.put(("event", {"type": event_type, "payload": payload}))

    router.set_state_listener(on_state_change)
    router.set_event_listener(on_event)

    while True:
        while True:
            try:
                event_type, payload = event_queue.get_nowait()
            except queue.Empty:
                break

            if event_type == "state":
                active_state = format_state_name(payload["state"])
                if loading:
                    status_text = f"Running {active_state}"
                    debug_log.append(f"[{time.strftime('%H:%M:%S')}] State -> {payload['state']}")
            elif event_type == "event":
                event_name = payload["type"]
                details = payload["payload"]
                if event_name == "responder_state":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Responder -> {details.get('state', 'UNKNOWN')}"
                    )
                elif event_name == "tool_start":
                    active_tool = details.get("name", "unknown_tool")
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Tool start -> {active_tool} {details.get('args', {})}"
                    )
                elif event_name == "tool_complete":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Tool complete -> "
                        f"{details.get('name', 'unknown_tool')} ({details.get('result_size', 0)} chars)"
                    )
                    active_tool = "-"
                elif event_name == "tool_error":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Tool error -> "
                        f"{details.get('name', 'unknown_tool')}: {details.get('error', 'unknown error')}"
                    )
                    active_tool = "-"
                elif event_name == "summarized_conversation_history":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Summarized conversation history -> "
                        f"recent_turns={details.get('recent_turns', 0)} "
                        f"summary_chars={details.get('summary_chars', 0)} "
                        f"summarized={details.get('summarized', False)}"
                    )
                elif event_name == "summarized_retrieved_docs":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Summarized retrieved docs -> "
                        f"raw_chars={details.get('raw_chars', 0)} "
                        f"summary_chars={details.get('summary_chars', 0)} "
                        f"summarized={details.get('summarized', False)}"
                    )
                elif event_name == "memory_loaded":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Memory loaded -> "
                        f"chars={details.get('chars', 0)} has_memory={details.get('has_memory', False)}"
                    )
                elif event_name == "memory_extracted":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Memory extracted -> "
                        f"facts={details.get('facts', 0)} "
                        f"issues={details.get('issues', 0)} "
                        f"attempts={details.get('attempts', 0)} "
                        f"constraints={details.get('constraints', 0)} "
                        f"preferences={details.get('preferences', 0)}"
                    )
                    examples = details.get("examples", {})
                    for label in ["facts", "issues", "attempts", "constraints", "preferences"]:
                        values = examples.get(label) or []
                        if values:
                            debug_log.append(f"  {label}: " + "; ".join(values))
                elif event_name == "memory_resolved":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Memory resolved -> "
                        f"committed={details.get('committed', {})} "
                        f"candidates={details.get('candidates', 0)} "
                        f"conflicts={details.get('conflicts', 0)}"
                    )
                    committed_examples = details.get("committed_examples", {})
                    for label in ["facts", "issues", "attempts", "constraints", "preferences"]:
                        values = committed_examples.get(label) or []
                        if values:
                            debug_log.append(f"  committed {label}: " + "; ".join(values))
                    for label in ["candidate_examples", "conflict_examples"]:
                        values = details.get(label) or []
                        if values:
                            rendered = "; ".join(
                                f"{item.get('item_type')}[{item.get('reason')}]: {item.get('summary')}"
                                for item in values
                            )
                            debug_log.append(f"  {label}: {rendered}")
                elif event_name == "memory_committed":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Memory committed -> "
                        f"committed={details.get('committed', {})} "
                        f"candidates={details.get('candidates', 0)} "
                        f"conflicts={details.get('conflicts', 0)}"
                    )
                elif event_name == "memory_error":
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Memory error -> "
                        f"{details.get('phase', 'unknown')}: {details.get('error', 'unknown error')}"
                    )
            elif event_type == "result":
                loading = False
                active_tool = "-"
                active_state = "Idle"
                status_text = "Idle"
                response = payload["response"]
                turn = payload["turn"]
                elapsed_ms = payload["elapsed_ms"]
                captured_lines = payload.get("captured_lines", [])

                chat_log.append(("assistant", response))
                for line in captured_lines:
                    debug_log.append(f"[{time.strftime('%H:%M:%S')}] {line}")

                if toggle_query and turn is not None:
                    debug_log.append(f"[{time.strftime('%H:%M:%S')}] Retrieval query: {turn.retrieval_query}")
                if toggle_context and turn is not None:
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Retrieved docs size: "
                        f"{len(turn.retrieved_docs)} chars, {len(turn.retrieved_docs.splitlines())} lines"
                    )
                    if getattr(turn, "summarized_retrieved_docs", ""):
                        debug_log.append(
                            f"[{time.strftime('%H:%M:%S')}] Summarized retrieved docs size: "
                            f"{len(turn.summarized_retrieved_docs)} chars, "
                            f"{len(turn.summarized_retrieved_docs.splitlines())} lines"
                        )
                if toggle_sources and turn is not None:
                    sources = extract_sources(turn.retrieved_docs)
                    if sources:
                        debug_log.append(f"[{time.strftime('%H:%M:%S')}] Sources:")
                        for source in sources[:10]:
                            debug_log.append(f"  {source}")
                    else:
                        debug_log.append(f"[{time.strftime('%H:%M:%S')}] Sources: none")
                if toggle_timing:
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] Timing: total={elapsed_ms:.1f}ms"
                    )
                if turn is not None:
                    debug_log.append(
                        f"[{time.strftime('%H:%M:%S')}] State trace: {' -> '.join(turn.state_trace)}"
                    )
                debug_log.append(f"[{time.strftime('%H:%M:%S')}] Response length: {len(response)}")
                last_saved_logs_dir = save_tui_logs(chat_log, debug_log)
                debug_log.append(
                    f"[{time.strftime('%H:%M:%S')}] Saved logs -> {last_saved_logs_dir}"
                )
                chat_scroll_offset = 0
                debug_scroll_offset = 0

        height, width = stdscr.getmaxyx()
        stdscr.erase()

        toggles = "  ".join([
            format_toggle("Dbg", debug_pane),
            format_toggle("Query", toggle_query),
            format_toggle("Src", toggle_sources),
            format_toggle("Time", toggle_timing),
            format_toggle("Ctx", toggle_context),
        ])
        header = (
            "AI Linux Assistant TUI  |  Enter=send  Ctrl+C=quit  "
            "F2=dbg  F3=query  F4=src  F5=time  F6=ctx  Tab=switch pane  PgUp/PgDn=scroll  "
            + toggles
        )
        status_header = (
            f"User: {session_info.username}  |  Project: {session_info.project_name}  |  "
            f"Chat: {session_info.chat_session_title or session_info.chat_session_id}  |  "
            f"Status: {status_text}  |  State: {active_state}  |  Tool: {active_tool}  |  "
            f"Focus: {format_pane_name(active_pane)}"
        )
        stdscr.addnstr(0, 0, header, width - 1)
        stdscr.addnstr(1, 0, status_header, width - 1, status_attr)

        divider_y = max(3, height // 2) if debug_pane else max(3, height - 2)  # AI Generated for the TUI
        stdscr.hline(2, 0, "-", width)  # AI Generated for the TUI
        if debug_pane:
            stdscr.hline(divider_y, 0, "-", width)

        chat_area_height = divider_y - 3  # AI Generated for the TUI
        debug_area_height = height - divider_y - 3

        chat_lines = []
        for role, msg in chat_log:
            prefix = "User: " if role == "user" else "Assistant: "
            available_width = max(10, width - 1 - len(prefix))  # AI Generated for the TUI
            wrapped = wrap_text(msg, available_width)  # AI Generated for the TUI
            if wrapped:
                chat_lines.append(  # AI Generated for the TUI
                    {"text": prefix + wrapped[0], "role": role, "is_label": True, "is_sep": False}  # AI Generated for the TUI
                )
                for line in wrapped[1:]:
                    chat_lines.append(  # AI Generated for the TUI
                        {"text": line, "role": role, "is_label": False, "is_sep": False}  # AI Generated for the TUI
                    )
            else:
                chat_lines.append(  # AI Generated for the TUI
                    {"text": prefix, "role": role, "is_label": True, "is_sep": False}  # AI Generated for the TUI
                )
            chat_lines.append({"text": "---", "role": role, "is_label": False, "is_sep": True})  # AI Generated for the TUI

        visible_chat = chat_lines[
            max(0, len(chat_lines) - chat_area_height - chat_scroll_offset):
            max(0, len(chat_lines) - chat_scroll_offset)
        ]
        for idx, item in enumerate(visible_chat):  # AI Generated for the TUI
            if item["is_sep"]:  # AI Generated for the TUI
                stdscr.addnstr(3 + idx, 0, "-" * (width - 1), width - 1, separator_attr)  # AI Generated for the TUI
                continue  # AI Generated for the TUI
            line = item["text"]  # AI Generated for the TUI
            if item["is_label"]:  # AI Generated for the TUI
                label = "User: " if item["role"] == "user" else "Assistant: "  # AI Generated for the TUI
                label_attr = user_attr if item["role"] == "user" else assistant_attr  # AI Generated for the TUI
                stdscr.addnstr(3 + idx, 0, label, width - 1, label_attr)  # AI Generated for the TUI
                stdscr.addnstr(3 + idx, len(label), line[len(label):], width - 1 - len(label), message_attr)  # AI Generated for the TUI
            else:
                stdscr.addnstr(3 + idx, 0, line, width - 1, message_attr)  # AI Generated for the TUI

        if debug_pane:
            debug_lines = []
            for line in debug_log[-200:]:
                debug_lines.extend(wrap_text(line, width - 1))
            visible_debug = debug_lines[
                max(0, len(debug_lines) - debug_area_height - debug_scroll_offset):
                max(0, len(debug_lines) - debug_scroll_offset)
            ]
            for idx, line in enumerate(visible_debug):
                stdscr.addnstr(divider_y + 1 + idx, 0, line, width - 1)

        input_y = height - 1
        prompt = "(thinking) " if loading else "> "  # AI Generated for the TUI
        stdscr.addnstr(input_y, 0, prompt + input_buffer, width - 1)
        stdscr.move(input_y, min(len(prompt) + len(input_buffer), width - 1))

        stdscr.refresh()

        ch = stdscr.getch()
        if ch == -1:
            continue
        if ch in (3, 4):
            break
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            input_buffer = input_buffer[:-1]
            continue
        if ch == 9:
            if debug_pane:
                active_pane = "debug_context" if active_pane == "chat" else "chat"
            else:
                active_pane = "chat"
            continue
        if ch in (curses.KEY_PPAGE,):
            if active_pane == "chat":
                chat_scroll_offset = min(chat_scroll_offset + 3, max(0, len(chat_lines) - chat_area_height))
            elif debug_pane:
                debug_lines = []
                for line in debug_log[-200:]:
                    debug_lines.extend(wrap_text(line, width - 1))
                debug_scroll_offset = min(debug_scroll_offset + 3, max(0, len(debug_lines) - debug_area_height))
            continue
        if ch in (curses.KEY_NPAGE,):
            if active_pane == "chat":
                chat_scroll_offset = max(chat_scroll_offset - 3, 0)
            elif debug_pane:
                debug_scroll_offset = max(debug_scroll_offset - 3, 0)
            continue
        if ch == curses.KEY_F2:
            debug_pane = not debug_pane
            if not debug_pane:
                active_pane = "chat"
            continue
        if ch == curses.KEY_F3:
            toggle_query = not toggle_query
            continue
        if ch == curses.KEY_F4:
            toggle_sources = not toggle_sources
            continue
        if ch == curses.KEY_F5:
            toggle_timing = not toggle_timing
            continue
        if ch == curses.KEY_F6:
            toggle_context = not toggle_context
            continue
        if ch in (10, 13):
            user_text = input_buffer.strip()
            input_buffer = ""
            if not user_text:
                continue
            if loading:
                debug_log.append(f"[{time.strftime('%H:%M:%S')}] Ignored input while busy")
                continue
            if user_text.lower() in {"exit", "quit"}:
                break
            chat_log.append(("user", user_text))
            debug_log.append(f"[{time.strftime('%H:%M:%S')}] User input received")
            loading = True  # AI Generated for the TUI
            status_text = "Starting turn"
            active_state = "Start"
            active_tool = "-"
            turn_started_at = time.perf_counter()

            def run_turn():
                captured_output = io.StringIO()
                try:
                    with contextlib.redirect_stdout(captured_output), contextlib.redirect_stderr(captured_output):
                        response = router.ask_question(user_text)
                except Exception as exc:
                    response = f"TUI error: {exc}"
                elapsed_ms = (time.perf_counter() - turn_started_at) * 1000
                captured_lines = [line for line in captured_output.getvalue().splitlines() if line.strip()]
                event_queue.put(
                    (
                        "result",
                        {
                            "response": response,
                            "turn": router.last_turn,
                            "elapsed_ms": elapsed_ms,
                            "captured_lines": captured_lines,
                        },
                    )
                )

            threading.Thread(target=run_turn, daemon=True).start()
            continue

        if 32 <= ch <= 126:
            input_buffer += chr(ch)

    if chat_log or debug_log:
        save_tui_logs(chat_log, debug_log)


def main():
    session_info = bootstrap_interactive_session()
    try:
        curses.wrapper(lambda stdscr: run_tui(stdscr, session_info))
    finally:
        # If the UI exits unexpectedly, the most recent completed turn has already been
        # autosaved by run_tui. No extra recovery action needed here.
        pass


if __name__ == "__main__":
    main()
