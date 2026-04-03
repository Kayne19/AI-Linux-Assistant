import { FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { mergeChatsPreservingActiveRunSnapshots } from "../runResume";
import type { AsyncState, ChatSession } from "../types";

type UseChatsOptions = {
  enabled: boolean;
  selectedProjectId: string;
  onStatusChange: (status: AsyncState) => void;
  onError: (message: string) => void;
  onChatDeleted?: (chatId: string) => void;
};

export function useChats({
  enabled,
  selectedProjectId,
  onStatusChange,
  onError,
  onChatDeleted,
}: UseChatsOptions) {
  const [chats, setChats] = useState<ChatSession[]>([]);
  const [selectedChatId, setSelectedChatId] = useState("");
  const [chatListsByProject, setChatListsByProject] = useState<Record<string, ChatSession[]>>({});
  const [editingChatId, setEditingChatId] = useState("");
  const [editChatTitleInput, setEditChatTitleInput] = useState("");
  const [creatingChat, setCreatingChat] = useState(false);

  const selectedChat = useMemo(
    () => chats.find((chat) => chat.id === selectedChatId) || null,
    [chats, selectedChatId],
  );

  async function reloadChats(projectId = selectedProjectId) {
    if (!projectId) {
      setChats([]);
      setSelectedChatId("");
      return [];
    }

    const nextChats = await api.listChats(projectId);
    setChatListsByProject((current) => ({
      ...current,
      [projectId]: mergeChatsPreservingActiveRunSnapshots(current[projectId] || [], nextChats),
    }));
    setChats((current) =>
      mergeChatsPreservingActiveRunSnapshots(
        current,
        nextChats,
      ),
    );
    setSelectedChatId((current) =>
      nextChats.some((chat) => chat.id === current) ? current : nextChats[0]?.id || "",
    );
    return nextChats;
  }

  useEffect(() => {
    if (!enabled || !selectedProjectId) {
      setChats([]);
      setSelectedChatId("");
      return;
    }

    void reloadChats(selectedProjectId).catch((err: Error) => {
      onError(err.message);
      onStatusChange("error");
    });
  }, [enabled, onError, onStatusChange, selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) {
      return;
    }

    const cachedChats = chatListsByProject[selectedProjectId];
    if (!cachedChats) {
      return;
    }

    setChats(cachedChats);
    setSelectedChatId((current) =>
      cachedChats.some((chat) => chat.id === current) ? current : cachedChats[0]?.id || "",
    );
  }, [chatListsByProject, selectedProjectId]);

  function updateChatRunStatus(chatId: string, activeRunId: string | null, activeRunStatus: string | null) {
    const apply = (items: ChatSession[]) =>
      items.map((chat) =>
        chat.id === chatId ? { ...chat, active_run_id: activeRunId, active_run_status: activeRunStatus } : chat,
      );

    setChats((current) => apply(current));
    setChatListsByProject((current) =>
      Object.fromEntries(Object.entries(current).map(([projectId, items]) => [projectId, apply(items)])),
    );
  }

  async function handleCreateChat(title = "") {
    if (!selectedProjectId || creatingChat) {
      return null;
    }

    setCreatingChat(true);
    onStatusChange("loading");
    onError("");

    try {
      const chat = await api.createChat(selectedProjectId, title);
      await reloadChats(selectedProjectId);
      setSelectedChatId(chat.id);
      onStatusChange("idle");
      return chat;
    } catch (err) {
      onError((err as Error).message);
      onStatusChange("error");
      return null;
    } finally {
      setCreatingChat(false);
    }
  }

  function openEditChatDialog(chat: ChatSession) {
    setEditingChatId(chat.id);
    setEditChatTitleInput(chat.title || "");
    onError("");
  }

  function closeEditChatDialog() {
    setEditingChatId("");
    setEditChatTitleInput("");
  }

  async function handleEditChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingChatId || !selectedProjectId) {
      return null;
    }

    onStatusChange("loading");
    onError("");

    try {
      const updatedChat = await api.updateChat(editingChatId, {
        title: editChatTitleInput,
      });
      setChats((current) => current.map((chat) => (chat.id === updatedChat.id ? updatedChat : chat)));
      setSelectedChatId(updatedChat.id);
      closeEditChatDialog();
      await reloadChats(selectedProjectId);
      onStatusChange("idle");
      return updatedChat;
    } catch (err) {
      onError((err as Error).message);
      onStatusChange("error");
      return null;
    }
  }

  async function handleDeleteChat() {
    if (!editingChatId || !selectedProjectId) {
      return false;
    }

    onStatusChange("loading");
    onError("");

    try {
      await api.deleteChat(editingChatId);
      if (selectedChatId === editingChatId) {
        setSelectedChatId("");
      }
      onChatDeleted?.(editingChatId);
      closeEditChatDialog();
      await reloadChats(selectedProjectId);
      onStatusChange("idle");
      return true;
    } catch (err) {
      onError((err as Error).message);
      onStatusChange("error");
      return false;
    }
  }

  async function deleteChatById(chatId: string) {
    if (!selectedProjectId) {
      return false;
    }
    onStatusChange("loading");
    onError("");
    try {
      await api.deleteChat(chatId);
      if (selectedChatId === chatId) {
        setSelectedChatId("");
      }
      onChatDeleted?.(chatId);
      await reloadChats(selectedProjectId);
      onStatusChange("idle");
      return true;
    } catch (err) {
      onError((err as Error).message);
      onStatusChange("error");
      return false;
    }
  }

  return {
    chats,
    setChats,
    selectedChatId,
    setSelectedChatId,
    chatListsByProject,
    setChatListsByProject,
    editingChatId,
    openEditChatDialog,
    closeEditChatDialog,
    editChatTitleInput,
    setEditChatTitleInput,
    creatingChat,
    selectedChat,
    reloadChats,
    handleCreateChat,
    handleEditChat,
    handleDeleteChat,
    deleteChatById,
    updateChatRunStatus,
  };
}
