import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";

import { useRunHistory } from "../../hooks/useRunHistory";

export function RunHistoryChart() {
	const { buckets } = useRunHistory(7);

	return (
		<div className="widget">
			<div className="widget__title">Last 7 days</div>
			{buckets.length === 0 && (
				<div
					className="empty-state"
					style={{ minHeight: 120, padding: "0.5rem" }}
				>
					<p className="lede" style={{ margin: 0 }}>
						No data yet
					</p>
				</div>
			)}
			{buckets.length > 0 && (
				<div style={{ height: 120 }}>
					<ResponsiveContainer>
						<BarChart data={buckets}>
							<XAxis
								dataKey="day"
								tickFormatter={(d: string) => d.slice(5)}
								tick={{ fontSize: 10, fill: "var(--text3)" }}
								axisLine={false}
								tickLine={false}
							/>
							<Tooltip />
							<Bar dataKey="passed" stackId="a" fill="#4ade80" />
							<Bar dataKey="failed" stackId="a" fill="#f87171" />
						</BarChart>
					</ResponsiveContainer>
				</div>
			)}
		</div>
	);
}
