import { type FormEvent, useState } from "react";
import { api, type EpisodeListItem, type RequestDetail } from "../api";
import { ErrorMessage, formatTimestamp, messageOf, Pagination, SuccessMessage, useLoad } from "../ui";

const PAGE_SIZE = 20;

// "bad" is not offered: bad episodes can never be assigned (the server refuses them too).
type QualityChoice = "good" | "usable" | "good_or_usable";

const QUALITY_LABELS: Record<QualityChoice, string> = {
  good: "Good",
  usable: "Usable",
  good_or_usable: "Good or usable",
};

interface Props {
  request: RequestDetail;
  // Changes after any assign or unassign on the page, so an unassigned episode shows up again here.
  episodesVersion: number;
  onAssigned: () => void;
}

/** Find unassigned episodes and assign a selection of them to the request (operators and admins). */
export default function AssignmentPicker({ request, episodesVersion, onAssigned }: Props) {
  const remaining = request.episodes_requested - request.assigned_count;

  const [taskDraft, setTaskDraft] = useState(request.task_name);
  const [qualityDraft, setQualityDraft] = useState<QualityChoice>("good");
  const [filters, setFilters] = useState({ taskName: request.task_name, quality: "good" as QualityChoice });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const page = useLoad(
    () =>
      api.listEpisodes({
        task_name: filters.taskName || undefined,
        // The API filters on one quality at a time. For "good or usable" we ask for
        // every quality and show bad episodes as not selectable.
        quality: filters.quality === "good_or_usable" ? undefined : filters.quality,
        assigned: false,
        limit: PAGE_SIZE,
        offset,
      }),
    [filters, offset, version, episodesVersion],
  );

  const selectable = (episode: EpisodeListItem) => episode.quality !== "bad";
  const pageItems = page.data?.items ?? [];
  const selectableOnPage = pageItems.filter(selectable);
  const allOnPageSelected =
    selectableOnPage.length > 0 && selectableOnPage.every((episode) => selected.has(episode.episode_id));

  const left = remaining - selected.size;
  const counter =
    left >= 0 ? `${selected.size} selected, ${left} more allowed` : `${selected.size} selected, ${-left} too many`;
  const canAssign = selected.size > 0 && left >= 0 && !busy;

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    setFilters({ taskName: taskDraft.trim(), quality: qualityDraft });
    setOffset(0);
    setSelected(new Set());
  }

  function toggle(episodeId: string) {
    const next = new Set(selected);
    if (next.has(episodeId)) {
      next.delete(episodeId);
    } else {
      next.add(episodeId);
    }
    setSelected(next);
  }

  function toggleAllOnPage() {
    const next = new Set(selected);
    for (const episode of selectableOnPage) {
      if (allOnPageSelected) {
        next.delete(episode.episode_id);
      } else {
        next.add(episode.episode_id);
      }
    }
    setSelected(next);
  }

  async function assignSelected() {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await api.assign(request.id, [...selected]);
      setSuccess(`Assigned ${result.assigned_episode_ids.length} episodes.`);
      setSelected(new Set());
      setVersion((v) => v + 1); // the assigned ones disappear from the list
      onAssigned();
    } catch (failure) {
      setError(messageOf(failure)); // e.g. 409 "Already assigned: EP-00012 (request 7)"
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="picker-title">
      <h2 id="picker-title">Assign episodes</h2>
      {remaining <= 0 && <p className="note">This request already has all {request.episodes_requested} episodes.</p>}

      <form className="filters" onSubmit={applyFilters}>
        <label>
          Task name
          <input value={taskDraft} onChange={(e) => setTaskDraft(e.target.value)} />
        </label>
        <label>
          Quality
          <select value={qualityDraft} onChange={(e) => setQualityDraft(e.target.value as QualityChoice)}>
            {Object.entries(QUALITY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Show episodes</button>
      </form>

      <ErrorMessage message={error} />
      <SuccessMessage message={success} />
      {page.error !== null && <ErrorMessage message={messageOf(page.error)} />}
      {page.loading && page.data === null && <p>Loading...</p>}
      {page.data !== null && page.data.total === 0 && <p>No unassigned episodes match these filters.</p>}

      {pageItems.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">
                    <input
                      type="checkbox"
                      checked={allOnPageSelected}
                      onChange={toggleAllOnPage}
                      aria-label="Select all on this page"
                    />
                  </th>
                  <th scope="col">Episode</th>
                  <th scope="col">Robot</th>
                  <th scope="col">Task</th>
                  <th scope="col">Quality</th>
                  <th scope="col">Recorded</th>
                  <th scope="col">Duration</th>
                </tr>
              </thead>
              <tbody>
                {pageItems.map((episode) => (
                  <tr key={episode.episode_id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.has(episode.episode_id)}
                        onChange={() => toggle(episode.episode_id)}
                        disabled={!selectable(episode)}
                        aria-label={`Select ${episode.episode_id}`}
                      />
                    </td>
                    <td>{episode.episode_id}</td>
                    <td>{episode.robot_id}</td>
                    <td>{episode.task_name}</td>
                    <td>{selectable(episode) ? episode.quality : "bad (cannot be assigned)"}</td>
                    <td>{formatTimestamp(episode.recorded_at)}</td>
                    <td>{episode.duration_seconds} s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            offset={offset}
            limit={PAGE_SIZE}
            shown={pageItems.length}
            total={page.data?.total}
            onChange={setOffset}
          />
        </>
      )}

      <div className="assign-bar">
        <span role="status">{counter}</span>
        <button type="button" className="primary" onClick={assignSelected} disabled={!canAssign}>
          {busy ? "Assigning..." : "Assign selected"}
        </button>
      </div>
    </section>
  );
}
