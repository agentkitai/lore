// Stats dashboard panel

import { fetchStats } from '../api.js';
import { MEMORY_COLORS, ENTITY_COLORS } from '../colors.js';
import { hexToRgba } from '../utils.js';

export class StatsPanel {
  constructor(container, state, interactionManager) {
    this.container = container;
    this.state = state;
    this.interaction = interactionManager;
    this._visible = false;
  }

  toggle() {
    this._visible = !this._visible;
    if (this._visible) {
      this.container.classList.add('open');
      this._load();
    } else {
      this.container.classList.remove('open');
    }
  }

  close() {
    this._visible = false;
    this.container.classList.remove('open');
  }

  async _load() {
    this.container.textContent = '';
    const loading = document.createElement('div');
    loading.className = 'stats-loading';
    loading.textContent = 'Loading stats...';
    this.container.appendChild(loading);

    try {
      const data = await fetchStats(this.state.filters.project);
      this._render(data);
    } catch (err) {
      this.container.textContent = '';
      const errDiv = document.createElement('div');
      errDiv.className = 'stats-error';
      errDiv.textContent = 'Failed to load stats';
      this.container.appendChild(errDiv);
    }
  }

  _render(data) {
    this.container.textContent = '';

    // Close button
    const closeBtn = document.createElement('button');
    closeBtn.className = 'detail-close';
    closeBtn.textContent = '\u00d7';
    closeBtn.onclick = () => this.close();
    this.container.appendChild(closeBtn);

    const title = document.createElement('h3');
    title.textContent = 'Knowledge Graph Stats';
    title.className = 'stats-title';
    this.container.appendChild(title);

    // Totals
    const totals = document.createElement('div');
    totals.className = 'stats-totals';
    const items = [
      { label: 'Memories', value: data.total_memories },
      { label: 'Entities', value: data.total_entities },
      { label: 'Relationships', value: data.total_relationships },
    ];
    for (const item of items) {
      const card = document.createElement('div');
      card.className = 'stat-card';
      const val = document.createElement('div');
      val.className = 'stat-value';
      val.textContent = item.value;
      const lbl = document.createElement('div');
      lbl.className = 'stat-label';
      lbl.textContent = item.label;
      card.appendChild(val);
      card.appendChild(lbl);
      totals.appendChild(card);
    }
    this.container.appendChild(totals);

    // Average importance
    const avgDiv = document.createElement('div');
    avgDiv.className = 'stat-row';
    avgDiv.textContent = 'Avg Importance: ' + ((data.avg_importance || 0) * 100).toFixed(0) + '%';
    this.container.appendChild(avgDiv);

    // Recent activity
    const recentDiv = document.createElement('div');
    recentDiv.className = 'stat-row';
    recentDiv.textContent = data.recent_24h + ' memories in last 24h, ' + data.recent_7d + ' in last 7d';
    this.container.appendChild(recentDiv);

    // Type distribution (simple bar list)
    if (data.by_type && Object.keys(data.by_type).length > 0) {
      const section = document.createElement('div');
      section.className = 'stats-section';
      const h4 = document.createElement('h4');
      h4.textContent = 'By Type';
      section.appendChild(h4);

      const maxVal = Math.max(...Object.values(data.by_type));
      for (const [type, count] of Object.entries(data.by_type).sort((a, b) => b[1] - a[1])) {
        const row = document.createElement('div');
        row.className = 'stat-bar-row';
        const label = document.createElement('span');
        label.className = 'stat-bar-label';
        label.textContent = type;
        const barContainer = document.createElement('div');
        barContainer.className = 'stat-bar-container';
        const bar = document.createElement('div');
        bar.className = 'stat-bar';
        bar.style.width = (count / maxVal * 100) + '%';
        bar.style.backgroundColor = MEMORY_COLORS[type] || '#9ca3af';
        barContainer.appendChild(bar);
        const countEl = document.createElement('span');
        countEl.className = 'stat-bar-count';
        countEl.textContent = count;
        row.appendChild(label);
        row.appendChild(barContainer);
        row.appendChild(countEl);
        section.appendChild(row);
      }
      this.container.appendChild(section);
    }

    // Top entities
    if (data.top_entities && data.top_entities.length > 0) {
      const section = document.createElement('div');
      section.className = 'stats-section';
      const h4 = document.createElement('h4');
      h4.textContent = 'Top Entities';
      section.appendChild(h4);

      for (const ent of data.top_entities) {
        const row = document.createElement('a');
        row.className = 'top-entity-link';
        row.href = '#';
        row.textContent = ent.name + ' (' + ent.type + ') \u2014 ' + ent.mention_count + ' mentions';
        row.onclick = (e) => {
          e.preventDefault();
          // Find entity node by name
          const node = this.state.nodes.find(n => n.kind === 'entity' && n.label === ent.name);
          if (node) {
            this.state.selectNode(node.id);
            if (this.interaction) this.interaction.centerOnNode(node.id);
          }
          this.close();
        };
        section.appendChild(row);
      }
      this.container.appendChild(section);
    }
  }
}
