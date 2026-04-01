import { useEffect, useState } from "react";

import { api } from "../api";
import type { ChatRun } from "../types";
import { dedupeRuns, isActiveRunStatus } from "./debugUtils";

const RUNS_PAGE_SIZE = 20;

type RunHistoryState = {
  runs: ChatRun[];
  total: number;
  page: number;
  hasMore: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: string;
};

export function useRunHistory(chatId: string) {
  const [state, setState] = useState<RunHistoryState>({
    runs: [],
    total: 0,
    page: 1,
    hasMore: false,
    loading: false,
    loadingMore: false,
    error: "",
  });

  useEffect(() => {
    if (!chatId) {
      setState({
        runs: [],
        total: 0,
        page: 1,
        hasMore: false,
        loading: false,
        loadingMore: false,
        error: "",
      });
      return;
    }

    let cancelled = false;
    setState((current) => ({ ...current, loading: true, error: "", runs: [], total: 0, page: 1, hasMore: false }));

    void (async () => {
      try {
        const [chat, response] = await Promise.all([
          api.getChat(chatId),
          api.listRuns(chatId, { page: 1, pageSize: RUNS_PAGE_SIZE }),
        ]);
        let runs = dedupeRuns(response.runs);
        if (chat.active_run_id && !runs.some((run) => run.id === chat.active_run_id)) {
          const activeRun = await api.getRun(chat.active_run_id);
          runs = dedupeRuns([activeRun, ...runs]);
        }
        if (cancelled) {
          return;
        }
        setState({
          runs,
          total: Math.max(response.total, runs.length),
          page: response.page,
          hasMore: response.has_more,
          loading: false,
          loadingMore: false,
          error: "",
        });
      } catch (error) {
        if (cancelled) {
          return;
        }
        setState((current) => ({
          ...current,
          loading: false,
          loadingMore: false,
          error: (error as Error).message,
        }));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [chatId]);

  async function loadMore() {
    if (!chatId || state.loading || state.loadingMore || !state.hasMore) {
      return;
    }
    setState((current) => ({ ...current, loadingMore: true, error: "" }));
    try {
      const response = await api.listRuns(chatId, { page: state.page + 1, pageSize: RUNS_PAGE_SIZE });
      setState((current) => ({
        ...current,
        runs: dedupeRuns([...current.runs, ...response.runs]),
        total: Math.max(current.total, response.total),
        page: response.page,
        hasMore: response.has_more,
        loadingMore: false,
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        loadingMore: false,
        error: (error as Error).message,
      }));
    }
  }

  async function refresh() {
    if (!chatId) {
      return;
    }
    setState((current) => ({ ...current, loading: true, error: "" }));
    try {
      const [chat, response] = await Promise.all([
        api.getChat(chatId),
        api.listRuns(chatId, { page: 1, pageSize: RUNS_PAGE_SIZE }),
      ]);
      let runs = dedupeRuns(response.runs);
      if (chat.active_run_id && !runs.some((run) => run.id === chat.active_run_id)) {
        const activeRun = await api.getRun(chat.active_run_id);
        runs = dedupeRuns([activeRun, ...runs]);
      }
      setState((current) => ({
        ...current,
        runs,
        total: Math.max(response.total, runs.length),
        page: response.page,
        hasMore: response.has_more,
        loading: false,
        loadingMore: false,
        error: "",
      }));
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        loadingMore: false,
        error: (error as Error).message,
      }));
    }
  }

  const activeRun = state.runs.find((run) => isActiveRunStatus(run.status)) || null;
  const historicalRuns = state.runs.filter((run) => !isActiveRunStatus(run.status));

  return {
    runs: state.runs,
    activeRun,
    historicalRuns,
    total: state.total,
    hasMore: state.hasMore,
    loading: state.loading,
    loadingMore: state.loadingMore,
    error: state.error,
    loadMore,
    refresh,
  };
}
