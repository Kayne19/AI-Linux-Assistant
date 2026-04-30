import { type MutableRefObject, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { getResumeAfterSeq, shouldReconcileDetachedRunUi } from "../runResume";
import { streamRunSession } from "../runStreamSession";
import type {
	ChatMessage,
	ChatRun,
	ChatRunUIState,
	ChatSession,
	PendingDonePayload,
	RunResumeInputKind,
	StreamStatusEvent,
	UICouncilEntry,
} from "../types";
import { AUTO_NAME_REFRESH_DELAYS_MS, optimisticIdsForRun } from "../utils";

type TextDeltaController = {
	queueTextDelta: (chatId: string, assistantId: number, delta: string) => void;
	clearPendingTextDeltaBatch: (chatId: string) => void;
	hasPendingDelta: (chatId: string) => boolean;
};

type CouncilController = {
	clearPendingCouncilDeltaBatchesForChat: (chatId: string) => void;
	handleMagiRoleStart: (
		chatId: string,
		payload: Record<string, unknown>,
	) => void;
	handleMagiRoleTextDelta: (
		chatId: string,
		payload: Record<string, unknown>,
	) => void;
	handleMagiRoleTextCheckpoint: (
		chatId: string,
		payload: Record<string, unknown>,
	) => void;
	handleMagiRoleComplete: (
		chatId: string,
		payload: Record<string, unknown>,
	) => void;
	handleMagiInterventionAdded: (
		chatId: string,
		payload: Record<string, unknown>,
		seq?: number,
	) => void;
	hasPendingCouncilWork: (chatId: string) => boolean;
};

type UseStreamingRunOptions = {
	chats: ChatSession[];
	selectedChatId: string;
	selectedChat: ChatSession | null;
	selectedProjectId: string;
	textDelta: TextDeltaController;
	council: CouncilController;
	reloadMessages: (chatId: string) => Promise<ChatMessage[]>;
	setMessagesForChat: (
		chatId: string,
		updater: (current: ChatMessage[]) => ChatMessage[],
	) => void;
	reloadChats: (projectId: string) => Promise<ChatSession[]>;
	updateChatRunStatus: (
		chatId: string,
		activeRunId: string | null,
		activeRunStatus: string | null,
	) => void;
	onError: (message: string) => void;
	onTextDrainCompleteRef: MutableRefObject<(chatId: string) => void>;
	onCouncilDrainCompleteRef: MutableRefObject<(chatId: string) => void>;
	runUiCouncilEntriesUpdaterRef: MutableRefObject<
		(
			chatId: string,
			updater: (entries: UICouncilEntry[]) => UICouncilEntry[],
		) => void
	>;
	runUiStreamStatusUpdaterRef: MutableRefObject<
		(chatId: string, streamStatus: StreamStatusEvent) => void
	>;
};

type CheckpointSeed = {
	runId: string;
	seq: number;
	text: string;
};

const MAGI_PAUSEABLE_STATES = new Set([
	"OPENING_ARGUMENTS",
	"ROLE_EAGER",
	"ROLE_SKEPTIC",
	"ROLE_HISTORIAN",
	"DISCUSSION_GATE",
	"DISCUSSION",
	"DISCUSSION_EAGER",
	"DISCUSSION_SKEPTIC",
	"DISCUSSION_HISTORIAN",
]);

export function useStreamingRun({
	chats,
	selectedChatId,
	selectedChat,
	selectedProjectId,
	textDelta,
	council,
	reloadMessages,
	setMessagesForChat,
	reloadChats,
	updateChatRunStatus,
	onError,
	onTextDrainCompleteRef,
	onCouncilDrainCompleteRef,
	runUiCouncilEntriesUpdaterRef,
	runUiStreamStatusUpdaterRef,
}: UseStreamingRunOptions) {
	const [runUiByChat, setRunUiByChat] = useState<
		Record<string, ChatRunUIState>
	>({});

	const streamControllersRef = useRef<Record<string, AbortController>>({});
	const streamingActiveRef = useRef<Record<string, boolean>>({});
	const lastCheckpointRef = useRef<Record<string, CheckpointSeed>>({});
	const pendingDonePayloadsRef = useRef<Record<string, PendingDonePayload>>({});
	const previousSelectedChatIdRef = useRef("");
	const selectedProjectIdRef = useRef(selectedProjectId);
	const reloadMessagesRef = useRef(reloadMessages);
	const reloadChatsRef = useRef(reloadChats);
	const updateChatRunStatusRef = useRef(updateChatRunStatus);
	const onErrorRef = useRef(onError);

	const selectedRunUi = selectedChatId
		? runUiByChat[selectedChatId] || null
		: null;
	const streamStatus = selectedRunUi?.streamStatus ?? null;
	const streamingAssistantId = selectedRunUi?.streamingAssistantId ?? null;
	const selectedChatBusy = Boolean(
		selectedChat?.active_run_id || selectedRunUi,
	);

	useEffect(() => {
		selectedProjectIdRef.current = selectedProjectId;
	}, [selectedProjectId]);

	useEffect(() => {
		reloadMessagesRef.current = reloadMessages;
	}, [reloadMessages]);

	useEffect(() => {
		reloadChatsRef.current = reloadChats;
	}, [reloadChats]);

	useEffect(() => {
		updateChatRunStatusRef.current = updateChatRunStatus;
	}, [updateChatRunStatus]);

	useEffect(() => {
		onErrorRef.current = onError;
	}, [onError]);

	function setRunUiForChat(
		chatId: string,
		updater: (
			current: ChatRunUIState | undefined,
		) => ChatRunUIState | undefined,
	) {
		setRunUiByChat((current) => {
			const nextValue = updater(current[chatId]);
			if (!nextValue) {
				const next = { ...current };
				delete next[chatId];
				return next;
			}

			return {
				...current,
				[chatId]: nextValue,
			};
		});
	}

	function updateRunUiCouncilEntries(
		chatId: string,
		updater: (entries: UICouncilEntry[]) => UICouncilEntry[],
	) {
		setRunUiForChat(chatId, (current) =>
			current
				? { ...current, councilEntries: updater(current.councilEntries) }
				: current,
		);
	}

	function updateRunUiStreamStatus(
		chatId: string,
		nextStreamStatus: StreamStatusEvent,
	) {
		setRunUiForChat(chatId, (current) =>
			current ? { ...current, streamStatus: nextStreamStatus } : current,
		);
	}

	function setRunUiPauseAvailability(chatId: string, canPauseRun: boolean) {
		setRunUiForChat(chatId, (current) =>
			current ? { ...current, canPauseRun } : current,
		);
	}

	function handlePausedRun(chatId: string, run: ChatRun, message: string) {
		streamingActiveRef.current[chatId] = false;
		updateChatRunStatusRef.current(chatId, run.id, "paused");
		setRunUiPauseAvailability(chatId, false);
		updateRunUiStreamStatus(chatId, {
			source: "event",
			code: "paused",
			payload: message ? { message } : undefined,
		});
	}

	async function handlePauseActiveRun(): Promise<boolean> {
		const runId = selectedChat?.active_run_id || selectedRunUi?.runId;
		if (!runId || !selectedChatId) {
			return false;
		}

		try {
			const run = await api.pauseRun(runId);
			updateChatRunStatusRef.current(selectedChatId, run.id, run.status);
			updateRunUiStreamStatus(selectedChatId, {
				source: "event",
				code: run.status === "pause_requested" ? "pause_requested" : "paused",
				payload: { run_id: run.id, status: run.status },
			});
			return true;
		} catch (err) {
			onErrorRef.current((err as Error).message);
			return false;
		}
	}

	async function handleResumeActiveRun(
		inputText = "",
		inputKind?: RunResumeInputKind,
	): Promise<boolean> {
		const runId = selectedChat?.active_run_id || selectedRunUi?.runId;
		if (!runId || !selectedChatId) {
			return false;
		}

		try {
			const run = await api.resumeRun(runId, {
				inputText,
				inputKind,
			});
			updateChatRunStatusRef.current(selectedChatId, run.id, run.status);
			if (!streamControllersRef.current[selectedChatId]) {
				await attachRunStream(selectedChatId, run);
			}
			return true;
		} catch (err) {
			onErrorRef.current((err as Error).message);
			return false;
		}
	}

	function scheduleAutoNameRefreshes(projectId: string) {
		AUTO_NAME_REFRESH_DELAYS_MS.forEach((delayMs) => {
			window.setTimeout(() => {
				void reloadChatsRef.current(projectId).catch(() => undefined);
			}, delayMs);
		});
	}

	async function finalizeDone(
		chatId: string,
		payload: PendingDonePayload["payload"],
		projectIdAtCompletion: string,
	) {
		setMessagesForChat(chatId, (current) => {
			const existingUser = current.find(
				(m) => m.id === payload.user_message.id,
			);
			const existingAssistant = current.find(
				(m) => m.id === payload.assistant_message.id,
			);

			const userChanged =
				!existingUser ||
				existingUser.content !== payload.user_message.content ||
				existingUser.created_at !== payload.user_message.created_at;
			const assistantChanged =
				!existingAssistant ||
				existingAssistant.content !== payload.assistant_message.content ||
				existingAssistant.created_at !== payload.assistant_message.created_at;

			if (!userChanged && !assistantChanged) {
				return current;
			}

			return [
				...current.filter((message) => message.id >= 0),
				userChanged ? payload.user_message : existingUser!,
				assistantChanged ? payload.assistant_message : existingAssistant!,
			];
		});
		clearRunUi(chatId);
		updateChatRunStatusRef.current(chatId, null, null);
		delete pendingDonePayloadsRef.current[chatId];
		if (projectIdAtCompletion) {
			await reloadChatsRef.current(projectIdAtCompletion);
			if (payload.debug?.auto_name_scheduled) {
				scheduleAutoNameRefreshes(projectIdAtCompletion);
			}
		}
	}

	function handleTextDrainComplete(chatId: string) {
		const pendingDone = pendingDonePayloadsRef.current[chatId];
		if (pendingDone && !council.hasPendingCouncilWork(chatId)) {
			void finalizeDone(
				chatId,
				pendingDone.payload,
				pendingDone.selectedProjectIdAtCompletion,
			);
		}
	}

	function handleCouncilDrainComplete(chatId: string) {
		const pendingDone = pendingDonePayloadsRef.current[chatId];
		if (
			pendingDone &&
			!textDelta.hasPendingDelta(chatId) &&
			!council.hasPendingCouncilWork(chatId)
		) {
			void finalizeDone(
				chatId,
				pendingDone.payload,
				pendingDone.selectedProjectIdAtCompletion,
			);
		}
	}

	function setCheckpointSeed(
		chatId: string,
		runId: string,
		seq: number,
		text: string,
	) {
		lastCheckpointRef.current[chatId] = {
			runId,
			seq: Math.max(0, seq),
			text,
		};
	}

	function getCheckpointSeed(chatId: string, runId: string) {
		const checkpoint = lastCheckpointRef.current[chatId];
		if (!checkpoint || checkpoint.runId !== runId) {
			return null;
		}

		return checkpoint;
	}

	function applyTextCheckpoint(
		chatId: string,
		assistantId: number,
		text: string,
	) {
		textDelta.clearPendingTextDeltaBatch(chatId);
		setMessagesForChat(chatId, (current) =>
			current.map((message) =>
				message.id === assistantId ? { ...message, content: text } : message,
			),
		);
	}

	function ensureOptimisticMessages(chatId: string, run: ChatRun) {
		const checkpointSeed = getCheckpointSeed(chatId, run.id);
		if (!checkpointSeed) {
			delete lastCheckpointRef.current[chatId];
		}

		streamingActiveRef.current[chatId] = false;
		const seedText =
			checkpointSeed && checkpointSeed.seq >= (run.latest_event_seq || 0)
				? checkpointSeed.text
				: run.partial_assistant_text || checkpointSeed?.text || "";
		const optimisticIds = optimisticIdsForRun(run.id);

		const optimisticUserMessage: ChatMessage = {
			id: optimisticIds.userId,
			session_id: chatId,
			role: "user",
			content: run.request_content,
			created_at: run.created_at || new Date().toISOString(),
		};
		const optimisticAssistantMessage: ChatMessage = {
			id: optimisticIds.assistantId,
			session_id: chatId,
			role: "assistant",
			content: seedText,
			created_at: run.created_at || new Date().toISOString(),
		};

		setMessagesForChat(chatId, (current) => {
			const existingUser = current.find((m) => m.id === optimisticIds.userId);
			const existingAssistant = current.find(
				(m) => m.id === optimisticIds.assistantId,
			);

			const userChanged =
				!existingUser ||
				existingUser.content !== optimisticUserMessage.content ||
				existingUser.created_at !== optimisticUserMessage.created_at;
			const assistantChanged =
				!existingAssistant ||
				existingAssistant.content !== optimisticAssistantMessage.content ||
				existingAssistant.created_at !== optimisticAssistantMessage.created_at;

			if (!userChanged && !assistantChanged) {
				return current;
			}

			return [
				...current.filter((message) => message.id >= 0),
				userChanged ? optimisticUserMessage : existingUser!,
				assistantChanged ? optimisticAssistantMessage : existingAssistant!,
			];
		});

		setRunUiForChat(chatId, (current) => ({
			runId: run.id,
			clientRequestId: run.client_request_id,
			pendingContent: run.request_content,
			streamStatus: run.latest_state_code
				? { source: "state", code: run.latest_state_code }
				: { source: "state", code: "START" },
			canPauseRun: current?.canPauseRun || false,
			streamingAssistantId: optimisticIds.assistantId,
			optimisticUserId: optimisticIds.userId,
			optimisticAssistantId: optimisticIds.assistantId,
			lastSeenSeq: Math.max(
				current?.lastSeenSeq || 0,
				checkpointSeed?.seq || 0,
				run.latest_event_seq || 0,
			),
			councilEntries: current?.councilEntries || [],
		}));
	}

	function clearRunUi(chatId: string) {
		const controller = streamControllersRef.current[chatId];
		if (controller) {
			controller.abort();
			delete streamControllersRef.current[chatId];
		}

		streamingActiveRef.current[chatId] = false;
		textDelta.clearPendingTextDeltaBatch(chatId);
		council.clearPendingCouncilDeltaBatchesForChat(chatId);
		delete pendingDonePayloadsRef.current[chatId];
		setRunUiForChat(chatId, () => undefined);
	}

	async function attachRunStream(chatId: string, run: ChatRun) {
		if (!selectedChatId || selectedChatId !== chatId) {
			return;
		}

		if (streamControllersRef.current[chatId]) {
			return;
		}

		ensureOptimisticMessages(chatId, run);
		const optimisticAssistantId = optimisticIdsForRun(run.id).assistantId;
		const checkpointSeed = getCheckpointSeed(chatId, run.id);
		const resumeAfterSeq = getResumeAfterSeq(
			checkpointSeed?.seq || 0,
			runUiByChat[chatId]?.lastSeenSeq || 0,
		);
		const controller = new AbortController();
		streamControllersRef.current[chatId] = controller;
		streamingActiveRef.current[chatId] = false;

		try {
			await streamRunSession(
				run.id,
				{
					onRunEvent: (event) => {
						if (
							event.type === "event" &&
							event.code === "magi_intervention_added"
						) {
							council.handleMagiInterventionAdded(
								chatId,
								{ ...(event.payload || {}), seq: event.seq },
								event.seq,
							);
						}
					},
					onSequence: (seq) =>
						setRunUiForChat(chatId, (current) =>
							current
								? {
										...current,
										lastSeenSeq: Math.max(current.lastSeenSeq, seq),
									}
								: current,
						),
					onState: (code) =>
						updateRunUiStreamStatus(chatId, { source: "state", code }),
					onEvent: (code, payload) => {
						if (code === "magi_state") {
							const stateName = String(payload?.state || "");
							if (stateName) {
								setRunUiPauseAvailability(
									chatId,
									MAGI_PAUSEABLE_STATES.has(stateName),
								);
							}
						} else if (code === "magi_phase") {
							const phaseName = String(payload?.phase || "");
							if (phaseName) {
								setRunUiPauseAvailability(
									chatId,
									phaseName === "opening_arguments" ||
										phaseName === "discussion",
								);
							}
						} else if (
							code === "magi_role_start" ||
							code === "magi_role_complete"
						) {
							const phaseName = String(payload?.phase || "");
							if (phaseName) {
								setRunUiPauseAvailability(
									chatId,
									phaseName === "opening_arguments" ||
										phaseName === "discussion",
								);
							}
						} else if (
							code === "magi_discussion_round" ||
							code === "magi_intervention_added"
						) {
							setRunUiPauseAvailability(chatId, true);
						} else if (
							code === "magi_pause_requested" ||
							code === "magi_synthesis_complete"
						) {
							setRunUiPauseAvailability(chatId, false);
						}

						if (
							code !== "text_delta" &&
							code !== "text_checkpoint" &&
							code !== "magi_role_text_delta" &&
							code !== "magi_role_text_checkpoint"
						) {
							updateRunUiStreamStatus(chatId, {
								source: "event",
								code,
								payload,
							});
						}

						if (code === "magi_role_start" && payload) {
							council.handleMagiRoleStart(chatId, payload);
						}

						if (code === "magi_role_text_delta" && payload) {
							council.handleMagiRoleTextDelta(chatId, payload);
						}

						if (code === "magi_role_complete" && payload) {
							council.handleMagiRoleComplete(chatId, payload);
						}
					},
					onPaused: (event) => {
						handlePausedRun(chatId, run, event.message);
					},
					onMagiRoleTextCheckpoint: (payload) => {
						council.handleMagiRoleTextCheckpoint(chatId, payload);
					},
					onTextCheckpoint: (text, seq) => {
						setCheckpointSeed(chatId, run.id, seq, text);
						if (streamingActiveRef.current[chatId]) {
							return;
						}
						applyTextCheckpoint(chatId, optimisticAssistantId, text);
					},
					onTextDelta: (delta) => {
						streamingActiveRef.current[chatId] = true;
						textDelta.queueTextDelta(chatId, optimisticAssistantId, delta);
					},
					onDone: async (payload) => {
						streamingActiveRef.current[chatId] = false;
						delete lastCheckpointRef.current[chatId];
						setRunUiPauseAvailability(chatId, false);

						if (
							textDelta.hasPendingDelta(chatId) ||
							council.hasPendingCouncilWork(chatId)
						) {
							pendingDonePayloadsRef.current[chatId] = {
								payload,
								selectedProjectIdAtCompletion: selectedProjectIdRef.current,
							};
							return;
						}

						await finalizeDone(chatId, payload, selectedProjectIdRef.current);
					},
					onCancelled: (message) => {
						streamingActiveRef.current[chatId] = false;
						delete lastCheckpointRef.current[chatId];
						setRunUiPauseAvailability(chatId, false);
						textDelta.clearPendingTextDeltaBatch(chatId);
						setMessagesForChat(chatId, (current) =>
							current.filter((messageItem) => messageItem.id >= 0),
						);
						clearRunUi(chatId);
						updateChatRunStatusRef.current(chatId, null, null);
						onErrorRef.current(message);
					},
					onError: (message) => {
						streamingActiveRef.current[chatId] = false;
						delete lastCheckpointRef.current[chatId];
						setRunUiPauseAvailability(chatId, false);
						textDelta.clearPendingTextDeltaBatch(chatId);
						setMessagesForChat(chatId, (current) =>
							current.filter((messageItem) => messageItem.id >= 0),
						);
						clearRunUi(chatId);
						updateChatRunStatusRef.current(chatId, null, null);
						onErrorRef.current(message);
					},
				},
				{
					afterSeq: resumeAfterSeq,
					signal: controller.signal,
				},
			);
		} catch (err) {
			const name = (err as Error).name || "";
			if (name !== "AbortError") {
				onErrorRef.current((err as Error).message);
			}
		} finally {
			streamingActiveRef.current[chatId] = false;
			if (streamControllersRef.current[chatId] === controller) {
				delete streamControllersRef.current[chatId];
			}
		}
	}

	async function handleCancelActiveRun() {
		const runId = selectedChat?.active_run_id || selectedRunUi?.runId;
		if (!runId || !selectedChatId) {
			return;
		}

		try {
			await api.cancelRun(runId);
			updateChatRunStatusRef.current(selectedChatId, runId, "cancel_requested");
		} catch (err) {
			onErrorRef.current((err as Error).message);
		}
	}

	function resetAll() {
		const chatIds = new Set([
			...Object.keys(streamControllersRef.current),
			...Object.keys(runUiByChat),
			...Object.keys(lastCheckpointRef.current),
			...Object.keys(pendingDonePayloadsRef.current),
		]);

		chatIds.forEach((chatId) => {
			streamControllersRef.current[chatId]?.abort();
			textDelta.clearPendingTextDeltaBatch(chatId);
			council.clearPendingCouncilDeltaBatchesForChat(chatId);
		});

		streamControllersRef.current = {};
		streamingActiveRef.current = {};
		lastCheckpointRef.current = {};
		pendingDonePayloadsRef.current = {};
		setRunUiByChat({});
	}

	useEffect(() => {
		onTextDrainCompleteRef.current = handleTextDrainComplete;
		onCouncilDrainCompleteRef.current = handleCouncilDrainComplete;
		runUiCouncilEntriesUpdaterRef.current = updateRunUiCouncilEntries;
		runUiStreamStatusUpdaterRef.current = updateRunUiStreamStatus;
	});

	useEffect(() => {
		const previousChatId = previousSelectedChatIdRef.current;
		if (previousChatId && previousChatId !== selectedChatId) {
			const controller = streamControllersRef.current[previousChatId];
			if (controller) {
				controller.abort();
				delete streamControllersRef.current[previousChatId];
			}
			streamingActiveRef.current[previousChatId] = false;
		}

		previousSelectedChatIdRef.current = selectedChatId;
	}, [selectedChatId]);

	useEffect(() => {
		const staleChatIds = chats
			.filter((chat) =>
				shouldReconcileDetachedRunUi(
					runUiByChat[chat.id]?.runId,
					chat.active_run_id,
					Boolean(streamControllersRef.current[chat.id]),
					runUiByChat[chat.id]?.streamStatus?.code || null,
				),
			)
			.map((chat) => chat.id);

		if (staleChatIds.length === 0) {
			return;
		}

		staleChatIds.forEach((chatId) => {
			clearRunUi(chatId);
			void reloadMessagesRef.current(chatId).catch((err: Error) => {
				onErrorRef.current(err.message);
			});
		});
	}, [chats, runUiByChat]);

	useEffect(() => {
		if (!selectedChatId || !selectedChat?.active_run_id) {
			return;
		}

		void api
			.getRun(selectedChat.active_run_id)
			.then((run) => attachRunStream(selectedChatId, run))
			.catch((err: Error) => {
				onErrorRef.current(err.message);
			});
	}, [selectedChat?.active_run_id, selectedChatId]);

	useEffect(() => {
		if (!selectedProjectId) {
			return;
		}

		const hasActiveRuns = chats.some((chat) => chat.active_run_id);
		if (!hasActiveRuns) {
			return;
		}

		const intervalId = window.setInterval(() => {
			void reloadChatsRef.current(selectedProjectId).catch(() => undefined);
		}, 2000);

		return () => {
			window.clearInterval(intervalId);
		};
	}, [chats, selectedProjectId]);

	return {
		selectedRunUi,
		streamStatus,
		streamingAssistantId,
		selectedChatBusy,
		attachRunStream,
		clearRunUi,
		handleCancelActiveRun,
		handlePauseActiveRun,
		handleResumeActiveRun,
		setRunUiForChat,
		resetAll,
	};
}
