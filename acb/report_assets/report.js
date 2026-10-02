/* Local report navigation, filtering and sorting. No network requests. */
(() => {
  for (const root of document.querySelectorAll('[data-results]')) {
    const table = root.querySelector('[data-results-table]');
    if (!table) continue;
    const rows = [...table.tBodies].filter(row => row.hasAttribute('data-result-row'));
    const controls = [...root.querySelectorAll('[data-filter]')];
    const count = root.querySelector('[data-result-count]');
    const empty = root.querySelector('[data-empty-results]');
    const filter = () => {
      const values = Object.fromEntries(controls.map(control => [control.dataset.filter,
        control.type === 'checkbox' ? control.checked : control.value]));
      let visible = 0;
      for (const row of rows) {
        const search = (values.search || '').trim().toLocaleLowerCase();
        const match = (!search || (row.dataset.search || '').toLocaleLowerCase().includes(search)) &&
          (!values.outcome || row.dataset.outcome === values.outcome) &&
          (!values.quality || row.dataset.quality === values.quality) &&
          (!values.harness || (row.dataset.harness || '').split('|').includes(values.harness)) &&
          (!values.incomplete || row.dataset.incomplete === 'true');
        row.hidden = !match;
        if (match) visible++;
      }
      if (count) count.textContent = `Showing ${visible} of ${rows.length}`;
      if (empty) empty.hidden = visible !== 0;
    };
    for (const control of controls) control.addEventListener('input', filter);
    let sortKey = null;
    let ascending = true;
    for (const button of table.querySelectorAll('thead [data-sort]')) {
      button.addEventListener('click', () => {
        ascending = sortKey === button.dataset.sort ? !ascending : true;
        sortKey = button.dataset.sort;
        const numeric = button.dataset.numeric === 'true';
        rows.sort((a, b) => {
          const av = a.dataset[sortKey], bv = b.dataset[sortKey];
          // Unknown values stay last in either sort direction.
          if (av === '' || av === undefined) return bv === '' || bv === undefined ? 0 : 1;
          if (bv === '' || bv === undefined) return -1;
          const order = numeric ? Number(av) - Number(bv) : av.localeCompare(bv, undefined, {numeric: true});
          return ascending ? order : -order;
        });
        for (const row of rows) table.appendChild(row);
        for (const header of table.querySelectorAll('thead th')) header.removeAttribute('aria-sort');
        button.closest('th').setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
        filter();
      });
    }
    root.querySelector('[data-reset-filters]')?.addEventListener('click', () => {
      for (const control of controls) {
        if (control.type === 'checkbox') control.checked = false;
        else control.value = '';
      }
      filter();
    });
    filter();
  }
})();
