export type PendingCouncilBatchSnapshot = {
  delta: string;
  frameId: number | null;
} | null | undefined;

export type PendingCouncilCompletionMap = Record<string, unknown>;
export type PendingCouncilBatchMap = Record<string, PendingCouncilBatchSnapshot>;

export function shouldDeferCouncilCompletion(batch: PendingCouncilBatchSnapshot): boolean {
  if (!batch) {
    return false;
  }
  return Boolean(batch.delta) || batch.frameId !== null;
}

export function takeReadyCouncilCompletion<T>(
  pendingCompletion: T | undefined,
  batch: PendingCouncilBatchSnapshot,
): T | null {
  if (!pendingCompletion) {
    return null;
  }
  return shouldDeferCouncilCompletion(batch) ? null : pendingCompletion;
}

export function getCouncilCompletionCatchupDelta(streamedText: string, finalText: string): string {
  const current = String(streamedText || "");
  const complete = String(finalText || "");

  if (!complete || complete.length <= current.length) {
    return "";
  }
  if (!current) {
    return complete;
  }
  if (!complete.startsWith(current)) {
    return "";
  }
  return complete.slice(current.length);
}

export function hasPendingCouncilWorkForChat(
  chatId: string,
  pendingBatches: PendingCouncilBatchMap,
  pendingCompletions: PendingCouncilCompletionMap,
): boolean {
  const prefix = `${chatId}:`;
  return (
    Object.entries(pendingBatches).some(([key, batch]) => key.startsWith(prefix) && shouldDeferCouncilCompletion(batch))
    || Object.keys(pendingCompletions).some((key) => key.startsWith(prefix))
  );
}
