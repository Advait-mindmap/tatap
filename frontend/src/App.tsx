import { useCallback, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  MarkerType,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";

import golden from "../../backend/tests/golden/simulation_output.json";
import { FlowNodeCard, type CardData } from "./components/FlowNodeCard";
import { HighlightContext, type HighlightState } from "./highlightContext";
import { TrailPanel } from "./components/TrailPanel";
import {
  buildAdjacency,
  computeHighlight,
  edgeId,
  EMPTY_HIGHLIGHT,
} from "./highlight";
import { layout, stageOrder } from "./layout";
import { EDGE_STYLES, KIND_ORDER, KIND_STYLES } from "./nodeKinds";
import type { NodeKind, SimulationOutput } from "./types";
import "./styles.css";

// The golden fixture the backend tests pin, read directly rather than copied. If the backend
// output changes shape, this view breaks in development rather than drifting silently.
const simulation = golden as unknown as SimulationOutput;

const nodeTypes = { card: FlowNodeCard };

export default function App() {
  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hiddenStages, setHiddenStages] = useState<Set<string>>(new Set());
  const [hiddenKinds, setHiddenKinds] = useState<Set<NodeKind>>(new Set());

  const {
    flow,
    reasoning_trail: trail,
    decisions,
    quality,
    project_meta: meta,
  } = simulation;

  const stages = useMemo(() => stageOrder(flow.nodes), [flow.nodes]);
  const adjacency = useMemo(() => buildAdjacency(flow.edges), [flow.edges]);
  const highlight = useMemo(
    () => (hovered ? computeHighlight(hovered, adjacency) : EMPTY_HIGHLIGHT),
    [hovered, adjacency],
  );

  const visible = useMemo(
    () =>
      flow.nodes.filter(
        (node) => !hiddenStages.has(node.stage) && !hiddenKinds.has(node.kind),
      ),
    [flow.nodes, hiddenStages, hiddenKinds],
  );
  const visibleIds = useMemo(
    () => new Set(visible.map((n) => n.id)),
    [visible],
  );

  // Depends on `visible` ONLY. Adding hover/selection here rebuilds every node's identity on
  // each mouse move, which makes React Flow re-create the elements and yanks the card out from
  // under the cursor mid-hover. See highlightContext.ts.
  const nodes: Node<CardData>[] = useMemo(
    () =>
      layout(visible).map((node) => ({
        id: node.id,
        type: "card",
        position: node.position,
        draggable: true,
        data: { node },
      })),
    [visible],
  );

  const highlightState: HighlightState = useMemo(
    () => ({
      hovered,
      selected,
      path: highlight.nodes,
      direct: highlight.direct,
    }),
    [hovered, selected, highlight],
  );

  const edges: Edge[] = useMemo(
    () =>
      flow.edges
        .filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to))
        .map((edge) => {
          const id = edgeId(edge);
          const style = EDGE_STYLES[edge.kind] ?? EDGE_STYLES.fragnet;
          const lit = highlight.edges.has(id);
          const dim = hovered !== null && !lit;
          return {
            id,
            source: edge.from,
            target: edge.to,
            type: "smoothstep",
            animated: lit && edge.kind !== "fragnet",
            label: edge.lag ? `${edge.type} +${edge.lag}d` : undefined,
            labelStyle: { fill: "#94a3b8", fontSize: 10 },
            labelBgStyle: { fill: "#0f172a" },
            style: {
              stroke: style.stroke,
              strokeWidth: lit ? 2.4 : 1.2,
              strokeDasharray: style.dash,
              opacity: dim ? 0.3 : 1,
            },
            markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
          };
        }),
    [flow.edges, visibleIds, highlight, hovered],
  );

  const selectedNode = useMemo(
    () => flow.nodes.find((n) => n.id === selected) ?? null,
    [flow.nodes, selected],
  );
  const selectedTrail = useMemo(
    () => trail.find((t) => t.ref_id === selected) ?? null,
    [trail, selected],
  );
  const selectedDecision = useMemo(() => {
    if (!selectedNode || selectedNode.kind !== "decision_point") return null;
    const id = selectedNode.id.replace(/^decision\./, "");
    return decisions.find((d) => d.id === id) ?? null;
  }, [selectedNode, decisions]);

  const toggle = useCallback(<T,>(set: Set<T>, value: T): Set<T> => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  }, []);

  const counts = useMemo(() => {
    const byKind = new Map<NodeKind, number>();
    for (const node of flow.nodes)
      byKind.set(node.kind, (byKind.get(node.kind) ?? 0) + 1);
    return byKind;
  }, [flow.nodes]);

  const openForks = flow.nodes.filter(
    (n) => n.kind === "decision_point" && n.status === "open",
  ).length;

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>{String(meta.project_name ?? "Simulation")}</h1>
          <p className="sub">
            {String(meta.city ?? "")} · Tier {String(meta.tier ?? "")} ·{" "}
            {String(meta.it_load_mw ?? "")} MW ·{" "}
            {String(meta.redundancy_topology ?? "")}
          </p>
        </div>
        <div className="badges">
          <span className="badge mono">
            {flow.nodes.length} nodes · {flow.edges.length} edges
          </span>
          {Boolean(quality.export_blocked) && (
            <span
              className="badge badge-alarm"
              data-testid="badge-export-blocked"
              title={String(quality.export_block_reason ?? "")}
            >
              Export blocked — {String(quality.tier_1_count ?? 0)} Tier-1
              unsigned
            </span>
          )}
          {openForks > 0 && (
            <span className="badge badge-alarm">
              {openForks} open decision point(s)
            </span>
          )}
          {!quality.governance_complete && (
            <span className="badge badge-warn">
              Governance incomplete · {String(quality.tier_2_count ?? 0)} on
              unverified data
            </span>
          )}
          <span className="badge mono">
            lib {String(meta.library_version ?? "")}
          </span>
        </div>
      </header>

      <div className="body">
        <nav className="sidebar">
          <section>
            <h3>Node kinds</h3>
            <p className="small muted">Click to hide a kind.</p>
            <ul className="legend">
              {KIND_ORDER.map((kind) => {
                const style = KIND_STYLES[kind];
                const off = hiddenKinds.has(kind);
                return (
                  <li key={kind}>
                    <button
                      className={off ? "legend-item is-off" : "legend-item"}
                      onClick={() => setHiddenKinds((s) => toggle(s, kind))}
                    >
                      <span
                        className="swatch"
                        style={{ background: style.accent }}
                      />
                      <span className="legend-label">
                        {style.glyph} {style.label}
                      </span>
                      <span className="count mono">
                        {counts.get(kind) ?? 0}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          <section>
            <h3>Stages</h3>
            <p className="small muted">
              Collapse a stage to keep a large graph navigable — the spec's rule
              is never to render everything flat.
            </p>
            <ul className="legend">
              {stages.map((stage) => (
                <li key={stage}>
                  <button
                    className={
                      hiddenStages.has(stage)
                        ? "legend-item is-off"
                        : "legend-item"
                    }
                    onClick={() => setHiddenStages((s) => toggle(s, stage))}
                  >
                    <span className="legend-label">
                      {stage.replace(/_/g, " ")}
                    </span>
                    <span className="count mono">
                      {flow.nodes.filter((n) => n.stage === stage).length}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3>Edge kinds</h3>
            <ul className="legend legend-static">
              {Object.entries(EDGE_STYLES).map(([kind, style]) => (
                <li key={kind}>
                  <span
                    className="edge-swatch"
                    style={{ background: style.stroke }}
                  />
                  <span className="legend-label">{style.label}</span>
                </li>
              ))}
            </ul>
          </section>
        </nav>

        <main className="canvas" data-testid="canvas">
          <HighlightContext.Provider value={highlightState}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              fitView
              // Open at a zoom where the cards can actually be read. A plain fitView packs all
              // 49 nodes into the canvas at ~0.31, which renders the labels as grey mush - a view
              // nobody can read is not a view. minZoom here is a LEGIBILITY FLOOR: it shows the
              // programme at working zoom and lets the reader pan, with the minimap for context.
              // The component minZoom below still allows zooming out to the whole graph.
              fitViewOptions={{ padding: 0.06, minZoom: 0.62, maxZoom: 1.0 }}
              minZoom={0.15}
              maxZoom={1.75}
              proOptions={{ hideAttribution: true }}
              onNodeMouseEnter={(_, node) => setHovered(node.id)}
              // Only clear if we are leaving the node we are actually tracking. Moving between
              // adjacent nodes can deliver leave(A) AFTER enter(B), and an unconditional null
              // there wipes the highlight while the cursor is sitting on B — the highlight
              // flickers out as you sweep across the graph.
              onNodeMouseLeave={(_, node) =>
                setHovered((current) => (current === node.id ? null : current))
              }
              onNodeClick={(_, node) => setSelected(node.id)}
              onPaneClick={() => setSelected(null)}
            >
              <Background color="#1e293b" gap={22} />
              <Controls showInteractive={false} />
              <MiniMap
                pannable
                zoomable
                nodeColor={(n) =>
                  KIND_STYLES[(n.data as CardData).node.kind].accent
                }
                maskColor="rgba(2, 6, 23, 0.75)"
                style={{ background: "#0b1220", border: "1px solid #1e293b" }}
              />
            </ReactFlow>
          </HighlightContext.Provider>
          {hovered && (
            <div className="hover-hint" data-testid="hover-hint">
              Highlighting everything upstream and downstream of{" "}
              <strong>{flow.nodes.find((n) => n.id === hovered)?.label}</strong>{" "}
              — {highlight.nodes.size} node(s) on the path
            </div>
          )}
        </main>

        <TrailPanel
          node={selectedNode}
          trail={selectedTrail}
          decision={selectedDecision}
          onClose={() => setSelected(null)}
        />
      </div>
    </div>
  );
}
