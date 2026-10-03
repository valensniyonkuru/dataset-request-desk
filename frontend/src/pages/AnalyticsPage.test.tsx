import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Analytics } from "../api";
import { AnalyticsSections, pivotEpisodesPerDay } from "./AnalyticsPage";

describe("pivotEpisodesPerDay", () => {
  it("lists every day of the range and every robot, with 0 where the API had no row", () => {
    const rows = [
      { date: "2026-09-01", robot_id: "arm-02", count: 2 },
      { date: "2026-09-03", robot_id: "arm-01", count: 1 },
    ];

    expect(pivotEpisodesPerDay(rows, "2026-09-01", "2026-09-03")).toEqual({
      robots: ["arm-01", "arm-02"],
      days: [
        { date: "2026-09-01", counts: [0, 2] },
        { date: "2026-09-02", counts: [0, 0] }, // no row at all for this day
        { date: "2026-09-03", counts: [1, 0] },
      ],
    });
  });

  it("crosses month ends in UTC", () => {
    const { days } = pivotEpisodesPerDay([], "2026-02-27", "2026-03-02");

    expect(days.map((day) => day.date)).toEqual(["2026-02-27", "2026-02-28", "2026-03-01", "2026-03-02"]);
  });
});

describe("request fulfilment", () => {
  it("says so when nothing was delivered", () => {
    const analytics: Analytics = {
      episodes_per_day: [],
      requests: {
        by_status: { submitted: 1, in_progress: 2, delivered: 0, accepted: 0, rejected: 0 },
        total: 3,
        delivered_count: 0,
        median_hours_submitted_to_delivered: null,
      },
      top_tasks: [],
      meta: { from: "2026-09-01", to: "2026-09-30", days: 30, generated_at: "2026-10-03T09:00:00Z" },
    };

    render(<AnalyticsSections analytics={analytics} />);

    expect(screen.getByText("no deliveries in this range")).toBeTruthy();
  });
});
