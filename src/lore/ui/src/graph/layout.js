// Layout modes: force, cluster-by-project, cluster-by-type

import { fetchClusters } from '../api.js';
import { hexToRgba } from '../utils.js';
import { MEMORY_COLORS, ENTITY_COLORS } from '../colors.js';

export class LayoutManager {
  constructor(state, simulation, renderer) {
    this.state = state;
    this.simulation = simulation;
    this.renderer = renderer;
    this._clusters = null;
  }

  async switchMode(mode) {
    this.state.setViewMode(mode);

    if (mode === 'force') {
      // Restore force simulation
      this.simulation.alpha(0.5).restart();
      this._clusters = null;
      return;
    }

    const groupBy = mode === 'cluster-project' ? 'project' : 'type';
    try {
      const data = await fetchClusters(groupBy);
      this._clusters = data.clusters;
      this._applyClusterPositions(data.clusters);
    } catch {
      // Fall back to force
    }
  }

  _applyClusterPositions(clusters) {
    // Arrange clusters in a grid
    const cols = Math.ceil(Math.sqrt(clusters.length));
    const spacing = 300;
    const nodeMap = this.state._nodeMap;

    for (let i = 0; i < clusters.length; i++) {
      const cluster = clusters[i];
      const cx = (i % cols) * spacing + spacing / 2;
      const cy = Math.floor(i / cols) * spacing + spacing / 2;

      // Position nodes in this cluster around center
      const nodeIds = cluster.node_ids;
      const count = nodeIds.length;
      for (let j = 0; j < count; j++) {
        const node = nodeMap.get(nodeIds[j]);
        if (!node) continue;
        const angle = (j / count) * Math.PI * 2;
        const r = 20 + count * 2;
        node.fx = cx + Math.cos(angle) * r;
        node.fy = cy + Math.sin(angle) * r;
      }
    }

    this.simulation.alpha(0.3).restart();

    // Unpin after settling
    setTimeout(() => {
      for (const node of this.state.nodes) {
        node.fx = null;
        node.fy = null;
      }
    }, 1000);
  }

  getClusters() {
    return this._clusters;
  }

  drawClusterHulls(ctx) {
    if (!this._clusters) return;

    for (const cluster of this._clusters) {
      const points = [];
      for (const id of cluster.node_ids) {
        const node = this.state._nodeMap.get(id);
        if (node && node.x != null) {
          points.push([node.x, node.y]);
        }
      }
      if (points.length < 3) continue;

      // Simple convex hull (gift wrapping)
      const hull = this._convexHull(points);
      if (hull.length < 3) continue;

      const colorMap = { ...MEMORY_COLORS, ...ENTITY_COLORS };
      const color = colorMap[cluster.label] || '#6b8afd';

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

      // Label at center
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
