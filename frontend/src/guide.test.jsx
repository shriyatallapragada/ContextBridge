import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ArchitectureTab from "./components/ArchitectureTab.jsx";
import { mockFetch, setToken } from "./test-utils.js";

// Minimal React Flow stand-in: captures props (so tests can drive
// onNodeClick / onNodesChange) and renders nodes with their real card content.
vi.mock("@xyflow/react", async () => {
  const React = await import("react");

  const applyNodeChanges = (changes, nodes) => {
    let out = nodes;
    for (const change of changes) {
      if (change.type === "position" && change.position) {
        out = out.map((node) =>
          node.id === change.id
            ? { ...node, position: change.position, dragging: change.dragging }
            : node
        );
      } else if (change.type === "select") {
        out = out.map((node) =>
          node.id === change.id ? { ...node, selected: change.selected } : node
        );
      } else if (change.type === "remove") {
        out = out.filter((node) => node.id !== change.id);
      }
    }
    return out;
  };

  const ReactFlow = (props) => {
    globalThis.__rfProps = props;
    return React.createElement(
      "div",
      { "data-testid": "react-flow" },
      props.nodes.map((node) => {
        const Comp = props.nodeTypes?.[node.type];
        return React.createElement(
          "div",
          {
            key: node.id,
            "data-testid": `rf-node-${node.id}`,
            style: {
              position: "absolute",
              transform: `translate(${node.position.x}px, ${node.position.y}px)`,
            },
          },
          Comp
            ? React.createElement(Comp, {
                id: node.id,
                data: node.data,
                selected: Boolean(node.selected),
              })
            : node.data?.label
        );
      }),
      props.edges.map((edge) =>
        React.createElement("div", { key: edge.id, "data-testid": `rf-edge-${edge.id}` }, edge.label ?? "")
      )
    );
  };

  return {
    ReactFlow,
    Handle: () => null,
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    Panel: ({ children }) => React.createElement(React.Fragment, null, children),
    Position: { Left: "left", Right: "right", Top: "top", Bottom: "bottom" },
    MarkerType: { ArrowClosed: "arrowclosed" },
    BackgroundVariant: { Dots: "dots" },
    applyNodeChanges,
    applyEdgeChanges: (_, edges) => edges,
  };
});

const META = {
  shown_nodes: 3,
  total_nodes: 30,
  hidden_nodes: 27,
  file_count: 30,
  available_modules: ["src/server.js", "src/admin.js"],
  entrypoints: ["src/server.js"],
  connections_notice: "No static imports were found between these files.",
  flow_notice: "Possible paths from a starting file, not a running request.",
  entrypoint_support: "package main/bin and index/main files",
};

const OVERVIEW = {
  view: "overview",
  module: null,
  nodes: [
    {
      id: "f1",
      kind: "folder",
      label: "Server",
      path: "server",
      group: "server",
      purpose: "Not explained yet",
      ai_explanation: null,
      file_count: 3,
      explained_files: 0,
      related_files: ["server/a.py", "server/b.py", "server/c.py"],
      depends_on: [],
      used_by: ["ui/app.py: app"],
      routes: [],
    },
    {
      id: "f2",
      kind: "folder",
      label: "UI",
      path: "ui",
      group: "ui",
      purpose: "Renders the dashboard screens",
      ai_explanation: {
        label: "AI-written",
        text: "Coordinates rendering across the dashboard screens with shared components.",
      },
      file_count: 4,
      explained_files: 1,
      related_files: ["ui/app.py", "ui/widgets.py"],
      depends_on: ["server: Server"],
      used_by: ["index.html: entry"],
      routes: [],
    },
  ],
  edges: [{ source: "f1", target: "f2", kind: "imports", count: 1 }],
  meta: META,
};

const MODULE_GRAPH = {
  view: "module",
  module: "src/server.js",
  nodes: [
    {
      id: "n1",
      kind: "file",
      label: "server.js",
      path: "src/server.js",
      group: "src",
      purpose: "Handles incoming requests",
      ai_explanation: { label: "AI-written", text: "Handles incoming requests and routes them." },
      related_files: ["src/app.js"],
      depends_on: ["src/app.js: app"],
      used_by: ["src/index.js: index"],
      routes: ["/api/tasks"],
      file_count: 1,
      explained_files: 1,
    },
    {
      id: "n2",
      kind: "file",
      label: "admin.js",
      path: "src/admin.js",
      group: "src",
      purpose: "Admin screens",
      ai_explanation: null,
      related_files: [],
      depends_on: ["src/server.js: server"],
      used_by: [],
      routes: [],
      file_count: 1,
      explained_files: 1,
    },
  ],
  edges: [{ source: "n1", target: "n2", kind: "imports", count: 1 }],
  meta: META,
};

const ADMIN_GRAPH = {
  ...MODULE_GRAPH,
  module: "src/admin.js",
  nodes: [
    { ...MODULE_GRAPH.nodes[1], id: "a1" },
    { ...MODULE_GRAPH.nodes[0], id: "a2" },
  ],
  edges: [{ source: "a1", target: "a2", kind: "imports", count: 1 }],
};

function graphRoutes() {
  return [
    {
      match: "/v1/graph",
      body: (url) => {
        const params = new URL(url, "http://localhost").searchParams;
        if (params.get("view") === "module") {
          return params.get("module") === "src/admin.js" ? ADMIN_GRAPH : MODULE_GRAPH;
        }
        if (params.get("view") === "flow") return { ...MODULE_GRAPH, view: "flow", module: null };
        return OVERVIEW;
      },
    },
  ];
}

describe("project guide graph", () => {
  it("shows automatic descriptions, file names, counts and imports without starting a model", async () => {
    const graph = {
      ...OVERVIEW,
      nodes: OVERVIEW.nodes.map((node) => ({
        ...node, purpose: "Reusable interface pieces for building screens.",
        purpose_source: "automatic", ai_explanation: null,
        top_files: ["components/Card.tsx", "components/Button.tsx", "components/Input.tsx"],
        uses_count: 3, used_by_count: 2,
      })),
      edges: [{ source: OVERVIEW.nodes[0].id, target: OVERVIEW.nodes[1].id, kind: "imports", count: 3 }],
    };
    const { calls } = mockFetch([{ match: "/v1/graph", body: graph }]);
    setToken();
    render(<ArchitectureTab />);
    await screen.findAllByText("Automatic · folder conventions");
    expect(screen.getAllByText("Card.tsx").length).toBeGreaterThan(0);
    expect(screen.getAllByText("uses 3 / used by 2").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Explain", exact: true }).length).toBeGreaterThan(0);
    expect(screen.getByText("imports 3 files")).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("/explain"))).toBe(false);
  });

  it("keeps 55 nodes in separate left-to-right layers at readable zoom with scroll panning", async () => {
    const nodes = Array.from({ length: 55 }, (_, index) => ({
      id: `large-${index}`, kind: "folder", path: `part${index}`, label: `Part ${index}`,
      purpose: "Shared helpers.", purpose_source: "automatic", file_count: 1,
    }));
    const edges = nodes.slice(1).map((node, index) => ({ source: nodes[index].id, target: node.id, kind: "imports", count: 1 }));
    mockFetch([{ match: "/v1/graph", body: { nodes, edges, meta: { scanned_files: 55 } } }]);
    setToken();
    render(<ArchitectureTab />);
    await screen.findByTestId("rf-node-large-54");
    const props = globalThis.__rfProps;
    expect(props.minZoom).toBe(1);
    expect(props.defaultViewport.zoom).toBe(1);
    expect(props.panOnScroll).toBe(true);
    expect(props.panOnDrag).toBe(true);
    expect(props.zoomOnScroll).toBe(false);
    const positions = new Map(props.nodes.map((node) => [node.id, node.position]));
    expect(new Set(props.nodes.map((node) => `${node.position.x},${node.position.y}`)).size).toBe(55);
    edges.forEach((edge) => expect(positions.get(edge.target).x).toBeGreaterThan(positions.get(edge.source).x));
  });

  it("lets offline users retry, close, or open Settings from the provider error", async () => {
    const onNavigate = vi.fn();
    const { calls } = mockFetch([
      ...graphRoutes(),
      { match: "/v1/explain/notice", status: 400, body: { detail: "Choose a model provider in Settings. The overview works offline." } },
    ]);
    setToken();
    render(<ArchitectureTab onNavigate={onNavigate} />);
    await screen.findByText("Not explained yet");
    fireEvent.click(screen.getByRole("button", { name: "Explain project" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(/Choose a model provider/);
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Open Settings" })).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: "Retry" }));
    await within(dialog).findByText(/Choose a model provider/);
    expect(calls.filter((call) => call.url.includes("/v1/explain/notice"))).toHaveLength(2);
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Explain project" }));
    await screen.findByText(/Choose a model provider/);
    fireEvent.click(screen.getByRole("button", { name: "Open Settings" }));
    expect(onNavigate).toHaveBeenCalledWith("settings");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((call) => call.url === "/v1/explain")).toBe(false);
  });

  it("initialises the file selector from the top-level module field and stays in sync", async () => {
    const { calls } = mockFetch(graphRoutes());
    setToken();
    render(<ArchitectureTab />);

    await screen.findByText("Project overview");
    fireEvent.click(screen.getByRole("button", { name: "Inside one file" }));

    const select = await screen.findByLabelText("File");
    await waitFor(() => expect(select.value).toBe("src/server.js"));

    // Exactly one request built the view the selector displays — no refetch loop.
    const moduleCalls = calls.filter((call) => {
      const params = new URL(call.url, "http://localhost").searchParams;
      return params.get("view") === "module" && !params.get("module");
    });
    expect(moduleCalls).toHaveLength(1);

    // Choosing another file requests it, and the selector keeps showing it.
    fireEvent.change(select, { target: { value: "src/admin.js" } });
    await waitFor(() =>
      expect(
        calls.some((call) => {
          const params = new URL(call.url, "http://localhost").searchParams;
          return params.get("module") === "src/admin.js";
        })
      ).toBe(true)
    );
    expect(screen.getByLabelText("File").value).toBe("src/admin.js");
    expect(await screen.findByTestId("rf-node-a1")).toBeInTheDocument();
  });

  it("keeps node drags in state and highlights selection", async () => {
    mockFetch(graphRoutes());
    setToken();
    render(<ArchitectureTab />);

    await screen.findByTestId("rf-node-f1");
    const props = globalThis.__rfProps;

    // Drag: onNodesChange must apply the position to state, not drop it.
    act(() => {
      props.onNodesChange([
        { type: "position", id: "f1", position: { x: 640, y: 480 }, dragging: false },
      ]);
    });
    await waitFor(() =>
      expect(document.querySelector('[data-testid="rf-node-f1"]').style.transform).toBe(
        "translate(640px, 480px)"
      )
    );

    // Selection: change + click open the details panel with full information.
    act(() => {
      props.onNodesChange([{ type: "select", id: "f2", selected: true }]);
      props.onNodeClick({}, { id: "f2" });
    });

    const details = await screen.findByLabelText("Details for UI");
    expect(details).toBeInTheDocument();
    expect(within(details).getByText("Renders the dashboard screens")).toBeInTheDocument();
    expect(within(details).getByText("Coordinates rendering across the dashboard screens with shared components.")).toBeInTheDocument();
    expect(within(details).getByText("AI-written")).toBeInTheDocument();
    expect(within(details).getByText("ui")).toBeInTheDocument(); // full path, unclamped
    expect(within(details).getByText("Uses")).toBeInTheDocument();
    expect(within(details).getByText("Used by")).toBeInTheDocument();
    expect(
      document.querySelector('[data-testid="rf-node-f2"]').firstElementChild.getAttribute("style")
    ).toContain("var(--focus)");
  });

  it("shows explanation, AI label, file count, coverage and an Explain action — without any model call on open", async () => {
    const { calls } = mockFetch([
      ...graphRoutes(),
      {
        match: "/v1/explain/notice",
        body: {
          text: "Explain the component using the configured provider. A small excerpt and its file names will be sent.",
          requests: 3,
          tokens: 5000,
        },
      },
      {
        match: "/v1/explain",
        method: "POST",
        status: 202,
        body: { running: true },
      },
      {
        match: "/v1/explain/status",
        body: {
          running: false,
          done: true,
          scope: "component",
          summary: "Explained 3 files (2 from cache).",
          messages: [{ text: "reused cache for server/a.py", at: "10:00:00" }],
          cache_hits: 2,
          requests_used: 3,
          requests_budget: 3,
          tokens_reserved: 5000,
          warnings: [],
        },
      },
    ]);
    setToken();
    render(<ArchitectureTab />);

    // Opening/refreshing the graph makes ZERO explain calls.
    await screen.findByTestId("rf-node-f1");
    expect(calls.some((call) => call.url.includes("/v1/explain"))).toBe(false);

    // Unexplained component card.
    expect(screen.getByText("Not explained yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Explain" })).toBeInTheDocument();
    expect(screen.getByText("3 files")).toBeInTheDocument();

    // Explained component card: purpose, AI badge, file count, partial coverage.
    expect(screen.getByText("Renders the dashboard screens")).toBeInTheDocument();
    expect(screen.getByText("4 files")).toBeInTheDocument();
    expect(screen.getByText("1 of 4 files explained")).toBeInTheDocument();
    const aiBadges = screen.getAllByText("AI-written");
    expect(aiBadges.length).toBeGreaterThanOrEqual(1);

    // Explain flow: notice → explicit confirmation → POST → live status.
    fireEvent.click(screen.getByRole("button", { name: "Explain" }));

    const notice = await waitFor(() => {
      const found = calls.find((call) => call.url.includes("/v1/explain/notice"));
      expect(found).toBeTruthy();
      return found;
    });
    expect(notice.url).toContain("scope=component");
    expect(notice.url).toContain("component=server");

    expect(
      await screen.findByText(/A model request is about to be sent/)
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Max requests")).toHaveValue(3);
    expect(screen.getByLabelText("Token budget")).toHaveValue(5000);
    // Nothing has been sent yet.
    expect(calls.some((call) => call.url === "/v1/explain")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Yes, explain it" }));

    const post = await waitFor(() => {
      const found = calls.find((call) => call.url === "/v1/explain" && call.method === "POST");
      expect(found).toBeTruthy();
      return found;
    });
    expect(post.body).toEqual({
      confirm: true,
      scope: "component",
      component: "server",
      max_requests: 3,
      token_budget: 5000,
    });

    expect(await screen.findByText(/Explained 3 files/)).toBeInTheDocument();
    expect(screen.getByText(/reused cache for server\/a.py/)).toBeInTheDocument();
    expect(screen.getByText(/2 cache hits reused/)).toBeInTheDocument();
    expect(screen.getByText(/Completed/)).toBeInTheDocument();
  });
});
