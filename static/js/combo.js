/*
 * Searchable dropdown ("combo box") built on top of a normal <select>.
 * The <select> stays in the form (hidden) and keeps the chosen value, so the
 * server and any code listening for "change" work as before.
 *
 *   const combo = PWCombo.enhance(selectEl, { icon: 'fa-store', onBlocked: () => '...' });
 *   combo.refresh();          // after changing the <select>'s options in code
 *   combo.showAlert('text');  // inline warning under the field
 *
 * Option extras: data-sub="right-hand detail text". <optgroup> labels become section headers.
 */
(function () {
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

  // Bold-highlight the part of `text` that matches what the user typed.
  function mark(text, q) {
    const i = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
    if (i < 0) return esc(text);
    return esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' + esc(text.slice(i + q.length));
  }

  class Combo {
    constructor(select, opts) {
      this.select = select;
      this.opts = opts || {};
      this.shown = [];
      this.active = -1;

      const wrap = (this.wrap = document.createElement('div'));
      wrap.className = 'combo';
      wrap.innerHTML =
        '<div class="combo-control">' +
          '<i class="fa-solid ' + (this.opts.icon || 'fa-magnifying-glass') + ' combo-icon"></i>' +
          '<input type="text" class="input-pw combo-input" autocomplete="off" spellcheck="false" ' +
            'role="combobox" aria-autocomplete="list" aria-expanded="false">' +
          '<button type="button" class="combo-clear" tabindex="-1" aria-label="Clear"><i class="fa-solid fa-xmark"></i></button>' +
          '<i class="fa-solid fa-chevron-down combo-chevron"></i>' +
        '</div>' +
        '<div class="combo-panel" role="listbox"></div>' +
        '<div class="combo-alert" role="alert" hidden></div>';
      select.hidden = true;
      select.after(wrap);

      this.input = wrap.querySelector('.combo-input');
      this.panel = wrap.querySelector('.combo-panel');
      this.alertEl = wrap.querySelector('.combo-alert');
      this.input.id = select.id + 'Search';
      this.panel.id = select.id + 'List';
      this.input.setAttribute('aria-controls', this.panel.id);
      const label = document.querySelector('label[for="' + select.id + '"]');
      if (label) label.setAttribute('for', this.input.id);
      select._combo = this;

      this.input.addEventListener('pointerdown', () => this.tryOpen());
      this.input.addEventListener('focus', () => this.tryOpen());
      this.input.addEventListener('input', () => { this.open(); this.render(this.input.value); });
      this.input.addEventListener('keydown', (e) => this.onKey(e));
      wrap.querySelector('.combo-clear').addEventListener('click', () => {
        this.choose('');
        this.input.focus();
      });
      this.panel.addEventListener('pointerdown', (e) => {
        e.preventDefault(); // keep focus in the input
        const opt = e.target.closest('.combo-option');
        if (opt) this.choose(opt.dataset.value);
      });
      document.addEventListener('pointerdown', (e) => {
        if (!wrap.contains(e.target)) this.close();
      });
      this.input.addEventListener('blur', () => setTimeout(() => {
        if (!wrap.contains(document.activeElement)) this.close();
      }, 0));

      this.refresh();
    }

    items() {
      return [...this.select.options].filter((o) => o.value !== '').map((o) => ({
        value: o.value,
        label: o.text,
        sub: o.dataset.sub || '',
        group: o.parentElement.tagName === 'OPTGROUP' ? o.parentElement.label : '',
      }));
    }

    // Sync the visible input with the hidden <select> (call after changing its options).
    refresh() {
      const s = this.select;
      const first = s.options[0];
      const sel = s.selectedOptions[0];
      this.input.placeholder = first && first.value === '' ? first.text : '';
      this.input.value = sel && sel.value ? sel.text : '';
      this.input.readOnly = s.disabled; // not "disabled", so taps still reach us and can explain why
      this.wrap.classList.toggle('is-disabled', s.disabled);
      this.wrap.classList.toggle('has-value', !!s.value);
      if (!s.disabled) this.hideAlert();
      this.close();
    }

    tryOpen() {
      if (this.select.disabled) {
        const msg = this.opts.onBlocked && this.opts.onBlocked();
        if (msg) this.showAlert(msg);
        this.input.blur();
        return;
      }
      if (this.isOpen) return;
      this.open();
      this.render('');
      if (this.select.value) this.input.select(); // typing replaces the current choice
    }

    open() {
      if (this.select.disabled) return;
      this.isOpen = true;
      this.wrap.classList.add('is-open');
      this.input.setAttribute('aria-expanded', 'true');
      this.hideAlert();
    }

    close() {
      if (!this.isOpen) return;
      this.isOpen = false;
      this.wrap.classList.remove('is-open');
      this.input.setAttribute('aria-expanded', 'false');
      const sel = this.select.selectedOptions[0];
      this.input.value = sel && sel.value ? sel.text : ''; // drop half-typed text
    }

    render(query) {
      const q = (query || '').trim();
      const ql = q.toLowerCase();
      this.shown = this.items().filter((i) => !ql
        || i.label.toLowerCase().includes(ql) || i.sub.toLowerCase().includes(ql) || i.group.toLowerCase().includes(ql));

      let html = '';
      let group = null;
      this.shown.forEach((i, idx) => {
        if (i.group && i.group !== group) {
          group = i.group;
          html += '<div class="combo-group">' + mark(group, q) + '</div>';
        }
        html += '<div class="combo-option' + (i.value === this.select.value ? ' is-selected' : '') +
          '" role="option" id="' + this.select.id + '-opt' + idx + '" data-idx="' + idx + '" data-value="' + esc(i.value) + '">' +
          '<span class="combo-label">' + mark(i.label, q) + '</span>' +
          (i.sub ? '<span class="combo-sub">' + mark(i.sub, q) + '</span>' : '') +
          '<i class="fa-solid fa-check combo-check"></i></div>';
      });
      if (!this.shown.length) {
        html = '<div class="combo-empty"><i class="fa-solid fa-magnifying-glass"></i> No match for “' + esc(q) + '”</div>';
      }
      this.panel.innerHTML = html;
      this.setActive(ql ? 0 : Math.max(0, this.shown.findIndex((i) => i.value === this.select.value)));
    }

    setActive(idx) {
      this.active = this.shown.length ? (idx + this.shown.length) % this.shown.length : -1;
      this.panel.querySelectorAll('.combo-option').forEach((el) => {
        const on = Number(el.dataset.idx) === this.active;
        el.classList.toggle('is-active', on);
        if (on) {
          el.scrollIntoView({ block: 'nearest' });
          this.input.setAttribute('aria-activedescendant', el.id);
        }
      });
    }

    onKey(e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (!this.isOpen) { this.tryOpen(); return; }
        this.setActive(this.active + (e.key === 'ArrowDown' ? 1 : -1));
      } else if (e.key === 'Enter') {
        if (this.isOpen) {
          e.preventDefault(); // don't submit the form
          if (this.shown[this.active]) this.choose(this.shown[this.active].value);
        }
      } else if (e.key === 'Escape') {
        this.close();
      } else if (e.key === 'Tab' && this.isOpen && this.input.value.trim() && this.shown.length === 1) {
        this.choose(this.shown[0].value); // only one match left: take it
      }
    }

    choose(value) {
      const changed = this.select.value !== value;
      this.select.value = value;
      if (changed) this.select.dispatchEvent(new Event('change', { bubbles: true }));
      this.refresh();
    }

    showAlert(msg) {
      this.alertEl.innerHTML = '<i class="fa-solid fa-circle-exclamation"></i><span>' + esc(msg) + '</span>';
      this.alertEl.hidden = false;
      this.wrap.classList.remove('shake');
      void this.wrap.offsetWidth; // restart the animation
      this.wrap.classList.add('shake');
    }

    hideAlert() {
      this.alertEl.hidden = true;
    }

    // Briefly glow, to point the user at the field they need to fill first.
    attention() {
      this.wrap.classList.remove('attention');
      void this.wrap.offsetWidth;
      this.wrap.classList.add('attention');
      this.wrap.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  window.PWCombo = { enhance: (select, opts) => new Combo(select, opts) };
})();
