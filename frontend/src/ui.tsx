// Small pieces shared by the pages: status badges, messages, dates, pagination,
// and one hook for loading data.

import { useEffect, useState } from "react";
import { ApiError, type Status } from "./api";

export const STATUS_LABELS: Record<Status, string> = {
  submitted: "Submitted",
  in_progress: "In progress",
  delivered: "Delivered",
  accepted: "Accepted",
  rejected: "Rejected",
};

/** Coloured, but always with the text too: colour alone is not enough. */
export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge badge-${status}`}>{STATUS_LABELS[status]}</span>;
}

/** What to tell the user about a failed call: the server's own message when there is one. */
export function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  return "Could not reach the server. Check your connection and try again.";
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export function ErrorMessage({ message }: { message: string | null }) {
  return message ? (
    <p role="alert" className="message message-error">
      {message}
    </p>
  ) : null;
}

export function SuccessMessage({ message }: { message: string | null }) {
  return message ? (
    <p role="status" className="message message-success">
      {message}
    </p>
  ) : null;
}

/** "2026-09-01 10:00 UTC". The API sends timestamps with an offset; we always show UTC. */
export function formatTimestamp(iso: string): string {
  return `${new Date(iso).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** Today's date in UTC, as YYYY-MM-DD (the server compares deadlines with today in UTC). */
export function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

interface PaginationProps {
  offset: number;
  limit: number;
  shown: number; // rows on this page
  total?: number; // when the API tells us
  onChange: (offset: number) => void;
}

export function Pagination({ offset, limit, shown, total, onChange }: PaginationProps) {
  const hasNext = total === undefined ? shown === limit : offset + shown < total;
  const range = shown === 0 ? "No rows" : `Rows ${offset + 1}–${offset + shown}${total === undefined ? "" : ` of ${total}`}`;
  return (
    <nav className="pagination" aria-label="Pages">
      <button type="button" onClick={() => onChange(Math.max(0, offset - limit))} disabled={offset === 0}>
        Previous
      </button>
      <span>{range}</span>
      <button type="button" onClick={() => onChange(offset + limit)} disabled={!hasNext}>
        Next
      </button>
    </nav>
  );
}

/**
 * Load data when the inputs in `deps` change. Returns the data, the error (if any),
 * whether it is loading, a way to replace the data, and reload().
 * A tiny hook instead of a data-fetching library.
 */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let current = true; // an answer for old inputs must not overwrite a newer one
    setLoading(true);
    setError(null);
    load()
      .then((result) => current && setData(result))
      .catch((failure) => current && setError(failure))
      .finally(() => current && setLoading(false));
    return () => {
      current = false;
    };
  }, [...deps, version]); // the caller lists the inputs that should trigger a reload

  return { data, setData, error, loading, reload: () => setVersion((v) => v + 1) };
}
