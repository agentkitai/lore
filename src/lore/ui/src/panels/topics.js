// Topics sidebar panel (E4)

import { fetchTopics, fetchTopicDetail } from '../api.js';

export class TopicsPanel {
  constructor(container, state, interactionManager) {
    this.container = container;
    this.state = state;
    this.interaction = interactionManager;
    this._built = false;

    state.addEventListener('dataLoaded', () => this._load());
  }

  async _load() {
    try {
      const data = await fetchTopics(3, 20);
      this._render(data.topics || []);
    } catch {
      this._renderEmpty('Topics unavailable');
    }
  }

  _render(topics) {
    this.container.textContent = '';
    this._built = true;

    const header = document.createElement('h3');
    header.textContent = 'Topics';
    header.className = 'topics-header';
    this.container.appendChild(header);

    if (!topics.length) {
      this._renderEmpty('No topics found');
      return;
    }

    const list = document.createElement('ul');
    list.className = 'topics-list';

    for (const topic of topics) {
      const li = document.createElement('li');
      li.className = 'topic-item';

      const nameSpan = document.createElement('span');
      nameSpan.className = 'topic-name';
      nameSpan.textContent = topic.name;
      li.appendChild(nameSpan);

      const countSpan = document.createElement('span');
      countSpan.className = 'topic-count';
      countSpan.textContent = String(topic.mention_count);
      li.appendChild(countSpan);

      li.onclick = () => this._showDetail(topic.name);
      list.appendChild(li);
    }

    this.container.appendChild(list);
  }

  _renderEmpty(message) {
    const msg = document.createElement('p');
    msg.className = 'topics-empty';
    msg.textContent = message;
    this.container.appendChild(msg);
  }

  async _showDetail(name) {
    try {
      const detail = await fetchTopicDetail(name);
      this._renderDetail(detail);

      // Center graph on entity
      if (this.interaction && detail.entity) {
        const entityId = detail.entity.id;
        this.state.dispatchEvent(new CustomEvent('selectionChange', {
          detail: { id: entityId }
        }));
      }
    } catch {
      this._renderEmpty('Could not load topic: ' + name);
    }
  }

  _renderDetail(detail) {
    this.container.textContent = '';

    const back = document.createElement('button');
    back.className = 'topic-back';
    back.textContent = '\u2190 Back';
    back.onclick = () => this._load();
    this.container.appendChild(back);

    const entity = detail.entity;
    const title = document.createElement('h3');
    title.textContent = entity.name + ' (' + entity.entity_type + ')';
    this.container.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'topic-meta';

    const countDiv = document.createElement('div');
    countDiv.textContent = detail.memory_count + ' memories';
    meta.appendChild(countDiv);

    if (entity.first_seen_at) {
      const firstDiv = document.createElement('div');
      firstDiv.textContent = 'First: ' + entity.first_seen_at.slice(0, 10);
      meta.appendChild(firstDiv);
    }
    if (entity.last_seen_at) {
      const lastDiv = document.createElement('div');
      lastDiv.textContent = 'Last: ' + entity.last_seen_at.slice(0, 10);
      meta.appendChild(lastDiv);
    }
    this.container.appendChild(meta);

    if (detail.related_entities && detail.related_entities.length) {
      const relHeader = document.createElement('h4');
      relHeader.textContent = 'Related';
      this.container.appendChild(relHeader);

      const relList = document.createElement('ul');
      relList.className = 'topic-related';
      for (const rel of detail.related_entities) {
        const li = document.createElement('li');
        li.textContent = rel.name + ' (' + rel.entity_type + ') [' + rel.relationship + ']';
        relList.appendChild(li);
      }
      this.container.appendChild(relList);
    }

    if (detail.memories && detail.memories.length) {
      const memHeader = document.createElement('h4');
      memHeader.textContent = 'Memories';
      this.container.appendChild(memHeader);

      const memList = document.createElement('ul');
      memList.className = 'topic-memories';
      for (const mem of detail.memories) {
        const li = document.createElement('li');
        const date = mem.created_at ? mem.created_at.slice(0, 10) : '?';
        const dateSpan = document.createElement('span');
        dateSpan.className = 'mem-date';
        dateSpan.textContent = date;
        li.appendChild(dateSpan);
        li.appendChild(document.createTextNode(' ' + mem.content.slice(0, 120)));
        memList.appendChild(li);
      }
      this.container.appendChild(memList);
    }
  }
}
