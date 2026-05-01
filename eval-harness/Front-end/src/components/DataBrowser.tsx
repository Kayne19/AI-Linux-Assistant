import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { DataTableResponse, TableMeta } from "../types";

export function DataBrowser() {
	const [tables, setTables] = useState<TableMeta[]>([]);
	const [activeTable, setActiveTable] = useState<string>("");
	const [data, setData] = useState<DataTableResponse | null>(null);
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const [page, setPage] = useState(1);
	const [sortBy, setSortBy] = useState("");
	const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
	const [toast, setToast] = useState<string | null>(null);
	const [adminMessage, setAdminMessage] = useState<string | null>(null);
	const pageSize = 50;

	// Load table list on mount
	useEffect(() => {
		api
			.listTables()
			.then(setTables)
			.catch(() => setError("Failed to load table list."));
	}, []);

	// Load table data when active table changes
	const loadTable = useCallback(
		async (table: string, pageNum: number) => {
			if (!table) return;
			setLoading(true);
			setError(null);
			try {
				const result = await api.browseTable(table, {
					page: pageNum,
					page_size: pageSize,
					sort_by: sortBy || undefined,
					sort_dir: sortDir,
				});
				setData(result);
			} catch (e) {
				setError(String(e));
			} finally {
				setLoading(false);
			}
		},
		[sortBy, sortDir],
	);

	useEffect(() => {
		if (activeTable) {
			setPage(1);
			loadTable(activeTable, 1);
		}
	}, [activeTable, sortBy, sortDir, loadTable]);

	const handlePageChange = (newPage: number) => {
		setPage(newPage);
		loadTable(activeTable, newPage);
	};

	const handleSort = (col: string) => {
		if (sortBy === col) {
			setSortDir((d) => (d === "asc" ? "desc" : "asc"));
		} else {
			setSortBy(col);
			setSortDir("asc");
		}
	};

	const copyRowAsJson = (row: Record<string, unknown>) => {
		const json = JSON.stringify(row, null, 2);
		navigator.clipboard.writeText(json).then(
			() => showToast("Row copied as JSON"),
			() => showToast("Copy failed"),
		);
	};

	const showToast = (msg: string) => {
		setToast(msg);
		setTimeout(() => setToast(null), 2000);
	};

	const handleInitDb = async () => {
		setAdminMessage(null);
		try {
			const result = await api.initDb();
			setAdminMessage(result.message);
		} catch (e) {
			setAdminMessage(`Error: ${e}`);
		}
	};

	const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

	const tableLabel = useMemo(() => {
		const t = tables.find((t) => t.name === activeTable);
		return t?.label ?? activeTable;
	}, [tables, activeTable]);

	return (
		<div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
			{/* Header */}
			<div
				style={{
					padding: "16px 20px 12px",
					borderBottom: "1px solid var(--border)",
					flexShrink: 0,
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
				}}
			>
				<h2
					style={{
						margin: 0,
						fontSize: 18,
						letterSpacing: "-0.02em",
					}}
				>
					Data
				</h2>

				<div style={{ display: "flex", gap: 8 }}>
					<button
						onClick={handleInitDb}
						style={{
							padding: "6px 14px",
							border: "1px solid var(--border)",
							borderRadius: 6,
							background: "transparent",
							color: "var(--text3)",
							fontSize: 12,
							fontFamily: "var(--mono)",
						}}
					>
						Init DB
					</button>
					{adminMessage && (
						<span
							style={{
								fontSize: 11,
								fontFamily: "var(--mono)",
								color: "var(--green)",
								alignSelf: "center",
							}}
						>
							{adminMessage}
						</span>
					)}
				</div>
			</div>

			{/* Body */}
			<div
				style={{
					flex: 1,
					minHeight: 0,
					overflow: "hidden",
					display: "flex",
					flexDirection: "column",
				}}
			>
				<BrowseView
					tables={tables}
					activeTable={activeTable}
					setActiveTable={setActiveTable}
					data={data}
					loading={loading}
					error={error}
					page={page}
					totalPages={totalPages}
					sortBy={sortBy}
					sortDir={sortDir}
					tableLabel={tableLabel}
					onPageChange={handlePageChange}
					onSort={handleSort}
					onCopyRow={copyRowAsJson}
				/>
			</div>

			{/* Toast */}
			{toast && (
				<div
					style={{
						position: "fixed",
						bottom: 20,
						right: 20,
						padding: "8px 16px",
						background: "var(--surface2)",
						border: "1px solid var(--border-mid)",
						borderRadius: 8,
						fontFamily: "var(--mono)",
						fontSize: 12,
						color: "var(--text)",
						zIndex: 50,
					}}
				>
					{toast}
				</div>
			)}
		</div>
	);
}

// ── Browse sub-view ────────────────────────────────────────────────────────

function BrowseView({
	tables,
	activeTable,
	setActiveTable,
	data,
	loading,
	error,
	page,
	totalPages,
	sortBy,
	sortDir,
	tableLabel,
	onPageChange,
	onSort,
	onCopyRow,
}: {
	tables: TableMeta[];
	activeTable: string;
	setActiveTable: (t: string) => void;
	data: DataTableResponse | null;
	loading: boolean;
	error: string | null;
	page: number;
	totalPages: number;
	sortBy: string;
	sortDir: "asc" | "desc";
	tableLabel: string;
	onPageChange: (p: number) => void;
	onSort: (col: string) => void;
	onCopyRow: (row: Record<string, unknown>) => void;
}) {
	return (
		<div style={{ display: "flex", height: "100%", minHeight: 0 }}>
			{/* Table selector sidebar */}
			<div
				style={{
					width: 200,
					flexShrink: 0,
					borderRight: "1px solid var(--border)",
					overflowY: "auto",
					padding: "8px 0",
				}}
			>
				{tables.map((t) => (
					<button
						key={t.name}
						onClick={() => setActiveTable(t.name)}
						style={{
							display: "block",
							width: "100%",
							textAlign: "left",
							padding: "8px 14px",
							border: "none",
							borderRadius: 0,
							background:
								activeTable === t.name ? "var(--accent-soft)" : "transparent",
							color:
								activeTable === t.name ? "var(--accent-text)" : "var(--text)",
							fontSize: 12,
							fontFamily: "var(--mono)",
							cursor: "pointer",
							whiteSpace: "nowrap",
							overflow: "hidden",
							textOverflow: "ellipsis",
						}}
					>
						{t.label}
					</button>
				))}
			</div>

			{/* Table contents */}
			<div
				style={{
					flex: 1,
					minWidth: 0,
					overflow: "auto",
					padding: "12px 16px",
				}}
			>
				{!activeTable && (
					<p
						style={{
							color: "var(--muted)",
							fontSize: 13,
							textAlign: "center",
							marginTop: 40,
						}}
					>
						Select a table to browse its rows.
					</p>
				)}

				{error && (
					<p style={{ color: "var(--danger)", fontSize: 12 }}>{error}</p>
				)}

				{loading && (
					<p style={{ color: "var(--muted)", fontSize: 12 }}>Loading...</p>
				)}

				{data && !loading && (
					<>
						<div
							style={{
								display: "flex",
								alignItems: "center",
								justifyContent: "space-between",
								marginBottom: 12,
							}}
						>
							<div>
								<span
									style={{
										fontFamily: "var(--mono)",
										fontSize: 11,
										color: "var(--accent-text)",
										letterSpacing: "0.08em",
										textTransform: "uppercase",
									}}
								>
									{tableLabel}
								</span>
								<span
									style={{
										fontFamily: "var(--mono)",
										fontSize: 11,
										color: "var(--text3)",
										marginLeft: 8,
									}}
								>
									{data.total.toLocaleString()} row
									{data.total !== 1 ? "s" : ""}
								</span>
							</div>

							{/* Pagination */}
							{totalPages > 1 && (
								<div
									style={{
										display: "flex",
										gap: 4,
										alignItems: "center",
									}}
								>
									<button
										onClick={() => onPageChange(page - 1)}
										disabled={page <= 1}
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
										Prev
									</button>
									<span
										style={{
											fontFamily: "var(--mono)",
											fontSize: 11,
											color: "var(--text3)",
										}}
									>
										{page} / {totalPages}
									</span>
									<button
										onClick={() => onPageChange(page + 1)}
										disabled={page >= totalPages}
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
										Next
									</button>
								</div>
							)}
						</div>

						{/* Data table */}
						<div
							style={{
								overflowX: "auto",
								border: "1px solid var(--border)",
								borderRadius: 6,
							}}
						>
							<table
								style={{
									width: "100%",
									borderCollapse: "collapse",
									fontFamily: "var(--mono)",
									fontSize: 11,
								}}
							>
								<thead>
									<tr
										style={{
											borderBottom: "1px solid var(--border-mid)",
										}}
									>
										{data.columns.map((col) => (
											<th
												key={col}
												onClick={() => onSort(col)}
												style={{
													textAlign: "left",
													padding: "8px 10px",
													cursor: "pointer",
													userSelect: "none",
													color:
														sortBy === col
															? "var(--accent-text)"
															: "var(--text3)",
													whiteSpace: "nowrap",
													fontWeight: 500,
													fontSize: 10,
													letterSpacing: "0.06em",
													textTransform: "uppercase",
												}}
											>
												{col}
												{sortBy === col && (
													<span style={{ marginLeft: 4 }}>
														{sortDir === "asc" ? "\u2191" : "\u2193"}
													</span>
												)}
											</th>
										))}
										<th
											style={{
												width: 90,
												padding: "8px 10px",
											}}
										/>
									</tr>
								</thead>
								<tbody>
									{data.rows.map((row, i) => (
										<tr
											key={String(row.id ?? i)}
											style={{
												borderBottom: "1px solid var(--border)",
											}}
										>
											{data.columns.map((col) => (
												<td
													key={col}
													style={{
														padding: "6px 10px",
														maxWidth: 300,
														overflow: "hidden",
														textOverflow: "ellipsis",
														whiteSpace: "nowrap",
														color: "var(--text)",
													}}
													title={_cellTitle(row[col])}
												>
													{_cellDisplay(row[col])}
												</td>
											))}
											<td
												style={{
													padding: "6px 10px",
													textAlign: "right",
												}}
											>
												<button
													onClick={() => onCopyRow(row)}
													style={{
														padding: "2px 8px",
														fontSize: 10,
														fontFamily: "var(--mono)",
														border: "1px solid var(--border)",
														borderRadius: 3,
														background: "transparent",
														color: "var(--text3)",
														cursor: "pointer",
													}}
												>
													JSON
												</button>
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					</>
				)}
			</div>
		</div>
	);
}

// ── Cell helpers ────────────────────────────────────────────────────────────

function _cellDisplay(value: unknown): string {
	if (value === null || value === undefined) return "\u2014";
	if (typeof value === "object") return JSON.stringify(value);
	return String(value);
}

function _cellTitle(value: unknown): string {
	if (typeof value === "object" && value !== null) {
		return JSON.stringify(value, null, 2);
	}
	return _cellDisplay(value);
}
