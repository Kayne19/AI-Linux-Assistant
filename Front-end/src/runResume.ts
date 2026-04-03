export function getResumeAfterSeq(checkpointSeq?: number | null, lastSeenSeq?: number | null): number {
  return Math.max(0, Number(checkpointSeq) || 0, Number(lastSeenSeq) || 0);
}

const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "cancel_requested", "pause_requested", "paused"]);
const PAUSED_RUN_STATUSES = new Set(["pause_requested", "paused"]);

type ChatRunSnapshot = {
  id: string;
  active_run_id?: string | null;
  active_run_status?: string | null;
};

export function mergeChatsPreservingActiveRunSnapshots<T extends ChatRunSnapshot>(
  currentChats: T[],
  nextChats: T[],
): T[] {
  const currentById = new Map((currentChats || []).map((chat) => [chat.id, chat]));

  return (nextChats || []).map((nextChat) => {
    const currentChat = currentById.get(nextChat.id);
    if (!currentChat) {
      return nextChat;
    }

    const currentActiveRunId = currentChat.active_run_id || null;
    const currentActiveRunStatus = currentChat.active_run_status || null;
    if (!currentActiveRunId || !currentActiveRunStatus || !ACTIVE_RUN_STATUSES.has(currentActiveRunStatus)) {
      return nextChat;
    }

    if (nextChat.active_run_id) {
      return nextChat;
    }

    return {
      ...nextChat,
      active_run_id: currentActiveRunId,
      active_run_status: currentActiveRunStatus,
    };
  });
}

export function shouldReconcileDetachedRunUi(
  localRunId: string | null | undefined,
  activeRunId: string | null | undefined,
  hasLiveController: boolean,
  localRunStatus?: string | null,
): boolean {
  if (!localRunId || hasLiveController) {
    return false;
  }
  if (!activeRunId && localRunStatus && PAUSED_RUN_STATUSES.has(localRunStatus)) {
    return false;
  }
  return localRunId !== (activeRunId || "");
}
