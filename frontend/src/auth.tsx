import { createContext, type ReactNode, useContext, useEffect, useState } from "react";
import { api, ApiError, setSessionExpiredHandler, type User } from "./api";

interface AuthState {
  user: User | null;
  loading: boolean; // true until the first "who am I" answer arrives
  notice: string | null; // e.g. "Your session expired", shown on the login page
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    // Any 401 after this point means the cookie expired or the user was deactivated.
    setSessionExpiredHandler(() => {
      setUser(null);
      setNotice("Your session expired. Please log in again.");
    });

    // The cookie is HttpOnly, so JavaScript cannot read it: ask the server who we are.
    api
      .me()
      .then(setUser)
      .catch((error) => {
        setUser(null);
        if (!(error instanceof ApiError)) {
          setNotice("Could not reach the server. Please try again later.");
        }
      })
      .finally(() => setLoading(false));
  }, []);

  async function login(email: string, password: string) {
    const loggedIn = await api.login(email, password);
    setNotice(null);
    setUser(loggedIn);
  }

  async function logout() {
    try {
      await api.logout();
    } finally {
      setUser(null);
    }
  }

  return <AuthContext.Provider value={{ user, loading, notice, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const auth = useContext(AuthContext);
  if (auth === null) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return auth;
}
