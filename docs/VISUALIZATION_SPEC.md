# VISUALIZATION SPEC — 2D flow, 3D/4D build, progressive, interactive

The simulation must be watchable and cross-verifiable. Three linked views, all driven by the one
simulation event stream and output (`SIMULATION_AND_REASONING.md` §2, §7). Frontend: React +
TypeScript (Vite). 2D flow: React Flow. 3D: react-three-fiber + three.js + drei. State shared so
the three views highlight in sync.

## 1. The 2D process-flow view (cross-verify the simulation)

- Renders `flow.nodes` and `flow.edges` as a process-flow / dependency diagram.
- **Node kinds are visually distinct:** stage, work package, activity, milestone, compliance gate,
  quality hold, and **decision point** (called out prominently — this is where thought stopped).
- **Draws progressively.** As the simulation emits events, nodes and edges appear one by one, so a
  reviewer literally watches the plan get built. A play/pause/step control replays it.
- **Hover-to-highlight.** Resting the cursor on any node highlights the specific flows tied to it:
  its predecessors and successors, the critical path through it, its compliance gates and material
  links. Everything else dims. This is the "highlight the flows for the highlighted section"
  behaviour, in 2D.
- **Click** opens the reasoning trail for that node (what, why, cited real-execution source,
  confidence, who decided it).
- Grouping/collapse by stage, WBS or department so a 40k-node graph stays navigable (never render
  the whole graph flat; expand on demand).

## 2. The 3D / 4D build model (see which stage we're in)

- A schematic 3D model of the data centre: the site, the building shell, and the internal
  **zones/systems** (data halls, electrical rooms, UPS/battery rooms, generator yard, cooling
  plant, etc.) from `zones`. Not photoreal BIM; a clean massing/schematic model, generated
  parametrically from the equipment counts and layout rules.
- **4D = 3D + the schedule timeline.** A time scrubber runs from start to RFS. As it advances (or
  as the simulation proceeds live), each zone/element **appears and changes state** in step with
  its stage: foundations, then structure, then MEP rough-in, then fit-out, then commissioning.
  You can see, at any point, which construction stage the project is in.
- **Progressive build.** In live mode the 3D model grows as the simulation completes each stage,
  one by one, matching the 2D flow drawing itself.
- **Hover-to-highlight, in 3D.** Resting the cursor on a zone (raycast pick) highlights that zone
  and drives the linked 2D view to highlight the specific flows for it (its activities, deps,
  critical path, gates). Cursor on the 2D node likewise highlights the zone in 3D. The two views
  are bound through a shared `highlight(zone_id | node_id)` state.
- Stage colour-coding and a legend; a per-zone panel showing its activities, dates, % complete,
  gates and reasoning trail.

## 3. Linked interaction model

One shared selection/highlight state across all views:
- `select(node_id)` / `select(zone_id)` — opens detail + reasoning trail.
- `highlight(ref)` — on hover; dims the rest, lights the connected flow in both 2D and 3D.
- `time(t)` — the scrubber; sets the 4D model state and can move the 2D view's "as-of" marker.
- `simulationEvent(e)` — live stream; appends to 2D, grows 3D, raises decision prompts.

## 4. Decision points in the UI

When the simulation emits `decision_needed`, the UI:
- pauses the progressive draw, pulses the decision node in the 2D flow,
- shows a panel: the question, *why it is stuck*, the options, and the impact of each,
- on answer, records it and resumes the draw and the 3D build.
Resolved decisions remain visible in the flow (with the answer) so the reasoning is auditable.

## 5. Performance at scale

- 2D: virtualise; render the current stage/zone and expand on demand; never mount 40k nodes at once.
- 3D: instanced meshes per zone/system; level-of-detail; only animate state changes in the visible
  time window.
- Both views read from the same normalised store; the timeline and highlight are O(1) lookups.

## 6. Build order for the visuals (see the build plan for placement)

1. 2D flow that renders a completed simulation, with hover-highlight and click-to-trail.
2. Progressive draw from the live event stream + decision-point prompts.
3. Schematic 3D model from `zones`, static.
4. 4D time scrubber + stage state changes.
5. 3D hover-pick bound to the 2D highlight (linked views).
6. Progressive 3D build synced to the live simulation.
