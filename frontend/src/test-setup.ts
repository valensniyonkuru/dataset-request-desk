import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup(); // unmount whatever a test rendered
  vi.restoreAllMocks(); // undo every vi.spyOn on the api functions
  vi.unstubAllGlobals(); // undo vi.stubGlobal("fetch", ...)
});
