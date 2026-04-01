import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChatRun } from "../types";

type RunSnapshotState = {
  run: ChatRun | null;
  loading: boolean;
  error: string;
  refresh: () => void;
};

export function useRunSnapshot(runId: string): RunSnapshotState {
  const [run, setRun] = useState<ChatRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    if (!runId) {
      setRun(null);
      setLoading(false);
      setError("");
      return;
    }

    let cancelled = false;

    async function load() {
      setLoading(true);
      setError("");
      try {
        const nextRun = await api.getRun(runId);
        if (!cancelled) {
          setRun(nextRun);
        }
      } catch (err) {
        if (!cancelled) {
          setError((err as Error).message);
          setRun(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, [runId, refreshNonce]);

  return {
    run,
    loading,
    error,
    refresh: () => setRefreshNonce((current) => current + 1),
  };
}
