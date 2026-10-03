import { useState } from "react";
import { api } from "../api";
import { ErrorMessage, formatTimestamp, messageOf, Pagination, useLoad } from "../ui";

const PAGE_SIZE = 20;

interface Props {
  requestId: number;
  canChange: boolean; // operator or admin, and the request is in progress
  version: number; // changes when the list must be loaded again
  onChanged: () => void;
}

/** The episodes assigned to a request, with an Unassign button when changes are allowed. */
export default function AssignedEpisodes({ requestId, canChange, version, onChanged }: Props) {
  const [offset, setOffset] = useState(0);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const page = useLoad(() => api.requestEpisodes(requestId, PAGE_SIZE, offset), [requestId, offset, version]);

  async function unassign(episodeId: string) {
    setBusyId(episodeId);
    setError(null);
    try {
      await api.unassign(requestId, episodeId);
      onChanged();
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusyId(null);
    }
  }

  if (page.error) {
    return <ErrorMessage message={messageOf(page.error)} />;
  }
  if (page.data === null) {
    return <p>Loading...</p>;
  }
  if (page.data.total === 0) {
    return <p>No episodes assigned yet.</p>;
  }

  return (
    <>
      <ErrorMessage message={error} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Episode</th>
              <th scope="col">Robot</th>
              <th scope="col">Task</th>
              <th scope="col">Quality</th>
              <th scope="col">Recorded</th>
              <th scope="col">Duration</th>
              <th scope="col">Operator</th>
              <th scope="col">Assigned</th>
              {canChange && (
                <th scope="col">
                  <span className="visually-hidden">Action</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {page.data.items.map((episode) => (
              <tr key={episode.episode_id}>
                <td>{episode.episode_id}</td>
                <td>{episode.robot_id}</td>
                <td>{episode.task_name}</td>
                <td>{episode.quality}</td>
                <td>{formatTimestamp(episode.recorded_at)}</td>
                <td>{episode.duration_seconds} s</td>
                <td>{episode.operator_name}</td>
                <td>{formatTimestamp(episode.assigned_at)}</td>
                {canChange && (
                  <td>
                    <button
                      type="button"
                      onClick={() => unassign(episode.episode_id)}
                      disabled={busyId !== null}
                      aria-label={`Unassign ${episode.episode_id}`}
                    >
                      {busyId === episode.episode_id ? "Removing..." : "Unassign"}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        offset={offset}
        limit={PAGE_SIZE}
        shown={page.data.items.length}
        total={page.data.total}
        onChange={setOffset}
      />
    </>
  );
}
