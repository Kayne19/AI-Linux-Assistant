import { useImages } from "../hooks/useImages";
import { useInstances } from "../hooks/useInstances";
import { usePreflight } from "../hooks/usePreflight";
import ActiveInstancesTable from "./ActiveInstancesTable";

export default function InfraPage() {
	const { instances, loading: instLoading, terminate } = useInstances();
	const { images, loading: imgLoading, deregister } = useImages();
	const {
		result: preflightResult,
		loading: prefLoading,
		runPreflight,
	} = usePreflight();

	return (
		<div
			style={{ padding: 20, display: "flex", flexDirection: "column", gap: 24 }}
		>
			<div
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
				}}
			>
				<h2
					style={{
						margin: 0,
						fontSize: 15,
						lineHeight: 1.1,
						letterSpacing: "-0.02em",
					}}
				>
					Infrastructure
				</h2>
				<div style={{ display: "flex", alignItems: "center", gap: 10 }}>
					{preflightResult && (
						<span
							style={{
								fontFamily: "var(--mono)",
								fontSize: 11,
								color: preflightResult.ok ? "var(--green)" : "var(--danger)",
							}}
						>
							{preflightResult.ok ? "Preflight OK" : "Preflight FAILED"}
						</span>
					)}
					<button
						type="button"
						className="ghost-button"
						onClick={runPreflight}
						disabled={prefLoading}
						style={{ fontSize: 12, padding: "4px 12px" }}
					>
						{prefLoading ? "Running..." : "Run Preflight"}
					</button>
				</div>
			</div>

			{preflightResult && !preflightResult.ok && (
				<div
					style={{
						padding: 10,
						border: "1px solid rgba(251, 113, 133, 0.2)",
						borderRadius: 4,
						background: "rgba(251, 113, 133, 0.08)",
						color: "var(--danger)",
						fontFamily: "var(--mono)",
						fontSize: 12,
						lineHeight: 1.5,
					}}
				>
					{preflightResult.message}
				</div>
			)}

			<div>
				<h3
					style={{
						margin: "0 0 12px",
						fontFamily: "var(--mono)",
						fontSize: 10,
						letterSpacing: "0.12em",
						textTransform: "uppercase",
						color: "var(--accent-text)",
						fontWeight: 500,
					}}
				>
					EC2 Instances
				</h3>
				{instLoading ? (
					<p className="lede">Loading instances...</p>
				) : instances.length === 0 ? (
					<div
						className="empty-state"
						style={{ padding: "1.5rem", minHeight: 120 }}
					>
						<p className="lede" style={{ margin: 0 }}>
							No EvalHarness instances found
						</p>
					</div>
				) : (
					<ActiveInstancesTable
						instances={instances}
						loading={false}
						onTerminate={async (id) => {
							await terminate(id);
						}}
					/>
				)}
			</div>

			<div>
				<h3
					style={{
						margin: "0 0 12px",
						fontFamily: "var(--mono)",
						fontSize: 10,
						letterSpacing: "0.12em",
						textTransform: "uppercase",
						color: "var(--accent-text)",
						fontWeight: 500,
					}}
				>
					AMIs (Golden &amp; Broken)
				</h3>
				{imgLoading ? (
					<p className="lede">Loading images...</p>
				) : images.length === 0 ? (
					<div
						className="empty-state"
						style={{ padding: "1.5rem", minHeight: 120 }}
					>
						<p className="lede" style={{ margin: 0 }}>
							No EvalHarness AMIs found
						</p>
					</div>
				) : (
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
									<th style={{ padding: "6px 8px" }}>Image ID</th>
									<th style={{ padding: "6px 8px" }}>Name</th>
									<th style={{ padding: "6px 8px" }}>Role</th>
									<th style={{ padding: "6px 8px" }}>State</th>
									<th style={{ padding: "6px 8px" }}>Created</th>
									<th style={{ padding: "6px 8px" }} />
								</tr>
							</thead>
							<tbody>
								{images.map((img) => {
									const role =
										img.tags?.EvalImageRole || img.tags?.EvalRole || "--";
									return (
										<tr
											key={img.image_id}
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
												title={img.image_id}
											>
												{img.image_id}
											</td>
											<td style={{ padding: "6px 8px" }}>{img.name ?? "--"}</td>
											<td style={{ padding: "6px 8px", color: "var(--muted)" }}>
												{role}
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
																img.state === "available"
																	? "var(--green)"
																	: "var(--muted)",
															flexShrink: 0,
														}}
													/>
													{img.state}
												</span>
											</td>
											<td style={{ padding: "6px 8px", color: "var(--muted)" }}>
												{img.created_at
													? new Date(img.created_at).toLocaleDateString()
													: "--"}
											</td>
											<td style={{ padding: "6px 8px", textAlign: "right" }}>
												<button
													type="button"
													className="ghost-button"
													onClick={() => deregister(img.image_id)}
													style={{ fontSize: 11, padding: "3px 8px" }}
												>
													Deregister
												</button>
											</td>
										</tr>
									);
								})}
							</tbody>
						</table>
					</div>
				)}
			</div>
		</div>
	);
}
