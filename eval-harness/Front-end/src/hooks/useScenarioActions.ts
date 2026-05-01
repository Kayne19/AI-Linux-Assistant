import { useCallback, useState } from "react";
import { api } from "../api";
import type {
	BenchmarkRequest,
	ControlResponse,
	JudgeRequest,
	RunAllRequest,
	VerifyRequest,
} from "../types";

export interface ScenarioActionsState {
	dispatching: string | null;
	lastResult: ControlResponse | null;
	error: string | null;
}

export function useScenarioActions(scenarioId: string) {
	const [state, setState] = useState<ScenarioActionsState>({
		dispatching: null,
		lastResult: null,
		error: null,
	});

	const dispatch = useCallback(
		async (action: string, fn: () => Promise<ControlResponse>) => {
			setState({ dispatching: action, lastResult: null, error: null });
			try {
				const result = await fn();
				setState({ dispatching: null, lastResult: result, error: null });
				return result;
			} catch (e: unknown) {
				const msg = e instanceof Error ? e.message : `${action} failed`;
				setState({ dispatching: null, lastResult: null, error: msg });
				throw e;
			}
		},
		[],
	);

	const verify = useCallback(
		(body: VerifyRequest = {}) =>
			dispatch("verify", () => api.verifyScenario(scenarioId, body)),
		[scenarioId, dispatch],
	);

	const benchmark = useCallback(
		(body: BenchmarkRequest) =>
			dispatch("benchmark", () => api.benchmarkScenario(scenarioId, body)),
		[scenarioId, dispatch],
	);

	const runAll = useCallback(
		(body: RunAllRequest = {}) =>
			dispatch("run-all", () => api.runAllScenario(scenarioId, body)),
		[scenarioId, dispatch],
	);

	const triggerJudge = useCallback(
		(benchmarkRunId: string, body: JudgeRequest = {}) =>
			dispatch("judge", () => api.triggerJudge(benchmarkRunId, body)),
		[dispatch],
	);

	const cancelRun = useCallback(
		(runId: string, runType?: string) =>
			dispatch("cancel", () => api.cancelRun(runId, runType)),
		[dispatch],
	);

	const clear = useCallback(() => {
		setState({ dispatching: null, lastResult: null, error: null });
	}, []);

	return {
		...state,
		verify,
		benchmark,
		runAll,
		triggerJudge,
		cancelRun,
		clear,
	};
}
