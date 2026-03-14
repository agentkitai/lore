// Layout modes: force, cluster-by-project, cluster-by-type
// Cluster view: collapsed (big circles) or expanded (individual nodes)

import { fetchClusters } from '../api.js';
import { hexToRgba } from '../utils.js';
import { MEMORY_COLORS, ENTITY_COLORS } from '../colors.js';

const COLOR_MAP = { ...MEMORY_COLORS, ...ENTITY_COLORS };

export class LayoutManager {
  constructor(state, simulation, renderer) {
    this.state = state;
    this.simulation = simulation;
    this.renderer = renderer;
    this._clusters = null;
    // Collapsed cluster view: show clusters as big circles
    this._collapsed = true;
    this._expandedCluster = null; // label of expanded cluster, or null
    this._clusterPositions = new Map(); // label -> {x, y, r, count, color, node_ids}
  }

  async switchMode(mode) {
    this.state.setViewMode(mode);

    if (mode === 'force') {
      this.simulation.alpha(0.5).restart();
      this._clusters = null;
      this._collapsed = false;
      this._expandedCluster = null;
      this._clusterPositions.clear();
      // Unhide all nodes
      for (const n of this.state.nodes) { n._clusterHidden = false; }
      return;
    }

    const groupBy = mode === 'cluster-project' ? 'project' : 'type';
    try {
      const data = await fetchClusters(groupBy);
      this._clusters = data.clusters;
      this._collapsed = true;
      this._expandedCluster = null;
      this._computeClusterPositions();
      // Hide all individual nodes in collapsed mode
      for (const n of this.state.nodes) { n._clusterHidden = true; }
      this.simulation.stop();
      this.renderer.render();
    } catch {
      // Fall back to force
    }
  }

  _computeClusterPositions() {
    if (!this._clusters) return;
    this._clusterPositions.clear();
    const cols = Math.ceil(Math.sqrt(this._clusters.length));
    const spacing = 250;

    for (let i = 0; i < this._clusters.length; i++) {
      const cluster = this._clusters[i];
      const count = cluster.node_ids.length;
      const color = COLOR_MAP[cluster.label] || '#6b8afd';
      const r = 30 + Math.sqrt(count) * 8; // radius scales with sqrt of count
      this._clusterPositions.set(cluster.label, {
        x: (i % cols) * spacing + spacing / 2,
        y: Math.floor(i / cols) * spacing + spacing / 2,
        r,
        count,
        color,
        node_ids: cluster.node_ids,
        label: cluster.label,
      });
    }
  }

  isCollapsed() {
    return this._collapsed && this._clusters != null;
  }

  getExpandedCluster() {
    return this._expandedCluster;
  }

  getClusterPositions() {
    return this._clusterPositions;
  }

  // Click handler: check if a cluster circle was clicked
  handleClusterClick(gx, gy) {
    if (!this.isCollapsed()) return false;

    for (const [label, cp] of this._clusterPositions) {
      const dx = gx - cp.x;
      const dy = gy - cp.y;
      if (dx * dx + dy * dy <= cp.r * cp.r) {
        this.expandCluster(label);
        return true;
      }
    }
    return false;
  }

  expandCluster(label) {
    this._expandedCluster = label;
    this._collapsed = false;
    const cp = this._clusterPositions.get(label);
    if (!cp) return;

    const nodeMap = this.state._nodeMap;
    const nodeIds = new Set(cp.node_ids);

    // Show nodes in expanded cluster, hide others
    for (const n of this.state.nodes) {
      n._clusterHidden = !nodeIds.has(n.id);
      if (nodeIds.has(n.id)) {
        // Position around cluster center
        const idx = cp.node_ids.indexOf(n.id);
        const angle = (idx / cp.count) * Math.PI * 2;
        const r = 20 + cp.count * 2;
        n.fx = cp.x + Math.cos(angle) * r;
        n.fy = cp.y + Math.sin(angle) * r;
      }
    }

    this.simulation.alpha(0.3).restart();

    // Unpin after settling
    setTimeout(() => {
      for (const n of this.state.nodes) {
        if (!n._clusterHidden) {
          n.fx = null;
          n.fy = null;
        }
      }
    }, 1000);
  }

  collapseBack() {
    this._collapsed = true;
    this._expandedCluster = null;
    for (const n of this.state.nodes) { n._clusterHidden = true; }
    this.simulation.stop();
    this.renderer.render();
  }

  // Draw collapsed cluster circles
  drawCollapsedClusters(ctx, zoom) {
    if (!this.isCollapsed()) return;

    for (const [label, cp] of this._clusterPositions) {
      // Outer glow
      ctx.beginPath();
      ctx.arc(cp.x, cp.y, cp.r + 4, 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(cp.color, 0.1);
      ctx.fill();

      // Main circle
      ctx.beginPath();
      ctx.arc(cp.x, cp.y, cp.r, 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(cp.color, 0.35);
      ctx.fill();
      ctx.strokeStyle = hexToRgba(cp.color, 0.7);
      ctx.lineWidth = 2;
      ctx.stroke();

      // Count
      ctx.font = 'bold ' + Math.max(14, cp.r * 0.5) + 'px -apple-system, sans-serif';
      ctx.fillStyle = '#fff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(cp.count, cp.x, cp.y);

      // Label below
      ctx.font = '12px -apple-system, sans-serif';
      ctx.fillStyle = hexToRgba(cp.color, 0.9);
      ctx.textBaseline = 'top';
      ctx.fillText(label, cp.x, cp.y + cp.r + 8);
    }
  }

  getClusters() {
    return this._clusters;
  }

  drawClusterHulls(ctx) {
    if (!this._clusters || this.isCollapsed()) return;

    for (const cluster of this._clusters) {
      const points = [];
      for (const id of cluster.node_ids) {
        const node = this.state._nodeMap.get(id);
        if (node && node.x != null && !node._clusterHidden) {
          points.push([node.x, node.y]);
        }
      }
      if (points.length < 3) continue;

      const hull = this._convexHull(points);
      if (hull.length < 3) continue;

      const color = COLOR_MAP[cluster.label] || '#6b8afd';

      ctx.beginPath();
      ctx.moveTo(hull[0][0], hull[0][1]);
      for (let i = 1; i < hull.length; i++) {
        ctx.lineTo(hull[i][0], hull[i][1]);
      }
      ctx.closePath();
      ctx.fillStyle = hexToRgba(color, 0.08);
      ctx.fill();
      ctx.strokeStyle = hexToRgba(color, 0.3);
      ctx.lineWidth = 1;
      ctx.stroke();

      const cx = points.reduce((s, p) => s + p[0], 0) / points.length;
      const cy = points.reduce((s, p) => s + p[1], 0) / points.length;
      ctx.font = '12px -apple-system, sans-serif';
      ctx.fillStyle = hexToRgba(color, 0.7);
      ctx.textAlign = 'center';
      ctx.fillText(cluster.label, cx, cy - points.length * 1.5 - 10);
    }
  }

  _convexHull(points) {
    if (points.length < 3) return points;
    points.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const lower = [];
    for (const p of points) {
      while (lower.length >= 2 && this._cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
        lower.pop();
      }
      lower.push(p);
    }
    const upper = [];
    for (let i = points.length - 1; i >= 0; i--) {
      const p = points[i];
      while (upper.length >= 2 && this._cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
        upper.pop();
      }
      upper.push(p);
    }
    lower.pop();
    upper.pop();
    return lower.concat(upper);
  }

  _cross(o, a, b) {
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  }
}
