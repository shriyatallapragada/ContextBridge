import { useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
} from "@xyflow/react";
import { apiRequest, apiText } from "../api.js";
import { Badge, EmptyState, ErrorBanner, Spinner } from "./ui.jsx";

const VIEWS = [
  { id: "overview", label: "Overview" },
  { id: "module", label: "Module" },
  { id: "flow", label: "Flow" },
];

const KIND_TONES = {
  folder: { box: "border-violet-700 bg-violet-950/70 text-violet-100", dot: "#8b5cf6" },
  module: { box: "border-sky-700 bg-sky-950/70 text-sky-100", dot: "#38bdf8" },
  file: { box: "border-zinc-600 bg-zinc-900 text-zinc-200", dot: "#a1a1aa" },
  function: { box: "border-amber-700 bg-amber-950/70 text-amber-100", dot: "#f59e0b" },
  class: { box: "border-emerald-700 bg-emerald-950/70 text-emerald-100", dot: "#34d399" },
  collapsed: {
    box: "border-dashed border-zinc-600 bg-zinc-950 text-zinc-400",
    dot: "#52525b",
  },
};

function KindNode({ data, selected }) {
  const tone = KIND_TONES[data.kind] ?? KIND_TONES.file;
  return (
    <div
      className={`rounded-lg border px-3 py-2 shadow-xl ${tone.box} ${
        selected ? "ring-2 ring-sky-400" : ""
      }`}
      style={{ width: 200 }}
      title={data.purpose || data.label}
    >
      <Handle type="target" position={Position.Left} />
      <div className="truncate text-xs font-semibold" title={data.label}>
        {data.label}
      </div>
      <div className="text-[10px] uppercase tracking-wide opacity-60">{data.kind}</div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { kind: KindNode };

const X_STEP = 260;
const Y_STEP = 90;
const ROWS_PER_COLUMN = 7;
const MAX_LAYER = 6;

// Deterministic layered layout: dependency depth → column, capped so cyclic
// import graphs stay on screen. Falls back to a grid when there are no edges.
function computePositions(nodes, edges) {
  const positions = new Map();
  if (nodes.length === 0) return positions;

  const ids = new Set(nodes.map((node) => node.id));
  const validEdges = edges.filter(
    (edge) =>
      ids.has(edge.source) &&
      ids.has(edge.target) &&
      edge.source !== edge.target // self-loops must not push a node off-screen
  );

  const center = (count) => -((count - 1) * Y_STEP) / 2;

  if (validEdges.length === 0) {
    const cols = Math.min(4, Math.ceil(Math.sqrt(nodes.length)));
    const rows = Math.ceil(nodes.length / cols);
    nodes.forEach((node, index) => {
      positions.set(node.id, {
        x: (index % cols) * X_STEP,
        y: Math.floor(index / cols) * Y_STEP + center(rows),
      });
    });
    return positions;
  }

  const layer = new Map(nodes.map((node) => [node.id, 0]));
  for (let pass = 0; pass < MAX_LAYER; pass += 1) {
    let changed = false;
    for (const edge of validEdges) {
      const next = Math.min(layer.get(edge.source) + 1, MAX_LAYER);
      if (next > layer.get(edge.target)) {
        layer.set(edge.target, next);
        changed = true;
      }
    }
    if (!changed) break;
  }

  const layers = new Map();
  for (const node of nodes) {
    const index = layer.get(node.id) ?? 0;
    if (!layers.has(index)) layers.set(index, []);
    layers.get(index).push(node);
  }

  let x = 0;
  for (const index of [...layers.keys()].sort((a, b) => a - b)) {
    const group = layers.get(index);
    const subColumns = Math.ceil(group.length / ROWS_PER_COLUMN);
    group.forEach((node, i) => {
      const col = Math.floor(i / ROWS_PER_COLUMN);
      const row = i % ROWS_PER_COLUMN;
      const colCount = Math.min(ROWS_PER_COLUMN, group.length - col * ROWS_PER_COLUMN);
      positions.set(node.id, {
        x: x + col * X_STEP,
        y: row * Y_STEP + center(colCount),
      });
    });
    x += subColumns * X_STEP;
  }
  return positions;
}

function DetailList({ title, values, mono }) {
  if (!values || values.length === 0) return null;
  return (
    <div>
      <h4 className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">{title}</h4>
      <ul className="mt-1.5 space-y-1">
        {values.slice(0, 20).map((value, index) => (
          <li
            key={`${value}-${index}`}
            className={`break-words rounded-md bg-zinc-800/60 px-2 py-1 text-[11px] text-zinc-300 ${
              mono ? "font-mono" : ""
            }`}
          >
            {value}
          </li>
        ))}
      </ul>
      {values.length > 20 && (
        <p className="mt-1 text-[10px] text-zinc-600">+{values.length - 20} more</p>
      )}
    </div>
  );
}

function NodeDetails({ node, onClose }) {
  const tone = KIND_TONES[node.kind] ?? KIND_TONES.file;
  const ai = node.ai_explanation;

  return (
    <aside className="thin-scroll w-full shrink-0 overflow-y-auto rounded-2xl border border-zinc-800 bg-zinc-900/70 p-4 lg:w-80 lg:max-h-[560px]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Badge className={tone.box}>{node.kind}</Badge>
          <h3 className="mt-2 break-words text-sm font-semibold text-zinc-100">{node.label}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          className="rounded-md border border-zinc-700 px-2 py-0.5 text-xs text-zinc-400 transition hover:border-zinc-500 hover:text-zinc-200"
        >
          ✕
        </button>
      </div>

      {node.path && (
        <p className="mt-2 break-all font-mono text-[11px] text-zinc-500">{node.path}</p>
      )}

      {node.purpose && (
        <div className="mt-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
            Purpose
          </h4>
          <p className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed text-zinc-300">
            {node.purpose}
          </p>
        </div>
      )}

      {ai && ai.text && (
        <div className="mt-3 rounded-xl border border-violet-800/60 bg-violet-950/30 p-3">
          <div className="flex items-center gap-2">
            <h4 className="text-[11px] font-semibold uppercase tracking-wide text-violet-300">
              AI explanation
            </h4>
            <Badge className="border-violet-700 bg-violet-950/70 text-violet-300">
              {ai.label ?? "AI-written"}
            </Badge>
          </div>
          <p className="mt-1 text-[10px] text-violet-400/80">
            Optional text generated by a model earlier and cached server-side. The dashboard never
            calls a model.
          </p>
          <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-zinc-300">{ai.text}</p>
        </div>
      )}

      <div className="mt-4 space-y-4">
        <DetailList title="Depends on" values={node.depends_on} mono />
        <DetailList title="Used by" values={node.used_by} mono />
        <DetailList title="Related files" values={node.related_files} mono />
        {node.routes && node.routes.length > 0 && (
          <DetailList title="Routes" values={node.routes} mono />
        )}
      </div>
    </aside>
  );
}

// Deterministic AST graph from GET /v1/graph, rendered with React Flow.
export default function ArchitectureTab() {
  const [view, setView] = useState("overview");
  const [module, setModule] = useState("");
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [copyState, setCopyState] = useState("idle");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSelectedId(null);
    const params = new URLSearchParams({ view, limit: "24" });
    if (view === "module" && module) params.set("module", module);
    apiRequest(`/v1/graph?${params.toString()}`)
      .then((data) => {
        if (!cancelled) setGraph(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [view, module, reloadKey]);

  const { rfNodes, rfEdges } = useMemo(() => {
    if (!graph) return { rfNodes: [], rfEdges: [] };
    const nodes = graph.nodes ?? [];
    const edges = graph.edges ?? [];
    const positions = computePositions(nodes, edges);
    return {
      rfNodes: nodes.map((node) => ({
        id: node.id,
        type: "kind",
        position: positions.get(node.id) ?? { x: 0, y: 0 },
        data: {
          label: node.label,
          kind: node.kind,
          purpose: node.purpose,
        },
      })),
      rfEdges: edges.map((edge) => ({
        id: `${edge.source}-${edge.target}-${edge.kind}`,
        source: edge.source,
        target: edge.target,
        label: edge.count > 1 ? `${edge.kind} ×${edge.count}` : edge.kind,
        markerEnd: { type: MarkerType.ArrowClosed, color: "#52525b" },
        style: { stroke: "#3f3f46" },
      })),
    };
  }, [graph]);

  const selectedNode = useMemo(
    () => (graph?.nodes ?? []).find((node) => node.id === selectedId) ?? null,
    [graph, selectedId]
  );

  const copyMermaid = async () => {
    try {
      const params = new URLSearchParams({ view });
      if (view === "module" && module) params.set("module", module);
      const text = await apiText(`/v1/mermaid?${params.toString()}`);
      if (!navigator.clipboard) throw new Error("Clipboard unavailable.");
      await navigator.clipboard.writeText(text);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
    window.setTimeout(() => setCopyState("idle"), 2500);
  };

  const meta = graph?.meta;

  return (
    <div className="space-y-4">
      {/* View switcher + module selector */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-zinc-700 p-0.5">
          {VIEWS.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => setView(option.id)}
              className={`rounded-md px-3 py-1 text-xs font-medium transition ${
                view === option.id
                  ? "bg-sky-600 text-white"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>

        {view === "module" && (
          <select
            value={module || meta?.module || ""}
            onChange={(event) => setModule(event.target.value)}
            className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-200 outline-none transition focus:border-sky-600"
          >
            {!module && meta?.module && <option value={meta.module}>{meta.module}</option>}
            {(meta?.available_modules ?? []).map((path) => (
              <option key={path} value={path}>
                {path}
              </option>
            ))}
            {(meta?.available_modules ?? []).length === 0 && !meta?.module && (
              <option value="">No modules detected</option>
            )}
          </select>
        )}

        {loading && <Spinner label="Loading graph…" />}

        {meta && (
          <span className="text-[11px] text-zinc-600">
            {meta.shown_nodes}/{meta.total_nodes} nodes
            {meta.hidden_nodes > 0 ? ` · ${meta.hidden_nodes} hidden` : ""}
          </span>
        )}
      </div>

      <ErrorBanner message={error} onRetry={() => setReloadKey((key) => key + 1)} />

      {loading && !graph ? (
        <Spinner label="Building graph…" />
      ) : error && !graph ? (
        <EmptyState
          title="Could not load the graph"
          hint="The local API answered with an error. Retry, or check that `cb serve` is running."
        />
      ) : graph && (graph.nodes ?? []).length === 0 ? (
        <EmptyState
          title="No graph to show"
          hint="No source files were detected for this view yet. Run the project scan, or try another view."
        />
      ) : (
        <div className="flex flex-col gap-4 lg:flex-row">
          <div className="h-[560px] min-w-0 flex-1 overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950">
            <ReactFlow
              key={`${view}|${module}|${rfNodes.length}`}
              nodes={rfNodes}
              edges={rfEdges}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.3 }}
              minZoom={0.2}
              maxZoom={2}
              nodesDraggable
              nodesConnectable={false}
              elementsSelectable
              onNodeClick={(_, node) => setSelectedId(node.id)}
              onPaneClick={() => setSelectedId(null)}
              proOptions={{ hideAttribution: false }}
            >
              <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#27272a" />
              <Controls showInteractive={false} />
              <MiniMap
                pannable
                zoomable
                nodeColor={(node) => KIND_TONES[node.data?.kind]?.dot ?? "#a1a1aa"}
                maskColor="rgba(9, 9, 11, 0.7)"
              />
              <Panel position="top-right">
                <div className="flex flex-col items-end gap-1.5">
                  <button
                    type="button"
                    onClick={copyMermaid}
                    className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 transition hover:border-zinc-500 hover:text-zinc-100"
                  >
                    {copyState === "copied"
                      ? "Copied ✓"
                      : copyState === "error"
                        ? "Copy failed"
                        : "Copy Mermaid"}
                  </button>
                  {meta?.notice && (
                    <span className="max-w-[240px] rounded-lg border border-zinc-800 bg-zinc-900/90 px-2 py-1 text-right text-[10px] leading-snug text-zinc-500">
                      {meta.notice}
                    </span>
                  )}
                </div>
              </Panel>
            </ReactFlow>
          </div>

          {selectedNode && (
            <NodeDetails node={selectedNode} onClose={() => setSelectedId(null)} />
          )}
        </div>
      )}

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-3 text-[11px] text-zinc-500">
        {Object.entries(KIND_TONES).map(([kind, tone]) => (
          <span key={kind} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: tone.dot }}
            />
            {kind}
          </span>
        ))}
        <span className="text-zinc-600">Solid arrow = detected import or call</span>
      </div>
    </div>
  );
}
