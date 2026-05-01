import { useCallback, useRef, useState } from "react";

import type { GenerateRequest } from "../types";

export type StreamEvent =
	| { type: "token"; text: string }
	| { type: "scenario"; scenario: Record<string, unknown> }
	| { type: "error"; message: string };

type State = {
	status: "idle" | "streaming" | "done" | "error";
	text: string;
	scenario: Record<string, unknown> | null;
	error: string | null;
};

const INITIAL: State = {
	status: "idle",
	text: "",
	scenario: null,
	error: null,
};

export function useGenerateStream() {
	const [state, setState] = useState<State>(INITIAL);
	const abortRef = useRef<AbortController | null>(null);

	const start = useCallback(async (req: GenerateRequest) => {
		setState({ ...INITIAL, status: "streaming" });
		const controller = new AbortController();
		abortRef.current = controller;

		try {
			const API_BASE_URL =
				(import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ||
				"http://localhost:8001";
			const response = await fetch(
				`${API_BASE_URL}/api/v1/scenarios/generate/stream`,
				{
					method: "POST",
					headers: { "content-type": "application/json" },
					body: JSON.stringify(req),
					signal: controller.signal,
				},
			);
			if (!response.ok || !response.body) {
				throw new Error(`HTTP ${response.status}`);
			}
			const reader = response.body.getReader();
			const decoder = new TextDecoder();
			let buffer = "";
			while (true) {
				const { value, done } = await reader.read();
				if (done) break;
				buffer += decoder.decode(value, { stream: true });
				const lines = buffer.split("\n\n");
				buffer = lines.pop() ?? "";
				for (const block of lines) {
					const dataLine = block.split("\n").find((l) => l.startsWith("data:"));
					if (!dataLine) continue;
					const json = dataLine.slice("data:".length).trim();
					if (!json) continue;
					const event = JSON.parse(json) as StreamEvent;
					setState((prev) => {
						if (event.type === "token") {
							return { ...prev, text: prev.text + event.text };
						}
						if (event.type === "scenario") {
							return {
								...prev,
								scenario: event.scenario,
								status: "done",
							};
						}
						return { ...prev, status: "error", error: event.message };
					});
				}
			}
			// If stream ended without a scenario event, mark as done
			setState((prev) =>
				prev.status === "streaming" ? { ...prev, status: "done" } : prev,
			);
		} catch (err) {
			if ((err as Error).name === "AbortError") {
				setState((prev) => ({ ...prev, status: "idle" }));
			} else {
				setState((prev) => ({
					...prev,
					status: "error",
					error: prev.error ?? String(err),
				}));
			}
		}
	}, []);

	const cancel = useCallback(() => {
		abortRef.current?.abort();
		setState((prev) => ({ ...prev, status: "idle" }));
	}, []);

	const reset = useCallback(() => setState(INITIAL), []);

	return { ...state, start, cancel, reset };
}
