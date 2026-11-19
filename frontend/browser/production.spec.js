import { test, expect } from "@playwright/test";
import { execFileSync, spawn } from "node:child_process";
import { cpSync, mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { once } from "node:events";
import { fileURLToPath } from "node:url";

if (process.env.CB_CHROME) {
  test.use({ launchOptions: { executablePath: process.env.CB_CHROME } });
}

for (const fixture of ["next_project", "flutter_project"]) {
  test(`production dashboard serves ${fixture}`, async ({ page, request }) => {
    page.setDefaultTimeout(3500);
    const root = mkdtempSync(join(tmpdir(), "cb-browser-"));
    const listener = createServer().listen(0, "127.0.0.1");
    await once(listener, "listening");
    const { port } = listener.address();
    await new Promise((resolve) => listener.close(resolve));
    const repo = fileURLToPath(new URL("../../", import.meta.url));
    cpSync(join(repo, "tests", "fixtures", fixture), root, { recursive: true });
    const git = (...args) => execFileSync("git", args, { cwd: root }).toString().trim();
    git("init", "-q");
    git("add", ".");
    git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "Fixture");
    const cli = process.env.CB_CLI || join(repo, ".venv", "bin", "cb");
    const server = spawn(cli, ["--repo", root, "serve", "--port", String(port)]);
    try {
      let log = "";
      server.stdout.on("data", (chunk) => { log += chunk; });
      const origin = `http://127.0.0.1:${port}`;
      await expect.poll(async () => {
        try { return (await request.get(`${origin}/ui/`)).status(); }
        catch { return 0; }
      }).toBe(200);
      const dashboard = log.match(/Dashboard:\s+(http:\/\/127\.0\.0\.1:\d+\/ui\/#token=[\w-]+)/)?.[1];
      expect(dashboard).toBeTruthy();
      expect((await request.get(`${origin}/v1/dashboard`)).status()).toBe(401);

      const assets = new Map();
      const errors = [];
      page.on("response", (response) => {
        if (response.url().includes("/ui/assets/")) assets.set(response.url().split(".").pop(), response.status());
      });
      page.on("pageerror", (error) => {
        errors.push(error.message);
        console.error(error.stack);
      });
      await page.goto(dashboard);
      await expect(page.getByRole("heading", { name: "Project goal" })).toBeVisible();
      expect(Object.fromEntries(assets)).toMatchObject({ css: 200, js: 200 });
      const headers = { Authorization: `Bearer ${new URLSearchParams(new URL(dashboard).hash.slice(1)).get("token")}` };
      const api = (path, data) => request.post(`${origin}/v1/${path}`, { headers, data });
      await expect(page.getByRole("button", { name: "Edit goal" })).toBeVisible();
      await page.getByRole("button", { name: "Edit goal" }).click();
      await page.getByLabel("Project goal", { exact: true }).fill("Ship a durable browser dashboard");
      await page.getByRole("button", { name: /save goal/i }).click();
      await expect(page.getByText("Ship a durable browser dashboard")).toBeVisible();
      const preview = await api("handoffs/preview", { to_agent: "generic" });
      expect((await preview.json()).payload).toContain("Ship a durable browser dashboard");
      const head = git("rev-parse", "HEAD");
      for (const commit of [false, true]) {
        expect((await (await api("chat-import", { text: "TODO: Check launch", commit })).json()).committed).toBe(commit);
      }
      expect(git("rev-parse", "HEAD")).toBe(head);
      await page.getByRole("button", { name: "Project guide", exact: true }).first().click();
      const node = page.locator(".react-flow__node").first();
      await expect(node).toBeVisible();
      await expect(page.getByText("Not explained yet").first()).toBeVisible();
      const viewport = page.locator(".react-flow__viewport");
      await page.getByRole("button", { name: "Fit View" }).click();
      const zoom = await viewport.evaluate((element) => element.style.transform);
      await page.getByRole("button", { name: "Zoom Out" }).click();
      await expect.poll(() => viewport.evaluate((element) => element.style.transform)).not.toBe(zoom);
      await page.getByRole("button", { name: "Fit View" }).click();
      const position = await node.evaluate((element) => element.style.transform);
      const bounds = await node.boundingBox();
      const x = bounds.x + bounds.width / 2;
      const y = bounds.y + 15;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + 50, y + 40, { steps: 8 });
      await page.mouse.up();
      await expect.poll(() => node.evaluate((element) => element.style.transform)).not.toBe(position);
      await page.getByRole("button", { name: "Inside one file" }).click();
      const graph = await (await request.get(`${origin}/v1/graph?view=module`, { headers })).json();
      await expect(page.locator("#cb-module-select")).toHaveValue(graph.module);
      expect(errors).toEqual([]);
    } finally {
      server.kill();
      if (server.exitCode === null && server.signalCode === null) await once(server, "exit");
      rmSync(root, { recursive: true, force: true });
    }
  });
}
