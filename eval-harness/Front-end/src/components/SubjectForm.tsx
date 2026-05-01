import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type {
	SubjectCreateRequest,
	SubjectItem,
	SubjectPatchRequest,
} from "../types";

const DEFAULT_ADAPTER_TYPES = [
	"ai_linux_assistant_http",
	"openai_chatgpt",
] as const;

const EMPTY_CREATE: SubjectCreateRequest = {
	subject_name: "",
	adapter_type: DEFAULT_ADAPTER_TYPES[0],
	display_name: "",
	adapter_config: null,
	is_active: true,
};

export function SubjectForm({ onToast }: { onToast: (msg: string) => void }) {
	const [subjects, setSubjects] = useState<SubjectItem[]>([]);
	const [adapterTypes, setAdapterTypes] = useState<string[]>([
		...DEFAULT_ADAPTER_TYPES,
	]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	// Create form
	const [createForm, setCreateForm] =
		useState<SubjectCreateRequest>(EMPTY_CREATE);
	const [configJson, setConfigJson] = useState("{}");
	const [creating, setCreating] = useState(false);

	// Edit state
	const [editingId, setEditingId] = useState<string | null>(null);
	const [editForm, setEditForm] = useState<SubjectPatchRequest>({});
	const [editConfigJson, setEditConfigJson] = useState("");
	const [saving, setSaving] = useState(false);

	const loadSubjects = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const [types, list] = await Promise.all([
				api.listSubjectAdapterTypes(),
				api.listSubjects(),
			]);
			if (types.length > 0) {
				setAdapterTypes(types);
				setCreateForm((form) =>
					types.includes(form.adapter_type)
						? form
						: { ...form, adapter_type: types[0] },
				);
			}
			setSubjects(list);
		} catch (e) {
			setError(String(e));
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		loadSubjects();
	}, [loadSubjects]);

	const handleCreate = async () => {
		let parsedConfig: Record<string, unknown> | null = null;
		try {
			parsedConfig = JSON.parse(configJson);
		} catch {
			onToast("Invalid JSON in adapter config");
			return;
		}
		setCreating(true);
		try {
			await api.createSubject({ ...createForm, adapter_config: parsedConfig });
			onToast("Subject created");
			setCreateForm(EMPTY_CREATE);
			setConfigJson("{}");
			await loadSubjects();
		} catch (e) {
			onToast(`Error: ${e}`);
		} finally {
			setCreating(false);
		}
	};

	const startEdit = (s: SubjectItem) => {
		setEditingId(s.id);
		setEditForm({
			adapter_type: s.adapter_type,
			display_name: s.display_name,
			adapter_config: s.adapter_config,
			is_active: s.is_active,
		});
		setEditConfigJson(JSON.stringify(s.adapter_config ?? {}, null, 2));
	};

	const cancelEdit = () => {
		setEditingId(null);
		setEditForm({});
		setEditConfigJson("");
	};

	const handleSave = async () => {
		if (!editingId) return;
		let parsedConfig: Record<string, unknown> | null = null;
		try {
			parsedConfig = JSON.parse(editConfigJson || "{}");
		} catch {
			onToast("Invalid JSON in adapter config");
			return;
		}
		setSaving(true);
		try {
			await api.updateSubject(editingId, {
				...editForm,
				adapter_config: parsedConfig,
			});
			onToast("Subject updated");
			cancelEdit();
			await loadSubjects();
		} catch (e) {
			onToast(`Error: ${e}`);
		} finally {
			setSaving(false);
		}
	};

	const handleDelete = async (id: string, name: string) => {
		if (!confirm(`Soft-delete subject "${name}"?`)) return;
		try {
			await api.deleteSubject(id);
			onToast("Subject deactivated");
			await loadSubjects();
		} catch (e) {
			onToast(`Error: ${e}`);
		}
	};

	return (
		<div style={{ height: "100%", overflow: "auto", padding: "16px 20px" }}>
			{/* Create form */}
			<div
				style={{
					border: "1px solid var(--border-mid)",
					borderRadius: 8,
					padding: 16,
					marginBottom: 24,
					background: "var(--surface)",
				}}
			>
				<h3
					style={{
						margin: "0 0 14px",
						fontSize: 14,
						letterSpacing: "-0.01em",
					}}
				>
					Create Subject
				</h3>
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "1fr 1fr",
						gap: 10,
					}}
				>
					<div>
						<label className="field-label">Subject Name</label>
						<input
							value={createForm.subject_name}
							onChange={(e) =>
								setCreateForm((f) => ({
									...f,
									subject_name: e.target.value,
								}))
							}
							placeholder="e.g. claude-sonnet-4"
						/>
					</div>
					<div>
						<label className="field-label">Display Name</label>
						<input
							value={createForm.display_name}
							onChange={(e) =>
								setCreateForm((f) => ({
									...f,
									display_name: e.target.value,
								}))
							}
							placeholder="e.g. Claude Sonnet 4"
						/>
					</div>
					<div>
						<label className="field-label">Adapter Type</label>
						<select
							value={createForm.adapter_type}
							onChange={(e) =>
								setCreateForm((f) => ({
									...f,
									adapter_type: e.target.value,
								}))
							}
							style={{
								width: "100%",
								padding: "0.9rem 1rem",
								border: "1px solid var(--border)",
								borderRadius: 3,
								background: "var(--surface)",
								color: "var(--text)",
								fontFamily: "inherit",
								fontSize: "inherit",
							}}
						>
							{adapterTypes.map((t) => (
								<option key={t} value={t}>
									{t}
								</option>
							))}
						</select>
					</div>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: 8,
							paddingTop: 22,
						}}
					>
						<label className="field-label" style={{ margin: 0 }}>
							Active
						</label>
						<input
							type="checkbox"
							checked={createForm.is_active}
							onChange={(e) =>
								setCreateForm((f) => ({
									...f,
									is_active: e.target.checked,
								}))
							}
						/>
					</div>
				</div>
				<div style={{ marginTop: 10 }}>
					<label className="field-label">Adapter Config (JSON)</label>
					<textarea
						value={configJson}
						onChange={(e) => setConfigJson(e.target.value)}
						rows={4}
						style={{ fontFamily: "var(--mono)", fontSize: 12 }}
						placeholder='{"model": "claude-sonnet-4-20250514"}'
					/>
				</div>
				<button
					onClick={handleCreate}
					disabled={creating || !createForm.subject_name.trim()}
					style={{ marginTop: 12 }}
				>
					{creating ? "Creating..." : "Create Subject"}
				</button>
			</div>

			{/* Subject list */}
			{error && (
				<p style={{ color: "var(--danger)", fontSize: 12, marginBottom: 12 }}>
					{error}
				</p>
			)}
			{loading && (
				<p style={{ color: "var(--muted)", fontSize: 12 }}>Loading...</p>
			)}

			{!loading && !error && (
				<div>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
							marginBottom: 12,
						}}
					>
						<span
							style={{
								fontFamily: "var(--mono)",
								fontSize: 11,
								color: "var(--accent-text)",
								letterSpacing: "0.08em",
								textTransform: "uppercase",
							}}
						>
							All Subjects ({subjects.length})
						</span>
					</div>

					{subjects.length === 0 && (
						<p style={{ color: "var(--muted)", fontSize: 13 }}>
							No subjects yet. Create one above.
						</p>
					)}

					{subjects.map((s) =>
						editingId === s.id ? (
							<EditSubjectCard
								key={s.id}
								form={editForm}
								setForm={setEditForm}
								configJson={editConfigJson}
								setConfigJson={setEditConfigJson}
								onSave={handleSave}
								onCancel={cancelEdit}
								saving={saving}
								adapterTypes={adapterTypes}
							/>
						) : (
							<SubjectCard
								key={s.id}
								subject={s}
								onEdit={() => startEdit(s)}
								onDelete={() => handleDelete(s.id, s.subject_name)}
							/>
						),
					)}
				</div>
			)}
		</div>
	);
}

// CSS-in-JS helper to reuse the label style via className
// (We inject a <style> for the field-label class once)
if (
	typeof document !== "undefined" &&
	!document.getElementById("m5-field-label")
) {
	const style = document.createElement("style");
	style.id = "m5-field-label";
	style.textContent = `
		.field-label {
			display: block;
			font-size: 10px;
			font-family: var(--mono);
			color: var(--text3);
			letter-spacing: 0.08em;
			text-transform: uppercase;
			margin-bottom: 4px;
		}
	`;
	document.head.appendChild(style);
}

// ── Subject card (view mode) ────────────────────────────────────────────────

function SubjectCard({
	subject,
	onEdit,
	onDelete,
}: {
	subject: SubjectItem;
	onEdit: () => void;
	onDelete: () => void;
}) {
	return (
		<div
			style={{
				border: "1px solid var(--border)",
				borderRadius: 6,
				padding: "10px 14px",
				marginBottom: 8,
				display: "flex",
				alignItems: "center",
				justifyContent: "space-between",
				background: "var(--surface)",
			}}
		>
			<div style={{ minWidth: 0, flex: 1 }}>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: 8,
						marginBottom: 4,
					}}
				>
					<strong
						style={{
							fontSize: 13,
							fontFamily: "var(--mono)",
							color: "var(--text)",
						}}
					>
						{subject.display_name || subject.subject_name}
					</strong>
					<span
						style={{
							fontSize: 10,
							fontFamily: "var(--mono)",
							color: "var(--text3)",
							padding: "2px 6px",
							border: "1px solid var(--border)",
							borderRadius: 3,
						}}
					>
						{subject.adapter_type}
					</span>
					<span
						style={{
							fontSize: 10,
							fontFamily: "var(--mono)",
							color: subject.is_active ? "var(--green)" : "var(--danger)",
						}}
					>
						{subject.is_active ? "active" : "inactive"}
					</span>
				</div>
				<div
					style={{
						fontSize: 10,
						fontFamily: "var(--mono)",
						color: "var(--text3)",
					}}
				>
					{subject.subject_name} · {subject.id}
				</div>
			</div>
			<div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
				<button
					onClick={onEdit}
					style={{
						padding: "4px 10px",
						fontSize: 11,
						fontFamily: "var(--mono)",
						border: "1px solid var(--border)",
						borderRadius: 4,
						background: "transparent",
						color: "var(--text3)",
					}}
				>
					Edit
				</button>
				<button
					onClick={onDelete}
					style={{
						padding: "4px 10px",
						fontSize: 11,
						fontFamily: "var(--mono)",
						border: "1px solid rgba(251,113,133,0.25)",
						borderRadius: 4,
						background: "rgba(251,113,133,0.06)",
						color: "var(--danger)",
					}}
				>
					Deactivate
				</button>
			</div>
		</div>
	);
}

// ── Subject card (edit mode) ────────────────────────────────────────────────

function EditSubjectCard({
	form,
	setForm,
	configJson,
	setConfigJson,
	onSave,
	onCancel,
	saving,
	adapterTypes,
}: {
	form: SubjectPatchRequest;
	setForm: (f: SubjectPatchRequest) => void;
	configJson: string;
	setConfigJson: (j: string) => void;
	onSave: () => void;
	onCancel: () => void;
	saving: boolean;
	adapterTypes: string[];
}) {
	return (
		<div
			style={{
				border: "1px solid var(--accent-mid)",
				borderRadius: 6,
				padding: 14,
				marginBottom: 8,
				background: "var(--accent-soft)",
			}}
		>
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "1fr 1fr",
					gap: 10,
					marginBottom: 10,
				}}
			>
				<div>
					<label className="field-label">Adapter Type</label>
					<select
						value={form.adapter_type ?? ""}
						onChange={(e) => setForm({ ...form, adapter_type: e.target.value })}
						style={{
							width: "100%",
							padding: "0.9rem 1rem",
							border: "1px solid var(--border)",
							borderRadius: 3,
							background: "var(--surface)",
							color: "var(--text)",
							fontFamily: "inherit",
							fontSize: "inherit",
						}}
					>
						{adapterTypes.map((t) => (
							<option key={t} value={t}>
								{t}
							</option>
						))}
					</select>
				</div>
				<div>
					<label className="field-label">Display Name</label>
					<input
						value={form.display_name ?? ""}
						onChange={(e) => setForm({ ...form, display_name: e.target.value })}
					/>
				</div>
			</div>
			<div style={{ marginBottom: 10 }}>
				<label className="field-label">Adapter Config (JSON)</label>
				<textarea
					value={configJson}
					onChange={(e) => setConfigJson(e.target.value)}
					rows={4}
					style={{ fontFamily: "var(--mono)", fontSize: 12 }}
				/>
			</div>
			<div
				style={{
					display: "flex",
					alignItems: "center",
					gap: 8,
					marginBottom: 12,
				}}
			>
				<label className="field-label" style={{ margin: 0 }}>
					Active
				</label>
				<input
					type="checkbox"
					checked={form.is_active ?? true}
					onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
				/>
			</div>
			<div style={{ display: "flex", gap: 8 }}>
				<button
					onClick={onSave}
					disabled={saving}
					style={{ padding: "6px 16px", fontSize: 12 }}
				>
					{saving ? "Saving..." : "Save"}
				</button>
				<button
					onClick={onCancel}
					style={{
						padding: "6px 16px",
						fontSize: 12,
						border: "1px solid var(--border)",
						borderRadius: 4,
						background: "transparent",
						color: "var(--text3)",
					}}
				>
					Cancel
				</button>
			</div>
		</div>
	);
}
