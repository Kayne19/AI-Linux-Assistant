import {
	ApiError,
	getAuthorizationHeader,
	handleUnauthorizedStatus,
} from "./apiAuth";
import type {
	ArtifactExportRequest,
	BenchmarkRequest,
	BenchmarkRunItem,
	ControlResponse,
	CreateRevisionRequest,
	CreateRevisionResponse,
	CreateScenarioRequest,
	CreateScenarioResponse,
	DataTableResponse,
	EvaluationRunItem,
	GenerateRequest,
	GenerateResponse,
	ImageItem,
	InstanceItem,
	JudgeJobDetail,
	JudgeJobItem,
	JudgeRequest,
	PreflightResponse,
	RunAllRequest,
	RunEventItem,
	RunListResponse,
	ScenarioDetail,
	ScenarioListItem,
	SetupRunEventItem,
	SetupRunItem,
	SubjectCreateRequest,
	SubjectItem,
	SubjectPatchRequest,
	TableMeta,
	ValidateRequest,
	ValidateResponse,
	VerifyRequest,
} from "./types";

const API_BASE_URL =
	(import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ||
	"http://localhost:8001";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const authHeaders = await getAuthorizationHeader(false);
	const response = await fetch(`${API_BASE_URL}${path}`, {
		headers: {
			"Content-Type": "application/json",
			...authHeaders,
			...(init?.headers || {}),
		},
		...init,
	});

	if (!response.ok) {
		await handleUnauthorizedStatus(response.status);
		const text = await response.text();
		throw new ApiError(
			response.status,
			text || `Request failed with status ${response.status}`,
		);
	}

	return response.json() as Promise<T>;
}

export const api = {
	health: () => request<{ ok: boolean }>("/health"),

	// Scenarios
	listScenarios: () => request<ScenarioListItem[]>("/api/v1/scenarios"),
	getScenario: (id: string) =>
		request<ScenarioDetail>(`/api/v1/scenarios/${id}`),

	// Runs
	listRuns: (params?: { page?: number; page_size?: number }) => {
		const p = new URLSearchParams();
		if (params?.page) p.set("page", String(params.page));
		if (params?.page_size) p.set("page_size", String(params.page_size));
		const qs = p.toString();
		return request<RunListResponse>(`/api/v1/runs${qs ? `?${qs}` : ""}`);
	},

	// Setup runs
	getSetupRun: (id: string) =>
		request<SetupRunItem>(`/api/v1/setup-runs/${id}`),
	listSetupRunEvents: (
		id: string,
		cursor?: { after_round_index?: number; after_seq?: number },
	) => {
		const p = new URLSearchParams();
		if (
			cursor?.after_round_index !== undefined &&
			cursor.after_round_index >= 0
		) {
			p.set("after_round_index", String(cursor.after_round_index));
			p.set("after_seq", String(cursor.after_seq ?? -1));
		}
		const qs = p.toString();
		return request<SetupRunEventItem[]>(
			`/api/v1/setup-runs/${id}/events${qs ? `?${qs}` : ""}`,
		);
	},

	// Benchmark runs
	getBenchmarkRun: (id: string) =>
		request<BenchmarkRunItem>(`/api/v1/benchmarks/${id}`),
	listBenchmarkEvaluations: (id: string) =>
		request<EvaluationRunItem[]>(`/api/v1/benchmarks/${id}/evaluations`),

	// Evaluation runs
	getEvaluation: (id: string) =>
		request<EvaluationRunItem>(`/api/v1/evaluations/${id}`),
	listEvaluationEvents: (id: string, afterSeq?: number) => {
		const p = new URLSearchParams();
		if (afterSeq !== undefined && afterSeq >= 0) {
			p.set("after_seq", String(afterSeq));
		}
		const qs = p.toString();
		return request<RunEventItem[]>(
			`/api/v1/evaluations/${id}/events${qs ? `?${qs}` : ""}`,
		);
	},

	// Judge jobs
	listJudgeJobs: () => request<JudgeJobItem[]>("/api/v1/judge-jobs"),
	getJudgeJob: (id: string) =>
		request<JudgeJobDetail>(`/api/v1/judge-jobs/${id}`),

	// Subjects
	listSubjects: () => request<SubjectItem[]>("/api/v1/subjects"),
	listSubjectAdapterTypes: () =>
		request<string[]>("/api/v1/subjects/adapter-types"),
	getSubject: (id: string) => request<SubjectItem>(`/api/v1/subjects/${id}`),

	// M4 — Authoring
	generateScenario: (body: GenerateRequest) =>
		request<GenerateResponse>("/api/v1/scenarios/generate", {
			method: "POST",
			body: JSON.stringify(body),
		}),
	validateScenario: (body: ValidateRequest) =>
		request<ValidateResponse>("/api/v1/scenarios/validate", {
			method: "POST",
			body: JSON.stringify(body),
		}),
	createScenario: (body: CreateScenarioRequest) =>
		request<CreateScenarioResponse>("/api/v1/scenarios", {
			method: "POST",
			body: JSON.stringify(body),
		}),
	createRevision: (scenarioId: string, body: CreateRevisionRequest) =>
		request<CreateRevisionResponse>(
			`/api/v1/scenarios/${scenarioId}/revisions`,
			{
				method: "POST",
				body: JSON.stringify(body),
			},
		),

	// M2 — Infra
	listInstances: () => request<InstanceItem[]>("/api/v1/infra/instances"),
	listImages: () => request<ImageItem[]>("/api/v1/infra/images"),
	preflight: () =>
		request<PreflightResponse>("/api/v1/infra/preflight", {
			method: "POST",
		}),
	terminateInstance: (instanceId: string) =>
		request<{ ok: boolean; instance_id: string }>(
			`/api/v1/infra/instances/${instanceId}?confirm=1`,
			{ method: "DELETE" },
		),
	deregisterImage: (imageId: string) =>
		request<{ ok: boolean; image_id: string }>(
			`/api/v1/infra/images/${imageId}`,
			{ method: "DELETE" },
		),

	// M5 — Data browser
	listTables: () => request<TableMeta[]>("/api/v1/data/tables"),
	browseTable: (
		table: string,
		params?: {
			page?: number;
			page_size?: number;
			sort_by?: string;
			sort_dir?: string;
		},
	) => {
		const p = new URLSearchParams();
		if (params?.page) p.set("page", String(params.page));
		if (params?.page_size) p.set("page_size", String(params.page_size));
		if (params?.sort_by) p.set("sort_by", params.sort_by);
		if (params?.sort_dir) p.set("sort_dir", params.sort_dir);
		const qs = p.toString();
		return request<DataTableResponse>(
			`/api/v1/data/${table}${qs ? `?${qs}` : ""}`,
		);
	},

	// M5 — Subject CRUD
	createSubject: (body: SubjectCreateRequest) =>
		request<SubjectItem>("/api/v1/subjects", {
			method: "POST",
			body: JSON.stringify(body),
		}),
	updateSubject: (id: string, body: SubjectPatchRequest) =>
		request<SubjectItem>(`/api/v1/subjects/${id}`, {
			method: "PATCH",
			body: JSON.stringify(body),
		}),
	deleteSubject: (id: string) =>
		request<{ ok: boolean; id: string }>(`/api/v1/subjects/${id}`, {
			method: "DELETE",
		}),

	// M5 — Artifact export
	exportArtifacts: (benchmarkId: string, body?: ArtifactExportRequest) =>
		request<Record<string, unknown>>(
			`/api/v1/benchmarks/${benchmarkId}/export-artifacts`,
			{
				method: "POST",
				body: body ? JSON.stringify(body) : undefined,
			},
		),

	// M5 — Admin
	initDb: () =>
		request<{ ok: boolean; message: string }>("/api/v1/admin/init-db", {
			method: "POST",
		}),

	// M3 — Controls
	verifyScenario: (scenarioId: string, body: VerifyRequest) =>
		request<ControlResponse>(`/api/v1/scenarios/${scenarioId}/verify`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
	benchmarkScenario: (scenarioId: string, body: BenchmarkRequest) =>
		request<ControlResponse>(`/api/v1/scenarios/${scenarioId}/benchmark`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
	runAllScenario: (scenarioId: string, body: RunAllRequest) =>
		request<ControlResponse>(`/api/v1/scenarios/${scenarioId}/run-all`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
	triggerJudge: (benchmarkRunId: string, body: JudgeRequest) =>
		request<ControlResponse>(`/api/v1/benchmarks/${benchmarkRunId}/judge`, {
			method: "POST",
			body: JSON.stringify(body),
		}),
	cancelRun: (runId: string, runType?: string) =>
		request<ControlResponse>(
			`/api/v1/runs/${runId}/cancel?run_type=${runType || "benchmark"}`,
			{ method: "POST" },
		),
};
