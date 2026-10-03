import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";
import { ADMIN, CLIENT, OPERATOR, renderApp } from "./test-utils";

async function mainNav() {
  return within(await screen.findByRole("navigation", { name: "Main" }));
}

describe("login", () => {
  it("shows the server's message on a wrong password", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new ApiError(401, "Invalid email or password"));
    renderApp("/login", null);
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Email"), "client-a@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Invalid email or password");
  });

  it("goes to the requests page after logging in", async () => {
    vi.spyOn(api, "login").mockResolvedValue(CLIENT);
    vi.spyOn(api, "listRequests").mockResolvedValue([]);
    renderApp("/login", null);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Use client-a@example.com" }));
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByRole("heading", { name: "My requests" })).toBeTruthy();
    expect(api.login).toHaveBeenCalledWith("client-a@example.com", "client123");
  });

  it("sends a visitor who is not logged in to the login page", async () => {
    renderApp("/requests", null);

    expect(await screen.findByRole("button", { name: "Log in" })).toBeTruthy();
  });
});

describe("navigation and role guards", () => {
  it("a client sees only their own links", async () => {
    vi.spyOn(api, "listRequests").mockResolvedValue([]);
    renderApp("/requests", CLIENT);

    const nav = await mainNav();
    expect(nav.getByRole("link", { name: "My requests" })).toBeTruthy();
    expect(nav.getByRole("link", { name: "New request" })).toBeTruthy();
    for (const hidden of ["Imports", "Analytics", "Users"]) {
      expect(nav.queryByRole("link", { name: hidden })).toBeNull();
    }
  });

  it("a client who opens /users sees the not-allowed page", async () => {
    vi.spyOn(api, "listUsers");
    renderApp("/users", CLIENT);

    expect(await screen.findByRole("heading", { name: "Not allowed" })).toBeTruthy();
    expect(api.listUsers).not.toHaveBeenCalled();
  });

  it("an operator sees Imports and Analytics but not Users", async () => {
    vi.spyOn(api, "listRequests").mockResolvedValue([]);
    renderApp("/requests", OPERATOR);

    const nav = await mainNav();
    expect(nav.getByRole("link", { name: "Imports" })).toBeTruthy();
    expect(nav.getByRole("link", { name: "Analytics" })).toBeTruthy();
    expect(nav.queryByRole("link", { name: "Users" })).toBeNull();
  });

  it("an admin also sees Users", async () => {
    vi.spyOn(api, "listRequests").mockResolvedValue([]);
    renderApp("/requests", ADMIN);

    expect((await mainNav()).getByRole("link", { name: "Users" })).toBeTruthy();
  });
});
