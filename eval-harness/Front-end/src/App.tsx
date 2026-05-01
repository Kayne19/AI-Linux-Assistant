import { useCallback, useEffect, useState } from "react";
import { DataBrowser } from "./components/DataBrowser";
import ScenarioDetail from "./components/ScenarioDetail";
import DebugDrawer from "./debug/DebugDrawer";
import { useScenarios } from "./hooks/useScenarios";
import type { ScenarioListItem } from "./types";

type Mode = "dashboard" | "scenarios" | "infra" | "data";

export default function App() {
	const [mode, setMode] = useState<Mode>("scenarios");
	const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(
		null,
	);
	const [debugOpen, setDebugOpen] = useState(false);
	const { scenarios, loading, error, refresh } = useScenarios();

	// Keyboard shortcut for debug drawer
	useEffect(() => {
		const handler = (e: KeyboardEvent) => {
			if ((e.metaKey || e.ctrlKey) && e.key === "d") {
				e.preventDefault();
				setDebugOpen((prev) => !prev);
			}
		};
		window.addEventListener("keydown", handler);
		return () => window.removeEventListener("keydown", handler);
	}, []);

	const handleSelectScenario = useCallback((id: string) => {
		setSelectedScenarioId(id);
		setMode("scenarios");
	}, []);

	return (
		<div className="app-shell">
			{/* Sidebar */}
			<aside className="sidebar">
				<div className="sidebar-top">
					<p className="eyebrow">Eval Harness</p>
					<h1
						style={{
							margin: 0,
							fontSize: 15,
							lineHeight: 1.1,
							letterSpacing: "-0.02em",
						}}
					>
						Control Center
					</h1>
				</div>

				{/* Mode nav */}
				<nav className="rail-section">
					{(["dashboard", "scenarios", "infra", "data"] as const).map((m) => (
						<button
							key={m}
							className={`rail-item ${mode === m ? "active" : ""}`}
							onClick={() => setMode(m)}
						>
							<span className="rail-item-copy">
								<strong>{m[0].toUpperCase() + m.slice(1)}</strong>
							</span>
						</button>
					))}
				</nav>

				{/* Scenario list (only in scenarios mode) */}
				{mode === "scenarios" && (
					<ScenarioSidebar
						scenarios={scenarios}
						loading={loading}
						error={error}
						selectedId={selectedScenarioId}
						onSelect={handleSelectScenario}
						onRefresh={refresh}
					/>
				)}

				{/* Footer */}
				<div className="sidebar-footer">
					<div className="sidebar-footer-actions">
						<button
							className={`debug-chip ${debugOpen ? "active" : ""}`}
							onClick={() => setDebugOpen(!debugOpen)}
						>
							Debug
						</button>
						<span
							style={{
								fontFamily: "var(--mono)",
								fontSize: 10,
								color: "var(--text3)",
							}}
						>
							Cmd+D
						</span>
					</div>
				</div>
			</aside>

			{/* Main panel */}
			<main className="main-panel">
				{mode === "scenarios" && selectedScenarioId && (
					<ScenarioDetail scenarioId={selectedScenarioId} />
				)}

				{mode === "scenarios" && !selectedScenarioId && (
					<div
						style={{
							display: "flex",
							flexDirection: "column",
							alignItems: "center",
							justifyContent: "center",
							height: "100%",
							padding: 40,
							textAlign: "center",
						}}
					>
						<h2
							style={{
								margin: "0 0 8px",
								fontSize: 22,
								letterSpacing: "-0.02em",
							}}
						>
							Scenarios
						</h2>
						<p className="lede">
							Select a scenario from the sidebar to view its details, revisions,
							and benchmark runs.
						</p>
					</div>
				)}

				{mode === "dashboard" && (
					<div style={{ padding: 20 }}>
						<h2>Dashboard</h2>
						<p className="lede">Mission Control — coming in M2.</p>
					</div>
				)}

				{mode === "infra" && (
					<div style={{ padding: 20 }}>
						<h2>Infra</h2>
						<p className="lede">AWS instances and images — coming in M2.</p>
					</div>
				)}

				{mode === "data" && <DataBrowser />}
			</main>

			{/* Debug Drawer */}
			<DebugDrawer open={debugOpen} onClose={() => setDebugOpen(false)} />
		</div>
	);
}

// ─── Scenario Sidebar ───────────────────────────────────────────────────────

function ScenarioSidebar({
	scenarios,
	loading,
	error,
	selectedId,
	onSelect,
	onRefresh,
}: {
	scenarios: ScenarioListItem[];
	loading: boolean;
	error: string | null;
	selectedId: string | null;
	onSelect: (id: string) => void;
	onRefresh: () => void;
}) {
	return (
		<div
			style={{
				minHeight: 0,
				display: "flex",
				flex: 1,
				flexDirection: "column",
				overflow: "hidden",
			}}
		>
			<div style={{ padding: "0 18px 8px", flexShrink: 0 }}>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
					}}
				>
					<p
						className="eyebrow"
						style={{ color: "var(--accent-text)", margin: 0 }}
					>
						Scenarios
					</p>
					<button
						className="subtle-action"
						onClick={onRefresh}
						style={{ fontSize: 11 }}
					>
						Refresh
					</button>
				</div>
			</div>

			<div className="sidebar-content" style={{ padding: "0 6px" }}>
				{loading && (
					<p
						style={{
							color: "var(--muted)",
							fontSize: 12,
							padding: "8px 12px",
						}}
					>
						Loading...
					</p>
				)}
				{error && (
					<p
						style={{
							color: "var(--danger)",
							fontSize: 12,
							padding: "8px 12px",
						}}
					>
						{error}
					</p>
				)}
				{!loading && !error && scenarios.length === 0 && (
					<p
						style={{
							color: "var(--muted)",
							fontSize: 12,
							padding: "8px 12px",
						}}
					>
						No scenarios yet.
					</p>
				)}
				<div className="rail-list">
					{scenarios.map((sc) => (
						<button
							key={sc.id}
							className={`rail-item ${selectedId === sc.id ? "active" : ""}`}
							onClick={() => onSelect(sc.id)}
						>
							<div className="rail-item-copy">
								<strong>{sc.title}</strong>
								<small>{sc.scenario_name}</small>
								<div
									style={{
										display: "flex",
										gap: 8,
										marginTop: 4,
									}}
								>
									<span
										style={{
											fontFamily: "var(--mono)",
											fontSize: 9,
											color:
												sc.lifecycle_status === "verified"
													? "var(--green)"
													: "var(--muted)",
										}}
									>
										{sc.lifecycle_status}
									</span>
									{sc.benchmark_run_count > 0 && (
										<span
											style={{
												fontFamily: "var(--mono)",
												fontSize: 9,
												color: "var(--text3)",
											}}
										>
											{sc.benchmark_run_count} run
											{sc.benchmark_run_count !== 1 ? "s" : ""}
										</span>
									)}
								</div>
							</div>
						</button>
					))}
				</div>
			</div>
		</div>
	);
}
