import { type FormEvent, useState } from "react";
import { type Analytics, api, STATUSES } from "../api";
import { ErrorMessage, formatTimestamp, messageOf, STATUS_LABELS, todayUtc, useLoad } from "../ui";

const DAY_MS = 24 * 60 * 60 * 1000;

/** YYYY-MM-DD, `days` before the given UTC date. */
function daysBefore(date: string, days: number): string {
  return new Date(Date.parse(`${date}T00:00:00Z`) - days * DAY_MS).toISOString().slice(0, 10);
}

/** Every UTC date from `from` to `to`, both included. */
export function datesBetween(from: string, to: string): string[] {
  const dates: string[] = [];
  for (let time = Date.parse(`${from}T00:00:00Z`); time <= Date.parse(`${to}T00:00:00Z`); time += DAY_MS) {
    dates.push(new Date(time).toISOString().slice(0, 10));
  }
  return dates;
}

/**
 * The API lists only (day, robot) pairs that have episodes. For the table we
 * want every day of the range and every robot, with 0 where there were none.
 */
export function pivotEpisodesPerDay(rows: Analytics["episodes_per_day"], from: string, to: string) {
  const robots = [...new Set(rows.map((row) => row.robot_id))].sort();
  const counts = new Map(rows.map((row) => [`${row.date} ${row.robot_id}`, row.count]));
  const days = datesBetween(from, to).map((date) => ({
    date,
    counts: robots.map((robot) => counts.get(`${date} ${robot}`) ?? 0),
  }));
  return { robots, days };
}

/** Operators and admins. Default range: the last 30 days, in UTC. */
export default function AnalyticsPage() {
  const [fromDraft, setFromDraft] = useState(() => daysBefore(todayUtc(), 29));
  const [toDraft, setToDraft] = useState(todayUtc);
  const [range, setRange] = useState({ from: fromDraft, to: toDraft });
  const result = useLoad(() => api.analytics(range.from, range.to), [range]);

  function run(event: FormEvent) {
    event.preventDefault();
    setRange({ from: fromDraft, to: toDraft });
  }

  return (
    <section>
      <h1>Analytics</h1>
      <form className="filters" onSubmit={run}>
        <label>
          From (UTC)
          <input type="date" value={fromDraft} onChange={(e) => setFromDraft(e.target.value)} required />
        </label>
        <label>
          To (UTC)
          <input type="date" value={toDraft} onChange={(e) => setToDraft(e.target.value)} required />
        </label>
        <button type="submit" className="primary" disabled={result.loading}>
          {result.loading ? "Running..." : "Run"}
        </button>
      </form>

      {/* e.g. 422 "The range is 400 days; at most 366 are allowed" */}
      <ErrorMessage message={result.error !== null ? messageOf(result.error) : null} />
      {result.loading && result.data === null && <p>Loading...</p>}
      {result.data !== null && result.error === null && <AnalyticsSections analytics={result.data} />}
    </section>
  );
}

export function AnalyticsSections({ analytics }: { analytics: Analytics }) {
  const { meta, requests, top_tasks } = analytics;
  const pivot = pivotEpisodesPerDay(analytics.episodes_per_day, meta.from, meta.to);
  const maxGood = Math.max(1, ...top_tasks.map((task) => task.good_episodes));

  return (
    <>
      <p className="note">
        {meta.from} to {meta.to} ({meta.days} days, UTC). Generated {formatTimestamp(meta.generated_at)}.
      </p>

      <h2>Episodes per day and robot</h2>
      {pivot.robots.length === 0 ? (
        <p>No episodes recorded in this range.</p>
      ) : (
        <div className="table-wrap table-tall">
          <table className="numbers">
            <thead>
              <tr>
                <th scope="col">Date (UTC)</th>
                {pivot.robots.map((robot) => (
                  <th scope="col" key={robot}>
                    {robot}
                  </th>
                ))}
                <th scope="col">Total</th>
              </tr>
            </thead>
            <tbody>
              {pivot.days.map((day) => (
                <tr key={day.date}>
                  <th scope="row">{day.date}</th>
                  {day.counts.map((count, index) => (
                    <td key={pivot.robots[index]}>{count}</td>
                  ))}
                  <td>{day.counts.reduce((a, b) => a + b, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Requests created in this range</h2>
      <div className="table-wrap">
        <table className="numbers">
          <thead>
            <tr>
              {STATUSES.map((status) => (
                <th scope="col" key={status}>
                  {STATUS_LABELS[status]}
                </th>
              ))}
              <th scope="col">Total</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              {STATUSES.map((status) => (
                <td key={status}>{requests.by_status[status]}</td>
              ))}
              <td>{requests.total}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>
        Median time from submitted to first delivery:{" "}
        <strong>
          {requests.median_hours_submitted_to_delivered === null
            ? "no deliveries in this range"
            : `${requests.median_hours_submitted_to_delivered} hours`}
        </strong>
        {requests.delivered_count > 0 && ` (over ${requests.delivered_count} delivered requests)`}
      </p>

      <h2>Top tasks by good episodes</h2>
      {top_tasks.length === 0 ? (
        <p>No good episodes in this range.</p>
      ) : (
        <ol className="bars">
          {top_tasks.map((task) => (
            <li key={task.task_name}>
              <span className="bar-label">{task.task_name}</span>
              <span className="bar-track" aria-hidden="true">
                <span className="bar" style={{ width: `${(task.good_episodes / maxGood) * 100}%` }} />
              </span>
              <span className="bar-value">{task.good_episodes}</span>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
