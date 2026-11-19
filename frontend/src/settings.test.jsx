import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import SettingsTab from "./components/SettingsTab.jsx";
import { mockFetch, setToken } from "./test-utils.js";

describe("settings", () => {
  it("loads settings, saves them and never writes a key to localStorage", async () => {
    const { calls } = mockFetch([
      {
        match: "/v1/settings",
        method: "GET",
        body: {
          mode: "offline",
          model: "",
          endpoint: "",
          provider: "none",
          polish_handoffs: false,
          has_api_key: true,
        },
      },
      { match: "/v1/settings", method: "POST", body: { ok: true } },
    ]);
    setToken("session-token-value");
    render(<SettingsTab />);

    expect(await screen.findByText("Works without a model")).toBeInTheDocument();
    // has_api_key indicator without exposing anything.
    expect(screen.getByText("configured on this machine")).toBeInTheDocument();
    expect(screen.getByText(/A newly typed key passes through this form to reach the system keychain/)).toBeInTheDocument();
    expect(screen.queryByText(/never in this browser|never exposes a key|no field for it here/i)).not.toBeInTheDocument();

    // No API key field exists at all — only session token wording.
    expect(screen.queryByRole("textbox", { name: /api key/i })).not.toBeInTheDocument();
    expect(document.querySelector('input[type="password"]')).toBeNull();

    fireEvent.change(screen.getByLabelText("Exact model name"), {
      target: { value: "llama3.2" },
    });
    fireEvent.change(screen.getByLabelText(/Endpoint/), {
      target: { value: "http://localhost:11434" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    const save = await waitFor(() => {
      const found = calls.find((call) => call.url === "/v1/settings" && call.method === "POST");
      expect(found).toBeTruthy();
      return found;
    });
    expect(save.body.mode).toBe("off");
    expect(save.body.model).toBe("llama3.2");
    expect(save.body.endpoint).toBe("http://localhost:11434");
    expect(save.body).not.toHaveProperty("api_key");
    expect(await screen.findByText("Saved.")).toBeInTheDocument();

    // localStorage holds only the session token — no provider key, ever.
    expect(Object.keys(window.localStorage)).toEqual(["cb_token"]);
    expect(window.localStorage.getItem("cb_token")).toBe("session-token-value");
    expect(window.localStorage.getItem("cb_api_key")).toBeNull();
    expect(window.localStorage.getItem("api_key")).toBeNull();
  });

  it("shows clear connection-test errors", async () => {
    mockFetch([
      { match: "/v1/settings", method: "GET", body: { mode: "hosted", model: "", endpoint: "", provider: "openai-compatible", polish_handoffs: false, has_api_key: false } },
      { match: "/v1/settings/test", method: "POST", status: 400, body: { detail: "Enter the model name to check." } },
    ]);
    setToken();
    render(<SettingsTab />);

    await screen.findByText("Works without a model");
    expect(screen.getByLabelText(/Provider API key \(leave blank/)).toBeInTheDocument();
    expect(screen.getByText(/Saved keys are never sent back to the browser or stored in browser storage/)).toBeInTheDocument();
    expect(screen.getByText(/A newly typed key passes through this form/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText("Enter the model name to check.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled();
  });

  it("distinguishes the session token from the provider key", async () => {
    mockFetch([
      { match: "/v1/settings", method: "GET", body: { mode: "ollama", model: "llama3.2", endpoint: "http://localhost:11434", provider: "ollama", polish_handoffs: false, has_api_key: false } },
    ]);
    setToken();
    render(<SettingsTab />);

    await screen.findByText("Session token vs provider key");
    expect(screen.getByText(/Session token \(this dashboard\)/)).toBeInTheDocument();
    expect(screen.getByText(/Provider API key \(the model\)/)).toBeInTheDocument();
    expect(screen.getByText(/#token=/)).toBeInTheDocument();
    // Mode restored from the response.
    expect(screen.getByRole("radio", { name: /Ollama — local model/ })).toBeChecked();
    expect(screen.getByLabelText("Exact model name")).toHaveValue("llama3.2");
  });
});
