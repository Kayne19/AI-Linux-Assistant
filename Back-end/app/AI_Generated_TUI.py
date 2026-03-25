import curses
import time
from model_router import modelRouter


def wrap_text(text, width):
    lines = []
    for paragraph in text.splitlines() or [""]:
        while len(paragraph) > width:
            lines.append(paragraph[:width])
            paragraph = paragraph[width:]
        lines.append(paragraph)
    return lines


def extract_sources(context_block):
    sources = []
    for line in context_block.splitlines():
        if line.startswith("[Source:"):
            sources.append(line.strip())
    return sources


def format_toggle(name, enabled):
    state = "on" if enabled else "off"
    return f"{name}:{state}"


def run_tui(stdscr):
    curses.curs_set(1)
    curses.start_color()
    curses.use_default_colors()
    stdscr.nodelay(False)
    curses.init_pair(1, curses.COLOR_CYAN, -1)  # AI Generated for the TUI
    curses.init_pair(2, curses.COLOR_YELLOW, -1)  # AI Generated for the TUI
    curses.init_pair(3, curses.COLOR_GREEN, -1)  # AI Generated for the TUI
    user_attr = curses.color_pair(1) | curses.A_BOLD  # AI Generated for the TUI
    assistant_attr = curses.color_pair(2) | curses.A_BOLD  # AI Generated for the TUI
    separator_attr = curses.color_pair(3)  # AI Generated for the TUI
    message_attr = curses.A_NORMAL  # AI Generated for the TUI

    router = modelRouter()
    chat_log = []
    debug_log = []
    input_buffer = ""
    scroll_offset = 0
    debug_pane = True
    toggle_query = True
    toggle_sources = True
    toggle_timing = True
    toggle_context = True
    status_text = "Idle"  # AI Generated for the TUI
    loading = False  # AI Generated for the TUI

    while True:
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
            "F2=dbg  F3=query  F4=src  F5=time  F6=ctx  PgUp/PgDn=scroll  "
            + toggles
        )
        status_header = f"Status: {status_text}"  # AI Generated for the TUI
        stdscr.addnstr(0, 0, header, width - 1)
        stdscr.addnstr(1, 0, status_header, width - 1)  # AI Generated for the TUI

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

        visible_chat = chat_lines[max(0, len(chat_lines) - chat_area_height - scroll_offset):
                                  max(0, len(chat_lines) - scroll_offset)]
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
            visible_debug = debug_lines[-debug_area_height:]
            for idx, line in enumerate(visible_debug):
                stdscr.addnstr(divider_y + 1 + idx, 0, line, width - 1)

        input_y = height - 1
        prompt = "(thinking) " if loading else "> "  # AI Generated for the TUI
        stdscr.addnstr(input_y, 0, prompt + input_buffer, width - 1)
        stdscr.move(input_y, min(len(prompt) + len(input_buffer), width - 1))

        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (3, 4):
            break
        if ch in (curses.KEY_BACKSPACE, 127, 8):
            input_buffer = input_buffer[:-1]
            continue
        if ch in (curses.KEY_PPAGE,):
            scroll_offset = min(scroll_offset + 3, max(0, len(chat_lines) - chat_area_height))
            continue
        if ch in (curses.KEY_NPAGE,):
            scroll_offset = max(scroll_offset - 3, 0)
            continue
        if ch == curses.KEY_F2:
            debug_pane = not debug_pane
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
            if user_text.lower() in {"exit", "quit"}:
                break
            chat_log.append(("user", user_text))
            debug_log.append(f"[{time.strftime('%H:%M:%S')}] User input received")
            loading = True  # AI Generated for the TUI
            status_text = "Thinking..."  # AI Generated for the TUI
            stdscr.refresh()
            retrieval_start = time.perf_counter()
            retrieval_query = router.ct.call_api(user_text, router.get_history())
            context_block = router.vdb.retrieve_context(retrieval_query)
            retrieval_elapsed = (time.perf_counter() - retrieval_start) * 1000
            llm_start = time.perf_counter()
            response = router.gc.call_api(user_text, context_block)
            llm_elapsed = (time.perf_counter() - llm_start) * 1000
            total_elapsed = retrieval_elapsed + llm_elapsed
            chat_log.append(("assistant", response))
            loading = False  # AI Generated for the TUI
            status_text = "Idle"  # AI Generated for the TUI
            if toggle_query:
                debug_log.append(f"[{time.strftime('%H:%M:%S')}] Retrieval query: {retrieval_query}")
            if toggle_context:
                debug_log.append(
                    f"[{time.strftime('%H:%M:%S')}] Context size: "
                    f"{len(context_block)} chars, {len(context_block.splitlines())} lines"
                )
            if toggle_sources:
                sources = extract_sources(context_block)
                if sources:
                    debug_log.append(f"[{time.strftime('%H:%M:%S')}] Sources:")
                    for source in sources[:10]:
                        debug_log.append(f"  {source}")
                else:
                    debug_log.append(f"[{time.strftime('%H:%M:%S')}] Sources: none")
            if toggle_timing:
                debug_log.append(
                    f"[{time.strftime('%H:%M:%S')}] Timing: "
                    f"retrieval={retrieval_elapsed:.1f}ms, "
                    f"llm={llm_elapsed:.1f}ms, total={total_elapsed:.1f}ms"
                )
            debug_log.append(f"[{time.strftime('%H:%M:%S')}] Response length: {len(response)}")
            scroll_offset = 0
            continue

        if 32 <= ch <= 126:
            input_buffer += chr(ch)


def main():
    curses.wrapper(run_tui)


if __name__ == "__main__":
    main()
