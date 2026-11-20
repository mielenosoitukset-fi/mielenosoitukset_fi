/* Admin manual: search box, heading links and step-by-step task progress. */
(() => {
  const normalize = (value) => String(value || '')
    .toLocaleLowerCase('fi')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');

  /* ---------- Search ---------- */
  const input = document.getElementById('manual-search');
  const list = document.getElementById('manual-search-results');
  const status = document.getElementById('manual-search-status');
  const dataEl = document.getElementById('manual-search-data');
  let entries = [];
  try {
    entries = JSON.parse(dataEl ? dataEl.textContent : '[]').map((entry) => ({
      ...entry,
      titleKey: normalize(entry.title),
      key: normalize(`${entry.title} ${entry.where} ${entry.text}`),
    }));
  } catch (error) {
    entries = [];
  }

  let active = -1;

  const links = () => Array.from(list.querySelectorAll('a'));

  const setActive = (index) => {
    const items = links();
    if (!items.length) {
      active = -1;
      input.removeAttribute('aria-activedescendant');
      return;
    }
    active = (index + items.length) % items.length;
    items.forEach((link, i) => link.setAttribute('aria-selected', i === active ? 'true' : 'false'));
    input.setAttribute('aria-activedescendant', items[active].id);
    items[active].scrollIntoView({ block: 'nearest' });
  };

  const close = () => {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  };

  const search = () => {
    const words = normalize(input.value).split(/\s+/).filter(Boolean);
    list.replaceChildren();
    if (!words.length) {
      close();
      status.textContent = '';
      return;
    }
    const hits = entries
      .filter((entry) => words.every((word) => entry.key.includes(word)))
      .map((entry) => ({
        entry,
        score: words.reduce((sum, word) => sum + (entry.titleKey.includes(word) ? 2 : 0)
          + (entry.titleKey.startsWith(word) ? 1 : 0), 0) + (entry.where === 'Sivu' ? 0.5 : 0),
      }))
      .sort((a, b) => b.score - a.score)
      .slice(0, 8);

    if (!hits.length) {
      const empty = document.createElement('li');
      empty.className = 'manual-search__empty';
      empty.textContent = input.dataset.empty;
      list.append(empty);
    }
    hits.forEach(({ entry }, i) => {
      const item = document.createElement('li');
      item.className = 'manual-search__result';
      item.setAttribute('role', 'presentation');
      const link = document.createElement('a');
      link.href = entry.url;
      link.id = `manual-search-option-${i}`;
      link.setAttribute('role', 'option');
      link.setAttribute('aria-selected', 'false');
      const title = document.createElement('strong');
      title.textContent = entry.title;
      const where = document.createElement('span');
      where.textContent = entry.where === 'Sivu' ? entry.text : entry.where;
      link.append(title, where);
      link.addEventListener('mousemove', () => setActive(i));
      item.append(link);
      list.append(item);
    });
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    status.textContent = hits.length
      ? input.dataset.count.replace('{n}', hits.length)
      : input.dataset.empty;
    active = -1;
    if (hits.length) setActive(0);
  };

  if (input && list) {
    input.addEventListener('input', search);
    input.addEventListener('focus', () => { if (input.value.trim()) search(); });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') { event.preventDefault(); if (list.hidden) search(); else setActive(active + 1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(active - 1); }
      else if (event.key === 'Enter') {
        const items = links();
        if (items[active]) { event.preventDefault(); window.location.assign(items[active].href); close(); }
      } else if (event.key === 'Escape') {
        if (!list.hidden) { event.preventDefault(); close(); } else { input.value = ''; }
      }
    });
    document.addEventListener('click', (event) => {
      if (!event.target.closest('.manual-search')) close();
    });
    list.addEventListener('click', (event) => { if (event.target.closest('a')) close(); });

    document.addEventListener('keydown', (event) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (target.closest('input, textarea, select, [contenteditable="true"]')) return;
      event.preventDefault();
      input.focus();
    });

    const initial = new URLSearchParams(window.location.search).get('q');
    if (initial) {
      input.value = initial.slice(0, 100);
      search();
    }
  }

  /* ---------- Contents on small screens ---------- */
  const nav = document.querySelector('.manual-nav');
  if (nav && window.matchMedia('(max-width: 991.98px)').matches) {
    nav.open = false;
  }

  /* ---------- Heading links ---------- */
  document.querySelectorAll('.manual-article :is(h2, h3)').forEach((heading) => {
    const id = heading.id || heading.closest('section[id], .manual-task[id]')?.id;
    if (!id || heading.querySelector('.manual-anchor')) return;
    if (!heading.id && heading.closest('section[id], .manual-task[id]').querySelector(':is(h2, h3)') !== heading) return;
    const link = document.createElement('a');
    link.className = 'manual-anchor';
    link.href = `#${id}`;
    link.setAttribute('aria-label', `${heading.dataset.anchorLabel || 'Linkki kohtaan'}: ${heading.textContent.trim()}`);
    link.textContent = '#';
    heading.append(link);
  });

  /* ---------- Step-by-step progress ---------- */
  const storage = {
    get(key) { try { return JSON.parse(window.localStorage.getItem(key) || '[]'); } catch (error) { return []; } },
    set(key, value) { try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (error) { /* private mode */ } },
    remove(key) { try { window.localStorage.removeItem(key); } catch (error) { /* private mode */ } },
  };

  document.querySelectorAll('.manual-task[data-task]').forEach((task) => {
    const key = `admin-manual-task:${task.dataset.task}`;
    const steps = Array.from(task.querySelectorAll('.manual-steps > li'));
    const progress = task.querySelector('.manual-task__progress');
    if (!steps.length || !progress) return;
    const label = progress.querySelector('[data-progress-label]');
    const bar = progress.querySelector('.manual-task__bar > span');
    const reset = progress.querySelector('[data-progress-reset]');
    const done = new Set(storage.get(key));
    const boxes = [];

    const render = () => {
      steps.forEach((step, i) => step.classList.toggle('is-done', done.has(i)));
      boxes.forEach((box, i) => { box.checked = done.has(i); });
      const count = steps.filter((_, i) => done.has(i)).length;
      label.textContent = progress.dataset.template
        .replace('{done}', count)
        .replace('{total}', steps.length);
      bar.style.width = `${Math.round((count / steps.length) * 100)}%`;
      reset.hidden = count === 0;
    };

    steps.forEach((step, i) => {
      let body = step.querySelector(':scope > .manual-step__body');
      if (!body) {
        body = document.createElement('div');
        body.className = 'manual-step__body';
        while (step.firstChild) body.append(step.firstChild);
        step.append(body);
      }
      const check = document.createElement('label');
      check.className = 'manual-step__check';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.className = 'form-check-input m-0';
      box.addEventListener('change', () => {
        if (box.checked) done.add(i); else done.delete(i);
        storage.set(key, Array.from(done));
        render();
      });
      check.append(box, document.createTextNode(progress.dataset.stepLabel));
      body.append(check);
      boxes.push(box);
    });

    reset.addEventListener('click', () => {
      done.clear();
      storage.remove(key);
      render();
      boxes[0]?.focus();
    });

    progress.hidden = false;
    render();
  });
})();
