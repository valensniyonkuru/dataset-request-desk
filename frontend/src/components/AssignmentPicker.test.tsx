import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api, type EpisodeListItem } from "../api";
import { requestDetail } from "../test-utils";
import AssignmentPicker from "./AssignmentPicker";

function episode(n: number): EpisodeListItem {
  return {
    episode_id: `EP-${n}`,
    robot_id: "arm-01",
    task_name: "pick cup",
    recorded_at: "2026-09-01T10:00:00Z",
    duration_seconds: 30,
    operator_name: "Aline",
    quality: "good",
    import_run_id: 1,
    created_at: "2026-09-02T10:00:00Z",
    assigned_request_id: null,
  };
}

function renderPicker() {
  // 3 requested, 1 already assigned: 2 more allowed.
  const request = requestDetail({ status: "in_progress", episodes_requested: 3, assigned_count: 1 });
  vi.spyOn(api, "listEpisodes").mockResolvedValue({
    items: [episode(1), episode(2), episode(3), episode(4)],
    total: 4,
    limit: 20,
    offset: 0,
  });
  render(<AssignmentPicker request={request} onAssigned={vi.fn()} />);
}

describe("assignment picker", () => {
  it("counts the selection against what is still allowed and disables Assign when it is too much", async () => {
    renderPicker();
    const user = userEvent.setup();
    const assign = screen.getByRole("button", { name: "Assign selected" }) as HTMLButtonElement;
    const counter = () => screen.getByRole("status").textContent;

    await screen.findByLabelText("Select EP-1");
    expect(counter()).toBe("0 selected, 2 more allowed");
    expect(assign.disabled).toBe(true); // nothing selected

    await user.click(screen.getByLabelText("Select EP-1"));
    await user.click(screen.getByLabelText("Select EP-2"));
    expect(counter()).toBe("2 selected, 0 more allowed");
    expect(assign.disabled).toBe(false);

    await user.click(screen.getByLabelText("Select EP-3"));
    expect(counter()).toBe("3 selected, 1 too many");
    expect(assign.disabled).toBe(true);
  });

  it("selects every episode on the page with one checkbox", async () => {
    renderPicker();
    const user = userEvent.setup();

    await user.click(await screen.findByLabelText("Select all on this page"));

    expect(screen.getByRole("status").textContent).toBe("4 selected, 2 too many");
  });

  it("does not offer the bad quality", () => {
    renderPicker();

    const options = within(screen.getByLabelText("Quality")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["Good", "Usable", "Good or usable"]);
  });

  it("asks only for unassigned episodes, prefilled with the request's task", async () => {
    renderPicker();
    await screen.findByLabelText("Select EP-1");

    expect(api.listEpisodes).toHaveBeenCalledWith(
      expect.objectContaining({ task_name: "pick cup", quality: "good", assigned: false }),
    );
  });
});
