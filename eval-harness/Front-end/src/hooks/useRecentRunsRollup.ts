import { useEffect, useState } from "react";

import { api } from "../api";

export function useRecentRunsRollup({ window: w }: { window: number }) {
	const [points, setPoints] = useState<{ idx: number; rate: number }[]>([]);

	useEffect(() => {
		api
			.listRuns({ page_size: w * 2 })
			.then((res) => {
				const benchmarks = res.items.filter((r) => r.kind === "benchmark");
				const sorted = [...benchmarks].sort(
					(a, b) =>
						new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
				);
				const windowed = sorted.slice(-w);

				const out: { idx: number; rate: number }[] = [];
				for (let i = 0; i < windowed.length; i++) {
					const slice = windowed.slice(Math.max(0, i - 9), i + 1);
					const passes = slice.filter((r) => r.status === "completed").length;
					out.push({ idx: i, rate: passes / slice.length });
				}
				setPoints(out);
			})
			.catch(() => {
				// Silently handle fetch failures
			});
	}, [w]);

	return { points };
}
