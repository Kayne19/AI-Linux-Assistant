import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { JudgeJobDetail, RunEventItem, SetupRunEventItem } from "../types";

type DebugTab = "events" | "execution" | "context" | "artifacts";

interface DebugDrawerProps {
	open: boolean;
	onClose: () => void;
	/** The evaluation run ID to pull events from (when viewing a run) */
	evaluationRunId?: string | null;
	/** The setup run ID to pull events from */
	setupRunId?: string | null;
	/** Scenario/revision JSON for the Context tab */
	scenarioJson?: unknown;
	/** Subject configuration for the Context tab */
	subjectConfig?: unknown;
	/** Run parameters for the Context tab */
	runParams?: unknown;
	/** Pre-loaded judge results for the Artifacts tab */
	judgeResults?: unknown;
}

export default function DebugDrawer({
	open,
	onClose,
	evaluationRunId,
	setupRunId,
	scenarioJson,
	subjectConfig,
	runParams,
	judgeResults,
}: DebugDrawerProps) {
	const [tab, setTab] = useState<DebugTab>("events");
	const [evalEvents, setEvalEvents] = useState<RunEventItem[]>([]);
	const [setupEvents, setSetupEvents] = useState<SetupRunEventItem[]>([]);
	const [loading, setLoading] = useState(false);

	useEffect(() => {
		if (!open) return;

		const loadEvents = async () => {
			setLoading(true);
			const allEval: RunEventItem[] = [];
			const allSetup: SetupRunEventItem[] = [];

			try {
				if (evaluationRunId) {
					const evts = await api.listEvaluationEvents(evaluationRunId);
					allEval.push(...evts);
				}
				if (setupRunId) {
					const evts = await api.listSetupRunEvents(setupRunId);
					allSetup.push(...evts);
				}
			} catch {
				// silently fail
			}

			setEvalEvents(allEval);
			setSetupEvents(allSetup);
			setLoading(false);
		};

		loadEvents();
	}, [open, evaluationRunId, setupRunId]);

	if (!open) return null;

	const tabs: { key: DebugTab; label: string }[] = [
		{ key: "events", label: "Events" },
		{ key: "execution", label: "Execution" },
		{ key: "context", label: "Context" },
		{ key: "artifacts", label: "Artifacts" },
	];

	return (
		<>
			{/* Backdrop */}
			<div
				onClick={onClose}
				style={{
					position: "fixed",
					inset: 0,
					background: "rgba(0, 0, 0, 0.35)",
					zIndex: 19,
				}}
			/>

			{/* Drawer */}
			<aside
				style={{
					position: "fixed",
					top: 0,
					right: 0,
					bottom: 0,
					width: "min(1080px, calc(100vw - 44px))",
					background: "var(--surface)",
					borderLeft: "1px solid var(--border-mid)",
					zIndex: 20,
					display: "flex",
					flexDirection: "column",
					boxShadow: "-8px 0 32px rgba(0, 0, 0, 0.35)",
				}}
			>
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
					<h2
						style={{
							margin: 0,
							fontSize: 15,
							fontWeight: 600,
							fontFamily: "var(--mono)",
							letterSpacing: "0.04em",
							color: "var(--accent)",
						}}
					>
						Debug
					</h2>
					<button className="ghost-button compact" onClick={onClose}>
						Close
					</button>
				</div>

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
								fontSize: 12,
								fontFamily: "var(--mono)",
								cursor: "pointer",
								borderRadius: 0,
								letterSpacing: "0.04em",
							}}
						>
							{t.label}
						</button>
					))}
				</div>

				{/* Content */}
				<div
					style={{
						flex: 1,
						overflowY: "auto",
						padding: 16,
						fontFamily: "var(--mono)",
					}}
				>
					{tab === "events" && (
						<EventsTab
							evalEvents={evalEvents}
							setupEvents={setupEvents}
							loading={loading}
						/>
					)}
					{tab === "execution" && (
						<ExecutionTab evalEvents={evalEvents} setupEvents={setupEvents} />
					)}
					{tab === "context" && (
						<ContextTab
							scenarioJson={scenarioJson}
							subjectConfig={subjectConfig}
							runParams={runParams}
						/>
					)}
					{tab === "artifacts" && <ArtifactsTab judgeResults={judgeResults} />}
				</div>
			</aside>
		</>
	);
}

// ─── Shared helpers ────────────────────────────────────────────────────────

/** Phase-friendly label for a setup round index. */
function roundPhaseLabel(ri: number): string {
	const labels: Record<number, string> = {
		0: "Planner Round",
		1: "Sabotage Round",
		2: "Verify Round",
	};
	return labels[ri] ?? `Round ${ri}`;
}

function CollapsibleSection({
	title,
	count,
	children,
	defaultOpen,
}: {
	title: string;
	count: number;
	children: React.ReactNode;
	defaultOpen?: boolean;
}) {
	const [open, setOpen] = useState(defaultOpen ?? false);

	return (
		<div
			style={{
				border: "1px solid var(--border)",
				borderRadius: 4,
				overflow: "hidden",
			}}
		>
			<button
				onClick={() => setOpen(!open)}
				style={{
					width: "100%",
					textAlign: "left",
					background: "var(--well)",
					border: "none",
					padding: "8px 12px",
					cursor: "pointer",
					display: "flex",
					alignItems: "center",
					gap: 8,
					color: "var(--text)",
					fontFamily: "var(--mono)",
					fontSize: 12,
				}}
			>
				<span
					style={{
						transform: open ? "rotate(90deg)" : "rotate(0deg)",
						transition: "transform 0.12s",
						fontSize: 10,
						color: "var(--text3)",
					}}
				>
					▶
				</span>
				<span style={{ flex: 1 }}>{title}</span>
				<span
					style={{
						fontSize: 10,
						color: "var(--accent)",
					}}
				>
					{count} event{count !== 1 ? "s" : ""}
				</span>
			</button>
			{open && <div style={{ padding: "4px 8px 8px" }}>{children}</div>}
		</div>
	);
}

// ─── Events Tab ─────────────────────────────────────────────────────────────

function EventsTab({
	evalEvents,
	setupEvents,
	loading,
}: {
	evalEvents: RunEventItem[];
	setupEvents: SetupRunEventItem[];
	loading: boolean;
}) {
	if (loading) {
		return (
			<p style={{ color: "var(--muted)", fontSize: 13 }}>Loading events...</p>
		);
	}

	const total = evalEvents.length + setupEvents.length;

	if (total === 0) {
		return (
			<div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
				<p style={{ color: "var(--muted)", fontSize: 13 }}>
					No events loaded. Open a run to see its event timeline.
				</p>
			</div>
		);
	}

	return (
		<div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
			{setupEvents.length > 0 && (
				<div>
					<p
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--accent)",
							textTransform: "uppercase",
							letterSpacing: "0.12em",
							margin: "0 0 8px",
						}}
					>
						Setup Events ({setupEvents.length})
					</p>
					<div
						style={{
							display: "flex",
							flexDirection: "column",
							gap: 4,
						}}
					>
						{setupEvents.map((ev) => (
							<EventRow
								key={ev.id}
								seq={`r${ev.round_index}.${ev.seq}`}
								actor={ev.actor_role}
								kind={ev.event_kind}
								payload={ev.payload}
								time={ev.created_at}
							/>
						))}
					</div>
				</div>
			)}

			{evalEvents.length > 0 && (
				<div>
					<p
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--accent)",
							textTransform: "uppercase",
							letterSpacing: "0.12em",
							margin: "0 0 8px",
						}}
					>
						Evaluation Events ({evalEvents.length})
					</p>
					<div
						style={{
							display: "flex",
							flexDirection: "column",
							gap: 4,
						}}
					>
						{evalEvents.map((ev) => (
							<EventRow
								key={ev.id}
								seq={String(ev.seq)}
								actor={ev.actor_role}
								kind={ev.event_kind}
								payload={ev.payload}
								time={ev.created_at}
							/>
						))}
					</div>
				</div>
			)}
		</div>
	);
}

function EventRow({
	seq,
	actor,
	kind,
	payload,
	time,
}: {
	seq: string;
	actor: string;
	kind: string;
	payload: Record<string, unknown> | null;
	time: string;
}) {
	const [expanded, setExpanded] = useState(false);

	return (
		<div>
			<button
				onClick={() => setExpanded(!expanded)}
				style={{
					width: "100%",
					textAlign: "left",
					background: "transparent",
					border: "none",
					borderRadius: 4,
					padding: "6px 8px",
					cursor: "pointer",
					display: "flex",
					alignItems: "center",
					gap: 8,
					color: "var(--text)",
				}}
			>
				<span
					style={{
						fontFamily: "var(--mono)",
						fontSize: 10,
						color: "var(--text3)",
						minWidth: 50,
					}}
				>
					#{seq}
				</span>
				<span
					style={{
						fontFamily: "var(--mono)",
						fontSize: 10,
						color: "var(--accent)",
						minWidth: 100,
					}}
				>
					{actor}
				</span>
				<span
					style={{
						fontFamily: "var(--mono)",
						fontSize: 10,
						color: "var(--muted)",
						flex: 1,
					}}
				>
					{kind}
				</span>
				<span
					style={{
						fontFamily: "var(--mono)",
						fontSize: 9,
						color: "var(--text3)",
					}}
				>
					{new Date(time).toLocaleTimeString()}
				</span>
				<span
					style={{
						fontFamily: "var(--mono)",
						fontSize: 10,
						color: "var(--text3)",
						transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
						transition: "transform 0.12s",
					}}
				>
					\u25B6
				</span>
			</button>

			{expanded && payload && (
				<pre
					style={{
						margin: "0 0 4px 58px",
						padding: "8px 10px",
						background: "var(--well)",
						border: "1px solid var(--border)",
						borderRadius: 4,
						fontSize: 11,
						fontFamily: "var(--mono)",
						color: "var(--muted)",
						whiteSpace: "pre-wrap",
						wordBreak: "break-word",
						maxHeight: 300,
						overflowY: "auto",
					}}
				>
					{JSON.stringify(payload, null, 2)}
				</pre>
			)}
		</div>
	);
}

// ─── Execution Tab ─────────────────────────────────────────────────────────

function ExecutionTab({
	evalEvents,
	setupEvents,
}: {
	evalEvents: RunEventItem[];
	setupEvents: SetupRunEventItem[];
}) {
	// Group setup events by round_index
	const setupByRound = new Map<number, SetupRunEventItem[]>();
	for (const ev of setupEvents) {
		const bucket = setupByRound.get(ev.round_index);
		if (bucket) {
			bucket.push(ev);
		} else {
			setupByRound.set(ev.round_index, [ev]);
		}
	}
	const sortedRounds = [...setupByRound.keys()].sort((a, b) => a - b);

	// Group eval events by event_kind
	const evalByKind = new Map<string, RunEventItem[]>();
	for (const ev of evalEvents) {
		const bucket = evalByKind.get(ev.event_kind);
		if (bucket) {
			bucket.push(ev);
		} else {
			evalByKind.set(ev.event_kind, [ev]);
		}
	}
	const sortedKinds = [...evalByKind.keys()].sort();

	const total = setupEvents.length + evalEvents.length;
	if (total === 0) {
		return (
			<p style={{ color: "var(--muted)", fontSize: 13 }}>
				No events loaded. Open a run to see execution phases.
			</p>
		);
	}

	return (
		<div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
			{/* Setup phases grouped by round */}
			{sortedRounds.length > 0 && (
				<div>
					<p
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--accent)",
							textTransform: "uppercase",
							letterSpacing: "0.12em",
							margin: "0 0 6px",
						}}
					>
						Setup Phases by Round
					</p>
					<div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
						{sortedRounds.map((ri) => {
							const events = setupByRound.get(ri)!;
							return (
								<CollapsibleSection
									key={ri}
									title={roundPhaseLabel(ri)}
									count={events.length}
								>
									{events.map((ev) => (
										<EventRow
											key={ev.id}
											seq={`r${ev.round_index}.${ev.seq}`}
											actor={ev.actor_role}
											kind={ev.event_kind}
											payload={ev.payload}
											time={ev.created_at}
										/>
									))}
								</CollapsibleSection>
							);
						})}
					</div>
				</div>
			)}

			{/* Eval phases grouped by event_kind */}
			{sortedKinds.length > 0 && (
				<div>
					<p
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--accent)",
							textTransform: "uppercase",
							letterSpacing: "0.12em",
							margin: "0 0 6px",
						}}
					>
						Evaluation Phases by Event Kind
					</p>
					<div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
						{sortedKinds.map((kind) => {
							const events = evalByKind.get(kind)!;
							return (
								<CollapsibleSection
									key={kind}
									title={kind}
									count={events.length}
								>
									{events.map((ev) => (
										<EventRow
											key={ev.id}
											seq={String(ev.seq)}
											actor={ev.actor_role}
											kind={ev.event_kind}
											payload={ev.payload}
											time={ev.created_at}
										/>
									))}
								</CollapsibleSection>
							);
						})}
					</div>
				</div>
			)}
		</div>
	);
}

// ─── Context Tab ───────────────────────────────────────────────────────────

function ContextTab({
	scenarioJson,
	subjectConfig,
	runParams,
}: {
	scenarioJson?: unknown;
	subjectConfig?: unknown;
	runParams?: unknown;
}) {
	const hasAny =
		scenarioJson != null || subjectConfig != null || runParams != null;

	if (!hasAny) {
		return (
			<p style={{ color: "var(--muted)", fontSize: 13 }}>No context loaded.</p>
		);
	}

	return (
		<div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
			{scenarioJson != null && (
				<div>
					<p
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--accent)",
							textTransform: "uppercase",
							letterSpacing: "0.12em",
							margin: "0 0 6px",
						}}
					>
						Scenario / Revision Config
					</p>
					<JsonBlock data={scenarioJson} />
				</div>
			)}
			{subjectConfig != null && (
				<div>
					<p
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--accent)",
							textTransform: "uppercase",
							letterSpacing: "0.12em",
							margin: "0 0 6px",
						}}
					>
						Subject Config
					</p>
					<JsonBlock data={subjectConfig} />
				</div>
			)}
			{runParams != null && (
				<div>
					<p
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--accent)",
							textTransform: "uppercase",
							letterSpacing: "0.12em",
							margin: "0 0 6px",
						}}
					>
						Run Params
					</p>
					<JsonBlock data={runParams} />
				</div>
			)}
		</div>
	);
}

function JsonBlock({ data }: { data: unknown }) {
	return (
		<pre
			style={{
				margin: 0,
				padding: "10px 12px",
				background: "var(--well)",
				border: "1px solid var(--border)",
				borderRadius: 4,
				fontSize: 11,
				fontFamily: "var(--mono)",
				color: "var(--muted)",
				whiteSpace: "pre-wrap",
				wordBreak: "break-word",
				maxHeight: 500,
				overflowY: "auto",
			}}
		>
			{JSON.stringify(data, null, 2)}
		</pre>
	);
}

// ─── Artifacts Tab ─────────────────────────────────────────────────────────

function ArtifactsTab({ judgeResults }: { judgeResults?: unknown }) {
	const [judgeJobId, setJudgeJobId] = useState("");
	const [fetchedJudge, setFetchedJudge] = useState<JudgeJobDetail | null>(null);
	const [fetchLoading, setFetchLoading] = useState(false);
	const [fetchError, setFetchError] = useState<string | null>(null);

	const loadJudgeJob = useCallback(async () => {
		const id = judgeJobId.trim();
		if (!id) return;
		setFetchLoading(true);
		setFetchError(null);
		try {
			const detail = await api.getJudgeJob(id);
			setFetchedJudge(detail);
		} catch (err: unknown) {
			setFetchedJudge(null);
			setFetchError(
				err instanceof Error ? err.message : "Failed to load judge job",
			);
		} finally {
			setFetchLoading(false);
		}
	}, [judgeJobId]);

	const hasResult =
		judgeResults != null || fetchedJudge != null || fetchError != null;

	return (
		<div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
			{/* Judge job lookup */}
			<div>
				<p
					style={{
						fontFamily: "var(--mono)",
						fontSize: 10,
						color: "var(--accent)",
						textTransform: "uppercase",
						letterSpacing: "0.12em",
						margin: "0 0 6px",
					}}
				>
					Judge Job
				</p>
				<div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
					<input
						type="text"
						value={judgeJobId}
						onChange={(e) => setJudgeJobId(e.target.value)}
						placeholder="Judge job ID"
						onKeyDown={(e) => {
							if (e.key === "Enter") void loadJudgeJob();
						}}
						style={{
							flex: 1,
							padding: "6px 10px",
							background: "var(--well)",
							border: "1px solid var(--border)",
							borderRadius: 4,
							color: "var(--text)",
							fontFamily: "var(--mono)",
							fontSize: 12,
						}}
					/>
					<button
						className="ghost-button compact"
						onClick={() => void loadJudgeJob()}
						disabled={fetchLoading || !judgeJobId.trim()}
					>
						{fetchLoading ? "Loading..." : "Load"}
					</button>
				</div>

				{fetchError && (
					<p style={{ color: "var(--danger-text, #f87171)", fontSize: 12 }}>
						{fetchError}
					</p>
				)}

				{fetchedJudge && (
					<div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
						<p
							style={{
								fontFamily: "var(--mono)",
								fontSize: 10,
								color: "var(--muted)",
								margin: 0,
							}}
						>
							Status: {fetchedJudge.status} | Adapter:{" "}
							{fetchedJudge.judge_adapter_type} | Items:{" "}
							{fetchedJudge.judge_items.length}
						</p>
						<JsonBlock data={fetchedJudge} />
					</div>
				)}
			</div>

			{/* Pre-loaded judge results (prop) */}
			{judgeResults != null && (
				<div>
					<p
						style={{
							fontFamily: "var(--mono)",
							fontSize: 10,
							color: "var(--accent)",
							textTransform: "uppercase",
							letterSpacing: "0.12em",
							margin: "0 0 6px",
						}}
					>
						Judge Results
					</p>
					<JsonBlock data={judgeResults} />
				</div>
			)}

			{/* Empty state when nothing loaded */}
			{!hasResult && (
				<p style={{ color: "var(--muted)", fontSize: 13 }}>
					Enter a judge job ID to view scores.
				</p>
			)}
		</div>
	);
}
