import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";

import { useRecentRunsRollup } from "../../hooks/useRecentRunsRollup";

export function SuccessRateSparkline() {
	const { points } = useRecentRunsRollup({ window: 50 });

	return (
		<div className="widget">
			<div className="widget__title">Success rate (last 50 runs)</div>
			{points.length === 0 && (
				<div
					className="empty-state"
					style={{ minHeight: 80, padding: "0.5rem" }}
				>
					<p className="lede" style={{ margin: 0 }}>
						No data yet
					</p>
				</div>
			)}
			{points.length > 0 && (
				<div style={{ height: 80 }}>
					<ResponsiveContainer>
						<LineChart data={points}>
							<YAxis hide domain={[0, 1]} />
							<Tooltip formatter={(v) => `${Math.round(Number(v) * 100)}%`} />
							<Line
								type="monotone"
								dataKey="rate"
								stroke="#4ade80"
								dot={false}
								strokeWidth={2}
							/>
						</LineChart>
					</ResponsiveContainer>
				</div>
			)}
		</div>
	);
}
