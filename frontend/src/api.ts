export interface Resume {
  id: number;
  name: string;
  job_type: string;
  page_count: number | null;
  parent_id: number | null;
  created_at: string;
  version_count: number;
}

export interface ResumeDetail extends Resume {
  tex_source: string | null;
}

export interface DocSummary {
  id: number;
  doc_type: string;
  approved: boolean;
  vetted: boolean;
  created_at: string;
}

export interface Job {
  id: number;
  company: string;
  title: string;
  url: string | null;
  status: string;
  deadline: string | null;
  applied_at: string | null;
  notes: string | null;
  created_at: string;
}

export interface JobDetail extends Job {
  jd_text: string | null;
  docs: DocSummary[];
}

export interface EditPair {
  original: string;
  replacement: string;
  reason?: string;
}

export interface GenerateResult {
  id: number;
  job_id: number;
  doc_type: string;
  approved: boolean;
  page_count: number;
  applied_edits: EditPair[];
  rejected_edits: EditPair[];
  warnings: string[];
  divergence: number | null;
  body_text: string | null;
}

export interface DocDetail {
  id: number;
  job_id: number;
  doc_type: string;
  approved: boolean;
  vetted: boolean;
  tex_source: string;
  base_tex_source: string | null;
  edits: Record<string, unknown>[] | null;
  created_at: string;
  body_text: string | null;
  draft_text: string | null;
}

export interface DocFeedItem {
  id: number;
  job_id: number;
  doc_type: string;
  approved: boolean;
  vetted: boolean;
  created_at: string;
  company: string;
  title: string;
  job_status: string;
  job_url: string | null;
}

export interface VoiceSample {
  id: number;
  title: string;
  kind: string;
  source: string | null;
  created_at: string;
}

export interface VoiceRule {
  date: string;
  rule: string;
}

export interface VoiceProfile {
  content: string | null;
  learned_rules: VoiceRule[];
  updated_at: string | null;
  sample_count: number;
}

export interface MemoryEntry {
  id: number;
  type: string;
  title: string;
  content: string;
  tags: string[];
  source: string | null;
  muted: boolean;
  created_at: string;
}

export interface LinkedEntry {
  link_id: number;
  relation: string | null;
  entry: MemoryEntry;
}

export interface MemoryEntryDetail extends MemoryEntry {
  links: LinkedEntry[];
}

export interface GraphNode {
  id: number;
  type: string;
  title: string;
  muted: boolean;
  degree: number;
}

export interface GraphLink {
  id: number;
  from_id: number;
  to_id: number;
  relation: string | null;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

export interface OrganizeResult {
  entries_processed: number;
  hubs_created: number;
  links_created: number;
  errors: string[];
}

export interface GithubSyncResult {
  username: string;
  repos_synced: number;
  hubs_created: number;
  links_created: number;
}

export interface MemorySearchHit {
  score: number;
  entry: MemoryEntry;
}

export interface QAEntry {
  id: number;
  question: string;
  answer: string;
  tags: string[];
  job_id: number | null;
  times_used: number;
  created_at: string;
}

export interface QASearchHit {
  score: number;
  qa: QAEntry;
}

export const MEMORY_TYPES = [
  "experience", "skill", "story", "personal", "preference",
  "project", "company", "trait",
] as const;
export const ENTITY_TYPES = ["skill", "project", "company", "trait"] as const;
export const RELATIONS = [
  "demonstrates", "used", "built", "worked_at", "part_of", "led_to", "related",
] as const;

export interface AtsReport {
  parsed_words: number;
  ats_readable: boolean;
  keywords_checked: number;
  present_keywords: string[];
  missing_keywords: string[];
  keyword_score: number | null;
}

export interface AtsScanRow {
  id: number;
  doc_id: number | null;
  resume_id: number | null;
  kind: string;
  status: string;
  report: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
}

export interface AtsScanList {
  scans: AtsScanRow[];
  capabilities: Record<string, boolean>;
}

export interface ResumeStats {
  resume_id: number;
  name: string;
  job_type: string;
  jobs_tailored: number;
  applications: number;
  responses: number;
  interviews: number;
  offers: number;
  response_rate: number | null;
}

export interface ReminderJob {
  id: number;
  company: string;
  title: string;
  status: string;
  deadline: string | null;
  applied_at: string | null;
}

export interface ActionJob {
  job_id: number;
  company: string;
  title: string;
  status: string;
}

export interface ActionDoc {
  doc_id: number;
  doc_type: string;
  company: string;
  title: string;
}

export interface PrepTurn {
  role: "interviewer" | "candidate";
  text: string;
}

export interface PrepSession {
  id: number;
  job_id: number;
  kind: string;
  status: string;
  transcript: PrepTurn[];
  report: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Analytics {
  funnel: Record<string, number>;
  by_resume: ResumeStats[];
  reminders: { deadlines: ReminderJob[]; stale: ReminderJob[] };
  action_queue: { needs_resume: ActionJob[]; prep_ready: ActionJob[]; drafts: ActionDoc[] };
  counts: Record<string, number>;
}

export interface Research {
  id: number;
  job_id: number;
  findings: string;
  sources: string[];
  created_at: string;
}

export interface DraftResult {
  draft: string;
  memories_used: { id: number; title: string; score: number }[];
  past_answers_used: { id: number; question: string; score: number }[];
}

export type BrainstormEvent =
  | { type: "session"; session_id: string }
  | { type: "text"; text: string }
  | { type: "tool"; name: string }
  | { type: "done"; session_id: string; is_error: boolean }
  | { type: "error"; message: string };

export const JOB_STATUSES = [
  "saved",
  "applied",
  "oa",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
] as const;

export interface EngineStatus {
  engine_preference: string;
  ai_provider: string;
  providers: Record<string, boolean>;
  subscription_available: boolean;
  api_key_configured: boolean;
  last_used: string | null;
  last_provider: string | null;
  models: Record<string, string>;
  model_defaults: Record<string, string>;
}

export interface NetworkTarget {
  id: number;
  company: string;
  role_type: string | null;
  source: string;
  active: boolean;
  discovered_at: string | null;
}

export interface NetworkMatchSignal {
  signal: string;
  detail: string;
}

export interface NetworkPerson {
  id: number;
  name: string;
  headline: string | null;
  company: string;
  location: string | null;
  person_type: string;
  profile_url: string | null;
  evidence_urls: string[];
  source: string;
  match_signals: NetworkMatchSignal[];
  summary: string | null;
  connection_note: string | null;
  followup: string | null;
  status: string;
  notes: string | null;
  created_at: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export interface BulkEditResult {
  id: number;
  name: string;
  status: string;
  new_id: number | null;
  error: string | null;
}

export interface ImportSessionStatus {
  id: number;
  status: string; // running|review|done|error
  stage: string;  // extract|convert|compile|verify|review
  progress: number;
  error: string | null;
  resume_id: number | null;
  report: { fidelity: string[]; fit: string[]; alignment: string[] } | null;
  rounds: number;
}

export const api = {
  listResumes: () => request<Resume[]>("/api/resumes"),
  getResume: (id: number) => request<ResumeDetail>(`/api/resumes/${id}`),
  createResume: (payload: { name: string; job_type: string; tex_source: string }) =>
    request<Resume>("/api/resumes", { method: "POST", body: JSON.stringify(payload) }),
  updateResume: (id: number, payload: { tex_source: string; name?: string }) =>
    request<Resume>(`/api/resumes/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteResume: (id: number) => request<void>(`/api/resumes/${id}`, { method: "DELETE" }),
  uploadPdfResume: (name: string, jobType: string, file: File) => {
    const form = new FormData();
    form.append("name", name);
    form.append("job_type", jobType);
    form.append("file", file);
    return fetch("/api/resumes/pdf", { method: "POST", body: form }).then(async (r) => {
      if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
      return r.json() as Promise<Resume>;
    });
  },
  bulkEdit: (payload: { find: string; replace: string; job_type?: string }) =>
    request<{ results: BulkEditResult[] }>("/api/resumes/bulk-edit", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  importPdf: (name: string, jobType: string, file: File) => {
    const form = new FormData();
    form.append("name", name);
    form.append("job_type", jobType);
    form.append("file", file);
    return fetch("/api/resumes/import-pdf", { method: "POST", body: form }).then(async (r) => {
      if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
      return r.json() as Promise<{ id: number }>;
    });
  },
  getImportSession: (id: number) =>
    request<ImportSessionStatus>(`/api/resumes/import-sessions/${id}`),
  acceptImport: (id: number) =>
    request<Resume>(`/api/resumes/import-sessions/${id}/accept`, { method: "POST" }),

  listJobs: () => request<Job[]>("/api/jobs"),
  getJob: (id: number) => request<JobDetail>(`/api/jobs/${id}`),
  createJob: (payload: Partial<Job> & { company: string; title: string }) =>
    request<Job>("/api/jobs", { method: "POST", body: JSON.stringify(payload) }),
  patchJob: (id: number, payload: Partial<Job>) =>
    request<Job>(`/api/jobs/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteJob: (id: number) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),

  getAnalytics: () => request<Analytics>("/api/analytics"),
  parsePosting: (payload: { text?: string; image_b64?: string; url?: string }) =>
    request<{
      company: string;
      title: string;
      location: string;
      jd_text: string;
      confidence: number;
    }>("/api/jobs/parse", { method: "POST", body: JSON.stringify(payload) }),
  getExtensionToken: () => request<{ token: string }>("/api/profile/extension-token"),

  getEngineStatus: () => request<EngineStatus>("/api/engine/status"),
  setEngineProvider: (provider: string) =>
    request<EngineStatus>("/api/engine/provider", {
      method: "PUT",
      body: JSON.stringify({ provider }),
    }),
  setEngineModel: (provider: string, model: string) =>
    request<EngineStatus>("/api/engine/model", {
      method: "PUT",
      body: JSON.stringify({ provider, model }),
    }),

  getProfile: () => request<Record<string, string>>("/api/profile"),
  putProfile: (values: Record<string, string>) =>
    request<Record<string, string>>("/api/profile", {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),

  interviewPrep: (jobId: number) =>
    request<{
      questions: {
        question: string;
        why_asked: string;
        story_titles: string[];
        talking_points: string[];
      }[];
    }>(`/api/jobs/${jobId}/prep`, { method: "POST" }),

  getResearch: (jobId: number) => request<Research>(`/api/jobs/${jobId}/research`),
  runResearch: (jobId: number, force = false) =>
    request<Research>(`/api/jobs/${jobId}/research${force ? "?force=true" : ""}`, {
      method: "POST",
    }),

  tailor: (payload: {
    resume_id: number;
    job_id?: number;
    company?: string;
    title?: string;
    url?: string;
    jd_text?: string;
  }) => request<GenerateResult>("/api/generate/tailor", { method: "POST", body: JSON.stringify(payload) }),
  coverLetter: (payload: { job_id: number; resume_id?: number }) =>
    request<GenerateResult>("/api/generate/cover-letter", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getDoc: (id: number) => request<DocDetail>(`/api/docs/${id}`),
  approveDoc: (id: number) => request<{ id: number; approved: boolean }>(`/api/docs/${id}/approve`, { method: "POST" }),
  amendDoc: (id: number, body_text: string) =>
    request<{ id: number; approved: boolean; vetted: boolean; page_count: number }>(
      `/api/docs/${id}/amend`,
      { method: "POST", body: JSON.stringify({ body_text }) },
    ),
  listDocs: (params: { doc_type?: string; status?: string; q?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]),
    ).toString();
    return request<DocFeedItem[]>(`/api/docs${qs ? `?${qs}` : ""}`);
  },
  deleteDoc: (id: number) => request<void>(`/api/docs/${id}`, { method: "DELETE" }),

  listMemory: (type?: string) =>
    request<MemoryEntry[]>(`/api/memory${type ? `?type=${encodeURIComponent(type)}` : ""}`),
  getMemory: (id: number) => request<MemoryEntryDetail>(`/api/memory/${id}`),
  createMemory: (payload: {
    type: string;
    title: string;
    content: string;
    tags?: string[];
    source?: string;
  }) => request<MemoryEntryDetail>("/api/memory", { method: "POST", body: JSON.stringify(payload) }),
  updateMemory: (
    id: number,
    payload: { type?: string; title?: string; content?: string; tags?: string[]; muted?: boolean },
  ) => request<MemoryEntry>(`/api/memory/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteMemory: (id: number) => request<void>(`/api/memory/${id}`, { method: "DELETE" }),
  linkMemory: (payload: { from_id: number; to_id: number; relation?: string }) =>
    request<{ id: number }>("/api/memory/links", { method: "POST", body: JSON.stringify(payload) }),
  searchMemory: (query: string, k = 8, types?: string[]) =>
    request<MemorySearchHit[]>("/api/memory/search", {
      method: "POST",
      body: JSON.stringify({ query, k, types }),
    }),
  getGraph: () => request<GraphData>("/api/memory/graph"),
  organizeBrain: () => request<OrganizeResult>("/api/memory/organize", { method: "POST" }),
  syncGithub: () => request<GithubSyncResult>("/api/profile/github/sync", { method: "POST" }),

  ingestDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch("/api/memory/ingest", { method: "POST", body: form }).then(async (r) => {
      if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
      return r.json() as Promise<MemoryEntry[]>;
    });
  },
  atsCheck: (docId: number) => request<AtsReport>(`/api/docs/${docId}/ats`),
  listAtsScans: (params: { doc_id?: number; resume_id?: number }) => {
    const qs = new URLSearchParams();
    if (params.doc_id != null) qs.set("doc_id", String(params.doc_id));
    if (params.resume_id != null) qs.set("resume_id", String(params.resume_id));
    return request<AtsScanList>(`/api/ats/scans?${qs}`);
  },
  runAtsScan: (payload: { doc_id?: number; resume_id?: number; kind: string }) =>
    request<AtsScanRow>("/api/ats/scan", { method: "POST", body: JSON.stringify(payload) }),
  cancelAtsScan: (id: number) => request<AtsScanRow>(`/api/ats/scan/${id}/cancel`, { method: "POST" }),
  retailorDoc: (docId: number) =>
    request<GenerateResult>("/api/generate/retailor", {
      method: "POST",
      body: JSON.stringify({ doc_id: docId }),
    }),
  getDocFit: (docId: number) =>
    request<{
      lines: number;
      budget: number;
      effective_budget: number;
      fits: boolean;
      sections: { name: string; lines: number }[];
    }>(`/api/docs/${docId}/fit`),
  draftAnswer: (payload: { question: string; job_id?: number }) =>
    request<DraftResult>("/api/qa/draft", { method: "POST", body: JSON.stringify(payload) }),

  listQA: () => request<QAEntry[]>("/api/qa"),
  saveQA: (payload: { question: string; answer: string; tags?: string[]; job_id?: number; draft?: string }) =>
    request<QAEntry>("/api/qa", { method: "POST", body: JSON.stringify(payload) }),
  searchQA: (query: string, k = 5) =>
    request<QASearchHit[]>("/api/qa/search", { method: "POST", body: JSON.stringify({ query, k }) }),

  listVoiceSamples: () => request<VoiceSample[]>("/api/voice/samples"),
  addVoiceSample: (payload: { title: string; kind: string; text: string }) =>
    request<VoiceSample>("/api/voice/samples", { method: "POST", body: JSON.stringify(payload) }),
  uploadVoiceSample: (title: string, kind: string, file: File) => {
    const form = new FormData();
    form.append("title", title);
    form.append("kind", kind);
    form.append("file", file);
    return fetch("/api/voice/samples/upload", { method: "POST", body: form }).then(async (r) => {
      if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
      return r.json() as Promise<VoiceSample>;
    });
  },
  deleteVoiceSample: (id: number) => request<void>(`/api/voice/samples/${id}`, { method: "DELETE" }),
  getVoiceProfile: () => request<VoiceProfile>("/api/voice/profile"),
  updateVoiceProfile: (payload: { content?: string; learned_rules?: VoiceRule[] }) =>
    request<VoiceProfile>("/api/voice/profile", { method: "PUT", body: JSON.stringify(payload) }),
  rebuildVoiceProfile: () => request<VoiceProfile>("/api/voice/profile/rebuild", { method: "POST" }),
  updateDocBody: (id: number, body_text: string) =>
    request<{ id: number; body_text: string }>(`/api/docs/${id}/body`, {
      method: "PUT",
      body: JSON.stringify({ body_text }),
    }),
  finalizeDoc: (id: number) =>
    request<{ id: number; approved: boolean; page_count: number }>(`/api/docs/${id}/finalize`, {
      method: "POST",
    }),
  updateDocTex: (id: number, tex_source: string) =>
    request<{ id: number; page_count: number; approved: boolean; warnings: string[] }>(
      `/api/docs/${id}/tex`,
      { method: "PUT", body: JSON.stringify({ tex_source }) },
    ),

  network: {
    targets: () => request<NetworkTarget[]>("/api/network/targets"),
    addTarget: (t: { company: string; role_type?: string }) =>
      request<NetworkTarget>("/api/network/targets", { method: "POST", body: JSON.stringify(t) }),
    patchTarget: (id: number, active: boolean) =>
      request<NetworkTarget>(`/api/network/targets/${id}`, { method: "PATCH", body: JSON.stringify({ active }) }),
    deleteTarget: (id: number) => request<void>(`/api/network/targets/${id}`, { method: "DELETE" }),
    discover: (force = false) =>
      request<{ started: number }>("/api/network/discover", { method: "POST", body: JSON.stringify({ force }) }),
    discoverStatus: () =>
      request<{ running: boolean; done: number; total: number; last_error: string | null }>(
        "/api/network/discover/status"),
    people: (params: { status?: string; company?: string; person_type?: string; q?: string } = {}) => {
      const qs = new URLSearchParams(
        Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]),
      ).toString();
      return request<NetworkPerson[]>(`/api/network/people${qs ? `?${qs}` : ""}`);
    },
    addPerson: (p: { name: string; company: string; headline?: string }) =>
      request<NetworkPerson>("/api/network/people", { method: "POST", body: JSON.stringify(p) }),
    enrich: (id: number) => request<void>(`/api/network/people/${id}/enrich`, { method: "POST" }),
    patchPerson: (id: number, patch: Partial<Pick<NetworkPerson, "status" | "notes" | "connection_note" | "followup">>) =>
      request<NetworkPerson>(`/api/network/people/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
    deletePerson: (id: number) => request<void>(`/api/network/people/${id}`, { method: "DELETE" }),
  },

  prep: {
    start: (job_id: number, kind: "interview" | "oa") =>
      request<PrepSession>("/api/prep/sessions", {
        method: "POST",
        body: JSON.stringify({ job_id, kind }),
      }),
    turn: (id: number, answer: string) =>
      request<PrepSession>(`/api/prep/sessions/${id}/turn`, {
        method: "POST",
        body: JSON.stringify({ answer }),
      }),
    finish: (id: number) =>
      request<PrepSession>(`/api/prep/sessions/${id}/finish`, { method: "POST" }),
    list: (job_id: number) => request<PrepSession[]>(`/api/prep/sessions?job_id=${job_id}`),
    get: (id: number) => request<PrepSession>(`/api/prep/sessions/${id}`),
    remove: (id: number) => request<void>(`/api/prep/sessions/${id}`, { method: "DELETE" }),
  },
};

export async function streamBrainstorm(
  message: string,
  sessionId: string | null,
  onEvent: (e: BrainstormEvent) => void,
): Promise<void> {
  const resp = await fetch("/api/brainstorm/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!resp.ok || !resp.body) throw new Error(resp.statusText);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
      }
    }
  }
}

export const pdfUrl = {
  resume: (id: number) => `/api/resumes/${id}/pdf`,
  doc: (id: number) => `/api/docs/${id}/pdf`,
};
