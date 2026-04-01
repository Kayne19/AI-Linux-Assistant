import { useState } from "react";
import { api } from "../api";
import type { ChatMessage } from "../types";

type UseMessagesOptions = {
  selectedChatId: string;
};

export function useMessages({ selectedChatId }: UseMessagesOptions) {
  const [messagesByChat, setMessagesByChat] = useState<Record<string, ChatMessage[]>>({});
  const [messageInput, setMessageInput] = useState("");

  const messages = selectedChatId ? (messagesByChat[selectedChatId] || []) : [];

  async function reloadMessages(chatId: string) {
    const nextMessages = await api.listMessages(chatId);
    setMessagesByChat((current) => ({
      ...current,
      [chatId]: nextMessages,
    }));
    return nextMessages;
  }

  function setMessagesForChat(chatId: string, updater: (current: ChatMessage[]) => ChatMessage[]) {
    setMessagesByChat((current) => ({
      ...current,
      [chatId]: updater(current[chatId] || []),
    }));
  }

  function clearMessagesForChat(chatId: string) {
    setMessagesByChat((current) => {
      const next = { ...current };
      delete next[chatId];
      return next;
    });
  }

  function resetAll() {
    setMessagesByChat({});
    setMessageInput("");
  }

  return {
    messagesByChat,
    setMessagesByChat,
    messages,
    reloadMessages,
    setMessagesForChat,
    clearMessagesForChat,
    resetAll,
    messageInput,
    setMessageInput,
  };
}
