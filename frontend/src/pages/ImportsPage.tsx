import { type FormEvent, useState } from "react";
import { api, type ImportProblem, type ImportReport } from "../api";
import { ErrorMessage, formatTimestamp, messageOf, Pagination, useLoad } from "../ui";

const PAGE_SIZE = 10;

interface ShownReport {
  title: string;
  report: ImportReport | null; // null while a run is still going
}

/** Operators and admins: upload a CSV export, read its report, browse past imports. */
export default function ImportsPage() {
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0); // changing it empties the file input
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<ShownReport | null>(null);
  const [offset, setOffset] = useState(0);
  const runs = useLoad(() => api.listImports(PAGE_SIZE, offset), [offset]);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (file === null) {
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const result = await api.uploadImport(file);
      setShown({ title: `Import #${result.import_run_id}: ${result.report.file_name}`, report: result.report });
      setFile(null);
      setFileInputKey((k) => k + 1);
      runs.reload();
    } catch (failure) {
      setError(messageOf(failure)); // e.g. 422 "Missing required columns: quality"
    } finally {
      setUploading(false);
    }
  }

  async function openRun(id: number) {
    setError(null);
    try {
      const run = await api.getImport(id);
      setShown({ title: `Import #${run.id}: ${run.file_name}`, report: run.report });
    } catch (failure) {
      setError(messageOf(failure));
    }
  }

  return (
    <section>
      <h1>Imports</h1>
      <form className="filters" onSubmit={upload}>
        <label>
          Episodes CSV (at most 20 MB)
          <input
            key={fileInputKey}
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <button type="submit" className="primary" disabled={file === null || uploading}>
          {uploading ? "Uploading..." : "Upload"}
        </button>
      </form>
      {uploading && <p role="status">Importing, this can take a little while for large files...</p>}
      <ErrorMessage message={error} />

      {shown && (
        <section className="panel" aria-labelledby="report-title">
          <h2 id="report-title">{shown.title}</h2>
          {shown.report ? <ImportReportView report={shown.report} /> : <p>This import is still running.</p>}
        </section>
      )}

      <h2>Past imports</h2>
      {runs.error !== null && <ErrorMessage message={messageOf(runs.error)} />}
      {runs.data === null && runs.error === null && <p>Loading...</p>}
      {runs.data !== null && runs.data.total === 0 && <p>No imports yet.</p>}
      {runs.data !== null && runs.data.items.length > 0 && (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">File</th>
                  <th scope="col">Started by</th>
                  <th scope="col">Started</th>
                  <th scope="col">Status</th>
                  <th scope="col">Rows</th>
                  <th scope="col">Imported</th>
                  <th scope="col">Skipped</th>
                  <th scope="col">Rejected</th>
                  <th scope="col">
                    <span className="visually-hidden">Report</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((run) => (
                  <tr key={run.id}>
                    <td>{run.id}</td>
                    <td>{run.file_name}</td>
                    <td>{run.started_by_name ?? "command line"}</td>
                    <td>{formatTimestamp(run.started_at)}</td>
                    <td>{run.status}</td>
                    <td>{run.total_rows ?? "—"}</td>
                    <td>{run.imported ?? "—"}</td>
                    <td>{run.skipped ?? "—"}</td>
                    <td>{run.rejected ?? "—"}</td>
                    <td>
                      <button type="button" onClick={() => openRun(run.id)} aria-label={`Open report of import ${run.id}`}>
                        Open report
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            offset={offset}
            limit={PAGE_SIZE}
            shown={runs.data.items.length}
            total={runs.data.total}
            onChange={setOffset}
          />
        </>
      )}
    </section>
  );
}

function sum(counts: Record<string, number>): number {
  return Object.values(counts).reduce((total, count) => total + count, 0);
}

export function ImportReportView({ report }: { report: ImportReport }) {
  return (
    <>
      {report.status === "failed" && (
        <p role="alert" className="message message-error">
          The import failed part-way: {report.error}. Rows from earlier chunks were saved; importing the same file
          again finishes the job.
        </p>
      )}
      <ul className="headline">
        <li>
          <strong>{report.total_rows}</strong> rows
        </li>
        <li>
          <strong>{report.imported}</strong> imported
        </li>
        <li>
          <strong>{sum(report.skipped)}</strong> skipped
        </li>
        <li>
          <strong>{sum(report.rejected)}</strong> rejected
        </li>
        <li>
          <strong>{report.blank_lines}</strong> blank lines ignored
        </li>
      </ul>
      {report.previous_runs_with_same_file.length > 0 && (
        <p className="note">
          The same file was imported before in runs {report.previous_runs_with_same_file.join(", ")}.
        </p>
      )}

      <div className="breakdowns">
        <CountTable title="Skipped" counts={report.skipped} />
        <CountTable title="Rejected" counts={report.rejected} />
        <CountTable title="Normalised" counts={report.normalised} />
      </div>

      <h3>Problems</h3>
      {report.problems.length === 0 ? (
        <p>No problems.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Line</th>
                <th scope="col">Episode</th>
                <th scope="col">Reason</th>
                <th scope="col">Value</th>
              </tr>
            </thead>
            <tbody>
              {report.problems.map((problem, index) => (
                <tr key={index}>
                  <td>{problem.line}</td>
                  <td>{problem.episode_id ?? "—"}</td>
                  <td>{problem.reason}</td>
                  <td>{problemValue(problem)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {report.problems_truncated && (
        <p className="note">Only the first {report.problems.length} problems are listed; the counts above include all.</p>
      )}
    </>
  );
}

/** Reason and count, leaving out reasons that did not happen. */
function CountTable({ title, counts }: { title: string; counts: Record<string, number> }) {
  const rows = Object.entries(counts).filter(([, count]) => count > 0);
  if (rows.length === 0) {
    return null;
  }
  return (
    <table>
      <caption>{title}</caption>
      <thead>
        <tr>
          <th scope="col">Reason</th>
          <th scope="col">Rows</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([reason, count]) => (
          <tr key={reason}>
            <td>{reason}</td>
            <td>{count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function problemValue(problem: ImportProblem): string {
  if (problem.fields) {
    // A conflict: each field that differs between the file and the database.
    return Object.entries(problem.fields)
      .map(([field, { file, database }]) => `${field}: file ${String(file)}, database ${String(database)}`)
      .join("; ");
  }
  return problem.value === "" ? "(blank)" : (problem.value ?? "");
}
