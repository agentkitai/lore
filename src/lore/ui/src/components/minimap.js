// Minimap component — overview of entire graph

import { getNodeColor } from '../colors.js';

export class Minimap {
  constructor(container, state, renderer) {
    this.state = state;
    this.renderer = renderer;
    this._canvas = document.createElement('canvas');
    this._canvas.className = 'minimap-canvas';
    this._canvas.width = 150;
    this._canvas.height = 150;
    container.appendChild(this._canvas);

    // Click to pan
    this._canvas.addEventListener('click', (e) => {
      const rect = this._canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      this._panTo(mx, my);
    });
  }

  render() {
    const canvas = this._canvas;
    const ctx = canvas.getContext('2d');
    const nodes = this.state.nodes;
    if (nodes.length === 0) return;

    ctx.clearRect(0, 0, 150, 150);
    ctx.fillStyle = '#0d0d14';
    ctx.fillRect(0, 0, 150, 150);

    // Compute bounds
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of nodes) {
      if (n.x == null) continue;
      if (n.x < minX) minX = n.x;
      if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.y > maxY) maxY = n.y;
    }
    const pad = 20;
    const rangeX = maxX - minX + pad * 2 || 1;
    const rangeY = maxY - minY + pad * 2 || 1;
    const scale = Math.min(150 / rangeX, 150 / rangeY);

    // Draw nodes as dots
    for (const n of nodes) {
      if (n.x == null) continue;
      const x = (n.x - minX + pad) * scale;
      const y = (n.y - minY + pad) * scale;
      const filtered = this.state.filteredNodeIds.has(n.id);
      ctx.fillStyle = getNodeColor(n);
      ctx.globalAlpha = filtered ? 0.8 : 0.1;
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    // Draw viewport rectangle
    const mainCanvas = this.renderer.canvas;
    const t = this.renderer.transform;
    const vw = mainCanvas.clientWidth;
    const vh = mainCanvas.clientHeight;

    const vx0 = (-t.x / t.k - minX + pad) * scale;
    const vy0 = (-t.y / t.k - minY + pad) * scale;
    const vw0 = (vw / t.k) * scale;
    const vh0 = (vh / t.k) * scale;

    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1;
    ctx.strokeRect(vx0, vy0, vw0, vh0);

    // Store scale info for panTo
    this._scale = scale;
    this._minX = minX;
    this._minY = minY;
    this._pad = pad;
  }

  _panTo(mx, my) {
    if (!this._scale) return;
    const gx = mx / this._scale + this._minX - this._pad;
    const gy = my / this._scale + this._minY - this._pad;
    const vw = this.renderer.canvas.clientWidth;
    const vh = this.renderer.canvas.clientHeight;
    const t = this.renderer.transform;
    this.renderer.setTransform({
      x: vw / 2 - gx * t.k,
      y: vh / 2 - gy * t.k,
      k: t.k,
    });
  }
}
