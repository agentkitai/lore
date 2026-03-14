// Review queue panel (E6 — Approval UX)

import { fetchReviewQueue, reviewRelationship, reviewBulk } from '../api.js';

export class ReviewPanel {
  constructor(container, state) {
    this.container = container;
    this.state = state;
    this._items = [];

    state.addEventListener('dataLoaded', () => this._load());
  }

  async _load() {
    try {
      const data = await fetchReviewQueue(50);
      this._items = data.pending || [];
      this._render();
    } catch {
      this._renderEmpty('Review queue unavailable');
    }
  }

  _render() {
    this.container.textContent = '';

    const header = document.createElement('h3');
    header.className = 'review-header';
    header.textContent = 'Review Queue';
    if (this._items.length) {
      const badge = document.createElement('span');
      badge.className = 'review-badge';
      badge.textContent = String(this._items.length);
      header.appendChild(badge);
    }
    this.container.appendChild(header);

    if (!this._items.length) {
      this._renderEmpty('No pending connections');
      return;
    }

    // Bulk actions
    const bulkRow = document.createElement('div');
    bulkRow.className = 'review-bulk';

    const approveAll = document.createElement('button');
    approveAll.className = 'review-btn review-btn-approve';
    approveAll.textContent = 'Approve All';
    approveAll.onclick = () => this._bulkAction('approve');
    bulkRow.appendChild(approveAll);

    const rejectAll = document.createElement('button');
    rejectAll.className = 'review-btn review-btn-reject';
    rejectAll.textContent = 'Reject All';
    rejectAll.onclick = () => this._bulkAction('reject');
    bulkRow.appendChild(rejectAll);

    this.container.appendChild(bulkRow);

    const list = document.createElement('ul');
    list.className = 'review-list';

    for (const item of this._items) {
      const li = document.createElement('li');
      li.className = 'review-item';
      li.dataset.id = item.id;

      const connDiv = document.createElement('div');
      connDiv.className = 'review-connection';
      connDiv.textContent =
        item.source_entity.name + ' \u2192[' + item.rel_type + ']\u2192 ' +
        item.target_entity.name;
      li.appendChild(connDiv);

      if (item.source_memory_content) {
        const srcDiv = document.createElement('div');
        srcDiv.className = 'review-source';
        srcDiv.textContent = item.source_memory_content.slice(0, 100);
        li.appendChild(srcDiv);
      }

      const actions = document.createElement('div');
      actions.className = 'review-actions';

      const approveBtn = document.createElement('button');
      approveBtn.className = 'review-btn review-btn-approve';
      approveBtn.textContent = 'Approve';
      approveBtn.onclick = (e) => { e.stopPropagation(); this._act(item.id, 'approve', li); };
      actions.appendChild(approveBtn);

      const rejectBtn = document.createElement('button');
      rejectBtn.className = 'review-btn review-btn-reject';
      rejectBtn.textContent = 'Reject';
      rejectBtn.onclick = (e) => { e.stopPropagation(); this._act(item.id, 'reject', li); };
      actions.appendChild(rejectBtn);

      li.appendChild(actions);
      list.appendChild(li);
    }

    this.container.appendChild(list);
  }

  _renderEmpty(message) {
    const msg = document.createElement('p');
    msg.className = 'review-empty';
    msg.textContent = message;
    this.container.appendChild(msg);
  }

  async _act(id, action, li) {
    try {
      await reviewRelationship(id, action);
      li.classList.add('review-' + action + 'd');
      this._items = this._items.filter(i => i.id !== id);
      setTimeout(() => li.remove(), 300);
      // Update badge
      const badge = this.container.querySelector('.review-badge');
      if (badge) badge.textContent = String(this._items.length);
      if (!this._items.length) this._renderEmpty('No pending connections');
    } catch (err) {
      li.classList.add('review-error');
    }
  }

  async _bulkAction(action) {
    const ids = this._items.map(i => i.id);
    if (!ids.length) return;
    try {
      await reviewBulk(action, ids);
      this._items = [];
      this._render();
    } catch {
      // Reload on error
      this._load();
    }
  }
}
