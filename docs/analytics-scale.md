# Analytics at volume

How the three queries behind `GET /analytics` behave on 200 000 and
2 000 000 episodes, which indexes they use, what was changed because of it,
and what I would do at 5 and 50 million episodes.

## How this was measured

- **Data:** episodes from `seed/generate_episodes.py` (clean, spread evenly over
  one year, 2025-09-01 to 2026-09-01, five robots, seven tasks). 200 000 rows
  were loaded through the importer, 2 000 000 with `\copy`. Plus 20 000
  synthetic requests (one every 26 minutes over the same year) with 60 000
  history rows: 70% delivered, a tenth of those through a rework loop.
- **Server:** PostgreSQL 17.7 on a Windows laptop, default configuration
  (`shared_buffers` 128 MB, `work_mem` 4 MB, 2 parallel workers per query).
  Production runs PostgreSQL 16 in Docker; the plans should be the same, the
  absolute times will not be.
- **Method:** `EXPLAIN (ANALYZE, BUFFERS)` of each query exactly as written in
  `backend/app/analytics.py`, after `VACUUM ANALYZE`, warm cache (one run
  discarded), **median of 7 runs**. Single runs on this laptop vary by up to
  ±30%, so treat the numbers as orders of magnitude.
- **Ranges:** 30 days (2026-03-01 to 2026-03-30) and 365 days (the whole year).
- **Caveat:** the generator writes rows in random time order (physical
  correlation of `recorded_at`: 0.006). Real exports arrive roughly in time
  order, which makes range scans cheaper (measured below). These numbers are
  the pessimistic case.

## The three queries

```sql
-- 1. episodes_per_day (sparse: only (day, robot) pairs that have episodes)
SELECT (recorded_at AT TIME ZONE 'UTC')::date AS day, robot_id, count(*) AS episodes
FROM episodes
WHERE recorded_at >= :start AND recorded_at < :end
GROUP BY day, robot_id
ORDER BY day, robot_id;

-- 2. requests_summary (requests created in the range)
WITH scoped AS (
    SELECT id, status
    FROM requests
    WHERE created_at >= :start AND created_at < :end
),
milestones AS (
    SELECT history.request_id,
           min(history.changed_at) FILTER (WHERE history.to_status = 'submitted') AS submitted_at,
           min(history.changed_at) FILTER (WHERE history.to_status = 'delivered') AS first_delivered_at
    FROM request_status_history AS history
    JOIN scoped ON scoped.id = history.request_id
    GROUP BY history.request_id
)
SELECT count(*) FILTER (WHERE scoped.status = 'submitted') AS submitted,
       count(*) FILTER (WHERE scoped.status = 'in_progress') AS in_progress,
       count(*) FILTER (WHERE scoped.status = 'delivered') AS delivered,
       count(*) FILTER (WHERE scoped.status = 'accepted') AS accepted,
       count(*) FILTER (WHERE scoped.status = 'rejected') AS rejected,
       count(*) AS total,
       count(milestones.first_delivered_at) AS delivered_count,
       round(
           extract(epoch FROM percentile_cont(0.5) WITHIN GROUP (
               ORDER BY milestones.first_delivered_at - milestones.submitted_at
           )) / 3600,
           2
       ) AS median_hours
FROM scoped
LEFT JOIN milestones ON milestones.request_id = scoped.id;

-- 3. top_tasks
SELECT task_name, count(*) AS good_episodes
FROM episodes
WHERE quality = 'good' AND recorded_at >= :start AND recorded_at < :end
GROUP BY task_name
ORDER BY good_episodes DESC, task_name
LIMIT 5;
```

`:start` and `:end` are a half-open UTC range. `recorded_at` and `created_at`
are compared bare (no function around them), so their indexes can be used.
One request runs exactly these three statements (plus one to load the
logged-in user), whatever the size of the data; a test counts them.

## Results (median of 7 runs, with the statistics described below)

| Query | 200k, 30 days | 200k, 365 days | 2M, 30 days | 2M, 365 days |
|---|---|---|---|---|
| episodes_per_day | 7.4 ms | 72 ms | 151 ms | 270 ms |
| requests_summary | 9.6 ms | 41 ms | 11 ms | 51 ms |
| top_tasks | 6.5 ms | 59 ms | 144 ms | 175 ms |

Rows read: a 30-day range is about 16 400 episodes at 200k and 164 000 at 2M;
365 days is the whole table. Everything aggregates in memory (hash aggregate),
nothing spills to disk.

### Which indexes are used

| Query | 30 days | 365 days |
|---|---|---|
| episodes_per_day | Bitmap Index Scan on `ix_episodes_recorded_at`, then bitmap heap scan (parallel at 2M) | Parallel Seq Scan |
| top_tasks | Bitmap Index Scan on `ix_episodes_recorded_at`, then heap scan filtering `quality = 'good'` | Parallel Seq Scan |
| requests_summary | Seq Scan on `requests` and on `request_status_history`, hash joins | same |

- A 365-day range covers the whole table, so a sequential scan is the right
  plan, not a missing index.
- `ix_episodes_task_name_good` (the partial index from the first migration) is
  **not used** by `top_tasks`: the time range is the selective condition, and
  that index has no time column. It would only help an all-time top-tasks query.
- `ix_episodes_robot_id_recorded_at` (73 MB at 2M) is not used by analytics
  either. Both are worth reviewing (they cost space and insert time), but I
  left them alone in this step: other queries may want them later.
- `requests` has no index usable for `created_at` alone
  (`ix_requests_status_created_at` starts with `status`). With 20 000
  requests the sequential scan takes about 2 ms, so no index was added.
  Requests grow far more slowly than episodes; at a few hundred thousand,
  an index on `requests (created_at)` would be the first thing to add.

## What was changed: expression statistics (migration `8802d98bd10a`)

The first plans showed `episodes_per_day` sorting every row in the range and
spilling the sort to disk. Postgres keeps no statistics on the expression
`(recorded_at AT TIME ZONE 'UTC')::date`, so it estimated **140 378 groups for
150 real ones** (30 days, 2M), chose a sort-based aggregate, and the sort did
not fit in 4 MB of `work_mem`.

An index would not fix a wrong estimate. Extended statistics on the expression
do: `CREATE STATISTICS st_episodes_utc_day_robot (ndistinct) ON ((recorded_at AT
TIME ZONE 'UTC')::date), robot_id FROM episodes`. With it, the estimate is 1 830
groups, the plan becomes an in-memory hash aggregate, and nothing touches disk.
It is not an index: nothing extra is written per insert; `ANALYZE` maintains it.

| episodes_per_day | without statistics | with statistics |
|---|---|---|
| 200k, 30 days | 12.0 ms (sort, in memory) | 7.4 ms (hash) |
| 200k, 365 days | 165 ms (sort, **spills to disk**) | 72 ms (hash) |
| 2M, 30 days | 254 ms (sort, **spills to disk**) | 151 ms (hash) |
| 2M, 365 days | 662 ms (sort, **spills to disk**) | 270 ms (hash) |

A test checks the statistics object exists after `alembic upgrade head`.

## Tried, measured, not added

| Option | Effect at 2M (30 days) | Why not now |
|---|---|---|
| Covering index `(recorded_at) INCLUDE (robot_id, task_name, quality)` | episodes_per_day 151 -> 60 ms, top_tasks 144 -> 72 ms (index-only scans); 365 days unchanged | 107 MB of extra index and slower inserts, for queries that already answer in about 150 ms |
| Same 2M rows stored in time order, B-tree on `recorded_at` | episodes_per_day 97 ms, top_tasks 81 ms | (shows how much physical order matters) |
| Same, with a **BRIN** index instead of the B-tree | episodes_per_day 95 ms, top_tasks 73 ms | BRIN is 24 kB against 25 MB for the B-tree, for the same speed, but only on time-ordered data |

## Extrapolation to 5 million episodes

From 200k to 2M (10x rows), the 365-day queries grew about 3 to 4x thanks to
parallel workers. The 30-day queries grew about 20x for 10x more rows in the
window: with rows in random physical order, the window's rows are scattered
over most of the table's pages, so at 2M the bitmap heap scan reads most of
the table anyway.

At 5M episodes spread over one year (2.5x the 2M case, still the pessimistic
random-order layout), a 30-day range approaches the cost of a full scan:
roughly **0.3 to 0.6 s** for a 30-day range and **0.4 to 0.7 s** for a full
year, per query, with the plans above. The requests query depends on the
number of requests, not episodes, and stays around 10 to 50 ms. If the
5M episodes span several years instead, a 30-day window holds fewer rows and
is faster; a 365-day range still reads about a year of data.

So at 5M the endpoint keeps working with the current design, answering in
roughly a second for the worst range, and every query still runs in the
database. The next steps below are about keeping it fast as data keeps growing.

## What I would do at 5M and at 50M episodes

**At 5M:**

1. **Keep the half-open, unwrapped ranges.** They are what lets every option
   below work; wrapping `recorded_at` in a function in WHERE would force a
   full scan whatever the index.
2. **Consider BRIN on `recorded_at`** once the real data is confirmed to arrive
   in time order (check `pg_stats.correlation` for `recorded_at`, close to 1.0).
   Measured above: same speed as the B-tree on ordered data at about 1/1000 of
   the size, and almost free to maintain on insert. If imports often backfill
   old dates, the correlation drops and BRIN stops helping; keep the B-tree then.
3. **A covering index** if 30-day ranges are the common case and their latency
   matters (measured above: about 2.5x faster at 2M).
4. **Review unused indexes** (`ix_episodes_robot_id_recorded_at`,
   `ix_episodes_task_name_good`) with `pg_stat_user_indexes.idx_scan` on
   production; drop the ones nothing uses, to speed up imports.

**At 50M:**

5. **A pre-aggregated daily table**, `episode_daily_counts (day, robot_id,
   task_name, quality, count)`. It has at most a few thousand rows per year, so
   every analytics query on it takes milliseconds whatever the range.
   `episodes_per_day` sums it by day and robot; `top_tasks` sums it by task
   where quality is good. Maintained by the import job in the same transaction
   as each chunk (it already knows which rows it inserted), or rebuilt for the
   affected days on a schedule. The raw `episodes` table stays the source of
   truth, and the daily table can always be rebuilt from it.
6. **Monthly partitioning of `episodes` by `recorded_at`.** A 30-day range
   then touches one or two partitions (partition pruning), old months can be
   moved or archived without long deletes, and per-partition indexes stay
   small. It works best together with the daily table, which serves the long
   ranges.
7. **A materialized view for top tasks** if the daily table is not built: for
   example good episodes per (day, task), refreshed after each import with
   `REFRESH MATERIALIZED VIEW CONCURRENTLY`. A simpler, coarser version of
   point 5, for one query.

**Why a cache comes last.** A response cache (in the app or in front of it)
hides slow queries instead of fixing them. Its hit rate is poor for an
endpoint whose input is an arbitrary date range. It would serve stale numbers
right after an import or a status change, and it would need a cache server,
which this deployment deliberately does not have (three containers: db, api,
web). The steps above make the query itself fast, so every answer is current.
A cache only makes sense after them, for something like a dashboard that many
people open with the same default range.
