import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { ErrorMessage, messageOf, todayUtc } from "../ui";

/** Client only. The checks below mirror the server's, to catch typos early; the server decides. */
export default function NewRequestPage() {
  const navigate = useNavigate();
  const [taskName, setTaskName] = useState("");
  const [episodes, setEpisodes] = useState("");
  const [deadline, setDeadline] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function problemWithInput(): string | null {
    const count = Number(episodes);
    if (!taskName.trim()) return "Please enter a task name.";
    if (!Number.isInteger(count) || count < 1 || count > 100000) return "Episodes must be a whole number from 1 to 100000.";
    if (!deadline || deadline < todayUtc()) return "The deadline must be today or later (UTC).";
    return null;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const problem = problemWithInput();
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.createRequest({
        task_name: taskName.trim(),
        episodes_requested: Number(episodes),
        deadline,
        notes: notes.trim() || null,
      });
      navigate(`/requests/${created.id}`);
    } catch (failure) {
      setError(messageOf(failure));
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>New request</h1>
      <form className="form" onSubmit={submit} noValidate>
        <label>
          Task name
          <input value={taskName} onChange={(e) => setTaskName(e.target.value)} maxLength={100} required />
        </label>
        <label>
          Episodes requested
          <input
            type="number"
            min={1}
            max={100000}
            step={1}
            value={episodes}
            onChange={(e) => setEpisodes(e.target.value)}
            required
          />
        </label>
        <label>
          Deadline (UTC)
          <input type="date" min={todayUtc()} value={deadline} onChange={(e) => setDeadline(e.target.value)} required />
        </label>
        <label>
          Notes (optional)
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} rows={4} />
        </label>
        <ErrorMessage message={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Creating..." : "Create request"}
        </button>
      </form>
    </section>
  );
}
