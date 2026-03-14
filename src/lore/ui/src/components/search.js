// Search bar component

import { searchMemories } from '../api.js';
import { debounce } from '../utils.js';

export class SearchBar {
  constructor(container, state, interactionManager) {
    this.container = container;
    this.state = state;
    this.interaction = interactionManager;
    this._dropdown = null;
    this._build();
  }

  _build() {
    const wrapper = document.createElement('div');
    wrapper.className = 'search-wrapper';

    const input = document.createElement('input');
    input.type = 'text';
    input.id = 'search-input';
    input.className = 'search-input';
    input.placeholder = 'Search memories...';
    input.autocomplete = 'off';

    const clearBtn = document.createElement('button');
    clearBtn.className = 'search-clear';
    clearBtn.textContent = '\u00d7';
    clearBtn.style.display = 'none';
    clearBtn.onclick = () => {
      input.value = '';
      clearBtn.style.display = 'none';
      this.state.clearSearch();
      this._hideDropdown();
    };

    this._dropdown = document.createElement('div');
    this._dropdown.className = 'search-dropdown';
    this._dropdown.style.display = 'none';

    const debouncedSearch = debounce(async (query) => {
      if (!query.trim()) {
        this.state.clearSearch();
        this._hideDropdown();
        return;
      }
      try {
        this._showLoading();
        const resp = await searchMemories(query);
        if (input.value.trim() !== query.trim()) return; // Stale

        if (resp.results.length === 0) {
          this._showNoResults();
        } else {
          this._showResults(resp.results);
        }
        this.state.setSearchResults(resp.results.map(r => r.id), query);
      } catch {
        this._hideDropdown();
      }
    }, 300);

    input.oninput = () => {
      clearBtn.style.display = input.value ? 'block' : 'none';
      debouncedSearch(input.value);
    };

    input.onkeydown = (e) => {
      if (e.key === 'Escape') {
        input.value = '';
        clearBtn.style.display = 'none';
        this.state.clearSearch();
        this._hideDropdown();
        input.blur();
      }
    };

    // Restore search from URL state
    if (this.state.searchQuery) {
      input.value = this.state.searchQuery;
      clearBtn.style.display = 'block';
      debouncedSearch(this.state.searchQuery);
    }

    wrapper.appendChild(input);
    wrapper.appendChild(clearBtn);
    wrapper.appendChild(this._dropdown);
    this.container.appendChild(wrapper);
  }

  _showLoading() {
    this._dropdown.textContent = '';
    const div = document.createElement('div');
    div.className = 'search-loading';
    div.textContent = 'Searching...';
    this._dropdown.appendChild(div);
    this._dropdown.style.display = 'block';
  }

  _showNoResults() {
    this._dropdown.textContent = '';
    const div = document.createElement('div');
    div.className = 'search-no-results';
    div.textContent = 'No results found';
    this._dropdown.appendChild(div);
    this._dropdown.style.display = 'block';
  }

  _showResults(results) {
    this._dropdown.textContent = '';
    for (const r of results) {
      const item = document.createElement('div');
      item.className = 'search-result';
      const label = document.createElement('span');
      label.className = 'search-result-label';
      label.textContent = r.label;
      const score = document.createElement('span');
      score.className = 'search-result-score';
      score.textContent = (r.score * 100).toFixed(0) + '%';
      const type = document.createElement('span');
      type.className = 'search-result-type';
      type.textContent = r.type;
      item.appendChild(label);
      item.appendChild(type);
      item.appendChild(score);
      item.onclick = () => {
        this.state.selectNode(r.id);
        if (this.interaction) this.interaction.centerOnNode(r.id);
        this._hideDropdown();
      };
      this._dropdown.appendChild(item);
    }
    this._dropdown.style.display = 'block';
  }

  _hideDropdown() {
    if (this._dropdown) this._dropdown.style.display = 'none';
  }
}
