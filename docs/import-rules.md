# Episode CSV import: findings and rules

The importer (`backend/app/importer.py`) follows the rules below. Every row of
a file ends up in exactly one bucket, so for every import:

    total_rows == imported + sum(skipped) + sum(rejected)

`total_rows` counts data rows: not the header, not blank lines.

## (a) What `seed/episodes.csv` contains

Analysed byte by byte (the committed file, as CI and the Docker image see it).
Line numbers are physical lines; line 1 is the header.

| Problem | Rows | Lines and values |
|---|---|---|
| Exact duplicate rows | 2 | 50 repeats 38 (`EP-00074`); 91 repeats 37 (`EP-00030`) |
| Same id, different content | 2 | 168 vs 3 (`EP-00011`, quality `good` vs `bad`); 189 vs 9 (`ep-00003` vs `EP-00003`, only equal once uppercased; robot, task, time, duration and operator differ) |
| Blank `episode_id` | 1 | 59 |
| Blank `robot_id` | 1 | 170 |
| Blank `task_name` | 0 | |
| Blank `recorded_at` | 0 | |
| Blank `duration_seconds` | 1 | 95 |
| Blank `operator_name` | 1 | 190 |
| Blank `quality` | 1 | 69 |
| Whitespace / casing in `episode_id` | 1 | 189 `ep-00003` (lowercase) |
| Whitespace / casing in `robot_id` | 1 | 32 ` arm-01` (leading space) |
| Whitespace / casing in `task_name` | 2 | 158 `  Pick Cup ` (spaces and capitals), 163 `PICK CUP` |
| Whitespace / casing in `quality` | 2 | 65 `Good`, 133 `USABLE` |
| Whitespace / casing in `operator_name` | 0 | |
| `recorded_at` as `YYYY-MM-DDTHH:MM:SS` | 184 | the normal format |
| `recorded_at` as `YYYY-MM-DD HH:MM:SS` | 1 | 35 |
| `recorded_at` as `DD/MM/YYYY HH:MM` | 1 | 29 `14/08/2026 09:15` |
| `recorded_at` as `YYYY-MM-DDTHH:MM:SSZ` | 1 | 127 `2026-08-14T09:20:00Z` **(not covered by the brief's rules)** |
| `recorded_at` not a date | 1 | 131 `not a date` |
| `recorded_at` in the future | 1 | 113 `2031-01-01T00:00:00` (all others are 2026-08-01 to 2026-09-15) |
| `duration_seconds` decimal | 1 | 66 `45.5` |
| `duration_seconds` negative | 1 | 100 `-5` |
| `duration_seconds` text | 1 | 187 `N/A` |
| `duration_seconds` zero, or decimal like `45.0` | 0 | |
| `duration_seconds` implausibly long | 1 | 188 `999999` (11.5 days; every other value is 9 to 120) **(not covered by the brief's rules)** |
| Invalid `quality` | 1 | 79 `excellent` |
| Unknown robot | 1 | 162 `arm-99` |
| Wrong number of columns | 1 | 185 (5 columns instead of 7) |
| Empty line | 1 | 191 |
| Line containing only spaces | 1 | 192 (two spaces) **(not covered by the brief's rules)** |
| Quoted field containing a comma | 1 | 186 `"pick cup, then place"`: valid CSV, read as one field |
| BOM, non-ASCII bytes, `\r` line endings | 0 | (a Windows checkout may have CRLF; the importer accepts both) |

Expected result of importing it into an empty database: **189 data rows,
171 imported, 4 skipped (`duplicate_in_file`), 14 rejected**, 2 blank lines.

## (b) Rules

### Normalised (the row is imported; counted in `report.normalised` by kind)

| Column | Rule | Kind counted when the value changed |
|---|---|---|
| `episode_id` | trim, uppercase | `episode_id` |
| `robot_id` | trim, lowercase | `robot_id` |
| `task_name` | trim, collapse inner whitespace to one space, lowercase | `task_name` |
| `quality` | trim, lowercase | `quality` |
| `operator_name` | trim, collapse inner whitespace; all-lowercase or all-uppercase values are title-cased (`DIANE` -> `Diane`), mixed case is kept (`McDonald`) | `operator_name` |
| `duration_seconds` | trim; whole numbers; a decimal with a zero fraction (`45.0`) becomes `45` | `duration_seconds` |
| `recorded_at` | accepted formats, all read as **UTC**: `YYYY-MM-DDTHH:MM:SS` (normal, not counted), `YYYY-MM-DDTHH:MM:SSZ`, `YYYY-MM-DD HH:MM:SS`, `YYYY-MM-DD` (time 00:00) | `recorded_at` |
| `recorded_at` | `DD/MM/YYYY HH:MM`, `DD/MM/YYYY HH:MM:SS`, `DD/MM/YYYY`: **day first** | `date_assumed_day_first` |

Normalisation counts describe the file: they count every row that passed
validation (including later duplicates), so re-importing a file reports the
same numbers.

### Rejected (not imported; counted in `report.rejected`; listed in `report.problems`)

Checked in this order; a row is rejected for the **first** problem found.

| Reason | When |
|---|---|
| `malformed_row` | the number of columns differs from the header |
| `missing_episode_id`, `missing_robot`, `missing_task`, `missing_timestamp`, `missing_duration`, `missing_operator`, `missing_quality` | the value is blank after trimming (checked in column order) |
| `unknown_robot` | not one of `arm-01`, `arm-02`, `arm-03`, `mobile-01`, `humanoid-01` (`KNOWN_ROBOTS` in `app/config.py`) |
| `invalid_timestamp` | none of the formats above |
| `future_timestamp` | more than 1 day after the import started |
| `invalid_duration` | not a whole number (or `N.0`), or less than or equal to 0 |
| `duration_too_long` | more than 3600 seconds (`MAX_DURATION_SECONDS` in `app/config.py`) |
| `invalid_quality` | not `good`, `usable` or `bad` |

### Skipped (valid, but not inserted; counted in `report.skipped`)

| Reason | When |
|---|---|
| `duplicate_in_file` | an earlier valid row in the same file has the same `episode_id` (first valid occurrence wins; a rejected earlier row does not count). If this copy differs from the first one, it is **also** listed in problems as `conflict_in_file`. |
| `already_exists` | the id is already in the database with identical values |
| `conflict` | the id is already in the database with different values. **The stored row is never overwritten.** Listed in problems with each differing field (file value and database value). |

### File-level rules (the whole file is refused; nothing is saved)

| Problem | Response |
|---|---|
| Larger than 20 MB (upload only; the CLI has no limit) | 413 |
| Empty (no header line) | 422 |
| Not valid UTF-8 (checked for the whole file before anything is saved) | 422 |
| Header missing a required column (names are trimmed and case-insensitive) | 422, listing the missing columns |

Accepted and ignored: a UTF-8 BOM, extra columns, columns in any order, `\r\n`
line endings, and lines with no values at all: empty, only spaces, or only
commas such as `,,,,,,` (spreadsheets often add these at the end). Those are
counted in `blank_lines`, not in `total_rows`.

A CSV syntax error part-way through (for example an unclosed quote or a NUL
byte) stops the import with 422. Chunks committed before that line stay; fix
the file and import it again: they are skipped as `already_exists`.

### Re-importing

Importing the same file again is safe: valid rows are reported as
`already_exists`, nothing is inserted twice, and the report lists the earlier
runs with the same SHA-256 in `previous_runs_with_same_file`.

### Decisions not covered by the brief

- **`Z` suffix** (`2026-08-14T09:20:00Z`): accepted. `Z` means UTC, which is
  how every timestamp is read anyway.
- **Implausible duration** (`999999` s): rejected as `duration_too_long` above
  1 hour. Episodes are short clips (9 to 120 s in the seed data); an 11-day
  clip is a data error, and accepting it would distort duration analytics.
- **Whitespace-only line** at the end of the file: treated as a blank line,
  not as a malformed row.
- **Lines with only commas** (`,,,,,,`): also blank lines. They hold no data,
  and reporting them as `missing_episode_id` would only add noise.
