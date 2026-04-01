export function getResumeAfterSeq(checkpointSeq?: number | null, lastSeenSeq?: number | null): number {
  return Math.max(0, Number(checkpointSeq) || 0, Number(lastSeenSeq) || 0);
}

export function shouldReconcileDetachedRunUi(
  localRunId: string | null | undefined,
  activeRunId: string | null | undefined,
  hasLiveController: boolean,
): boolean {
  if (!localRunId || hasLiveController) {
    return false;
  }
  return localRunId !== (activeRunId || "");
}
