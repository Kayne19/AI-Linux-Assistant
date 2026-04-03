import type { FormEvent, KeyboardEvent } from "react";
import { useEffect, useRef } from "react";

type MessageComposerProps = {
  error: string;
  councilMode: "off" | "full" | "lite";
  selectedChatBusy: boolean;
  isPaused: boolean;
  selectedChatId: string;
  messageInput: string;
  placeholder: string;
  onMessageChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onCycleCouncilMode: () => void;
};

export function MessageComposer({
  error,
  councilMode,
  selectedChatBusy,
  isPaused,
  selectedChatId,
  messageInput,
  placeholder,
  onMessageChange,
  onSubmit,
  onCycleCouncilMode,
}: MessageComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    if (selectedChatBusy && !isPaused) {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [messageInput]);

  return (
    <form className="composer" onSubmit={onSubmit}>
      <div className="composer-shell">
        {error ? <p className="composer-error-text">{error}</p> : null}
        <div className="composer-input-wrap">
          <div className="composer-left-actions">
            <button
              type="button"
              className={`council-toggle-btn${councilMode === "lite" ? " active lite" : councilMode === "full" ? " active" : ""}`}
              onClick={onCycleCouncilMode}
              title="Council mode: click to cycle off → full → lite → off"
            >
              <svg viewBox="0 0 20 20" aria-hidden="true" className="council-icon">
                <circle cx="10" cy="10" r="6.5" stroke="currentColor" strokeWidth="1.4" fill="none" />
                <path
                  d="M7 10l2 2 4-4"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  fill="none"
                  className="council-check"
                />
              </svg>
              {councilMode === "lite" ? "Council Lite" : "Council"}
            </button>
          </div>
          <textarea
            ref={textareaRef}
            value={messageInput}
            onChange={(event) => onMessageChange(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder={placeholder}
            disabled={!selectedChatId || (selectedChatBusy && !isPaused)}
          />
          <div className="composer-actions">
            <button
              type="submit"
              className="composer-send"
              aria-label="Send message"
              disabled={!selectedChatId || (selectedChatBusy && !isPaused)}
            >
              <svg viewBox="0 0 20 20" aria-hidden="true" className="send-icon">
                <path d="M3 10L16 4L11 17L9.5 11.5L3 10Z" fill="currentColor" stroke="none" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </form>
  );
}
