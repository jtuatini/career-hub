import { useEffect, useMemo, useRef, useState } from "react";
import {
  forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation,
  type Simulation, type SimulationNodeDatum,
} from "d3-force";
import { ENTITY_TYPES } from "../api";
import type { GraphData, GraphNode } from "../api";

interface SimNode extends SimulationNodeDatum {
  id: number;
  node: GraphNode;
}
interface SimLink {
  id: number;
  relation: string | null;
  source: SimNode;
  target: SimNode;
}

const isEntity = (type: string) => (ENTITY_TYPES as readonly string[]).includes(type);
const radius = (n: GraphNode) =>
  (isEntity(n.type) ? 13 : 8) + Math.min(6, n.degree * 1.2);

interface Props {
  data: GraphData;
  selectedId: number | null;
  highlightIds: Set<number> | null; // semantic-search matches, null = no search
  onSelect: (id: number | null) => void;
}

export default function MemoryGraph({ data, selectedId, highlightIds, onSelect }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const simRef = useRef<Simulation<SimNode, SimLink> | null>(null);
  const [nodes, setNodes] = useState<SimNode[]>([]);
  const [links, setLinks] = useState<SimLink[]>([]);
  const [hoverId, setHoverId] = useState<number | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const dragRef = useRef<{ mode: "node" | "pan"; node?: SimNode; sx: number; sy: number; ox: number; oy: number } | null>(null);
  const suppressClickRef = useRef(false);

  const neighbors = useMemo(() => {
    const map = new Map<number, Set<number>>();
    for (const l of data.links) {
      if (!map.has(l.from_id)) map.set(l.from_id, new Set());
      if (!map.has(l.to_id)) map.set(l.to_id, new Set());
      map.get(l.from_id)!.add(l.to_id);
      map.get(l.to_id)!.add(l.from_id);
    }
    return map;
  }, [data]);

  useEffect(() => {
    const simNodes: SimNode[] = data.nodes.map((n) => ({ id: n.id, node: n }));
    const byId = new Map(simNodes.map((n) => [n.id, n]));
    const simLinks: SimLink[] = data.links
      .filter((l) => byId.has(l.from_id) && byId.has(l.to_id))
      .map((l) => ({
        id: l.id, relation: l.relation,
        source: byId.get(l.from_id)!, target: byId.get(l.to_id)!,
      }));
    const sim = forceSimulation<SimNode>(simNodes)
      .force("link", forceLink<SimNode, SimLink>(simLinks).id((d) => d.id).distance(70).strength(0.4))
      .force("charge", forceManyBody().strength(-160))
      .force("collide", forceCollide<SimNode>().radius((d) => radius(d.node) + 6))
      .force("center", forceCenter(0, 0))
      .on("tick", () => {
        setNodes([...simNodes]);
        setLinks([...simLinks]);
      });
    simRef.current = sim;
    return () => { sim.stop(); };
  }, [data]);

  // The lit set: hover wins; else selection; else search matches; else everything.
  const lit = useMemo(() => {
    const focus = hoverId ?? selectedId;
    if (focus != null) {
      const set = new Set([focus, ...(neighbors.get(focus) ?? [])]);
      return set;
    }
    return highlightIds; // null = no dimming at all
  }, [hoverId, selectedId, highlightIds, neighbors]);

  // The svg uses a fixed viewBox (-450 -300 900 600) with the default
  // preserveAspectRatio (xMidYMid meet), so at real (non-900x600) aspect
  // ratios the viewBox is letterboxed and does not span the full client
  // rect. Map through the screen CTM inverse to get true viewBox
  // coordinates, then un-apply the pan/zoom transform as before.
  const toWorld = (clientX: number, clientY: number) => {
    const svg = svgRef.current!;
    const pt = new DOMPoint(clientX, clientY).matrixTransform(svg.getScreenCTM()!.inverse());
    return { x: (pt.x - view.x) / view.k, y: (pt.y - view.y) / view.k };
  };

  const onPointerDown = (e: React.PointerEvent, node?: SimNode) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    if (node) {
      dragRef.current = { mode: "node", node, sx: e.clientX, sy: e.clientY, ox: e.clientX, oy: e.clientY };
      simRef.current?.alphaTarget(0.25).restart();
    } else {
      dragRef.current = { mode: "pan", sx: e.clientX, sy: e.clientY, ox: e.clientX, oy: e.clientY };
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag) return;
    if (drag.mode === "node" && drag.node) {
      const p = toWorld(e.clientX, e.clientY);
      drag.node.fx = p.x;
      drag.node.fy = p.y;
    } else {
      setView((v) => ({ ...v, x: v.x + e.clientX - drag.sx, y: v.y + e.clientY - drag.sy }));
      drag.sx = e.clientX;
      drag.sy = e.clientY;
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (drag?.mode === "node" && drag.node) {
      const moved = Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) > 4;
      drag.node.fx = null;
      drag.node.fy = null;
      simRef.current?.alphaTarget(0);
      if (!moved) onSelect(drag.node.id);
    } else if (drag?.mode === "pan") {
      suppressClickRef.current = Math.hypot(e.clientX - drag.ox, e.clientY - drag.oy) > 4;
    }
    dragRef.current = null;
  };

  const onWheel = (e: React.WheelEvent) => {
    const k = Math.max(0.3, Math.min(3, view.k * (e.deltaY < 0 ? 1.12 : 0.89)));
    setView((v) => ({ ...v, k }));
  };

  const dim = (id: number) => (lit != null && !lit.has(id) ? " dim" : "");
  const litNode = (id: number) => (lit != null && lit.has(id) ? " lit" : "");

  return (
    <svg
      ref={svgRef}
      className="memory-graph"
      viewBox="-450 -300 900 600"
      onPointerDown={(e) => onPointerDown(e)}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onWheel={onWheel}
      onClick={() => {
        if (suppressClickRef.current) {
          suppressClickRef.current = false;
          return;
        }
        onSelect(null);
      }}
    >
      <g transform={`translate(${view.x}, ${view.y}) scale(${view.k})`}>
        <g>
          {links.map((l) => (
            <line
              key={l.id}
              className={`gedge${lit != null && (lit.has(l.source.id) && lit.has(l.target.id)) ? " lit" : ""}${
                lit != null && !(lit.has(l.source.id) && lit.has(l.target.id)) ? " dim" : ""}`}
              x1={l.source.x} y1={l.source.y} x2={l.target.x} y2={l.target.y}
            />
          ))}
          {nodes.map((n) => (
            <g
              key={n.id}
              className={`gnode gnode-${n.node.type}${n.node.muted ? " gmuted" : ""}${dim(n.id)}${litNode(n.id)}${
                n.id === selectedId ? " gselected" : ""}`}
              transform={`translate(${n.x ?? 0}, ${n.y ?? 0})`}
              onPointerDown={(e) => onPointerDown(e, n)}
              onMouseEnter={() => setHoverId(n.id)}
              onMouseLeave={() => setHoverId(null)}
              onClick={(e) => e.stopPropagation()}
            >
              <circle r={radius(n.node)} />
              {n.node.muted && <line className="gslash" x1={-radius(n.node)} y1={radius(n.node)} x2={radius(n.node)} y2={-radius(n.node)} />}
              {(isEntity(n.node.type) || n.id === hoverId || n.id === selectedId ||
                (lit != null && lit.has(n.id))) && (
                <text className="glabel" dy={radius(n.node) + 12}>{n.node.title}</text>
              )}
              <title>{`${n.node.type}: ${n.node.title}`}</title>
            </g>
          ))}
        </g>
      </g>
    </svg>
  );
}
