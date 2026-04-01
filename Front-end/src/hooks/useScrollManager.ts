import { useEffect, useRef } from "react";
import type { ChatMessage, StreamStatusEvent } from "../types";

type UseScrollManagerOptions = {
  selectedChatId: string;
  messages: ChatMessage[];
  selectedChatBusy: boolean;
  streamStatus: StreamStatusEvent | null;
};

export function useScrollManager({
  selectedChatId,
  messages,
  selectedChatBusy,
  streamStatus,
}: UseScrollManagerOptions) {
  const messagesContainerRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);

  function scrollMessagesToBottom(behavior: ScrollBehavior = "auto") {
    const container = messagesContainerRef.current;
    if (!container) {
      return;
    }

    container.scrollTo({
      top: container.scrollHeight,
      behavior,
    });
  }

  function updateStickToBottom() {
    const container = messagesContainerRef.current;
    if (!container) {
      return;
    }

    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    stickToBottomRef.current = distanceFromBottom <= 64;
  }

  function resetStickToBottom() {
    stickToBottomRef.current = true;
  }

  useEffect(() => {
    if (!stickToBottomRef.current) {
      return;
    }

    requestAnimationFrame(() => {
      scrollMessagesToBottom(selectedChatBusy ? "auto" : messages.length > 0 ? "smooth" : "auto");
    });
  }, [messages, selectedChatBusy]);

  useEffect(() => {
    resetStickToBottom();
    requestAnimationFrame(() => {
      scrollMessagesToBottom();
    });
  }, [selectedChatId]);

  useEffect(() => {
    if (!selectedChatBusy) {
      return;
    }

    requestAnimationFrame(() => {
      scrollMessagesToBottom("auto");
    });
  }, [selectedChatBusy, streamStatus]);

  return {
    messagesContainerRef,
    messagesEndRef,
    scrollMessagesToBottom,
    updateStickToBottom,
    resetStickToBottom,
  };
}
