import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  try {
    window.localStorage.clear();
  } catch {
    // Storage may be unavailable; nothing to clear.
  }
  window.history.replaceState(null, "", "/");
});
