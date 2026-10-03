import type { ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router-dom";
import type { Role } from "./api";
import { AuthProvider, useAuth } from "./auth";
import AnalyticsPage from "./pages/AnalyticsPage";
import ImportsPage from "./pages/ImportsPage";
import LoginPage from "./pages/LoginPage";
import NewRequestPage from "./pages/NewRequestPage";
import RequestDetailPage from "./pages/RequestDetailPage";
import RequestsPage from "./pages/RequestsPage";
import UsersPage from "./pages/UsersPage";

const STAFF: Role[] = ["operator", "admin"];

// Links each role may use. Hiding a link is a convenience: the server checks every call.
const NAV_LINKS: { to: string; label: string; roles: Role[] }[] = [
  { to: "/requests", label: "My requests", roles: ["client"] },
  { to: "/requests/new", label: "New request", roles: ["client"] },
  { to: "/requests", label: "Requests", roles: STAFF },
  { to: "/imports", label: "Imports", roles: STAFF },
  { to: "/analytics", label: "Analytics", roles: STAFF },
  { to: "/users", label: "Users", roles: ["admin"] },
];

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}

/** The routes on their own, so tests can render them inside a MemoryRouter. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireLogin>
            <Layout />
          </RequireLogin>
        }
      >
        <Route index element={<Navigate to="/requests" replace />} />
        <Route path="/requests" element={<RequestsPage />} />
        <Route
          path="/requests/new"
          element={
            <RequireRole roles={["client"]}>
              <NewRequestPage />
            </RequireRole>
          }
        />
        <Route path="/requests/:id" element={<RequestDetailPage />} />
        <Route
          path="/imports"
          element={
            <RequireRole roles={STAFF}>
              <ImportsPage />
            </RequireRole>
          }
        />
        <Route
          path="/analytics"
          element={
            <RequireRole roles={STAFF}>
              <AnalyticsPage />
            </RequireRole>
          }
        />
        <Route
          path="/users"
          element={
            <RequireRole roles={["admin"]}>
              <UsersPage />
            </RequireRole>
          }
        />
        <Route path="*" element={<p>Page not found.</p>} />
      </Route>
    </Routes>
  );
}

function RequireLogin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return <p className="page-loading">Loading...</p>;
  }
  if (user === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}

/** Shows a "Not allowed" page for other roles. Cosmetic: the API refuses them anyway. */
export function RequireRole({ roles, children }: { roles: Role[]; children: ReactNode }) {
  const { user } = useAuth();
  if (user === null || !roles.includes(user.role)) {
    return (
      <section>
        <h1>Not allowed</h1>
        <p>Your role cannot use this page.</p>
      </section>
    );
  }
  return children;
}

function Layout() {
  const { user, logout } = useAuth();
  if (user === null) {
    return null; // RequireLogin has already redirected
  }
  const links = NAV_LINKS.filter((link) => link.roles.includes(user.role));
  return (
    <>
      <header className="site-header">
        <span className="site-name">Dataset Request Desk</span>
        <nav aria-label="Main">
          {links.map((link) => (
            // "end": /requests is not also marked active on /requests/new.
            <NavLink key={link.label} to={link.to} end>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <span className="who">
          {user.name} <span className="role">({user.role})</span>
        </span>
        <button type="button" onClick={logout}>
          Log out
        </button>
      </header>
      <main>
        <Outlet />
      </main>
    </>
  );
}
