import { useCallback, useEffect, useState } from "react";
import Dashboard from "./components/Dashboard";
import { DataBrowser } from "./components/DataBrowser";
import InfraPage from "./components/InfraPage";
import { NewScenarioPage } from "./components/NewScenarioPage";
import ScenarioDetail from "./components/ScenarioDetail";
import { ScenariosGrid } from "./components/ScenariosGrid";
import DebugDrawer from "./debug/DebugDrawer";
import { useScenarios } from "./hooks/useScenarios";

type Mode = "dashboard" | "scenarios" | "infra" | "data" | "new-scenario";

export default function App() {
	const [mode, setMode] = useState<Mode>("scenarios");
	const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(
		null,
	);
	const [newScenarioSource, setNewScenarioSource] = useState<
		string | undefined
	>();
	const [debugOpen, setDebugOpen] = useState(false);
	const { scenarios } = useScenarios();

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
					<ScenarioDetail
						scenarioId={selectedScenarioId}
						onGenerateVariant={(sourceId: string) => {
							setNewScenarioSource(sourceId);
							setMode("new-scenario");
							setSelectedScenarioId(null);
						}}
					/>
				)}

				{mode === "scenarios" && !selectedScenarioId && (
					<ScenariosGrid
						onSelectScenario={(id) => setSelectedScenarioId(id)}
						onNewScenario={() => {
							setNewScenarioSource(undefined);
							setMode("new-scenario");
						}}
					/>
				)}

				{mode === "new-scenario" && (
					<NewScenarioPage
						sourceScenarioId={newScenarioSource}
						onDiscard={() => {
							setMode("scenarios");
							setNewScenarioSource(undefined);
						}}
						onSaved={(id: string) => {
							setMode("scenarios");
							setSelectedScenarioId(id);
							setNewScenarioSource(undefined);
						}}
					/>
				)}

				{mode === "dashboard" && (
					<Dashboard
						scenarios={scenarios}
						onModeChange={handleSelectScenario}
						onNewScenario={() => {
							setNewScenarioSource(undefined);
							setMode("new-scenario");
						}}
					/>
				)}

				{mode === "infra" && <InfraPage />}

				{mode === "data" && <DataBrowser />}
			</main>

			{/* Debug Drawer */}
			<DebugDrawer open={debugOpen} onClose={() => setDebugOpen(false)} />
		</div>
	);
}
