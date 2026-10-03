import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type RequestDetail, type Status } from "../api";
import { useAuth } from "../auth";
import AssignedEpisodes from "../components/AssignedEpisodes";
import AssignmentPicker from "../components/AssignmentPicker";
import { ErrorMessage, formatTimestamp, isNotFound, messageOf, STATUS_LABELS, StatusBadge, useLoad } from "../ui";

export default function RequestDetailPage() {
  const requestId = Number(useParams().id);
  const { user } = useAuth();
  const detail = useLoad(() => api.getRequest(requestId), [requestId]);
  // Bumped after an assign or unassign, so the episode lists load again.
  const [episodesVersion, setEpisodesVersion] = useState(0);

  if (detail.error) {
    if (isNotFound(detail.error)) {
      return (
        <section>
          <h1>Request not found</h1>
          <p>
            It does not exist, or it is not one of your requests. <Link to="/requests">Back to the list</Link>
          </p>
        </section>
      );
    }
    return <ErrorMessage message={messageOf(detail.error)} />;
  }
  if (detail.data === null) {
    return <p>Loading...</p>;
  }

  const request = detail.data;
  const isStaff = user?.role === "operator" || user?.role === "admin";
  const episodesCanChange = isStaff && request.status === "in_progress";

  function afterEpisodesChanged() {
    detail.reload(); // the assigned count changed
    setEpisodesVersion((v) => v + 1);
  }

  return (
    <section>
      <p>
        <Link to="/requests">← All requests</Link>
      </p>
      <h1>
        Request #{request.id}: {request.task_name} <StatusBadge status={request.status} />
      </h1>

      <dl className="details">
        <dt>Client</dt>
        <dd>{request.client_name}</dd>
        <dt>Episodes</dt>
        <dd>
          <progress value={request.assigned_count} max={request.episodes_requested} aria-label="Episodes assigned" />{" "}
          {request.assigned_count} / {request.episodes_requested} assigned
        </dd>
        <dt>Deadline</dt>
        <dd>{request.deadline}</dd>
        <dt>Notes</dt>
        <dd>{request.notes || "None"}</dd>
        <dt>Created</dt>
        <dd>{formatTimestamp(request.created_at)}</dd>
        <dt>Last updated</dt>
        <dd>{formatTimestamp(request.updated_at)}</dd>
      </dl>

      <TransitionActions request={request} onUpdated={detail.setData} />

      <h2>History</h2>
      <ol className="timeline">
        {request.history.map((entry, index) => (
          <li key={index}>
            <strong>
              {entry.from_status === null
                ? `Created (${STATUS_LABELS[entry.to_status]})`
                : `${STATUS_LABELS[entry.from_status]} → ${STATUS_LABELS[entry.to_status]}`}
            </strong>{" "}
            by {entry.changed_by_name}, {formatTimestamp(entry.changed_at)}
          </li>
        ))}
      </ol>

      <h2>Assigned episodes</h2>
      {isStaff && !episodesCanChange && (
        <p className="note">Episodes can only be changed while the request is in progress.</p>
      )}
      <AssignedEpisodes
        requestId={request.id}
        canChange={episodesCanChange}
        version={episodesVersion}
        onChanged={afterEpisodesChanged}
      />

      {episodesCanChange && (
        <AssignmentPicker request={request} episodesVersion={episodesVersion} onAssigned={afterEpisodesChanged} />
      )}
    </section>
  );
}

function transitionLabel(from: Status, to: Status): string {
  if (to === "in_progress") {
    return from === "rejected" ? "Resume work" : "Start work";
  }
  const labels: Partial<Record<Status, string>> = {
    delivered: "Mark delivered",
    accepted: "Accept delivery",
    rejected: "Reject delivery",
  };
  return labels[to] ?? STATUS_LABELS[to];
}

/**
 * One button per status in available_transitions, which the server computed
 * for this user. Nothing here decides who may do what; it only picks labels.
 */
export function TransitionActions({
  request,
  onUpdated,
}: {
  request: RequestDetail;
  onUpdated: (updated: RequestDetail) => void;
}) {
  const [confirming, setConfirming] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const missing = request.episodes_requested - request.assigned_count;

  async function run(toStatus: Status) {
    setBusy(true);
    setError(null);
    try {
      onUpdated(await api.transition(request.id, toStatus));
      setConfirming(null);
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  if (request.available_transitions.length === 0 && error === null) {
    return null;
  }

  return (
    <div className="actions">
      <ErrorMessage message={error} />
      {request.available_transitions.map((toStatus) => {
        const label = transitionLabel(request.status, toStatus);

        if (toStatus === "delivered" && missing > 0) {
          // A hint only: the server checks the count itself and its message is shown if it says no.
          return (
            <span key={toStatus} className="action-with-hint">
              <button type="button" disabled aria-describedby="deliver-hint">
                {label}
              </button>
              <span id="deliver-hint" className="hint-text">
                Assign {missing} more episode{missing === 1 ? "" : "s"} first
              </span>
            </span>
          );
        }

        if (confirming === toStatus) {
          return (
            <span key={toStatus} role="group" aria-label="Confirm" className="confirm">
              {toStatus === "accepted" ? "Accept this delivery?" : "Reject this delivery?"}
              <button type="button" className="primary" onClick={() => run(toStatus)} disabled={busy}>
                {toStatus === "accepted" ? "Yes, accept" : "Yes, reject"}
              </button>
              <button type="button" onClick={() => setConfirming(null)} disabled={busy}>
                Cancel
              </button>
            </span>
          );
        }

        // Accepting or rejecting is final for the client, so it gets a confirmation step.
        const needsConfirmation = toStatus === "accepted" || toStatus === "rejected";
        return (
          <button
            key={toStatus}
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => (needsConfirmation ? setConfirming(toStatus) : run(toStatus))}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
