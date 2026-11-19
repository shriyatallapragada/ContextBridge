import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  Handle,
  getSmoothStepPath,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
} from "@xyflow/react";
import { apiRequest, apiText } from "../api.js";
import { Badge, EmptyState, ErrorBanner, Spinner } from "./ui.jsx";
import ExplainDialog from "./ExplainDialog.jsx";

const VIEWS = [
  { id: "overview", label: "Project overview" },
  { id: "module", label: "Inside one file" },
  { id: "flow", label: "How a request travels" },
];

const KIND_TONES = {
  folder: { box: "border-accent bg-surface text-ink", dot: "#BF8B67" },
  module: { box: "border-masala bg-surface text-ink", dot: "#9D5353" },
  file: { box: "border-line bg-surface text-ink", dot: "#855656" },
  function: { box: "border-ok bg-ok-bg text-ok", dot: "#2F6B4F" },
  class: { box: "border-info bg-info-bg text-info", dot: "#4A5A8A" },
  collapsed: { box: "border-dashed border-line bg-surface-alt text-ink-muted", dot: "#DBCB96" },
};

const KIND_BADGE_TONES = {
  folder: "accent",
  module: "primary",
  file: "neutral",
  function: "ok",
  class: "info",
  collapsed: "neutral",
};

const NODE_WIDTH = 300;

function coverageText(node) {
  const total = node.file_count ?? 1;
  const explained = node.explained_files ?? 0;
  if (node.kind !== "folder" || total <= 1) return null;
  if (explained <= 0) return null;
  if (explained >= total) return `all ${total} files explained`;
  return `${explained} of ${total} files explained`;
}

function isUnexplained(node) {
  if (node.kind === "folder") return !node.ai_explanation;
  return !node.purpose || node.purpose === "Not explained yet";
}

// Card contents are deliberately rich: name, explanation (or the honest
// "Not explained yet"), AI provenance, file count, coverage and an Explain
// action — no need to open the details panel first.
function KindNode({ data, selected }) {
  const tone = KIND_TONES[data.kind] ?? KIND_TONES.file;
  const coverage = data.coverage;
  const needsExplain = data.unexplained;

  return (
    <div
      className={`rounded-card border px-3 py-2.5 text-left shadow-card transition ${tone.box} ${
        selected ? "border-chai" : "hover:border-accent"
      }`}
      style={{
        width: NODE_WIDTH,
        background: "var(--surface)",
        boxShadow: selected ? "0 0 0 3px var(--focus)" : undefined,
      }}
      title={data.purpose || data.label}
    >
      {(data.targetPorts ?? []).map((id, index, ports) => (
        <Handle key={id} id={`in-${id}`} type="target" position={Position.Left}
          style={{ top: `${20 + (index + 1) * 60 / (ports.length + 1)}%` }} />
      ))}
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 flex-1 truncate text-base font-bold text-ink" title={data.label}>
          {data.label}
        </span>
        {data.ai && (
          <Badge tone="accent" symbol="✦">
            AI-written
          </Badge>
        )}
      </div>
      <div className="mt-0.5 text-sm uppercase tracking-wide text-ink-muted">{data.kind}</div>
      {data.automatic && <Badge tone="neutral">Automatic · folder conventions</Badge>}

      <p className="mt-1.5 line-clamp-3 text-sm leading-snug text-ink-muted">
        {data.purpose || "Not explained yet"}
      </p>
      {data.topFiles?.length > 0 && (
        <ul className="mt-2 text-sm leading-snug text-ink">
          {data.topFiles.map((path) => <li key={path} className="truncate" title={path}>{path.split("/").pop()}</li>)}
        </ul>
      )}
      {data.kind === "folder" && (
        <p className="mt-2 text-sm text-ink-muted">uses {data.usesCount} / used by {data.usedByCount}</p>
      )}

      {(data.kind === "folder" || coverage) && (
        <div className="mt-1.5 flex flex-wrap gap-1.5 text-sm text-ink-muted">
          {data.kind === "folder" && (
            <Badge tone="neutral" symbol="▤">
              {data.fileCount} {data.fileCount === 1 ? "file" : "files"}
            </Badge>
          )}
          {coverage && <Badge tone="primary">{coverage}</Badge>}
        </div>
      )}

      {needsExplain && data.onExplain && (
        <button
          type="button"
          className="nodrag btn btn-secondary mt-2 w-full"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            data.onExplain();
          }}
        >
          Explain
        </button>
      )}
      {(data.sourcePorts ?? []).map((id, index, ports) => (
        <Handle key={id} id={`out-${id}`} type="source" position={Position.Right}
          style={{ top: `${20 + (index + 1) * 60 / (ports.length + 1)}%` }} />
      ))}
    </div>
  );
}

const nodeTypes = { kind: KindNode };

function ImportEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, style }) {
  const [path] = getSmoothStepPath({
    sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
    centerX: sourceX + 24 + (data.channel ?? 0) * 16,
    borderRadius: 10,
  });
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style}
    label={data.label} labelX={targetX - 80} labelY={targetY - 14}
    labelStyle={{ fontSize: 14, fill: "#632626" }} labelBgStyle={{ fill: "#FCFBF7" }} />;
}

const edgeTypes = { imports: ImportEdge };

const X_STEP = 460;
const Y_STEP = 420;

// Collapse import cycles before assigning dependency layers. Each layer has its
// own column, so large graphs grow vertically instead of shrinking their text.
function computePositions(nodes, edges) {
  const positions = new Map();
  const ids = new Set(nodes.map((node) => node.id));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  for (const edge of edges) {
    if (ids.has(edge.source) && ids.has(edge.target)) outgoing.get(edge.source).push(edge.target);
  }
  const indices = new Map(), low = new Map(), stack = [], active = new Set(), groups = [];
  let nextIndex = 0;
  function visit(id) {
    indices.set(id, nextIndex); low.set(id, nextIndex++);
    stack.push(id); active.add(id);
    for (const target of outgoing.get(id)) {
      if (!indices.has(target)) {
        visit(target); low.set(id, Math.min(low.get(id), low.get(target)));
      } else if (active.has(target)) low.set(id, Math.min(low.get(id), indices.get(target)));
    }
    if (low.get(id) === indices.get(id)) {
      const group = [];
      let member;
      do { member = stack.pop(); active.delete(member); group.push(member); } while (member !== id);
      groups.push(group.sort());
    }
  }
  [...ids].sort().forEach((id) => { if (!indices.has(id)) visit(id); });
  const owner = new Map();
  groups.forEach((group, index) => group.forEach((id) => owner.set(id, index)));
  const links = groups.map(() => new Set()), degree = groups.map(() => 0), depth = groups.map(() => 0);
  for (const edge of edges) {
    const a = owner.get(edge.source), b = owner.get(edge.target);
    if (a !== undefined && b !== undefined && a !== b && !links[a].has(b)) {
      links[a].add(b); degree[b]++;
    }
  }
  const queue = groups.map((_, index) => index).filter((index) => degree[index] === 0);
  for (const index of queue) {
    for (const target of links[index]) {
      depth[target] = Math.max(depth[target], depth[index] + 1);
      if (--degree[target] === 0) queue.push(target);
    }
  }
  const rows = new Map();
  [...nodes].sort((a, b) => Number(a.label.startsWith("+")) - Number(b.label.startsWith("+"))).forEach((node) => {
    const column = depth[owner.get(node.id)], row = rows.get(column) ?? 0;
    positions.set(node.id, { x: column * X_STEP, y: row * Y_STEP });
    rows.set(column, row + 1);
  });
  return positions;
}

function DetailList({ title, values, mono }) {
  if (!values || values.length === 0) return null;
  return (
    <div>
      <h4 className="text-sm font-semibold" style={{ color: "var(--red-chai)" }}>
        {title}
      </h4>
      <ul className="mt-1.5 space-y-1">
        {values.slice(0, 20).map((value, index) => (
          <li
            key={`${value}-${index}`}
            className={`break-words rounded-control bg-surface-alt px-2 py-1 text-sm text-ink ${
              mono ? "font-mono" : ""
            }`}
          >
            {value}
          </li>
        ))}
      </ul>
      {values.length > 20 && (
        <p className="mt-1 text-sm text-ink-muted">+{values.length - 20} more</p>
      )}
    </div>
  );
}

function NodeDetails({ node, onClose, onExplain }) {
  const badgeTone = KIND_BADGE_TONES[node.kind] ?? "neutral";
  const ai = node.ai_explanation;

  return (
    <aside
      className="thin-scroll w-full shrink-0 overflow-y-auto rounded-card border border-line bg-surface p-4 lg:max-h-[560px] lg:w-80"
      aria-label={`Details for ${node.label}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Badge tone={badgeTone}>{node.kind}</Badge>
          <h3 className="mt-2 break-words text-base font-bold text-ink">{node.label}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          className="btn btn-secondary shrink-0"
        >
          ✕
        </button>
      </div>

      {node.path && (
        <p className="mt-2 break-all font-mono text-sm text-ink-muted" title={node.path}>
          {node.path}
        </p>
      )}

      {node.purpose && (
        <div className="mt-3">
          <h4 className="text-sm font-semibold" style={{ color: "var(--red-chai)" }}>
            Purpose
          </h4>
          <p className="mt-1.5 whitespace-pre-wrap text-base leading-relaxed text-ink">
            {node.purpose}
          </p>
        </div>
      )}

      {ai && ai.text && (
        <div className="mt-3 rounded-card border border-accent bg-surface-alt p-3">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-semibold" style={{ color: "var(--red-chai)" }}>
              Explanation
            </h4>
            <Badge tone="accent" symbol="✦">
              {ai.label ?? "AI-written"}
            </Badge>
          </div>
          <p className="mt-1 text-sm text-ink-muted">
            Written by a model earlier and cached on the server. The dashboard does not call a
            model on its own.
          </p>
          <p className="mt-2 whitespace-pre-wrap text-base leading-relaxed text-ink">{ai.text}</p>
        </div>
      )}

      <div className="mt-4 space-y-4">
        <DetailList title="Uses" values={node.depends_on} mono />
        <DetailList title="Used by" values={node.used_by} mono />
        <DetailList title="Related files" values={node.related_files} mono />
        {node.routes && node.routes.length > 0 && (
          <DetailList title="Detected routes" values={node.routes} mono />
        )}
      </div>

      <div className="mt-4">
        <button type="button" className="btn btn-secondary btn-block" onClick={onExplain}>
          {node.kind === "folder" ? "Explain this component" : "Explain this file"}
        </button>
        <p className="hint mt-1.5">Asks for confirmation and a budget first — nothing is sent until you approve.</p>
      </div>
    </aside>
  );
}

// Deterministic AST graph from GET /v1/graph, rendered with React Flow.
export default function ArchitectureTab({ onNavigate }) {
  const [view, setView] = useState("overview");
  const [requestModule, setRequestModule] = useState(""); // "" → server default
  const [selected, setSelected] = useState(""); // what the selector displays
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [rfNodes, setRfNodes] = useState([]);
  const [rfEdges, setRfEdges] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [hoverEdgeId, setHoverEdgeId] = useState(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState(null);
  const [copyState, setCopyState] = useState("idle");
  const [explainTarget, setExplainTarget] = useState(null);
  const seqRef = useRef(0);

  // Fetch the graph for the current view + selected file. Never calls a model.
  useEffect(() => {
    const mySeq = (seqRef.current += 1);
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ view, limit: "24" });
    if (requestModule) params.set("module", requestModule);
    apiRequest(`/v1/graph?${params.toString()}`)
      .then((data) => {
        if (mySeq !== seqRef.current) return; // stale response guard
        setGraph(data);
        // Keep the selector and the displayed graph in sync: the top-level
        // `module` field IS the module the response was built from.
        if (data && typeof data === "object" && data.module) setSelected(data.module);
        else if (view === "module" || view === "flow") setSelected("");
      })
      .catch((err) => {
        if (mySeq === seqRef.current) setError(err.message);
      })
      .finally(() => {
        if (mySeq === seqRef.current) setLoading(false);
      });
  }, [view, requestModule, reloadKey]);

  const startExplain = useCallback((target) => setExplainTarget(target), []);

  // Rebuild React Flow nodes/edges whenever the graph data changes, then let
  // onNodesChange keep user-driven drags in state.
  useEffect(() => {
    if (!graph) {
      setRfNodes([]);
      setRfEdges([]);
      setSelectedId(null);
      return;
    }
    const nodes = graph.nodes ?? [];
    const edges = graph.edges ?? [];
    const positions = computePositions(nodes, edges);
    setRfNodes(
      nodes.map((node) => ({
        id: node.id,
        type: "kind",
        initialWidth: NODE_WIDTH,
        initialHeight: Y_STEP - 16,
        position: positions.get(node.id) ?? { x: 0, y: 0 },
        data: {
          label: node.label,
          kind: node.kind,
          path: node.path,
          purpose: node.purpose,
          automatic: node.purpose_source === "automatic",
          topFiles: node.top_files ?? [],
          usesCount: node.uses_count ?? 0,
          usedByCount: node.used_by_count ?? 0,
          sourcePorts: edges.filter((edge) => edge.source === node.id).map((edge) => `${edge.source}-${edge.target}-${edge.kind}`),
          targetPorts: edges.filter((edge) => edge.target === node.id).map((edge) => `${edge.source}-${edge.target}-${edge.kind}`),
          ai: node.ai_explanation ?? null,
          fileCount: node.file_count ?? 1,
          explainedFiles: node.explained_files ?? 0,
          coverage: coverageText(node),
          unexplained: isUnexplained(node),
          onExplain:
            node.kind === "folder"
              ? () =>
                  startExplain({
                    scope: "component",
                    component: node.path,
                    label: node.label,
                  })
              : node.path
                ? () => startExplain({ scope: "file", path: node.path, label: node.label })
                : null,
        },
      }))
    );
    setRfEdges(
      edges.map((edge, index) => ({
        id: `${edge.source}-${edge.target}-${edge.kind}`,
        source: edge.source,
        target: edge.target,
        sourceHandle: `out-${edge.source}-${edge.target}-${edge.kind}`,
        targetHandle: `in-${edge.source}-${edge.target}-${edge.kind}`,
        type: edge.kind === "imports" ? "imports" : "smoothstep",
        pathOptions: { offset: 24 + index % 5 * 12, borderRadius: 10 },
        label: edge.kind === "imports" ? `imports ${edge.count ?? 1} files` : edge.kind,
        labelStyle: { fontSize: 14, fill: "#632626" },
        labelBgStyle: { fill: "#FCFBF7" },
        data: {
          channel: edges.filter((item) => item.source === edge.source).indexOf(edge),
          label: edge.kind === "imports" ? `imports ${edge.count ?? 1} files` : edge.kind,
          kind: edge.kind,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#BF8B67" },
        style: { stroke: "#BF8B67" },
      }))
    );
    setSelectedId(null);
    setHoverEdgeId(null);
    setSelectedEdgeId(null);
  }, [graph, startExplain]);

  // Import counts stay visible; selection preserves the same grounded label.
  const visibleEdges = useMemo(
    () =>
      rfEdges.map((edge) =>
        edge.id === hoverEdgeId || edge.id === selectedEdgeId
          ? { ...edge, label: edge.data?.label }
          : edge
      ),
    [rfEdges, hoverEdgeId, selectedEdgeId]
  );

  const rawNodes = graph?.nodes ?? [];
  const selectedNode = useMemo(
    () => rawNodes.find((node) => node.id === selectedId) ?? null,
    [rawNodes, selectedId]
  );
  const meta = graph?.meta ?? {};
  const moduleOptions = meta.available_modules ?? [];
  const startOptions = moduleOptions;
  const hasEntryPoints = (meta.entrypoints ?? []).length > 0;

  const changeView = (next) => {
    setView(next);
    setRequestModule("");
    setSelected("");
    setSelectedId(null);
  };

  const copyMermaid = async () => {
    try {
      const params = new URLSearchParams({ view });
      if (requestModule) params.set("module", requestModule);
      const text = await apiText(`/v1/mermaid?${params.toString()}`);
      const ok = await (async () => {
        if (navigator.clipboard?.writeText) {
          try {
            await navigator.clipboard.writeText(text);
            return true;
          } catch {
            return false;
          }
        }
        return false;
      })();
      setCopyState(ok ? "copied" : "error");
    } catch {
      setCopyState("error");
    }
    window.setTimeout(() => setCopyState("idle"), 2500);
  };

  const explainProject = () =>
    setExplainTarget({ scope: "project", label: "the whole project" });

  const onExplainFinished = () => setReloadKey((key) => key + 1);

  const initialViewport = useMemo(() => {
    const positions = [...computePositions(graph?.nodes ?? [], graph?.edges ?? []).values()];
    return {
      x: 32 - Math.min(0, ...positions.map((position) => position.x)),
      y: 32 - Math.min(0, ...positions.map((position) => position.y)),
      zoom: 1,
    };
  }, [graph]);

  const emptyNotice =
    meta.notice ||
    (view === "flow"
      ? meta.flow_notice
      : meta.connections_notice) ||
    "Nothing to show for this view yet.";

  return (
    <div className="space-y-4">
      {/* View switcher */}
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Guide views">
        {VIEWS.map((option) => (
          <button
            key={option.id}
            type="button"
            onClick={() => changeView(option.id)}
            aria-pressed={view === option.id}
            className="chip"
          >
            {option.label}
          </button>
        ))}

        {view === "module" && (
          <>
            <label className="label mb-0" htmlFor="cb-module-select">
              File
            </label>
            <select
              id="cb-module-select"
              className="field w-auto min-w-56"
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value);
                setRequestModule(event.target.value);
              }}
            >
              {!selected && <option value="">Choose a file…</option>}
              {selected && !moduleOptions.includes(selected) && (
                <option value={selected}>{selected}</option>
              )}
              {moduleOptions.map((path) => (
                <option key={path} value={path}>
                  {path}
                </option>
              ))}
              {moduleOptions.length === 0 && !selected && (
                <option value="" disabled>
                  No files detected
                </option>
              )}
            </select>
          </>
        )}

        {view === "flow" && (
          <>
            <label className="label mb-0" htmlFor="cb-start-select">
              Starting file
            </label>
            <select
              id="cb-start-select"
              className="field w-auto min-w-56"
              value={selected}
              onChange={(event) => {
                setSelected(event.target.value);
                setRequestModule(event.target.value);
              }}
            >
              {hasEntryPoints && (
                <option value="">Detected entry points (auto)</option>
              )}
              {selected && !startOptions.includes(selected) && (
                <option value={selected}>{selected}</option>
              )}
              {startOptions.map((path) => (
                <option key={path} value={path}>
                  {path}
                </option>
              ))}
              {startOptions.length === 0 && (
                <option value="" disabled>
                  No starting file detected
                </option>
              )}
            </select>
          </>
        )}

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {loading && <Spinner label="Loading graph…" />}
          <button type="button" className="btn btn-secondary" onClick={explainProject}>
            Explain project
          </button>
          <button type="button" className="btn btn-secondary" onClick={copyMermaid}>
            {copyState === "copied"
              ? "Copied ✓"
              : copyState === "error"
                ? "Copy failed"
                : "Copy Mermaid"}
          </button>
        </div>
      </div>

      {meta && (
        <p className="hint">
          {meta.shown_nodes}/{meta.total_nodes} nodes shown
          {meta.hidden_nodes > 0 ? ` · ${meta.hidden_nodes} hidden to keep the view bounded` : ""}
          {meta.file_count ? ` · ${meta.file_count} files scanned` : ""}
        </p>
      )}

      <ErrorBanner message={error} onRetry={() => setReloadKey((key) => key + 1)} />
      {meta.notice && <p className="hint">{meta.notice}</p>}

      {loading ? (
        <Spinner label="Building graph…" />
      ) : error && !graph ? (
        <EmptyState
          title="Could not load the graph"
          hint="The local API answered with an error. Retry, or check that `cb serve` is running."
        />
      ) : graph && (graph.nodes ?? []).length === 0 ? (
        <EmptyState
          title={view === "flow" ? "No request path to show yet" : "No files to show yet"}
          hint={`${emptyNotice} ${view === "flow"
            ? "Pick a starting file above to explore its detected imports."
            : "Add source files to this project, then reopen the project guide."}`}
        />
      ) : rfNodes.length === 0 ? (
        <Spinner label="Laying out graph…" />
      ) : (
        <div className="flex flex-col gap-4 lg:flex-row">
          <div className="project-graph min-w-0 flex-1 overflow-hidden rounded-card border border-line bg-page" style={{ height: 560 }}>
            <ReactFlow
              key={`${view}|${requestModule}|${reloadKey}`}
              nodes={rfNodes}
              edges={visibleEdges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              onNodesChange={(changes) => setRfNodes((current) => applyNodeChanges(changes, current))}
              onEdgesChange={(changes) => setRfEdges((current) => applyEdgeChanges(changes, current))}
              defaultViewport={initialViewport}
              minZoom={1}
              maxZoom={1.8}
              panOnScroll
              panOnDrag
              zoomOnScroll={false}
              nodesDraggable
              nodesConnectable={false}
              elementsSelectable
              onNodeClick={(_, node) => setSelectedId(node.id)}
              onPaneClick={() => {
                setSelectedId(null);
                setSelectedEdgeId(null);
              }}
              onEdgeMouseEnter={(_, edge) => setHoverEdgeId(edge.id)}
              onEdgeMouseLeave={() => setHoverEdgeId(null)}
              onEdgeClick={(_, edge) => setSelectedEdgeId(edge.id)}
              proOptions={{ hideAttribution: false }}
            >
              <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#D8CDB0" />
              <Controls showInteractive={false} />
              <MiniMap
                pannable
                zoomable
                nodeColor={(node) => KIND_TONES[node.data?.kind]?.dot ?? "#855656"}
                maskColor="rgba(99, 38, 38, 0.12)"
              />
            </ReactFlow>
          </div>

          {selectedNode && (
            <NodeDetails
              node={selectedNode}
              onClose={() => setSelectedId(null)}
              onExplain={() =>
                selectedNode.kind === "folder"
                  ? startExplain({
                      scope: "component",
                      component: selectedNode.path,
                      label: selectedNode.label,
                    })
                  : startExplain({
                      scope: "file",
                      path: selectedNode.path,
                      label: selectedNode.label,
                    })
              }
            />
          )}
        </div>
      )}

      {/* Connections / entry-point notices */}
      {graph && (graph.nodes ?? []).length > 0 && (graph.edges ?? []).length === 0 && (
        <p className="hint rounded-card border border-line bg-surface-alt px-4 py-3">
          <strong>Connections not detected.</strong>{" "}
          {meta.connections_notice ||
            "No static imports or calls were found between the files shown here. That usually means imports are dynamic, resolved at runtime, or genuinely absent — the files above are still real."}
        </p>
      )}

      {view === "flow" && graph && !hasEntryPoints && (
        <p className="hint rounded-card border border-warn bg-warn-bg px-4 py-3 text-warn">
          <strong>No entry point detected.</strong>{" "}
          {meta.entrypoint_support
            ? "ContextBridge looks for package scripts, main/app/server files and framework routes; none matched in this project. "
            : ""}
          Choose a starting file above to see how one file's requests travel.
        </p>
      )}

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-3 text-sm text-ink-muted">
        {Object.entries(KIND_TONES).map(([kind, tone]) => (
          <span key={kind} className="flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: tone.dot }}
            />
            {kind}
          </span>
        ))}
        <span>Arrow = detected import or call; import counts refer to distinct files.</span>
      </div>

      {explainTarget && (
        <ExplainDialog
          target={explainTarget}
          onClose={() => setExplainTarget(null)}
          onFinished={onExplainFinished}
          onOpenSettings={() => {
            setExplainTarget(null);
            onNavigate?.("settings");
          }}
        />
      )}
    </div>
  );
}
