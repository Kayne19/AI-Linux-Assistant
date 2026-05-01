import { useEffect, useState } from "react";
import { api } from "../api";
import type { RunEventItem, SetupRunEventItem } from "../types";

type DebugTab = "events" | "execution" | "context" | "artifacts";

interface DebugDrawerProps {
	open: boolean;
	onClose: () => void;
	/** The evaluation run ID to pull events from (when viewing a run) */
	evaluationRunId?: string | null;
	/** The setup run ID to pull events from */
	setupRunId?: string | null;
}

export default function DebugDrawer({
	open,
	onClose,
	evaluationRunId,
	setupRunId,
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
						<p style={{ color: "var(--muted)", fontSize: 13 }}>
							Execution view — events grouped by phase (M2+)
						</p>
					)}
					{tab === "context" && (
						<p style={{ color: "var(--muted)", fontSize: 13 }}>
							Context view — scenario JSON, subject config, run params (M2+)
						</p>
					)}
					{tab === "artifacts" && (
						<p style={{ color: "var(--muted)", fontSize: 13 }}>
							Artifacts view — judge scores, exports (M2+)
						</p>
					)}
				</div>
			</aside>
		</>
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
