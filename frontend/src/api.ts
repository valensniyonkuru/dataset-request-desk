// The one place that talks to the backend.
//
// Every call goes to /api/... on the same origin; the frontend's nginx forwards
// it to the API. Being logged in is an HttpOnly cookie that the browser sends by
// itself ("same-origin" credentials), so this file never sees or stores a token.
// The types below mirror backend/app/schemas.py.

export type Role = "client" | "operator" | "admin";
export type Status = "submitted" | "in_progress" | "delivered" | "accepted" | "rejected";
export type Quality = "good" | "usable" | "bad";

export const STATUSES: Status[] = ["submitted", "in_progress", "delivered", "accepted", "rejected"];

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  organisation: string | null;
}

export interface AdminUser extends User {
  is_active: boolean;
  created_at: string;
}

export interface NewUser {
  email: string;
  name: string;
  role: Role;
  organisation: string | null;
  password: string;
}

export interface UserChanges {
  name?: string;
  role?: Role;
  organisation?: string | null;
  is_active?: boolean;
  password?: string;
}

export interface RequestSummary {
  id: number;
  client_id: number;
  client_name: string;
  task_name: string;
  episodes_requested: number;
  deadline: string; // YYYY-MM-DD
  notes: string | null;
  status: Status;
  assigned_count: number;
  created_at: string;
  updated_at: string;
}

export interface HistoryEntry {
  from_status: Status | null; // null on the row written when the request was created
  to_status: Status;
  changed_by_name: string;
  changed_at: string;
}

export interface RequestDetail extends RequestSummary {
  history: HistoryEntry[]; // oldest first
  available_transitions: Status[]; // what the current user may do now, decided by the server
}

export interface NewRequest {
  task_name: string;
  episodes_requested: number;
  deadline: string;
  notes: string | null;
}

export interface Episode {
  episode_id: string;
  robot_id: string;
  task_name: string;
  recorded_at: string;
  duration_seconds: number;
  operator_name: string;
  quality: Quality;
  import_run_id: number | null;
  created_at: string;
}

export interface EpisodeListItem extends Episode {
  assigned_request_id: number | null;
}

export interface AssignedEpisode extends Episode {
  assigned_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface AssignResult {
  request_id: number;
  assigned_count: number;
  assigned_episode_ids: string[];
}

export interface ImportProblem {
  line: number;
  episode_id: string | null;
  reason: string;
  value?: string | null;
  fields?: Record<string, { file: unknown; database: unknown }> | null;
}

export interface ImportReport {
  status: "running" | "finished" | "failed";
  error?: string | null;
  file_name: string;
  file_sha256: string;
  total_rows: number;
  imported: number;
  skipped: Record<string, number>;
  rejected: Record<string, number>;
  normalised: Record<string, number>;
  blank_lines: number;
  problems: ImportProblem[];
  problems_truncated: boolean;
  previous_runs_with_same_file: number[];
  duration_ms: number;
}

export interface ImportResult {
  import_run_id: number;
  report: ImportReport;
}

export interface ImportRunSummary {
  id: number;
  file_name: string;
  started_by_name: string | null;
  started_at: string;
  finished_at: string | null;
  status: "running" | "finished" | "failed";
  total_rows: number | null;
  imported: number | null;
  skipped: number | null;
  rejected: number | null;
}

export interface ImportRun {
  id: number;
  file_name: string;
  file_sha256: string;
  started_by_name: string | null;
  started_at: string;
  finished_at: string | null;
  report: ImportReport | null;
}

export interface Analytics {
  episodes_per_day: { date: string; robot_id: string; count: number }[];
  requests: {
    by_status: Record<Status, number>;
    total: number;
    delivered_count: number;
    median_hours_submitted_to_delivered: number | null;
  };
  top_tasks: { task_name: string; good_episodes: number }[];
  meta: { from: string; to: string; days: number; generated_at: string };
}

/** A non-2xx answer. `message` is the server's own explanation (its "detail"). */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Readable text from a FastAPI error body: {"detail": "..."} or, for 422, {"detail": [{loc, msg}, ...]}. */
export function detailText(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map(describeValidationError).join("; ");
  }
  return `Request failed (${status})`;
}

function describeValidationError(error: { loc?: (string | number)[]; msg?: string }): string {
  // loc is like ["body", "episodes_requested"]: keep the field name, drop where it came from.
  const field = (error.loc ?? []).filter((part) => !["body", "query", "path"].includes(String(part))).join(".");
  const message = (error.msg ?? "is invalid").replace(/^Value error, /, "");
  return field ? `${field}: ${message}` : message;
}

let onSessionExpired: () => void = () => {};

/** The auth context registers what to do when a call comes back 401 (the session ended). */
export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler;
}

interface Options {
  method?: string;
  json?: unknown;
  form?: FormData;
  // Login and the startup "who am I" check expect 401 as a normal answer.
  unauthorizedIsExpected?: boolean;
}

async function request<T>(path: string, options: Options = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form; // the browser sets the multipart Content-Type itself
  } else if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }

  const response = await fetch(`/api${path}`, {
    method: options.method ?? "GET",
    headers,
    body,
    credentials: "same-origin",
  });

  if (response.status === 401 && !options.unauthorizedIsExpected) {
    onSessionExpired();
  }
  if (!response.ok) {
    const errorBody = await response.json().catch(() => null);
    throw new ApiError(response.status, detailText(errorBody, response.status));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** "?a=1&b=x" from the values that are set. A list repeats its key: quality=good&quality=usable. */
function query(params: Record<string, string | number | boolean | string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, item));
    } else if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  me: () => request<User>("/auth/me", { unauthorizedIsExpected: true }),
  login: (email: string, password: string) =>
    request<User>("/auth/login", { method: "POST", json: { email, password }, unauthorizedIsExpected: true }),
  logout: () => request<void>("/auth/logout", { method: "POST", unauthorizedIsExpected: true }),

  listRequests: (filters: { status?: Status; task_name?: string; limit: number; offset: number }) =>
    request<RequestSummary[]>(`/requests${query(filters)}`),
  getRequest: (id: number) => request<RequestDetail>(`/requests/${id}`),
  createRequest: (body: NewRequest) => request<RequestDetail>("/requests", { method: "POST", json: body }),
  transition: (id: number, toStatus: Status) =>
    request<RequestDetail>(`/requests/${id}/transition`, { method: "POST", json: { to_status: toStatus } }),

  requestEpisodes: (id: number, limit: number, offset: number) =>
    request<Page<AssignedEpisode>>(`/requests/${id}/episodes${query({ limit, offset })}`),
  // quality: any of these (sent as quality=good&quality=usable); leave out for all qualities.
  listEpisodes: (filters: {
    task_name?: string;
    quality?: Quality[];
    assigned?: boolean;
    limit: number;
    offset: number;
  }) =>
    request<Page<EpisodeListItem>>(`/episodes${query(filters)}`),
  assign: (id: number, episodeIds: string[]) =>
    request<AssignResult>(`/requests/${id}/assignments`, { method: "POST", json: { episode_ids: episodeIds } }),
  unassign: (id: number, episodeId: string) =>
    request<void>(`/requests/${id}/assignments/${encodeURIComponent(episodeId)}`, { method: "DELETE" }),

  listUsers: () => request<AdminUser[]>("/users"),
  createUser: (body: NewUser) => request<AdminUser>("/users", { method: "POST", json: body }),
  updateUser: (id: number, changes: UserChanges) =>
    request<AdminUser>(`/users/${id}`, { method: "PATCH", json: changes }),

  uploadImport: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ImportResult>("/imports", { method: "POST", form });
  },
  listImports: (limit: number, offset: number) => request<Page<ImportRunSummary>>(`/imports${query({ limit, offset })}`),
  getImport: (id: number) => request<ImportRun>(`/imports/${id}`),

  analytics: (from: string, to: string) => request<Analytics>(`/analytics${query({ from, to })}`),
};
