// Canvas-based graph renderer

import { getNodeColor } from '../colors.js';
import { truncateText, hexToRgba } from '../utils.js';
import { getNodeRadius } from './simulation.js';

export class GraphRenderer {
  constructor(canvas, state) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.state = state;
    this.transform = { x: 0, y: 0, k: 1 };
    this._animFrame = null;
    this._pulsePhase = 0;
  }

  setTransform(t) {
    this.transform = t;
  }

  resize(w, h) {
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = w * dpr;
    this.canvas.height = h * dpr;
    this.canvas.style.width = w + 'px';
    this.canvas.style.height = h + 'px';
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  render() {
    const ctx = this.ctx;
    const { x, y, k } = this.transform;
    const w = this.canvas.width / (window.devicePixelRatio || 1);
    const h = this.canvas.height / (window.devicePixelRatio || 1);

    ctx.save();
    ctx.clearRect(0, 0, w, h);
    ctx.translate(x, y);
    ctx.scale(k, k);

    this._pulsePhase = (Date.now() % 1500) / 1500;

    // Draw edges
    this._drawEdges(ctx, k);

    // Draw nodes
    this._drawNodes(ctx, k);

    ctx.restore();
  }

  _drawEdges(ctx, zoom) {
    const { edges, nodes, filteredNodeIds, hoveredEdge, searchResults } = this.state;
    const nodeMap = this.state._nodeMap;

    for (const edge of edges) {
      const src = typeof edge.source === 'object' ? edge.source : nodeMap.get(edge.source);
      const tgt = typeof edge.target === 'object' ? edge.target : nodeMap.get(edge.target);
      if (!src || !tgt || src.x == null || tgt.x == null) continue;

      const srcFiltered = filteredNodeIds.has(src.id);
      const tgtFiltered = filteredNodeIds.has(tgt.id);
      const dimmed = !srcFiltered || !tgtFiltered;
      const isHovered = hoveredEdge === edge;

      const color = getNodeColor(src);
      const alpha = isHovered ? 1.0 : dimmed ? 0.05 : 0.4;

      ctx.beginPath();
      ctx.moveTo(src.x, src.y);
      ctx.lineTo(tgt.x, tgt.y);
      ctx.strokeStyle = hexToRgba(color, alpha);
      ctx.lineWidth = isHovered ? 2.5 : 0.5 + (edge.weight || 0.5) * 2;
      ctx.stroke();

      // Arrowhead
      if (alpha > 0.1) {
        const dx = tgt.x - src.x;
        const dy = tgt.y - src.y;
        const len = Math.sqrt(dx * dx + dy * dy);
        if (len > 0) {
          const r = getNodeRadius(tgt);
          const ax = tgt.x - (dx / len) * (r + 4);
          const ay = tgt.y - (dy / len) * (r + 4);
          const angle = Math.atan2(dy, dx);
          const aSize = 5;
          ctx.beginPath();
          ctx.moveTo(ax, ay);
          ctx.lineTo(ax - aSize * Math.cos(angle - 0.4), ay - aSize * Math.sin(angle - 0.4));
          ctx.lineTo(ax - aSize * Math.cos(angle + 0.4), ay - aSize * Math.sin(angle + 0.4));
          ctx.closePath();
          ctx.fillStyle = hexToRgba(color, alpha);
          ctx.fill();
        }
      }

      // Edge label on hover
      if (isHovered && zoom > 0.5) {
        const mx = (src.x + tgt.x) / 2;
        const my = (src.y + tgt.y) / 2;
        ctx.font = '10px -apple-system, sans-serif';
        ctx.fillStyle = '#e2e8f0';
        ctx.textAlign = 'center';
        ctx.fillText(edge.rel_type || edge.label || '', mx, my - 4);
      }
    }
  }

  _drawNodes(ctx, zoom) {
    const { nodes, filteredNodeIds, selectedNodeId, hoveredNodeId, searchResults } = this.state;

    for (const node of nodes) {
      if (node.x == null) continue;
      const r = getNodeRadius(node);
      const color = getNodeColor(node);
      const isFiltered = filteredNodeIds.has(node.id);
      const isSelected = selectedNodeId === node.id;
      const isHovered = hoveredNodeId === node.id;
      const isSearchMatch = searchResults.size > 0 && searchResults.has(node.id);
      const isDimmedBySearch = searchResults.size > 0 && !searchResults.has(node.id);

      let alpha = isFiltered ? (node.confidence || 1.0) : 0.1;
      if (isDimmedBySearch) alpha = 0.2;
      alpha = Math.max(0.05, Math.min(1.0, alpha));

      // Search match pulse
      if (isSearchMatch) {
        const pulse = 0.6 + 0.4 * Math.sin(this._pulsePhase * Math.PI * 2);
        ctx.save();
        ctx.beginPath();
        ctx.arc(node.x, node.y, r + 6, 0, Math.PI * 2);
        ctx.fillStyle = hexToRgba(color, 0.3 * pulse);
        ctx.fill();
        ctx.restore();
      }

      // Selection glow
      if (isSelected) {
        ctx.save();
        ctx.beginPath();
        ctx.arc(node.x, node.y, r + 5, 0, Math.PI * 2);
        ctx.fillStyle = hexToRgba('#fbbf24', 0.4);
        ctx.fill();
        ctx.restore();
      }

      // Draw node shape
      ctx.beginPath();
      if (node.kind === 'entity') {
        this._drawHexagon(ctx, node.x, node.y, r);
      } else {
        ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
      }
      ctx.fillStyle = hexToRgba(color, alpha);
      ctx.fill();

      // Border
      const borderColor = isSelected ? '#fbbf24' : isHovered ? '#ffffff' : color;
      const borderAlpha = isSelected || isHovered ? 1.0 : alpha * 0.8;
      ctx.strokeStyle = hexToRgba(borderColor, borderAlpha);
      ctx.lineWidth = isSelected ? 2.5 : 1;

      // Tier border style
      if (node.tier === 'working') {
        ctx.setLineDash([4, 3]);
      } else if (node.tier === 'short') {
        ctx.setLineDash([2, 2]);
      } else {
        ctx.setLineDash([]);
      }
      ctx.stroke();
      ctx.setLineDash([]);

      // Label (only when zoomed in enough)
      if (zoom > 0.8 && isFiltered && alpha > 0.3) {
        const label = truncateText(node.label, 16);
        ctx.font = '11px -apple-system, BlinkMacSystemFont, sans-serif';
        ctx.fillStyle = hexToRgba('#e2e8f0', alpha);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(label, node.x, node.y + r + 3);
      }
    }
  }

  _drawHexagon(ctx, x, y, r) {
    const sides = 6;
    ctx.moveTo(x + r * Math.cos(0), y + r * Math.sin(0));
    for (let i = 1; i <= sides; i++) {
      const angle = (i * 2 * Math.PI) / sides;
      ctx.lineTo(x + r * Math.cos(angle), y + r * Math.sin(angle));
    }
    ctx.closePath();
  }

  startLoop() {
    const loop = () => {
      this.render();
      this._animFrame = requestAnimationFrame(loop);
    };
    loop();
  }

  stopLoop() {
    if (this._animFrame) {
      cancelAnimationFrame(this._animFrame);
      this._animFrame = null;
    }
  }
}
