// Filter sidebar

import { MEMORY_COLORS, ENTITY_COLORS } from '../colors.js';
import { debounce } from '../utils.js';

export class FilterPanel {
  constructor(container, state) {
    this.container = container;
    this.state = state;
    this._built = false;

    state.addEventListener('dataLoaded', () => this._build());
  }

  _build() {
    if (this._built) return;
    this._built = true;

    this.container.textContent = '';

    // Collapse toggle
    const toggle = document.createElement('button');
    toggle.className = 'filter-toggle';
    toggle.textContent = '\u2630';
    toggle.title = 'Toggle filters';
    toggle.onclick = () => this.container.classList.toggle('collapsed');
    this.container.appendChild(toggle);

    const inner = document.createElement('div');
    inner.className = 'filter-inner';

    // Title + badge + reset
    const titleRow = document.createElement('div');
    titleRow.className = 'filter-title-row';
    const title = document.createElement('h3');
    title.textContent = 'Filters';
    titleRow.appendChild(title);
    this._badge = document.createElement('span');
    this._badge.className = 'filter-badge';
    this._badge.style.display = 'none';
    titleRow.appendChild(this._badge);
    inner.appendChild(titleRow);

    const resetBtn = document.createElement('button');
    resetBtn.className = 'filter-reset';
    resetBtn.textContent = 'Reset filters';
    resetBtn.onclick = () => {
      this.state.resetFilters();
      this._refreshControls();
    };
    inner.appendChild(resetBtn);

    // Project dropdown
    const projects = new Set();
    for (const n of this.state.nodes) {
      if (n.kind === 'memory' && n.project) projects.add(n.project);
    }
    if (projects.size > 0) {
      inner.appendChild(this._createLabel('Project'));
      const sel = document.createElement('select');
      sel.className = 'filter-select';
      const optAll = document.createElement('option');
      optAll.value = '';
      optAll.textContent = 'All projects';
      sel.appendChild(optAll);
      for (const p of [...projects].sort()) {
        const opt = document.createElement('option');
        opt.value = p;
        opt.textContent = p;
        sel.appendChild(opt);
      }
      sel.value = this.state.filters.project || '';
      sel.onchange = () => this.state.setFilter('project', sel.value || null);
      inner.appendChild(sel);
      this._projectSelect = sel;
    }

    // Memory type checkboxes
    const memTypes = new Set();
    for (const n of this.state.nodes) {
      if (n.kind === 'memory') memTypes.add(n.type);
    }
    if (memTypes.size > 0) {
      inner.appendChild(this._createLabel('Memory Type'));
      this._memTypeCheckboxes = this._createCheckboxGroup(
        [...memTypes].sort(), MEMORY_COLORS, 'types', inner
      );
    }

    // Entity type checkboxes
    const entTypes = new Set();
    for (const n of this.state.nodes) {
      if (n.kind === 'entity') entTypes.add(n.type);
    }
    if (entTypes.size > 0) {
      inner.appendChild(this._createLabel('Entity Type'));
      this._entTypeCheckboxes = this._createCheckboxGroup(
        [...entTypes].sort(), ENTITY_COLORS, 'entityTypes', inner
      );
    }

    // Tier checkboxes
    inner.appendChild(this._createLabel('Tier'));
    this._tierCheckboxes = this._createCheckboxGroup(
      ['working', 'short', 'long'], {}, 'tiers', inner
    );

    // Importance slider
    inner.appendChild(this._createLabel('Min Importance'));
    const sliderRow = document.createElement('div');
    sliderRow.className = 'slider-row';
    const slider = document.createElement('input');
    slider.type = 'range';
    slider.min = '0';
    slider.max = '100';
    slider.value = String(Math.round((this.state.filters.minImportance || 0) * 100));
    slider.className = 'filter-slider';
    const sliderVal = document.createElement('span');
    sliderVal.className = 'slider-value';
    sliderVal.textContent = slider.value + '%';
    const debouncedSlider = debounce((v) => {
      this.state.setFilter('minImportance', v / 100);
    }, 100);
    slider.oninput = () => {
      sliderVal.textContent = slider.value + '%';
      debouncedSlider(parseInt(slider.value));
    };
    sliderRow.appendChild(slider);
    sliderRow.appendChild(sliderVal);
    inner.appendChild(sliderRow);
    this._importanceSlider = slider;
    this._importanceValue = sliderVal;

    // Date range
    inner.appendChild(this._createLabel('Date Range'));
    const dateRow = document.createElement('div');
    dateRow.className = 'date-row';
    const since = document.createElement('input');
    since.type = 'date';
    since.className = 'filter-date';
    since.placeholder = 'From';
    since.onchange = () => {
      const dr = [...this.state.filters.dateRange];
      dr[0] = since.value || null;
      this.state.setFilter('dateRange', dr);
    };
    const until = document.createElement('input');
    until.type = 'date';
    until.className = 'filter-date';
    until.placeholder = 'To';
    until.onchange = () => {
      const dr = [...this.state.filters.dateRange];
      dr[1] = until.value || null;
      this.state.setFilter('dateRange', dr);
    };
    // Pre-populate from state
    if (this.state.filters.dateRange[0]) since.value = this.state.filters.dateRange[0];
    if (this.state.filters.dateRange[1]) until.value = this.state.filters.dateRange[1];

    dateRow.appendChild(since);
    dateRow.appendChild(until);
    inner.appendChild(dateRow);
    this._sinceInput = since;
    this._untilInput = until;

    this.container.appendChild(inner);

    // Update badge on filter change
    this.state.addEventListener('filterChange', () => this._updateBadge());
    this._updateBadge();
  }

  _createLabel(text) {
    const label = document.createElement('div');
    label.className = 'filter-label';
    label.textContent = text;
    return label;
  }

  _createCheckboxGroup(items, colorMap, filterKey, parent) {
    const group = document.createElement('div');
    group.className = 'checkbox-group';
    const checkboxes = {};

    for (const item of items) {
      const label = document.createElement('label');
      label.className = 'filter-checkbox';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = this.state.filters[filterKey].has(item);
      cb.onchange = () => {
        const set = new Set(this.state.filters[filterKey]);
        if (cb.checked) set.add(item);
        else set.delete(item);
        this.state.setFilter(filterKey, set);
      };
      label.appendChild(cb);

      if (colorMap[item]) {
        const dot = document.createElement('span');
        dot.className = 'color-dot';
        dot.style.backgroundColor = colorMap[item];
        label.appendChild(dot);
      }

      const text = document.createTextNode(' ' + item);
      label.appendChild(text);
      group.appendChild(label);
      checkboxes[item] = cb;
    }
    parent.appendChild(group);
    return checkboxes;
  }

  _updateBadge() {
    const count = this.state.getActiveFilterCount();
    if (count > 0) {
      this._badge.textContent = count;
      this._badge.style.display = 'inline-block';
    } else {
      this._badge.style.display = 'none';
    }
  }

  _refreshControls() {
    if (this._projectSelect) this._projectSelect.value = '';
    if (this._importanceSlider) {
      this._importanceSlider.value = '0';
      this._importanceValue.textContent = '0%';
    }
    if (this._sinceInput) this._sinceInput.value = '';
    if (this._untilInput) this._untilInput.value = '';
    if (this._memTypeCheckboxes) {
      for (const cb of Object.values(this._memTypeCheckboxes)) cb.checked = false;
    }
    if (this._entTypeCheckboxes) {
      for (const cb of Object.values(this._entTypeCheckboxes)) cb.checked = false;
    }
    if (this._tierCheckboxes) {
      for (const cb of Object.values(this._tierCheckboxes)) cb.checked = false;
    }
  }
}
