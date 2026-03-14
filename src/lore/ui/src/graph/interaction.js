// Mouse/touch interactions: zoom, pan, drag, click, hover, context menu

import { zoom, zoomIdentity } from 'd3-zoom';
import { select } from 'd3-selection';
import { quadtree } from 'd3-quadtree';
import { getNodeRadius } from './simulation.js';

export class InteractionManager {
  constructor(canvas, state, renderer, simulation) {
    this.canvas = canvas;
    this.state = state;
    this.renderer = renderer;
    this.simulation = simulation;
    this._quadtree = null;
    this._dragNode = null;
    this._contextMenu = null;
    this._tooltip = null;
    this._sortedNodes = [];

    this._initZoom();
    this._initMouseEvents();
    this._initKeyboard();
    this._initContextMenu();
    this._initTooltip();
  }

  rebuildQuadtree() {
    const nodes = this.state.nodes.filter(n => n.x != null);
    this._quadtree = quadtree()
      .x(d => d.x)
      .y(d => d.y)
      .addAll(nodes);
  }

  _findNode(sx, sy) {
    if (!this._quadtree) return null;
    const { x, y, k } = this.renderer.transform;
    const gx = (sx - x) / k;
    const gy = (sy - y) / k;
    let found = null;
    let minDist = Infinity;

    this._quadtree.visit((quad, x0, y0, x1, y1) => {
      if (quad.data) {
        const r = getNodeRadius(quad.data) + 4;
        const dx = gx - quad.data.x;
        const dy = gy - quad.data.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < r && dist < minDist) {
          found = quad.data;
          minDist = dist;
        }
      }
      return false;
    });
    return found;
  }

  _findEdge(sx, sy) {
    const { x, y, k } = this.renderer.transform;
    const gx = (sx - x) / k;
    const gy = (sy - y) / k;
    const threshold = 5 / k;
    let closest = null;
    let closestDist = threshold;

    for (const edge of this.state.edges) {
      const src = typeof edge.source === 'object' ? edge.source : this.state.getNode(edge.source);
      const tgt = typeof edge.target === 'object' ? edge.target : this.state.getNode(edge.target);
      if (!src || !tgt || src.x == null) continue;

      const dist = this._pointToSegmentDist(gx, gy, src.x, src.y, tgt.x, tgt.y);
      if (dist < closestDist) {
        closestDist = dist;
        closest = edge;
      }
    }
    return closest;
  }

  _pointToSegmentDist(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return Math.sqrt((px - x1) ** 2 + (py - y1) ** 2);
    let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
    t = Math.max(0, Math.min(1, t));
    const projX = x1 + t * dx, projY = y1 + t * dy;
    return Math.sqrt((px - projX) ** 2 + (py - projY) ** 2);
  }

  _initZoom() {
    this._zoomBehavior = zoom()
      .scaleExtent([0.1, 8])
      .on('zoom', (event) => {
        const t = event.transform;
        this.renderer.setTransform({ x: t.x, y: t.y, k: t.k });
      });

    const sel = select(this.canvas);
    sel.call(this._zoomBehavior);
    sel.on('dblclick.zoom', null);
  }

  _initMouseEvents() {
    let isDragging = false;

    this.canvas.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      const node = this._findNode(e.offsetX, e.offsetY);
      if (node) {
        isDragging = true;
        this._dragNode = node;
        node.fx = node.x;
        node.fy = node.y;
        this.simulation.alphaTarget(0.3).restart();
        this.canvas.style.cursor = 'grabbing';
      }
    });

    this.canvas.addEventListener('mousemove', (e) => {
      if (isDragging && this._dragNode) {
        const { x, y, k } = this.renderer.transform;
        this._dragNode.fx = (e.offsetX - x) / k;
        this._dragNode.fy = (e.offsetY - y) / k;
        return;
      }

      const node = this._findNode(e.offsetX, e.offsetY);
      if (node) {
        this.canvas.style.cursor = 'pointer';
        this.state.setHoveredNode(node.id);
        this.state.setHoveredEdge(null);
        this._showTooltip(e, node);
      } else {
        this.canvas.style.cursor = 'default';
        this.state.setHoveredNode(null);
        this._hideTooltip();

        const edge = this._findEdge(e.offsetX, e.offsetY);
        this.state.setHoveredEdge(edge);
        if (edge) {
          this._showEdgeTooltip(e, edge);
        }
      }
    });

    this.canvas.addEventListener('mouseup', () => {
      if (isDragging) {
        isDragging = false;
        this.simulation.alphaTarget(0);
        this._dragNode = null;
        this.canvas.style.cursor = 'default';
      }
    });

    this.canvas.addEventListener('click', (e) => {
      if (isDragging) return;
      const node = this._findNode(e.offsetX, e.offsetY);
      if (node) {
        this.state.selectNode(node.id);
      } else {
        this.state.selectNode(null);
      }
      this._hideContextMenu();
    });
  }

  _initKeyboard() {
    this._sortedNodes = [];

    document.addEventListener('keydown', (e) => {
      // Don't intercept when typing in an input
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        if (e.key === 'Escape') {
          e.target.blur();
        }
        return;
      }

      if (e.key === 'Escape') {
        if (this._contextMenu && this._contextMenu.style.display !== 'none') {
          this._hideContextMenu();
        } else if (this.state.searchQuery) {
          this.state.clearSearch();
          const searchInput = document.getElementById('search-input');
          if (searchInput) searchInput.value = '';
        } else if (this.state.selectedNodeId) {
          this.state.selectNode(null);
        }
        return;
      }

      if (e.key === 'Tab') {
        e.preventDefault();
        if (this._sortedNodes.length === 0) {
          this._sortedNodes = [...this.state.nodes]
            .filter(n => this.state.filteredNodeIds.has(n.id))
            .sort((a, b) => (b.importance || 0) - (a.importance || 0));
        }
        if (this._sortedNodes.length === 0) return;

        const currentIdx = this._sortedNodes.findIndex(n => n.id === this.state.selectedNodeId);
        const nextIdx = e.shiftKey
          ? (currentIdx <= 0 ? this._sortedNodes.length - 1 : currentIdx - 1)
          : (currentIdx + 1) % this._sortedNodes.length;
        const next = this._sortedNodes[nextIdx];
        this.state.selectNode(next.id);
        this.centerOnNode(next.id);
        this._announceSelection(next);
        return;
      }

      if (e.key === 'Enter' && this.state.selectedNodeId) {
        this.state.dispatchEvent(new CustomEvent('openDetail', { detail: { id: this.state.selectedNodeId } }));
        return;
      }

      const PAN = 50;
      if (e.key === 'ArrowUp') { this._pan(0, PAN); return; }
      if (e.key === 'ArrowDown') { this._pan(0, -PAN); return; }
      if (e.key === 'ArrowLeft') { this._pan(PAN, 0); return; }
      if (e.key === 'ArrowRight') { this._pan(-PAN, 0); return; }

      if (e.key === '+' || e.key === '=') { this._zoomBy(1.2); return; }
      if (e.key === '-') { this._zoomBy(0.8); return; }
    });

    this.state.addEventListener('filterChange', () => { this._sortedNodes = []; });
  }

  _pan(dx, dy) {
    const t = this.renderer.transform;
    this.renderer.setTransform({ x: t.x + dx, y: t.y + dy, k: t.k });
    const sel = select(this.canvas);
    sel.call(this._zoomBehavior.transform, zoomIdentity.translate(t.x + dx, t.y + dy).scale(t.k));
  }

  _zoomBy(factor) {
    const t = this.renderer.transform;
    const w = this.canvas.clientWidth / 2;
    const h = this.canvas.clientHeight / 2;
    const newK = Math.max(0.1, Math.min(8, t.k * factor));
    const nx = w - (w - t.x) * (newK / t.k);
    const ny = h - (h - t.y) * (newK / t.k);
    this.renderer.setTransform({ x: nx, y: ny, k: newK });
    const sel = select(this.canvas);
    sel.call(this._zoomBehavior.transform, zoomIdentity.translate(nx, ny).scale(newK));
  }

  centerOnNode(id) {
    const node = this.state.getNode(id);
    if (!node || node.x == null) return;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    const targetK = 2;
    const tx = w / 2 - node.x * targetK;
    const ty = h / 2 - node.y * targetK;

    const sel = select(this.canvas);
    sel.transition().duration(500).call(
      this._zoomBehavior.transform,
      zoomIdentity.translate(tx, ty).scale(targetK)
    );
  }

  _announceSelection(node) {
    let region = document.getElementById('aria-live');
    if (!region) {
      region = document.createElement('div');
      region.id = 'aria-live';
      region.setAttribute('role', 'status');
      region.setAttribute('aria-live', 'polite');
      region.className = 'sr-only';
      document.body.appendChild(region);
    }
    region.textContent = `Selected: ${node.label}, ${node.type}`;
  }

  // ── Tooltip ──

  _initTooltip() {
    this._tooltip = document.createElement('div');
    this._tooltip.className = 'tooltip';
    this._tooltip.style.display = 'none';
    document.body.appendChild(this._tooltip);
  }

  _showTooltip(event, node) {
    const tip = this._tooltip;
    // Build tooltip safely with DOM methods
    tip.textContent = '';
    const b = document.createElement('b');
    b.textContent = node.label;
    tip.appendChild(b);
    tip.appendChild(document.createElement('br'));
    tip.appendChild(document.createTextNode('Type: ' + node.type));
    tip.appendChild(document.createElement('br'));
    if (node.kind === 'memory') {
      tip.appendChild(document.createTextNode('Importance: ' + ((node.importance || 0) * 100).toFixed(0) + '%'));
    } else {
      tip.appendChild(document.createTextNode('Mentions: ' + (node.mention_count || 0)));
    }
    tip.style.display = 'block';
    this._positionTooltip(event);
  }

  _showEdgeTooltip(event, edge) {
    const tip = this._tooltip;
    tip.textContent = '';
    const b = document.createElement('b');
    b.textContent = edge.rel_type || edge.label || '';
    tip.appendChild(b);
    tip.style.display = 'block';
    this._positionTooltip(event);
  }

  _positionTooltip(event) {
    const tip = this._tooltip;
    let left = event.pageX + 12;
    let top = event.pageY + 12;
    if (left + 200 > window.innerWidth) left = event.pageX - 200;
    if (top + 80 > window.innerHeight) top = event.pageY - 80;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }

  _hideTooltip() {
    if (this._tooltip) this._tooltip.style.display = 'none';
  }

  // ── Context Menu ──

  _initContextMenu() {
    this._contextMenu = document.createElement('div');
    this._contextMenu.className = 'context-menu';
    this._contextMenu.style.display = 'none';
    document.body.appendChild(this._contextMenu);

    this.canvas.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const node = this._findNode(e.offsetX, e.offsetY);
      if (!node) {
        this._hideContextMenu();
        return;
      }
      this._showContextMenu(e, node);
    });

    // Long press for touch
    let longPressTimer = null;
    this.canvas.addEventListener('touchstart', (e) => {
      const touch = e.touches[0];
      const rect = this.canvas.getBoundingClientRect();
      const ox = touch.clientX - rect.left;
      const oy = touch.clientY - rect.top;
      longPressTimer = setTimeout(() => {
        const node = this._findNode(ox, oy);
        if (node) {
          this._showContextMenu({ pageX: touch.pageX, pageY: touch.pageY }, node);
        }
      }, 500);
    });
    this.canvas.addEventListener('touchend', () => clearTimeout(longPressTimer));
    this.canvas.addEventListener('touchmove', () => clearTimeout(longPressTimer));

    document.addEventListener('click', () => this._hideContextMenu());
  }

  _showContextMenu(event, node) {
    const menu = this._contextMenu;
    menu.textContent = '';

    const items = [
      { action: 'copy-id', text: 'Copy ID' },
      { action: 'copy-recall', text: 'Copy Recall Command' },
      { action: 'view-details', text: 'View Details' },
    ];
    for (const item of items) {
      const div = document.createElement('div');
      div.className = 'ctx-item';
      div.dataset.action = item.action;
      div.textContent = item.text;
      menu.appendChild(div);
    }

    menu.style.display = 'block';
    let left = event.pageX;
    let top = event.pageY;
    if (left + 200 > window.innerWidth) left = window.innerWidth - 210;
    if (top + 100 > window.innerHeight) top = window.innerHeight - 110;
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';

    menu.onclick = (e) => {
      const action = e.target.dataset.action;
      if (action === 'copy-id') {
        navigator.clipboard.writeText(node.id).catch(() => {});
      } else if (action === 'copy-recall') {
        navigator.clipboard.writeText('lore recall --id ' + node.id).catch(() => {});
      } else if (action === 'view-details') {
        this.state.selectNode(node.id);
      }
      this._hideContextMenu();
    };
  }

  _hideContextMenu() {
    if (this._contextMenu) this._contextMenu.style.display = 'none';
  }
}
