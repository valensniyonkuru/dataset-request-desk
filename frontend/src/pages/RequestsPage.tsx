import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, STATUSES, type Status } from "../api";
import { useAuth } from "../auth";
import { ErrorMessage, messageOf, Pagination, STATUS_LABELS, StatusBadge, formatTimestamp, useLoad } from "../ui";

const PAGE_SIZE = 20;

/** All roles use this page; the server only returns a client's own requests. */
export default function RequestsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const isClient = user?.role === "client";

  const [status, setStatus] = useState<Status | "">("");
  const [taskDraft, setTaskDraft] = useState("");
  const [taskName, setTaskName] = useState("");
  const [offset, setOffset] = useState(0);

  const requests = useLoad(
    () => api.listRequests({ status: status || undefined, task_name: taskName || undefined, limit: PAGE_SIZE, offset }),
    [status, taskName, offset],
  );

  function applyTaskFilter(event: FormEvent) {
    event.preventDefault();
    setTaskName(taskDraft.trim());
    setOffset(0);
  }

  return (
    <section>
      <h1>{isClient ? "My requests" : "Requests"}</h1>

      {!isClient && (
        <form className="filters" onSubmit={applyTaskFilter}>
          <label>
            Status
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as Status | "");
                setOffset(0);
              }}
            >
              <option value="">Any status</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Task (exact name)
            <input value={taskDraft} onChange={(e) => setTaskDraft(e.target.value)} placeholder="pick cup" />
          </label>
          <button type="submit">Filter</button>
        </form>
      )}

      <ErrorMessage message={requests.error ? messageOf(requests.error) : null} />
      {requests.loading && <p>Loading...</p>}
      {!requests.loading && requests.data?.length === 0 && (
        <p>
          {isClient ? (
            <>
              No requests yet. <Link to="/requests/new">Create your first request</Link>.
            </>
          ) : (
            "No requests match these filters."
          )}
        </p>
      )}

      {requests.data && requests.data.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="clickable-rows">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">Client</th>
                  <th scope="col">Task</th>
                  <th scope="col">Assigned / requested</th>
                  <th scope="col">Deadline</th>
                  <th scope="col">Status</th>
                  <th scope="col">Created</th>
                </tr>
              </thead>
              <tbody>
                {requests.data.map((request) => (
                  // The whole row is clickable for mouse users; the link in the first cell is for keyboards.
                  <tr key={request.id} onClick={() => navigate(`/requests/${request.id}`)}>
                    <td>
                      <Link to={`/requests/${request.id}`}>#{request.id}</Link>
                    </td>
                    <td>{request.client_name}</td>
                    <td>{request.task_name}</td>
                    <td>
                      {request.assigned_count} / {request.episodes_requested}
                    </td>
                    <td>{request.deadline}</td>
                    <td>
                      <StatusBadge status={request.status} />
                    </td>
                    <td>{formatTimestamp(request.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination offset={offset} limit={PAGE_SIZE} shown={requests.data.length} onChange={setOffset} />
        </>
      )}
    </section>
  );
}
