import { useMemo, useState } from "react";
import { api } from "../api";
import type { ChatMessage } from "../types";

const MESSAGE_DISPLAY_LIMIT = 50;

type UseMessagesOptions = {
	selectedChatId: string;
};

export function useMessages({ selectedChatId }: UseMessagesOptions) {
	const [messagesByChat, setMessagesByChat] = useState<
		Record<string, ChatMessage[]>
	>({});
	const [messageInput, setMessageInput] = useState("");
	const [showAllByChat, setShowAllByChat] = useState<Record<string, boolean>>(
		{},
	);

	const messages = selectedChatId ? messagesByChat[selectedChatId] || [] : [];

	const displayedMessages = useMemo(() => {
		if (!selectedChatId) return [];
		const all = messagesByChat[selectedChatId] || [];
		if (showAllByChat[selectedChatId] || all.length <= MESSAGE_DISPLAY_LIMIT) {
			return all;
		}
		return all.slice(all.length - MESSAGE_DISPLAY_LIMIT);
	}, [selectedChatId, messagesByChat, showAllByChat]);

	const hasMoreMessages = selectedChatId
		? (messagesByChat[selectedChatId]?.length || 0) > MESSAGE_DISPLAY_LIMIT &&
			!showAllByChat[selectedChatId]
		: false;

	async function reloadMessages(chatId: string) {
		const nextMessages = await api.listMessages(chatId);
		setMessagesByChat((current) => ({
			...current,
			[chatId]: nextMessages,
		}));
		return nextMessages;
	}

	function setMessagesForChat(
		chatId: string,
		updater: (current: ChatMessage[]) => ChatMessage[],
	) {
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

	function loadAllMessages() {
		if (!selectedChatId) return;
		setShowAllByChat((prev) => ({ ...prev, [selectedChatId]: true }));
	}

	function resetAll() {
		setMessagesByChat({});
		setMessageInput("");
		setShowAllByChat({});
	}

	return {
		messagesByChat,
		setMessagesByChat,
		messages,
		displayedMessages,
		hasMoreMessages,
		loadAllMessages,
		reloadMessages,
		setMessagesForChat,
		clearMessagesForChat,
		resetAll,
		messageInput,
		setMessageInput,
	};
}
