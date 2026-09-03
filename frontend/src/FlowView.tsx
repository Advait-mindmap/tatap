import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  MarkerType,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  useStore,
  type Edge,
  type Node,
} from "reactflow";
import "reactflow/dist/style.css";

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
import type {
  DecisionRecord,
  FlowEdge,
  FlowNode,
  NodeKind,
  TrailEntry,
} from "./types";
import "./styles.css";

const nodeTypes = { card: FlowNodeCard };

export interface FlowViewProps {
  nodes: FlowNode[]
  edges: FlowEdge[]
  trail: TrailEntry[]
  decisions: DecisionRecord[]
  /** Rendered in the top bar. Absent while a run is still streaming. */
  quality: Record<string, unknown>
  meta: Record<string, unknown>
  /** Slot for run controls (progress, decision prompt) above the legend. */
  aside?: React.ReactNode
  /**
   * Keep the growing graph in view while a run streams.
   *
   * fitView runs only on mount, and on mount the graph is EMPTY - so every node arriving
   * afterwards lands outside the viewport and the user watches a blank canvas while the plan
   * builds. Auto-fitting stops once the run settles, so it never fights a reader who has panned.
   */
  autoFit?: boolean
  /** A zone highlighted from the 3D model. Lights the activities that build it. */
  linkedZone?: string | null
  /** Fired when the cursor rests on a node that belongs to a zone, to drive the 3D model. */
  onHoverZone?: (zoneId: string | null) => void
  /**
   * Canvas only: no legend column, no trail panel.
   *
   * In the linked view this pane holds half the window, and the three-column layout squeezed
   * the graph itself down to a sliver between two panels - the one thing the reader is there to
   * watch. The panels are a click away in the full 2D view.
   */
  compact?: boolean
}

/**
 * The view, wrapped so React Flow's hooks are available to it.
 *
 * Driving the canvas through an imperative instance ref turned out to be the wrong tool: the
 * ref was never assigned, and once it was, `fitView` still refused to run because React Flow
 * had not measured the nodes and there was no way to ask it when it had. `useNodesInitialized`
 * answers exactly that question, and needs a provider above the component that calls it.
 */
export function FlowView(props: FlowViewProps) {
  return (
    <ReactFlowProvider>
      <FlowViewInner {...props} />
    </ReactFlowProvider>
  );
}

function FlowViewInner({
  nodes: flowNodes,
  edges: flowEdges,
  trail,
  decisions,
  quality,
  meta,
  aside,
  autoFit = false,
  linkedZone = null,
  onHoverZone,
  compact = false,
}: FlowViewProps) {
  const reactFlow = useReactFlow();
  // True once React Flow has measured every node. Until then it marks them `visibility: hidden`
  // and refuses to fit - which is why every fit attempted the instant a batch of nodes arrived
  // returned false and did nothing.
  const nodesInitialized = useNodesInitialized();
  // React Flow can only fit once its zoom behaviour is attached and the pane has a size.
  // Watching that in the store is what makes the fit reliable: it is false for the first
  // frames, which is exactly when a finished graph lands.
  const canFit = useStore(
    (st) => Boolean(st.d3Zoom && st.d3Selection && st.width && st.height),
  );
  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hiddenStages, setHiddenStages] = useState<Set<string>>(new Set());
  const [hiddenKinds, setHiddenKinds] = useState<Set<NodeKind>>(new Set());

  const flow = { nodes: flowNodes, edges: flowEdges };

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

  /**
   * Does any activity here actually build the linked zone?
   *
   * Only a few activities carry a zone_id, so most zones have no work pointing at them. Passing
   * such a zone through would dim EVERY card and light none - the reader gets a uniformly grey
   * plan with no explanation, which is worse than no link at all. When nothing matches, the 2D
   * view is left alone and the 3D panel says why.
   */
  const zoneHasWork = useMemo(
    () => (linkedZone ? flow.nodes.some((n) => n.zone_id === linkedZone) : false),
    [flow.nodes, linkedZone],
  );

  const highlightState: HighlightState = useMemo(
    () => ({
      hovered,
      selected,
      path: highlight.nodes,
      direct: highlight.direct,
      zone: zoneHasWork ? linkedZone : null,
    }),
    [hovered, selected, highlight, linkedZone, zoneHasWork],
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

  /**
   * A signature of what is on the canvas, not just how much.
   *
   * The fit used to key on `nodes.length`, which fails at the single most important moment: when
   * a run completes, the authoritative SimulationOutput REPLACES the streamed graph with the
   * same number of nodes, so the length never changes and the fit never fires. The reader was
   * left parked wherever the last mid-run fit had put them - in practice, on an empty corner of
   * a 3700px-wide programme.
   */
  const signature = useMemo(() => nodes.map((n) => n.id).join("|"), [nodes]);
  const signatureRef = useRef("");
  const fittedRef = useRef("");
  signatureRef.current = signature;

  /**
   * Fit whenever the canvas changes, once React Flow is able to.
   *
   * Three separate faults conspired here, and each hid the next:
   *
   *  - the fit keyed on `nodes.length`, so the most important fit of all - the one after a
   *    completed run replaces the streamed graph with the authoritative output, node for node -
   *    never fired at all;
   *  - it ran through an instance ref that was never assigned, so every call was a no-op;
   *  - and when that was fixed it still recorded a FAILED fit as done. `fitView` returns false
   *    until the nodes are measured and the zoom behaviour is attached, which is precisely the
   *    state a moment after a batch of nodes arrives.
   *
   * The result a user saw: one card in the corner of an empty canvas, with the other 67 nodes
   * of a 3700px programme off-screen. Only a successful fit counts as done, so the next render
   * retries until it takes.
   */
  useEffect(() => {
    if (!autoFit || !nodesInitialized || !canFit) return;
    if (!signature || signature === fittedRef.current) return;
    // Low enough to show a full thirteen-stage programme. Seeing all of it is not the same as
    // reading it - that is what the decision stepper is for - but it beats seeing none of it.
    if (reactFlow.fitView({ padding: 0.08, minZoom: 0.12, maxZoom: 1.0 })) {
      fittedRef.current = signature;
    }
  }, [autoFit, nodesInitialized, canFit, signature, reactFlow]);

  /**
   * Bring the decision points within reach.
   *
   * Stop-and-ask is the product's differentiator, and at a whole-programme zoom every fork is a
   * two-pixel smudge somewhere in 3700px of canvas. Measured on a real completed run: seven of
   * eight decision points were off-screen and all twenty-nine activities were. Cycling centres
   * them one at a time at a readable zoom, so the questions the plan stopped on are one click
   * away instead of a hunt.
   */
  const forkNodes = useMemo(
    () => nodes.filter((n) => (n.data as CardData).node.kind === "decision_point"),
    [nodes],
  );
  const forkCursor = useRef(0);

  const focusNextFork = useCallback(() => {
    if (forkNodes.length === 0) return;
    const target = forkNodes[forkCursor.current % forkNodes.length];
    forkCursor.current += 1;

    // setCenter with an explicit zoom, not fitView with a large padding.
    //
    // fitView derives zoom from the node's bounds and the pane size, and its minZoom is a floor
    // on that calculation - which did not survive the trip to a CI runner: the same click that
    // gave 0.75 locally gave about 0.5 there, rendering the label at 6.2px instead of 9.4px.
    // "Centre this fork at a readable zoom" is a statement about zoom, so say it directly and
    // it holds on every machine.
    const node = reactFlow.getNode(target.id);
    const width = node?.width ?? 240;
    const height = node?.height ?? 80;
    reactFlow.setCenter(
      (node?.position.x ?? 0) + width / 2,
      (node?.position.y ?? 0) + height / 2,
      { zoom: 0.9, duration: 400 },
    );
    setSelected(target.id);
  }, [forkNodes, reactFlow]);

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
          {/* The differentiator, made reachable. At whole-programme zoom every fork is a
              smudge somewhere in 3700px of canvas; this walks them one at a time. */}
          {forkNodes.length > 0 && (
            <button
              className="badge badge-action"
              onClick={focusNextFork}
              data-testid="focus-decision-button"
              title="Centre the next decision point"
            >
              ⌖ {forkNodes.length} decision{forkNodes.length === 1 ? "" : "s"}
            </button>
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
        {!compact && <nav className="sidebar">
          {aside}
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
        </nav>}

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
              onNodeMouseEnter={(_, node) => {
                setHovered(node.id);
                // Drive the 3D model. Only nodes that build a zone have one to point at;
                // approvals and procurement work is real but has no geometry.
                onHoverZone?.((node.data as CardData).node.zone_id ?? null);
              }}
              // Only clear if we are leaving the node we are actually tracking. Moving between
              // adjacent nodes can deliver leave(A) AFTER enter(B), and an unconditional null
              // there wipes the highlight while the cursor is sitting on B — the highlight
              // flickers out as you sweep across the graph.
              onNodeMouseLeave={(_, node) => {
                setHovered((current) => (current === node.id ? null : current));
                onHoverZone?.(null);
              }}
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

        {!compact && (
          <TrailPanel
            node={selectedNode}
            trail={selectedTrail}
            decision={selectedDecision}
            onClose={() => setSelected(null)}
          />
        )}
      </div>
    </div>
  );
}
