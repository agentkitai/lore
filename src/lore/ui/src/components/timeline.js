// Timeline scrubber component

import { fetchTimeline } from '../api.js';
import { MEMORY_COLORS } from '../colors.js';

export class TimelineScrubber {
  constructor(container, state) {
    this.container = container;
    this.state = state;
    this._data = null;
    this._canvas = null;

    state.addEventListener('dataLoaded', () => this._load());
  }

  async _load() {
    try {
      const data = await fetchTimeline('day', this.state.filters.project);
      this._data = data;
      this._build();
    } catch {
      // Timeline is optional, don't block on failure
    }
  }

  _build() {
    if (!this._data || this._data.buckets.length === 0) {
      this.container.style.display = 'none';
      return;
    }
    this.container.textContent = '';
    this.container.style.display = 'block';

    // Collapse toggle
    const toggle = document.createElement('button');
    toggle.className = 'timeline-toggle';
    toggle.textContent = '\u25b2';
    toggle.onclick = () => {
      this.container.classList.toggle('collapsed');
      toggle.textContent = this.container.classList.contains('collapsed') ? '\u25bc' : '\u25b2';
    };
    this.container.appendChild(toggle);

    // Canvas for bars
    this._canvas = document.createElement('canvas');
    this._canvas.className = 'timeline-canvas';
    this._canvas.height = 36;
    this.container.appendChild(this._canvas);

    this._drawBars();

    // Handle resize
    const ro = new ResizeObserver(() => this._drawBars());
    ro.observe(this.container);
  }

  _drawBars() {
    if (!this._canvas || !this._data) return;
    const canvas = this._canvas;
    const buckets = this._data.buckets;
    const w = this.container.clientWidth - 40;
    canvas.width = w;
    canvas.style.width = w + 'px';

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, w, 36);

    if (buckets.length === 0) return;

    const maxCount = Math.max(...buckets.map(b => b.count));
    const barW = Math.max(2, Math.floor(w / buckets.length) - 1);

    for (let i = 0; i < buckets.length; i++) {
      const b = buckets[i];
      const barH = (b.count / maxCount) * 32;
      const x = (i / buckets.length) * w;

      // Stack by type
      let y = 36;
      const types = Object.entries(b.by_type || {}).sort((a, b) => b[1] - a[1]);
      for (const [type, count] of types) {
        const segH = (count / b.count) * barH;
        ctx.fillStyle = MEMORY_COLORS[type] || '#9ca3af';
        ctx.fillRect(x, y - segH, barW, segH);
        y -= segH;
      }
    }

    // Click to set date filter
    canvas.onclick = (e) => {
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const idx = Math.floor((x / w) * buckets.length);
      if (idx >= 0 && idx < buckets.length) {
        const date = buckets[idx].date;
        this.state.setFilter('dateRange', [date, date]);
      }
    };
  }
}
