import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { RunEventItem, SetupRunEventItem } from "../types";

export type EventItem = RunEventItem | SetupRunEventItem;

interface UseEventsOptions {
	/** "setup" for setup runs, "evaluation" for evaluation runs */
	kind: "setup" | "evaluation";
	/** The run ID */
	runId: string | null;
	/** Is the run still active? If false, polling drops to 0.1Hz */
	active: boolean;
}

export function useEvents({ kind, runId, active }: UseEventsOptions) {
	const [events, setEvents] = useState<EventItem[]>([]);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	// Cursor state
	const cursorRef = useRef<{
		after_round_index: number;
		after_seq: number;
	}>({ after_round_index: -1, after_seq: -1 });

	const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

	const fetch = useCallback(async () => {
		if (!runId) return;
		try {
			if (kind === "evaluation") {
				const newEvents = await api.listEvaluationEvents(
					runId,
					cursorRef.current.after_seq,
				);
				if (newEvents.length > 0) {
					setEvents((prev) => [...prev, ...newEvents]);
					const last = newEvents[newEvents.length - 1];
					cursorRef.current = {
						after_round_index: -1,
						after_seq: (last as RunEventItem).seq,
					};
				}
			} else {
				const newEvents = await api.listSetupRunEvents(runId, {
					after_round_index: cursorRef.current.after_round_index,
					after_seq: cursorRef.current.after_seq,
				});
				if (newEvents.length > 0) {
					setEvents((prev) => [...prev, ...newEvents]);
					const last = newEvents[newEvents.length - 1] as SetupRunEventItem;
					cursorRef.current = {
						after_round_index: last.round_index,
						after_seq: last.seq,
					};
				}
			}
		} catch (e: unknown) {
			const msg = e instanceof Error ? e.message : "Event fetch failed";
			setError(msg);
		}
	}, [kind, runId]);

	// Initial load when runId changes
	useEffect(() => {
		setEvents([]);
		setError(null);
		cursorRef.current = { after_round_index: -1, after_seq: -1 };
		if (runId) {
			setLoading(true);
			fetch().finally(() => setLoading(false));
		}
	}, [runId, fetch]);

	// Polling
	useEffect(() => {
		if (intervalRef.current) {
			clearInterval(intervalRef.current);
			intervalRef.current = null;
		}

		if (!runId) return;

		const intervalMs = active ? 1000 : 10000;
		intervalRef.current = setInterval(() => {
			fetch();
		}, intervalMs);

		return () => {
			if (intervalRef.current) {
				clearInterval(intervalRef.current);
				intervalRef.current = null;
			}
		};
	}, [runId, active, fetch]);

	return { events, loading, error };
}
