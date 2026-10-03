import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError, type RequestDetail, type User } from "../api";
import { CLIENT, EMPTY_PAGE, OPERATOR, renderApp, requestDetail } from "../test-utils";

const ACTION_NAMES = /work|deliver|accept|reject/i;

function openDetail(user: User, detail: RequestDetail) {
  vi.spyOn(api, "getRequest").mockResolvedValue(detail);
  vi.spyOn(api, "requestEpisodes").mockResolvedValue(EMPTY_PAGE);
  vi.spyOn(api, "listEpisodes").mockResolvedValue(EMPTY_PAGE);
  renderApp(`/requests/${detail.id}`, user);
}

function actionButtonNames(): string[] {
  return screen
    .queryAllByRole("button")
    .map((button) => button.textContent ?? "")
    .filter((name) => ACTION_NAMES.test(name));
}

describe("action buttons come only from available_transitions", () => {
  it("a client sees Accept and Reject on a delivered request", async () => {
    openDetail(CLIENT, requestDetail({ status: "delivered", available_transitions: ["accepted", "rejected"] }));

    await screen.findByRole("heading", { name: /Request #7/ });
    expect(actionButtonNames()).toEqual(["Accept delivery", "Reject delivery"]);
  });

  it("an operator sees Mark delivered on an in-progress request", async () => {
    openDetail(
      OPERATOR,
      requestDetail({ status: "in_progress", assigned_count: 5, available_transitions: ["delivered"] }),
    );

    await screen.findByRole("heading", { name: /Request #7/ });
    expect(actionButtonNames()).toEqual(["Mark delivered"]);
  });

  it("a client sees no action on a submitted request", async () => {
    openDetail(CLIENT, requestDetail({ status: "submitted", available_transitions: [] }));

    await screen.findByRole("heading", { name: /Request #7/ });
    expect(actionButtonNames()).toEqual([]);
  });

  it("shows whatever the server lists, even where the UI would not expect it", async () => {
    // The UI holds no rules of its own: if the server says "accepted", the button is there.
    openDetail(OPERATOR, requestDetail({ status: "delivered", available_transitions: ["accepted"] }));

    await screen.findByRole("heading", { name: /Request #7/ });
    expect(actionButtonNames()).toEqual(["Accept delivery"]);
  });
});

describe("Mark delivered", () => {
  it("is disabled with a hint while fewer episodes are assigned than requested", async () => {
    openDetail(
      OPERATOR,
      requestDetail({ status: "in_progress", assigned_count: 2, episodes_requested: 5, available_transitions: ["delivered"] }),
    );

    const button = (await screen.findByRole("button", { name: "Mark delivered" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("Assign 3 more episodes first")).toBeTruthy();
  });
});

describe("accepting a delivery", () => {
  it("asks for confirmation before calling the API", async () => {
    openDetail(CLIENT, requestDetail({ status: "delivered", available_transitions: ["accepted", "rejected"] }));
    const transition = vi
      .spyOn(api, "transition")
      .mockResolvedValue(requestDetail({ status: "accepted", available_transitions: [] }));
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Accept delivery" }));
    expect(transition).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Yes, accept" }));
    expect(transition).toHaveBeenCalledWith(7, "accepted");
    expect(await screen.findByText("Accepted", { selector: ".badge" })).toBeTruthy();
  });

  it("shows the server's message if the change is refused", async () => {
    openDetail(OPERATOR, requestDetail({ status: "in_progress", assigned_count: 5, available_transitions: ["delivered"] }));
    vi.spyOn(api, "transition").mockRejectedValue(new ApiError(409, "Cannot deliver: 4 of 5 episodes assigned"));
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Mark delivered" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Cannot deliver: 4 of 5 episodes assigned");
  });
});
