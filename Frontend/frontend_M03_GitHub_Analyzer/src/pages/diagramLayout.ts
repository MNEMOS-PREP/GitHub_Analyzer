/**
 * M03 · a left-to-right layered layout for the import graph. Pure, no dependencies, no DOM.
 *
 * Column 0 holds the entry points (and the files nothing imports); every other connected file sits one column right
 * of the deepest file that imports it, so most arrows point right. Import loops are tolerated: one edge of each loop
 * is set aside before layering and drawn as a dashed arrow back. Files with no arrows at all are put in their own
 * columns at the end. A tall column wraps into several.
 */

export interface LayoutNodeIn {
  id: string;
  path: string;
  group: string;
  in_degree: number;
  entry: boolean;
}

export interface LayoutEdgeIn {
  from: string;
  to: string;
}

export interface PlacedNode {
  id: string;
  x: number;
  y: number;
  /** The import depth (column before wrapping); -1 for files with no arrows. */
  layer: number;
}

export interface PlacedEdge {
  from: string;
  to: string;
  /** True when the arrow points left or straight down a column: part of a loop, or a shortcut back. */
  back: boolean;
  d: string;
}

export interface Layout {
  width: number;
  height: number;
  nodes: Map<string, PlacedNode>;
  edges: PlacedEdge[];
  /** x where the "no arrows" columns start, or null when there are none. */
  looseX: number | null;
}

export const NODE_W = 156;
export const NODE_H = 30;
const COL_GAP = 64;
const ROW_GAP = 12;
const PAD = 16;
const MAX_ROWS = 9;

export function layout(nodes: LayoutNodeIn[], edges: LayoutEdgeIn[]): Layout {
  const ids = new Set(nodes.map((n) => n.id));
  const live = edges.filter((e) => ids.has(e.from) && ids.has(e.to) && e.from !== e.to);
  const out = new Map<string, string[]>();
  const into = new Map<string, string[]>();
  for (const e of live) {
    out.set(e.from, [...(out.get(e.from) ?? []), e.to]);
    into.set(e.to, [...(into.get(e.to) ?? []), e.from]);
  }
  const linked = (id: string) => out.has(id) || into.has(id);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const stable = (a: LayoutNodeIn, b: LayoutNodeIn) => b.in_degree - a.in_degree || a.path.localeCompare(b.path);

  /* break loops: a depth-first walk from the entry points (then the files nothing imports, then the rest) drops
     every edge that points back into the current path; what is left has no cycle */
  const connected = nodes.filter((n) => linked(n.id) || n.entry);
  const entries = connected.filter((n) => n.entry).sort(stable).map((n) => n.id);
  const sources = connected.filter((n) => !into.has(n.id)).sort(stable).map((n) => n.id);
  const busiest = [...connected].sort((a, b) => (out.get(b.id)?.length ?? 0) - (out.get(a.id)?.length ?? 0) || stable(a, b));
  const looping = new Set<string>();
  const state = new Map<string, 1 | 2>();                                       // 1 on the path, 2 finished
  const walk = (start: string) => {
    if (state.has(start)) return;
    const stack: [string, number][] = [[start, 0]];
    state.set(start, 1);
    while (stack.length) {
      const top = stack[stack.length - 1];
      const next = (out.get(top[0]) ?? [])[top[1]++];
      if (next === undefined) { state.set(top[0], 2); stack.pop(); continue; }
      if (state.get(next) === 1) looping.add(`${top[0]}\u0000${next}`);
      else if (!state.has(next)) { state.set(next, 1); stack.push([next, 0]); }
    }
  };
  for (const id of [...entries, ...sources, ...busiest.map((n) => n.id)]) walk(id);

  /* layers: the longest import chain to a file, with the entry points pinned to the first column */
  const pinned = new Set(entries);
  const layer = new Map<string, number>();
  const preds = (id: string) => (into.get(id) ?? []).filter((p) => !looping.has(`${p}\u0000${id}`));
  const depth = (start: string): number => {
    const stack = [start];
    while (stack.length) {
      const id = stack[stack.length - 1];
      if (layer.has(id)) { stack.pop(); continue; }
      if (pinned.has(id)) { layer.set(id, 0); stack.pop(); continue; }
      const todo = preds(id).filter((p) => !layer.has(p));
      if (todo.length) { stack.push(...todo); continue; }
      layer.set(id, Math.max(-1, ...preds(id).map((p) => layer.get(p)!)) + 1);
      stack.pop();
    }
    return layer.get(start)!;
  };
  for (const n of connected) depth(n.id);

  /* order inside a layer: group, then by the mean row of the files that import it (fewer crossings) */
  const layers: string[][] = [];
  for (const [id, l] of layer) (layers[l] ??= []).push(id);
  const row = new Map<string, number>();
  layers.forEach((ids, l) => {
    const score = (id: string) => {
      const ps = (into.get(id) ?? []).filter((p) => (layer.get(p) ?? l) < l).map((p) => row.get(p) ?? 0);
      return ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : Number.POSITIVE_INFINITY;
    };
    ids.sort((a, b) => {
      const na = byId.get(a)!, nb = byId.get(b)!;
      if (l === 0) return Number(nb.entry) - Number(na.entry) || na.group.localeCompare(nb.group) || stable(na, nb);
      const sa = score(a), sb = score(b);
      return sa === sb ? na.group.localeCompare(nb.group) || stable(na, nb) : sa - sb;
    });
    ids.forEach((id, i) => row.set(id, i));
  });

  /* place: each layer wraps into ceil(n / MAX_ROWS) physical columns */
  const placed = new Map<string, PlacedNode>();
  let col = 0;
  let rows = 0;
  const put = (ids: string[], l: number) => {
    for (let i = 0; i < ids.length; i += MAX_ROWS) {
      const chunk = ids.slice(i, i + MAX_ROWS);
      chunk.forEach((id, r) =>
        placed.set(id, { id, layer: l, x: PAD + col * (NODE_W + COL_GAP), y: PAD + r * (NODE_H + ROW_GAP) }));
      rows = Math.max(rows, chunk.length);
      col += 1;
    }
  };
  layers.forEach((ids, l) => ids && put(ids, l));
  const loose = nodes.filter((n) => !layer.has(n.id)).sort((a, b) => a.group.localeCompare(b.group) || stable(a, b));
  const looseX = loose.length ? PAD + col * (NODE_W + COL_GAP) : null;
  put(loose.map((n) => n.id), -1);

  const edgesOut: PlacedEdge[] = live.map((e) => {
    const a = placed.get(e.from)!, b = placed.get(e.to)!;
    const ay = a.y + NODE_H / 2, by = b.y + NODE_H / 2;
    if (b.x > a.x) {
      const x1 = a.x + NODE_W, x2 = b.x, mid = (x1 + x2) / 2;
      return { from: e.from, to: e.to, back: false, d: `M${x1},${ay} C${mid},${ay} ${mid},${by} ${x2},${by}` };
    }
    // a loop or a same-column import: leave from the right edge and come back into the target's right edge
    const x1 = a.x + NODE_W, x2 = b.x + NODE_W, bulge = Math.max(x1, x2) + COL_GAP * 0.45;
    return { from: e.from, to: e.to, back: true, d: `M${x1},${ay} C${bulge},${ay} ${bulge},${by} ${x2},${by}` };
  });

  return {
    width: Math.max(PAD * 2 + NODE_W, PAD + col * (NODE_W + COL_GAP) - COL_GAP + PAD),
    height: PAD * 2 + Math.max(1, rows) * (NODE_H + ROW_GAP) - ROW_GAP,
    nodes: placed,
    edges: edgesOut,
    looseX,
  };
}

/** The file name, shortened to fit a node. */
export function basename(path: string, max = 22): string {
  const name = path.split("/").pop() || path;
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}
