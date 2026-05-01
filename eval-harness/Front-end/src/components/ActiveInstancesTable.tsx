import { useState } from "react";
import type { InstanceItem } from "../types";

function ageFrom(launchedAt: string | null): string {
	if (!launchedAt) return "--";
	const ms = Date.now() - new Date(launchedAt).getTime();
	const mins = Math.floor(ms / 60000);
	if (mins < 60) return `${mins}m`;
	const hrs = Math.floor(mins / 60);
	if (hrs < 24) return `${hrs}h`;
	return `${Math.floor(hrs / 24)}d`;
}

export default function ActiveInstancesTable({
	instances,
	loading,
	onTerminate,
}: {
	instances: InstanceItem[];
	loading: boolean;
	onTerminate: (id: string) => void;
}) {
	const [confirmId, setConfirmId] = useState<string | null>(null);
	const [terminating, setTerminating] = useState(false);

	const handleTerminate = async (id: string) => {
		setTerminating(true);
		await onTerminate(id);
		setTerminating(false);
		setConfirmId(null);
	};

	if (loading) {
		return (
			<p className="lede" style={{ margin: 0, padding: "1rem 0" }}>
				Loading instances...
			</p>
		);
	}

	if (instances.length === 0) {
		return (
			<div
				className="empty-state"
				style={{ padding: "1.5rem", minHeight: 120 }}
			>
				<p className="lede" style={{ margin: 0 }}>
					No active EvalHarness instances
				</p>
			</div>
		);
	}

	return (
		<div style={{ overflowX: "auto" }}>
			<table
				style={{
					width: "100%",
					borderCollapse: "collapse",
					fontSize: 12,
					fontFamily: "var(--mono)",
				}}
			>
				<thead>
					<tr
						style={{
							borderBottom: "1px solid var(--border)",
							color: "var(--muted)",
							textAlign: "left",
							fontSize: 10,
							letterSpacing: "0.08em",
							textTransform: "uppercase",
						}}
					>
						<th style={{ padding: "6px 8px" }}>ID</th>
						<th style={{ padding: "6px 8px" }}>State</th>
						<th style={{ padding: "6px 8px" }}>Type</th>
						<th style={{ padding: "6px 8px" }}>Role</th>
						<th style={{ padding: "6px 8px" }}>Public IP</th>
						<th style={{ padding: "6px 8px" }}>Age</th>
						<th style={{ padding: "6px 8px" }} />
					</tr>
				</thead>
				<tbody>
					{instances.map((inst) => {
						const role = inst.tags?.EvalRole ?? "--";
						return (
							<tr
								key={inst.instance_id}
								style={{ borderBottom: "1px solid var(--border)" }}
							>
								<td
									style={{
										padding: "6px 8px",
										color: "var(--accent-text)",
										maxWidth: 160,
										overflow: "hidden",
										textOverflow: "ellipsis",
										whiteSpace: "nowrap",
									}}
									title={inst.instance_id}
								>
									{inst.instance_id}
								</td>
								<td style={{ padding: "6px 8px" }}>
									<span
										style={{
											display: "inline-flex",
											alignItems: "center",
											gap: 6,
										}}
									>
										<span
											style={{
												width: 6,
												height: 6,
												borderRadius: 999,
												background:
													inst.state === "running"
														? "var(--green)"
														: inst.state === "pending"
															? "#fbbf24"
															: "var(--muted)",
												flexShrink: 0,
											}}
										/>
										{inst.state}
									</span>
								</td>
								<td style={{ padding: "6px 8px" }}>{inst.instance_type}</td>
								<td style={{ padding: "6px 8px", color: "var(--muted)" }}>
									{role}
								</td>
								<td style={{ padding: "6px 8px", color: "var(--muted)" }}>
									{inst.public_ip ?? "--"}
								</td>
								<td style={{ padding: "6px 8px", color: "var(--muted)" }}>
									{ageFrom(inst.launched_at)}
								</td>
								<td style={{ padding: "6px 8px", textAlign: "right" }}>
									{confirmId === inst.instance_id ? (
										<span style={{ display: "inline-flex", gap: 6 }}>
											<button
												type="button"
												className="cancel-run-btn"
												onClick={() => handleTerminate(inst.instance_id)}
												disabled={terminating}
												style={{ fontSize: 11, padding: "3px 8px" }}
											>
												Terminate
											</button>
											<button
												type="button"
												className="ghost-button icon-button"
												onClick={() => setConfirmId(null)}
												style={{ width: 24, height: 24, fontSize: 11 }}
											>
												x
											</button>
										</span>
									) : (
										<button
											type="button"
											className="ghost-button"
											onClick={() => setConfirmId(inst.instance_id)}
											style={{ fontSize: 11, padding: "3px 8px" }}
										>
											Terminate
										</button>
									)}
								</td>
							</tr>
						);
					})}
				</tbody>
			</table>
		</div>
	);
}
