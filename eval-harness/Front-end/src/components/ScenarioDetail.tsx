import { useEffect, useState } from "react";
import { api } from "../api";
import { useEvents } from "../hooks/useEvents";
import { useScenarioActions } from "../hooks/useScenarioActions";
import type {
	BenchmarkRunItem,
	EvaluationRunItem,
	RunEventItem,
	ScenarioDetail as ScenarioDetailType,
	SetupRunItem,
	SubjectItem,
} from "../types";
import { JsonEditorTab } from "./JsonEditorTab";
import { OverviewSummary } from "./OverviewSummary";
import { RunsMasterDetail } from "./RunsMasterDetail";

// ─── Helpers ────────────────────────────────────────────────────────────────

function statusColor(status: string): string {
	switch (status) {
		case "running":
		case "pending":
			return "var(--accent)";
		case "completed":
		case "verified":
			return "var(--green)";
		case "failed":
		case "cancelled":
		case "interrupted":
			return "var(--danger)";
		default:
			return "var(--muted)";
	}
}

function formatTime(iso: string | null): string {
	if (!iso) return "—";
	return new Date(iso).toLocaleString();
}

function formatDuration(start: string | null, end: string | null): string {
	if (!start) return "—";
	const s = new Date(start).getTime();
	const e = end ? new Date(end).getTime() : Date.now();
	const secs = Math.max(0, Math.round((e - s) / 1000));
	if (secs < 60) return `${secs}s`;
	const mins = Math.floor(secs / 60);
	const remain = secs % 60;
	return `${mins}m ${remain}s`;
}

// ─── Phase rail ─────────────────────────────────────────────────────────────

function PhaseRail({
	hasSetup,
	setupStatus,
	hasBenchmark,
	benchmarkStatus,
}: {
	hasSetup: boolean;
	setupStatus: string | null;
	hasBenchmark: boolean;
	benchmarkStatus: string | null;
}) {
	const phases = [
		{ label: "Setup", done: hasSetup, status: setupStatus },
		{ label: "Benchmark", done: hasBenchmark, status: benchmarkStatus },
		{ label: "Judge", done: false, status: null },
	];

	return (
		<div
			style={{ display: "flex", gap: 8, padding: "8px 0", flexWrap: "wrap" }}
		>
			{phases.map((p, i) => {
				const dot =
					p.done && p.status !== "running"
						? "\u2713"
						: p.status === "running"
							? "\u25CF"
							: "\u25CB";
				const color =
					p.done && p.status !== "running"
						? "var(--green)"
						: p.status === "running"
							? "var(--accent)"
							: "var(--border-mid)";
				return (
					<span
						key={p.label}
						style={{
							fontFamily: "var(--mono)",
							fontSize: 11,
							color,
							display: "inline-flex",
							alignItems: "center",
							gap: 4,
						}}
					>
						<span>{dot}</span>
						{p.label}
						{i < phases.length - 1 && (
							<span style={{ color: "var(--border-mid)", margin: "0 2px" }}>
								\u2192
							</span>
						)}
					</span>
				);
			})}
		</div>
	);
}

// ─── Transcript event bubbles ───────────────────────────────────────────────

function EventBubble({ event }: { event: RunEventItem }) {
	const isUser =
		event.actor_role === "user_proxy" || event.actor_role === "user";
	const isSystem =
		event.actor_role === "system" ||
		event.event_kind === "repair_check" ||
		event.event_kind === "repair_result";

	const bubbleStyle: React.CSSProperties = isSystem
		? {
				background: "rgba(74, 222, 128, 0.06)",
				border: "1px solid var(--green-dim)",
				borderRadius: 6,
				padding: "8px 12px",
				fontSize: 12,
				fontFamily: "var(--mono)",
				color: "var(--muted)",
				marginBottom: 6,
			}
		: {
				background: isUser ? "var(--user)" : "var(--assistant)",
				borderRadius: 12,
				padding: "12px 14px",
				maxWidth: "72%",
				alignSelf: isUser ? "flex-end" : "flex-start",
				borderBottomRightRadius: isUser ? 6 : 12,
				borderBottomLeftRadius: isUser ? 12 : 6,
				marginBottom: 8,
			};

	const containerStyle: React.CSSProperties = {
		display: "flex",
		flexDirection: "column",
		alignItems: isUser ? "flex-end" : "flex-start",
	};

	const payloadText =
		event.payload && typeof event.payload.text === "string"
			? event.payload.text
			: event.payload && typeof event.payload.content === "string"
				? event.payload.content
				: event.payload
					? JSON.stringify(event.payload, null, 2)
					: null;

	return (
		<div style={containerStyle}>
			<div
				style={{
					display: "flex",
					alignItems: "center",
					gap: 6,
					marginBottom: 2,
				}}
			>
				<span
					style={{
						fontFamily: "var(--mono)",
						fontSize: 10,
						color: "var(--muted)",
						textTransform: "uppercase",
						letterSpacing: "0.08em",
					}}
				>
					{event.actor_role}
				</span>
				<span
					style={{
						fontFamily: "var(--mono)",
						fontSize: 9,
						color: "var(--text3)",
					}}
				>
					{event.event_kind}
				</span>
			</div>
			<div style={bubbleStyle}>
				{payloadText ? (
					<pre
						style={{
							margin: 0,
							fontFamily: isSystem ? "var(--mono)" : "inherit",
							fontSize: isSystem ? 11 : 13,
							whiteSpace: "pre-wrap",
							wordBreak: "break-word",
							color: isSystem ? "var(--muted)" : "var(--text)",
							lineHeight: 1.6,
						}}
					>
						{payloadText}
					</pre>
				) : (
					<span style={{ color: "var(--text3)", fontSize: 12 }}>
						{event.event_kind}
					</span>
				)}
			</div>
		</div>
	);
}

// ─── RunViewer ───────────────────────────────────────────────────────────────

function RunViewer({
	evaluationRunId,
	benchmarkRunId,
	onClose,
}: {
	evaluationRunId: string;
	benchmarkRunId: string;
	onClose: () => void;
}) {
	const [evalRun, setEvalRun] = useState<EvaluationRunItem | null>(null);
	const [benchmarkRun, setBenchmarkRun] = useState<BenchmarkRunItem | null>(
		null,
	);
	const [setupRun, setSetupRun] = useState<SetupRunItem | null>(null);
	const active = evalRun?.status === "running" || evalRun?.status === "pending";
	const { events, loading } = useEvents({
		kind: "evaluation",
		runId: evaluationRunId,
		active,
	});

	useEffect(() => {
		api
			.getEvaluation(evaluationRunId)
			.then(setEvalRun)
			.catch(() => {});
		api
			.getBenchmarkRun(benchmarkRunId)
			.then(setBenchmarkRun)
			.catch(() => {});
	}, [evaluationRunId, benchmarkRunId]);

	useEffect(() => {
		if (benchmarkRun?.verified_setup_run_id) {
			api
				.getSetupRun(benchmarkRun.verified_setup_run_id)
				.then(setSetupRun)
				.catch(() => {});
		}
	}, [benchmarkRun?.verified_setup_run_id]);

	return (
		<div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
			{/* Header */}
			<div
				style={{
					padding: "14px 16px",
					borderBottom: "1px solid var(--border)",
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					flexShrink: 0,
				}}
			>
				<div style={{ display: "flex", alignItems: "center", gap: 14 }}>
					<h2
						style={{
							margin: 0,
							fontSize: 15,
							fontWeight: 600,
							letterSpacing: "-0.01em",
						}}
					>
						Evaluation Run
					</h2>
					{evalRun && (
						<>
							<span
								style={{
									fontFamily: "var(--mono)",
									fontSize: 10,
									color: "var(--text3)",
								}}
							>
								{evalRun.id.slice(0, 8)}
							</span>
							<span
								style={{
									fontFamily: "var(--mono)",
									fontSize: 10,
									display: "inline-flex",
									alignItems: "center",
									gap: 4,
									color: statusColor(evalRun.status),
								}}
							>
								<span
									style={{
										width: 6,
										height: 6,
										borderRadius: "50%",
										background: statusColor(evalRun.status),
										display: "inline-block",
									}}
								/>
								{evalRun.status}
							</span>
							<span
								style={{
									fontFamily: "var(--mono)",
									fontSize: 10,
									color: "var(--text3)",
								}}
							>
								{formatDuration(evalRun.started_at, evalRun.finished_at)}
							</span>
						</>
					)}
				</div>
				<button className="ghost-button compact" onClick={onClose}>
					Close
				</button>
			</div>

			{/* Phase rail */}
			<div
				style={{
					padding: "6px 16px",
					borderBottom: "1px solid var(--border)",
					flexShrink: 0,
				}}
			>
				<PhaseRail
					hasSetup={!!setupRun}
					setupStatus={setupRun?.status ?? null}
					hasBenchmark={!!benchmarkRun}
					benchmarkStatus={benchmarkRun?.status ?? null}
				/>
			</div>

			{/* Transcript body */}
			<div
				style={{
					flex: 1,
					overflowY: "auto",
					padding: "16px",
					display: "flex",
					flexDirection: "column",
					gap: 2,
				}}
			>
				{loading && events.length === 0 && (
					<p style={{ color: "var(--muted)", fontSize: 13, padding: 20 }}>
						Loading events...
					</p>
				)}
				{!loading && events.length === 0 && (
					<p style={{ color: "var(--muted)", fontSize: 13, padding: 20 }}>
						No events yet.
					</p>
				)}
				{events.map((ev) => (
					<EventBubble key={ev.id} event={ev as RunEventItem} />
				))}
				{active && (
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: 8,
							padding: "8px 0",
						}}
					>
						<span className="status-dot" />
						<span
							style={{
								fontFamily: "var(--mono)",
								fontSize: 11,
								color: "var(--muted)",
							}}
						>
							Live — polling every 1s
						</span>
					</div>
				)}
			</div>
		</div>
	);
}

// ─── Scenario Detail ────────────────────────────────────────────────────────

type Tab = "overview" | "revisions" | "runs" | "edit-json";

export default function ScenarioDetail({
	scenarioId,
	onGenerateVariant,
}: {
	scenarioId: string;
	onGenerateVariant?: (sourceId: string) => void;
}) {
	const [scenario, setScenario] = useState<ScenarioDetailType | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [tab, setTab] = useState<Tab>("overview");
	const [selectedEvalId, setSelectedEvalId] = useState<string | null>(null);
	const [selectedBenchId, setSelectedBenchId] = useState<string | null>(null);
	// M3 Control state
	const {
		dispatching,
		lastResult: actionResult,
		error: actionError,
		verify,
		benchmark: dispatchBenchmark,
		runAll,
		triggerJudge,
		cancelRun,
		clear: clearAction,
	} = useScenarioActions(scenarioId);
	const [activeDrawer, setActiveDrawer] = useState<string | null>(null);
	const [drawerSetupRunId, setDrawerSetupRunId] = useState<string>("");
	const [drawerSubjectIds, setDrawerSubjectIds] = useState<string[]>([]);
	const [drawerJudgeMode, setDrawerJudgeMode] = useState<string>("absolute");
	const [drawerAnchorSubject, setDrawerAnchorSubject] = useState<string>("");
	const [subjects, setSubjects] = useState<SubjectItem[]>([]);
	const [subjectsLoaded, setSubjectsLoaded] = useState(false);
	const [refreshCounter, setRefreshCounter] = useState(0);

	// Load subjects for the Benchmark drawer
	const loadSubjects = () => {
		if (subjectsLoaded) return;
		api
			.listSubjects()
			.then((s) => {
				setSubjects(s.filter((sub) => sub.is_active));
				setSubjectsLoaded(true);
			})
			.catch(() => {});
	};

	const openDrawer = (key: string) => {
		clearAction();
		setActiveDrawer(key);
		if (key === "benchmark" || key === "run-all") loadSubjects();
	};

	const closeDrawer = () => {
		setActiveDrawer(null);
	};

	useEffect(() => {
		setLoading(true);
		setError(null);
		setSelectedEvalId(null);
		setSelectedBenchId(null);
		api
			.getScenario(scenarioId)
			.then(setScenario)
			.catch((e) => setError(e.message || "Failed to load scenario"))
			.finally(() => setLoading(false));
	}, [scenarioId, refreshCounter]);

	if (loading) {
		return (
			<p
				style={{
					color: "var(--muted)",
					padding: 20,
					fontFamily: "var(--mono)",
					fontSize: 13,
				}}
			>
				Loading scenario...
			</p>
		);
	}

	if (error || !scenario) {
		return (
			<p style={{ color: "var(--danger)", padding: 20, fontSize: 13 }}>
				{error || "Scenario not found"}
			</p>
		);
	}

	// Run viewer takes over
	if (selectedEvalId && selectedBenchId) {
		return (
			<RunViewer
				evaluationRunId={selectedEvalId}
				benchmarkRunId={selectedBenchId}
				onClose={() => {
					setSelectedEvalId(null);
					setSelectedBenchId(null);
				}}
			/>
		);
	}

	const tabs: { key: Tab; label: string }[] = [
		{ key: "overview", label: "Overview" },
		{ key: "revisions", label: "Revisions" },
		{ key: "runs", label: "Runs" },
		{ key: "edit-json", label: "Edit JSON" },
	];

	return (
		<div
			style={{
				display: "flex",
				flexDirection: "column",
				height: "100%",
				overflowY: "auto",
			}}
		>
			{/* Header */}
			<div
				style={{
					padding: "14px 16px",
					borderBottom: "1px solid var(--border)",
					flexShrink: 0,
				}}
			>
				<h2
					style={{
						margin: "0 0 4px",
						fontSize: 18,
						fontWeight: 600,
						letterSpacing: "-0.02em",
					}}
				>
					{scenario.title}
				</h2>
				<div
					style={{
						display: "flex",
						gap: 14,
						alignItems: "center",
						flexWrap: "wrap",
					}}
				>
					<span
						style={{
							fontFamily: "var(--mono)",
							fontSize: 11,
							color: "var(--accent)",
						}}
					>
						{scenario.scenario_name}
					</span>
					<span
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--muted)",
							display: "inline-flex",
							alignItems: "center",
							gap: 4,
						}}
					>
						<span
							style={{
								width: 6,
								height: 6,
								borderRadius: "50%",
								background:
									scenario.lifecycle_status === "verified"
										? "var(--green)"
										: "var(--muted)",
								display: "inline-block",
							}}
						/>
						{scenario.lifecycle_status}
					</span>
					<span
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--text3)",
						}}
					>
						{scenario.benchmark_run_count} runs
					</span>
					{scenario.last_verified_at && (
						<span
							style={{
								fontFamily: "var(--mono)",
								fontSize: 10,
								color: "var(--text3)",
							}}
						>
							Verified {formatTime(scenario.last_verified_at)}
						</span>
					)}
				</div>

				{/* M3 Action buttons */}
				<div
					style={{
						display: "flex",
						gap: 6,
						marginTop: 10,
						flexWrap: "wrap",
						alignItems: "center",
					}}
				>
					<button
						className="ghost-button compact"
						disabled={!!dispatching || scenario.revisions.length === 0}
						onClick={() => {
							clearAction();
							verify({}).then(() => {
								closeDrawer();
							});
						}}
						style={{ fontSize: 12, padding: "4px 10px" }}
					>
						{dispatching === "verify" ? "Verifying..." : "Verify"}
					</button>
					<button
						className="ghost-button compact"
						disabled={!!dispatching || !scenario.current_verified_revision_id}
						onClick={() => openDrawer("benchmark")}
						style={{ fontSize: 12, padding: "4px 10px" }}
					>
						Benchmark
					</button>
					<button
						className="ghost-button compact"
						disabled={!!dispatching}
						onClick={() => openDrawer("judge")}
						style={{ fontSize: 12, padding: "4px 10px" }}
					>
						Judge
					</button>
					<button
						className="ghost-button compact"
						disabled={!!dispatching || scenario.revisions.length === 0}
						onClick={() => openDrawer("run-all")}
						style={{ fontSize: 12, padding: "4px 10px" }}
					>
						{dispatching === "run-all" ? "Running..." : "Run all"}
					</button>
					{dispatching && (
						<button
							className="cancel-run-btn"
							onClick={() => {
								cancelRun(scenarioId, "benchmark");
							}}
						>
							Cancel
						</button>
					)}
				</div>

				{/* Action result feedback */}
				{actionResult && (
					<div
						style={{
							marginTop: 6,
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--green)",
						}}
					>
						✓ dispatched — ok
					</div>
				)}
				{actionError && (
					<div
						style={{
							marginTop: 6,
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--danger)",
						}}
					>
						{actionError}
					</div>
				)}
			</div>

			{/* M3 Option drawers */}
			{activeDrawer && (
				<div
					style={{
						padding: "10px 16px",
						borderBottom: "1px solid var(--border)",
						background: "var(--surface)",
						flexShrink: 0,
					}}
				>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
							marginBottom: 8,
						}}
					>
						<span
							style={{
								fontSize: 12,
								fontWeight: 500,
								color: "var(--accent)",
							}}
						>
							{activeDrawer === "benchmark"
								? "Benchmark Options"
								: activeDrawer === "judge"
									? "Judge Options"
									: "Run All Options"}
						</span>
						<button
							className="ghost-button compact"
							onClick={closeDrawer}
							style={{ fontSize: 11, padding: "2px 8px" }}
						>
							Close
						</button>
					</div>

					{/* Benchmark drawer */}
					{activeDrawer === "benchmark" && (
						<div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
							<label style={{ fontSize: 11, color: "var(--muted)" }}>
								Setup Run ID
								<input
									value={drawerSetupRunId}
									onChange={(e) => setDrawerSetupRunId(e.target.value)}
									placeholder="e.g. setup-uuid"
									style={{ marginTop: 4, fontSize: 12 }}
								/>
							</label>
							<label style={{ fontSize: 11, color: "var(--muted)" }}>
								Subjects (check to include)
							</label>
							{subjects.length === 0 && (
								<span style={{ fontSize: 11, color: "var(--text3)" }}>
									Loading subjects...
								</span>
							)}
							<div
								style={{
									display: "flex",
									flexDirection: "column",
									gap: 2,
									maxHeight: 140,
									overflowY: "auto",
								}}
							>
								{subjects.map((s) => (
									<label
										key={s.id}
										style={{
											display: "flex",
											alignItems: "center",
											gap: 6,
											fontSize: 11,
											color: "var(--text)",
											cursor: "pointer",
											padding: "2px 0",
										}}
									>
										<input
											type="checkbox"
											checked={drawerSubjectIds.includes(s.id)}
											onChange={() => {
												setDrawerSubjectIds((prev) =>
													prev.includes(s.id)
														? prev.filter((id) => id !== s.id)
														: [...prev, s.id],
												);
											}}
										/>
										{s.display_name}{" "}
										<span style={{ color: "var(--text3)" }}>
											({s.subject_name})
										</span>
									</label>
								))}
							</div>
							<button
								disabled={!drawerSetupRunId || dispatching !== null}
								onClick={() => {
									dispatchBenchmark({
										setup_run_id: drawerSetupRunId,
										subject_ids:
											drawerSubjectIds.length > 0 ? drawerSubjectIds : null,
									}).then(() => closeDrawer());
								}}
								style={{ fontSize: 12, padding: "6px 12px" }}
							>
								{dispatching === "benchmark"
									? "Starting..."
									: "Start Benchmark"}
							</button>
						</div>
					)}

					{/* Judge drawer */}
					{activeDrawer === "judge" && (
						<div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
							<label style={{ fontSize: 11, color: "var(--muted)" }}>
								Benchmark Run ID
								<input
									value={drawerSetupRunId}
									onChange={(e) => setDrawerSetupRunId(e.target.value)}
									placeholder="Benchmark run UUID"
									style={{ marginTop: 4, fontSize: 12 }}
								/>
							</label>
							<label style={{ fontSize: 11, color: "var(--muted)" }}>
								Mode
								<select
									value={drawerJudgeMode}
									onChange={(e) => setDrawerJudgeMode(e.target.value)}
									style={{
										marginTop: 4,
										fontSize: 12,
										width: "100%",
										padding: "4px 8px",
										background: "var(--surface)",
										color: "var(--text)",
										border: "1px solid var(--border)",
										borderRadius: 3,
									}}
								>
									<option value="absolute">Absolute</option>
									<option value="pairwise">Pairwise</option>
								</select>
							</label>
							{drawerJudgeMode === "pairwise" && (
								<label style={{ fontSize: 11, color: "var(--muted)" }}>
									Anchor Subject
									<input
										value={drawerAnchorSubject}
										onChange={(e) => setDrawerAnchorSubject(e.target.value)}
										placeholder="Anchor subject ID"
										style={{ marginTop: 4, fontSize: 12 }}
									/>
								</label>
							)}
							<button
								disabled={!drawerSetupRunId || dispatching !== null}
								onClick={() => {
									triggerJudge(drawerSetupRunId, {
										mode: drawerJudgeMode,
										anchor_subject: drawerAnchorSubject || undefined,
									}).then(() => closeDrawer());
								}}
								style={{ fontSize: 12, padding: "6px 12px" }}
							>
								{dispatching === "judge" ? "Starting..." : "Start Judge"}
							</button>
						</div>
					)}

					{/* Run-all drawer */}
					{activeDrawer === "run-all" && (
						<div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
							<label style={{ fontSize: 11, color: "var(--muted)" }}>
								Subjects (check to include)
							</label>
							{subjects.length === 0 && (
								<span style={{ fontSize: 11, color: "var(--text3)" }}>
									Loading subjects...
								</span>
							)}
							<div
								style={{
									display: "flex",
									flexDirection: "column",
									gap: 2,
									maxHeight: 140,
									overflowY: "auto",
								}}
							>
								{subjects.map((s) => (
									<label
										key={s.id}
										style={{
											display: "flex",
											alignItems: "center",
											gap: 6,
											fontSize: 11,
											color: "var(--text)",
											cursor: "pointer",
											padding: "2px 0",
										}}
									>
										<input
											type="checkbox"
											checked={drawerSubjectIds.includes(s.id)}
											onChange={() => {
												setDrawerSubjectIds((prev) =>
													prev.includes(s.id)
														? prev.filter((id) => id !== s.id)
														: [...prev, s.id],
												);
											}}
										/>
										{s.display_name}{" "}
										<span style={{ color: "var(--text3)" }}>
											({s.subject_name})
										</span>
									</label>
								))}
							</div>
							<label style={{ fontSize: 11, color: "var(--muted)" }}>
								Judge Mode
								<select
									value={drawerJudgeMode}
									onChange={(e) => setDrawerJudgeMode(e.target.value)}
									style={{
										marginTop: 4,
										fontSize: 12,
										width: "100%",
										padding: "4px 8px",
										background: "var(--surface)",
										color: "var(--text)",
										border: "1px solid var(--border)",
										borderRadius: 3,
									}}
								>
									<option value="absolute">Absolute</option>
									<option value="pairwise">Pairwise</option>
								</select>
							</label>
							<button
								disabled={dispatching !== null}
								onClick={() => {
									runAll({
										revision_id:
											scenario.revisions.length > 0
												? scenario.revisions[0].id
												: undefined,
										subject_ids:
											drawerSubjectIds.length > 0 ? drawerSubjectIds : null,
										judge_mode: drawerJudgeMode,
										judge_anchor_subject: drawerAnchorSubject || undefined,
									}).then(() => closeDrawer());
								}}
								style={{ fontSize: 12, padding: "6px 12px" }}
							>
								{dispatching === "run-all" ? "Starting..." : "Run All"}
							</button>
						</div>
					)}
				</div>
			)}

			{/* Tabs */}
			<div
				style={{
					display: "flex",
					gap: 0,
					borderBottom: "1px solid var(--border)",
					flexShrink: 0,
				}}
			>
				{tabs.map((t) => (
					<button
						key={t.key}
						onClick={() => setTab(t.key)}
						style={{
							background: "none",
							border: "none",
							borderBottom:
								tab === t.key
									? "2px solid var(--accent)"
									: "2px solid transparent",
							color: tab === t.key ? "var(--text)" : "var(--muted)",
							padding: "10px 16px",
							fontSize: 13,
							fontFamily: "var(--sans)",
							cursor: "pointer",
							borderRadius: 0,
						}}
					>
						{t.label}
					</button>
				))}
			</div>

			{/* Tab content */}
			<div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
				{tab === "overview" && (
					<OverviewSummary
						scenario={scenario}
						onRunBenchmark={() => openDrawer("benchmark")}
						onEditJson={() => setTab("edit-json")}
						onGenerateVariant={() => onGenerateVariant?.(scenarioId)}
					/>
				)}

				{tab === "revisions" && (
					<div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
						{scenario.revisions.length === 0 && (
							<p style={{ color: "var(--muted)", fontSize: 13 }}>
								No revisions yet.
							</p>
						)}
						{scenario.revisions.map((rev) => (
							<div
								key={rev.id}
								style={{
									border: "1px solid var(--border)",
									borderRadius: 6,
									padding: "10px 12px",
									background:
										rev.id === scenario.current_verified_revision_id
											? "var(--green-dim)"
											: "transparent",
								}}
							>
								<div
									style={{
										display: "flex",
										justifyContent: "space-between",
										alignItems: "center",
										marginBottom: 4,
									}}
								>
									<span
										style={{
											fontFamily: "var(--mono)",
											fontSize: 12,
											fontWeight: 500,
											color: "var(--text)",
										}}
									>
										Revision {rev.revision_number}
									</span>
									{rev.id === scenario.current_verified_revision_id && (
										<span
											style={{
												fontFamily: "var(--mono)",
												fontSize: 10,
												color: "var(--green)",
											}}
										>
											\u2713 verified
										</span>
									)}
								</div>
								<p
									style={{
										margin: 0,
										fontSize: 12,
										color: "var(--muted)",
										lineHeight: 1.5,
									}}
								>
									{rev.summary}
								</p>
								<div
									style={{
										marginTop: 6,
										fontFamily: "var(--mono)",
										fontSize: 10,
										color: "var(--text3)",
									}}
								>
									Image: {rev.target_image} &middot;{" "}
									{formatTime(rev.created_at)}
								</div>
							</div>
						))}
					</div>
				)}

				{tab === "runs" && (
					<RunsMasterDetail
						scenarioId={scenarioId}
						onViewRun={(evalId, benchId) => {
							setSelectedEvalId(evalId);
							setSelectedBenchId(benchId);
						}}
					/>
				)}

				{tab === "edit-json" && (
					<JsonEditorTab
						scenarioId={scenarioId}
						initialJson={scenario}
						onRevisionSaved={() => setRefreshCounter((c) => c + 1)}
					/>
				)}
			</div>
		</div>
	);
}
