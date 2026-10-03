import { describe, expect, it, vi } from "vitest";
import { api, detailText, setSessionExpiredHandler } from "./api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("detailText", () => {
  it("turns FastAPI's list-style 422 detail into readable text", () => {
    const body = {
      detail: [
        {
          type: "greater_than_equal",
          loc: ["body", "episodes_requested"],
          msg: "Input should be greater than or equal to 1",
        },
        { type: "value_error", loc: ["body"], msg: "Value error, organisation is required for clients" },
      ],
    };

    expect(detailText(body, 422)).toBe(
      "episodes_requested: Input should be greater than or equal to 1; organisation is required for clients",
    );
  });

  it("keeps a plain string detail as it is", () => {
    expect(detailText({ detail: "Cannot move from submitted to accepted" }, 409)).toBe(
      "Cannot move from submitted to accepted",
    );
  });

  it("falls back to the status when there is no detail", () => {
    expect(detailText(null, 502)).toBe("Request failed (502)");
  });
});

describe("request", () => {
  it("calls /api on the same origin, with the cookie", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, []));
    vi.stubGlobal("fetch", fetchMock);

    await api.listUsers();

    expect(fetchMock).toHaveBeenCalledWith("/api/users", expect.objectContaining({ credentials: "same-origin" }));
  });

  it("sends a list filter as a repeated parameter", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { items: [], total: 0, limit: 20, offset: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.listEpisodes({ quality: ["good", "usable"], assigned: false, limit: 20, offset: 0 });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/episodes?quality=good&quality=usable&assigned=false&limit=20&offset=0");
  });

  it("a 401 from a normal call ends the session", async () => {
    const sessionExpired = vi.fn();
    setSessionExpiredHandler(sessionExpired);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Not authenticated" })));

    await expect(api.listUsers()).rejects.toMatchObject({ status: 401, message: "Not authenticated" });
    expect(sessionExpired).toHaveBeenCalledOnce();
  });

  it("a 401 from login is a wrong password, not an expired session", async () => {
    const sessionExpired = vi.fn();
    setSessionExpiredHandler(sessionExpired);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Invalid email or password" })));

    await expect(api.login("a@example.com", "wrong")).rejects.toMatchObject({ message: "Invalid email or password" });
    expect(sessionExpired).not.toHaveBeenCalled();
  });
});
