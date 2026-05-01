import { useEffect, useState } from "react";

import { api } from "../api";

type Bucket = { day: string; passed: number; failed: number };

export function useRunHistory(days = 7) {
	const [buckets, setBuckets] = useState<Bucket[]>([]);

	useEffect(() => {
		api
			.listRuns({ page_size: 500 })
			.then((res) => {
				const since = Date.now() - days * 86400000;
				const benchmarks = res.items.filter(
					(r) =>
						r.kind === "benchmark" && new Date(r.created_at).getTime() >= since,
				);

				const byDay = new Map<string, Bucket>();
				for (let i = days - 1; i >= 0; i--) {
					const d = new Date(Date.now() - i * 86400000);
					const key = d.toISOString().slice(0, 10);
					byDay.set(key, { day: key, passed: 0, failed: 0 });
				}

				for (const r of benchmarks) {
					const key = r.created_at.slice(0, 10);
					const b = byDay.get(key);
					if (!b) continue;
					if (r.status === "completed") b.passed++;
					else if (r.status === "failed") b.failed++;
				}

				setBuckets([...byDay.values()]);
			})
			.catch(() => {
				// Silently handle fetch failures
			});
	}, [days]);

	return { buckets };
}
