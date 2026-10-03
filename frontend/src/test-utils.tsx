// Helpers for the tests: render the real routes with a given logged-in user.
// No network: every test replaces the api functions it needs with vi.spyOn.

import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { api, ApiError, type RequestDetail, type User } from "./api";
import { AppRoutes } from "./App";
import { AuthProvider } from "./auth";

export const CLIENT: User = { id: 4, email: "client-a@example.com", name: "Acme Robotics", role: "client", organisation: "Acme Robotics" };
export const OPERATOR: User = { id: 2, email: "ops1@example.com", name: "Olu Operator", role: "operator", organisation: null };
export const ADMIN: User = { id: 1, email: "admin@example.com", name: "Ada Admin", role: "admin", organisation: null };

/** Render the app at `route`, with `user` logged in (null: not logged in). */
export function renderApp(route: string, user: User | null) {
  vi.spyOn(api, "me").mockImplementation(() =>
    user ? Promise.resolve(user) : Promise.reject(new ApiError(401, "Not authenticated")),
  );
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </MemoryRouter>,
  );
}

export function requestDetail(changes: Partial<RequestDetail> = {}): RequestDetail {
  return {
    id: 7,
    client_id: CLIENT.id,
    client_name: CLIENT.name,
    task_name: "pick cup",
    episodes_requested: 5,
    deadline: "2030-01-01",
    notes: null,
    status: "submitted",
    assigned_count: 0,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    history: [{ from_status: null, to_status: "submitted", changed_by_name: CLIENT.name, changed_at: "2026-09-01T10:00:00Z" }],
    available_transitions: [],
    ...changes,
  };
}

export const EMPTY_PAGE = { items: [], total: 0, limit: 20, offset: 0 };
