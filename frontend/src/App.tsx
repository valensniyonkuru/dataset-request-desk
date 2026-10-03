import { useEffect, useState } from "react";

type ApiStatus = "checking" | "ok" | "down";

export default function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");

  useEffect(() => {
    // Same origin: nginx forwards /api/health to the backend's /health.
    fetch("/api/health")
      .then((response) => setApiStatus(response.ok ? "ok" : "down"))
      .catch(() => setApiStatus("down"));
  }, []);

  return (
    <main>
      <h1>Dataset Request Desk</h1>
      <p>API: {apiStatus}</p>
    </main>
  );
}
