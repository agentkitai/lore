// Detail panel — shows memory/entity info on click

import { fetchMemoryDetail, fetchEntityDetail } from '../api.js';
import { formatDate } from '../utils.js';
import { getNodeColor } from '../colors.js';

export class DetailPanel {
  constructor(container, state, interactionManager) {
    this.container = container;
    this.state = state;
    this.interaction = interactionManager;
    this._currentId = null;

    state.addEventListener('selectionChange', (e) => {
      const id = e.detail.id;
      if (id) {
        this._show(id);
      } else {
        this._hide();
      }
    });

    state.addEventListener('openDetail', (e) => {
      this._show(e.detail.id);
    });
  }

  async _show(id) {
    this._currentId = id;
    this.container.classList.add('open');
    this.container.textContent = '';

    // Loading state
    const loading = document.createElement('div');
    loading.className = 'detail-loading';
    loading.textContent = 'Loading...';
    this.container.appendChild(loading);

    const node = this.state.getNode(id);
    if (!node) {
      loading.textContent = 'Node not found';
      return;
    }

    try {
      if (node.kind === 'memory') {
        const data = await fetchMemoryDetail(id);
        if (this._currentId !== id) return; // Selection changed
        this._renderMemory(data);
      } else {
        const data = await fetchEntityDetail(id);
        if (this._currentId !== id) return;
        this._renderEntity(data);
      }
    } catch (err) {
      this.container.textContent = '';
      const errDiv = document.createElement('div');
      errDiv.className = 'detail-error';
      errDiv.textContent = 'Failed to load details';
      this.container.appendChild(errDiv);
    }
  }

  _hide() {
    this._currentId = null;
    this.container.classList.remove('open');
    this.container.textContent = '';
  }

  _renderMemory(data) {
    this.container.textContent = '';

    // Close button
    const closeBtn = document.createElement('button');
    closeBtn.className = 'detail-close';
    closeBtn.textContent = '\u00d7';
    closeBtn.onclick = () => this.state.selectNode(null);
    this.container.appendChild(closeBtn);

    // Header
    const header = document.createElement('div');
    header.className = 'detail-header';
    const color = getNodeColor({ kind: 'memory', type: data.type });
    const badge = document.createElement('span');
    badge.className = 'detail-badge';
    badge.style.backgroundColor = color;
    badge.textContent = data.type;
    header.appendChild(badge);
    if (data.tier) {
      const tierBadge = document.createElement('span');
      tierBadge.className = 'detail-badge tier-badge';
      tierBadge.textContent = data.tier;
      header.appendChild(tierBadge);
    }
    if (data.project) {
      const projBadge = document.createElement('span');
      projBadge.className = 'detail-badge proj-badge';
      projBadge.textContent = data.project;
      header.appendChild(projBadge);
    }
    this.container.appendChild(header);

    // Content
    const contentDiv = document.createElement('div');
    contentDiv.className = 'detail-content';
    contentDiv.textContent = data.content;
    this.container.appendChild(contentDiv);

    // Metadata
    const meta = document.createElement('div');
    meta.className = 'detail-meta';
    const fields = [
      ['Importance', ((data.importance_score || 0) * 100).toFixed(0) + '%'],
      ['Confidence', ((data.confidence || 0) * 100).toFixed(0) + '%'],
      ['Votes', '+' + data.upvotes + ' / -' + data.downvotes],
      ['Access Count', data.access_count],
      ['Created', formatDate(data.created_at)],
      ['Updated', formatDate(data.updated_at)],
      ['Source', data.source || 'N/A'],
    ];
    for (const [label, value] of fields) {
      const row = document.createElement('div');
      row.className = 'meta-row';
      const labelEl = document.createElement('span');
      labelEl.className = 'meta-label';
      labelEl.textContent = label;
      const valueEl = document.createElement('span');
      valueEl.className = 'meta-value';
      valueEl.textContent = value;
      row.appendChild(labelEl);
      row.appendChild(valueEl);
      meta.appendChild(row);
    }

    if (data.tags && data.tags.length > 0) {
      const tagsRow = document.createElement('div');
      tagsRow.className = 'detail-tags';
      for (const tag of data.tags) {
        const t = document.createElement('span');
        t.className = 'tag';
        t.textContent = tag;
        tagsRow.appendChild(t);
      }
      meta.appendChild(tagsRow);
    }
    this.container.appendChild(meta);

    // Connected entities
    if (data.connected_entities && data.connected_entities.length > 0) {
      this._renderConnections('Connected Entities', data.connected_entities, 'entity');
    }

    // Connected memories
    if (data.connected_memories && data.connected_memories.length > 0) {
      this._renderConnections('Related Memories', data.connected_memories, 'memory');
    }
  }

  _renderEntity(data) {
    this.container.textContent = '';

    const closeBtn = document.createElement('button');
    closeBtn.className = 'detail-close';
    closeBtn.textContent = '\u00d7';
    closeBtn.onclick = () => this.state.selectNode(null);
    this.container.appendChild(closeBtn);

    const header = document.createElement('div');
    header.className = 'detail-header';
    const title = document.createElement('h3');
    title.textContent = data.name;
    header.appendChild(title);
    const color = getNodeColor({ kind: 'entity', type: data.entity_type });
    const badge = document.createElement('span');
    badge.className = 'detail-badge';
    badge.style.backgroundColor = color;
    badge.textContent = data.entity_type;
    header.appendChild(badge);
    this.container.appendChild(header);

    if (data.description) {
      const desc = document.createElement('div');
      desc.className = 'detail-content';
      desc.textContent = data.description;
      this.container.appendChild(desc);
    }

    const meta = document.createElement('div');
    meta.className = 'detail-meta';
    const fields = [
      ['Mentions', data.mention_count],
      ['First Seen', formatDate(data.first_seen_at)],
      ['Last Seen', formatDate(data.last_seen_at)],
    ];
    if (data.aliases && data.aliases.length > 0) {
      fields.push(['Aliases', data.aliases.join(', ')]);
    }
    for (const [label, value] of fields) {
      const row = document.createElement('div');
      row.className = 'meta-row';
      const labelEl = document.createElement('span');
      labelEl.className = 'meta-label';
      labelEl.textContent = label;
      const valueEl = document.createElement('span');
      valueEl.className = 'meta-value';
      valueEl.textContent = value;
      row.appendChild(labelEl);
      row.appendChild(valueEl);
      meta.appendChild(row);
    }
    this.container.appendChild(meta);

    if (data.connected_entities && data.connected_entities.length > 0) {
      this._renderConnections('Connected Entities', data.connected_entities, 'entity');
    }
    if (data.connected_memories && data.connected_memories.length > 0) {
      this._renderConnections('Connected Memories', data.connected_memories, 'memory');
    }
  }

  _renderConnections(title, items, defaultKind) {
    const section = document.createElement('div');
    section.className = 'detail-connections';
    const h4 = document.createElement('h4');
    h4.textContent = title;
    section.appendChild(h4);

    for (const item of items) {
      const link = document.createElement('a');
      link.className = 'connection-link';
      link.href = '#';
      link.textContent = item.name || item.label || item.id;
      const relType = item.rel_type ? ' (' + item.rel_type + ')' : '';
      if (relType) {
        const span = document.createElement('span');
        span.className = 'connection-rel';
        span.textContent = relType;
        link.appendChild(span);
      }
      link.onclick = (e) => {
        e.preventDefault();
        this.state.selectNode(item.id);
        if (this.interaction) {
          this.interaction.centerOnNode(item.id);
        }
      };
      section.appendChild(link);
    }
    this.container.appendChild(section);
  }
}
