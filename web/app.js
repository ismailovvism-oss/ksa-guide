/* Справочник по Саудии — фронтенд.

   Без сборки и зависимостей: один файл, который можно прочитать целиком.
   Состояние фильтров живёт в адресной строке, поэтому любую выдачу
   («аренда в Джидде») можно просто отправить ссылкой. */

const PAGE_SIZE = 48;

const state = { category: null, city: null, subcategory: null, q: '', offset: 0 };

const el = {
  categories: document.getElementById('categories'),
  filters: document.getElementById('filters'),
  cities: document.getElementById('cities'),
  citiesGroup: document.getElementById('cities-group'),
  subcategories: document.getElementById('subcategories'),
  subcategoriesGroup: document.getElementById('subcategories-group'),
  results: document.getElementById('results'),
  count: document.getElementById('count'),
  reset: document.getElementById('reset'),
  more: document.getElementById('more'),
  empty: document.getElementById('empty'),
  search: document.getElementById('search'),
  searchClear: document.getElementById('search-clear'),
  panel: document.getElementById('panel'),
  panelBody: document.getElementById('panel-body'),
};

// --- вспомогательное --------------------------------------------------------

/* Два режима работы.

   Обычный: данные берутся у бэкенда по /api/…
   Статический: сайт выложен на GitHub Pages, бэкенда нет, и весь справочник
   лежит в одном файле data/listings.json — фильтрация считается в браузере.
   Включается флагом window.KSA_STATIC, который ставит scripts/export_static.py.

   Ответы в обоих режимах одинаковы по форме, поэтому остальной код
   про этот разрыв ничего не знает. */

const fetchJson = (url) =>
  fetch(url).then((response) => {
    if (!response.ok) throw new Error(response.statusText);
    return response.json();
  });

let staticItems = null;
const loadStaticItems = async () => {
  if (!staticItems) staticItems = await fetchJson('data/listings.json');
  return staticItems;
};

const matchesFilters = (item, params) =>
  (!params.category || item.category === params.category) &&
  (!params.city || item.city === params.city) &&
  (!params.subcategory || item.subcategory === params.subcategory) &&
  (!params.q ||
    [item.title, item.summary, item.city, item.subcategory]
      .some((field) => (field || '').toLowerCase().includes(params.q.toLowerCase())));

// Тот же порядок, что и в SQL на бэкенде: подъём → свежесть → наличие фото.
const byRank = (a, b) =>
  Number(b.promoted) - Number(a.promoted) ||
  String(b.last_seen_at).localeCompare(String(a.last_seen_at)) ||
  Number(Boolean(b.photo)) - Number(Boolean(a.photo)) ||
  a.id - b.id;

function countBy(items, key) {
  const counts = new Map();
  items.forEach((item) => {
    if (item[key]) counts.set(item[key], (counts.get(item[key]) || 0) + 1);
  });
  return [...counts]
    .map(([value, count]) => ({ key: value, count }))
    .sort((a, b) => b.count - a.count);
}

async function staticApi(path, params = {}) {
  const items = await loadStaticItems();

  if (path === '/api/facets') {
    const scoped = params.category
      ? items.filter((item) => item.category === params.category)
      : items;
    return {
      categories: countBy(items, 'category').map((row) => ({
        slug: row.key,
        title: items.find((item) => item.category === row.key).categoryTitle,
        count: row.count,
      })),
      cities: countBy(scoped, 'city'),
      subcategories: countBy(scoped, 'subcategory'),
      total: items.length,
    };
  }

  if (path === '/api/listings') {
    const found = items.filter((item) => matchesFilters(item, params)).sort(byRank);
    const offset = params.offset || 0;
    const page = found.slice(offset, offset + (params.limit || PAGE_SIZE));
    return { total: found.length, items: page, hasMore: offset + page.length < found.length };
  }

  const id = Number(path.split('/').pop());
  const item = items.find((entry) => entry.id === id);
  if (!item) throw new Error('Карточка не найдена');
  return { ...item, sources: [] };
}

const api = (path, params) => {
  if (window.KSA_STATIC) return staticApi(path, params || {});
  const url = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') url.searchParams.set(key, value);
  });
  return fetchJson(url);
};

const escape = (value) =>
  String(value ?? '').replace(/[&<>"']/g, (ch) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

const plural = (n, one, few, many) => {
  const mod100 = n % 100;
  const mod10 = n % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
};

const PERIODS = { month: 'в месяц', year: 'в год', day: 'в сутки', once: 'разово' };

const formatPrice = (item) => {
  if (!item.price_amount) return '';
  const amount = new Intl.NumberFormat('ru-RU').format(item.price_amount);
  const currency = item.price_currency === 'SAR' ? '﷼' : (item.price_currency || '');
  const period = PERIODS[item.price_period] || '';
  return `${amount} ${currency}${period ? ' ' + period : ''}`.trim();
};

// «Свежесть» важнее точной даты: справочник ценен тем, что объявление живое.
const formatFreshness = (iso) => {
  if (!iso) return '';
  const days = Math.floor((Date.now() - new Date(iso)) / 86400000);
  if (Number.isNaN(days)) return '';
  if (days <= 0) return 'сегодня';
  if (days === 1) return 'вчера';
  if (days < 7) return `${days} ${plural(days, 'день', 'дня', 'дней')} назад`;
  if (days < 31) {
    const weeks = Math.floor(days / 7);
    return `${weeks} ${plural(weeks, 'неделю', 'недели', 'недель')} назад`;
  }
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} ${plural(months, 'месяц', 'месяца', 'месяцев')} назад`;
  return new Date(iso).toLocaleDateString('ru-RU', { year: 'numeric', month: 'long' });
};

// У объявления свежесть — главный признак: живое оно или протухло. У места
// её нет, и дата попадания в базу выглядела бы как дата открытия музея.
const freshnessFor = (item) =>
  item.category === 'location' ? '' : formatFreshness(item.last_seen_at);

// --- адресная строка --------------------------------------------------------

const readUrl = () => {
  const params = new URLSearchParams(location.search);
  state.category = params.get('category');
  state.city = params.get('city');
  state.subcategory = params.get('type');
  state.q = params.get('q') || '';
  el.search.value = state.q;
};

const writeUrl = () => {
  const params = new URLSearchParams();
  if (state.category) params.set('category', state.category);
  if (state.city) params.set('city', state.city);
  if (state.subcategory) params.set('type', state.subcategory);
  if (state.q) params.set('q', state.q);
  const query = params.toString();
  history.replaceState(null, '', query ? `?${query}` : location.pathname);
};

// --- навигация --------------------------------------------------------------

function renderCategories(facets) {
  const tabs = [{ slug: null, title: 'Всё', count: facets.total }, ...facets.categories];
  el.categories.innerHTML = tabs
    .map(
      (tab) => `
      <button type="button" class="tab" role="tab"
              aria-selected="${tab.slug === state.category}"
              data-category="${escape(tab.slug ?? '')}">
        ${escape(tab.title)}
        <span class="tab__count">${tab.count}</span>
      </button>`
    )
    .join('');
}

function renderChips(container, group, items, active, attribute) {
  if (!items.length) {
    group.hidden = true;
    return;
  }
  group.hidden = false;
  container.innerHTML = items
    .map(
      (item) => `
      <button type="button" class="chip" aria-pressed="${item.key === active}"
              data-${attribute}="${escape(item.key)}">
        ${escape(item.key)}
        <span class="chip__count">${item.count}</span>
      </button>`
    )
    .join('');
}

async function loadFacets() {
  const facets = await api('/api/facets', { category: state.category });
  renderCategories(facets);
  renderChips(el.cities, el.citiesGroup, facets.cities, state.city, 'city');
  renderChips(el.subcategories, el.subcategoriesGroup, facets.subcategories,
              state.subcategory, 'subcategory');
  el.filters.hidden = el.citiesGroup.hidden && el.subcategoriesGroup.hidden;
}

// --- выдача -----------------------------------------------------------------

function cardHtml(item) {
  const place = [item.subcategory, item.city].filter(Boolean);
  const price = formatPrice(item);
  const media = item.photo
    ? `<img src="${escape(item.photo)}" alt="" loading="lazy" decoding="async">`
    : `<div class="card__placeholder">${escape((item.title || '·').trim()[0] || '·')}</div>`;

  return `
    <button type="button" class="card" data-id="${item.id}">
      <div class="card__media">
        ${media}
        ${item.promoted ? '<span class="badge">Поднято</span>' : ''}
      </div>
      <div class="card__body">
        ${place.length ? `<div class="card__meta">
          ${place.map((part, index) =>
            (index ? '<span class="card__dot">·</span>' : '') +
            (index === 0 ? `<b>${escape(part)}</b>` : `<span>${escape(part)}</span>`)
          ).join('')}
        </div>` : ''}
        <h2 class="card__title">${escape(item.title)}</h2>
        ${item.summary ? `<p class="card__text">${escape(item.summary)}</p>` : ''}
        <div class="card__foot">
          <span>${escape(price || item.categoryTitle)}</span>
          <span>${escape(freshnessFor(item))}</span>
        </div>
      </div>
    </button>`;
}

const skeletons = (n) =>
  Array.from({ length: n }, () => `
    <div class="skeleton">
      <div class="skeleton__media"></div>
      <div class="skeleton__line" style="width:45%"></div>
      <div class="skeleton__line" style="width:85%"></div>
      <div class="skeleton__line" style="width:65%"></div>
    </div>`).join('');

async function loadListings({ append = false } = {}) {
  if (!append) {
    state.offset = 0;
    el.results.innerHTML = skeletons(8);
    el.empty.hidden = true;
    el.more.hidden = true;
  }

  const data = await api('/api/listings', {
    category: state.category,
    city: state.city,
    subcategory: state.subcategory,
    q: state.q,
    limit: PAGE_SIZE,
    offset: state.offset,
  });

  const html = data.items.map(cardHtml).join('');
  if (append) el.results.insertAdjacentHTML('beforeend', html);
  else el.results.innerHTML = html;

  state.offset += data.items.length;
  el.more.hidden = !data.hasMore;
  el.empty.hidden = data.total > 0;
  el.count.textContent = data.total
    ? `${data.total} ${plural(data.total, 'карточка', 'карточки', 'карточек')}`
    : '';
  el.reset.hidden = !(state.category || state.city || state.subcategory || state.q);
}

async function refresh() {
  writeUrl();
  await Promise.all([loadFacets(), loadListings()]);
}

// --- карточка ---------------------------------------------------------------

function detailHtml(item) {
  const facts = [
    item.rooms && { label: 'Комнат', value: item.rooms },
    item.area_sqm && { label: 'Площадь', value: `${item.area_sqm} м²` },
    item.city && { label: 'Город', value: item.city },
    item.district && { label: 'Район', value: item.district },
    item.repost_count > 1 && { label: 'Повторов в каналах', value: item.repost_count },
  ].filter(Boolean);

  const contacts = (item.contacts || []).map((contact) =>
    contact.type === 'phone'
      ? `<a class="action" href="tel:${escape(contact.value)}">${escape(contact.value)}</a>`
      : `<a class="action" href="https://t.me/${escape(contact.value)}" target="_blank"
            rel="noopener">@${escape(contact.value)}</a>`
  );

  // У склеенной карточки источников несколько — тогда подписываем каналами.
  // У импортированной локации канал неизвестен, только ссылка на пост.
  const sources = item.sources?.length
    ? item.sources
    : (item.source_url ? [{ url: item.source_url, label: 'Исходный пост' }] : []);

  const price = formatPrice(item);
  const place = [item.subcategory, item.city].filter(Boolean);

  return `
    ${item.photo ? `<div class="detail__media">
        <img src="${escape(item.photo)}" alt=""></div>` : ''}
    <div class="detail__body">
      <div class="detail__meta">
        <b>${escape(item.categoryTitle)}</b>
        ${place.map((part) => `<span class="card__dot">·</span><span>${escape(part)}</span>`).join('')}
      </div>
      <h2 class="detail__title" id="panel-title">${escape(item.title)}</h2>
      ${price ? `<div class="detail__price">${escape(price)}</div>` : ''}
      ${item.summary ? `<p class="detail__text">${escape(item.summary)}</p>` : ''}

      ${facts.length ? `<dl class="facts">${facts.map((fact) => `
        <div class="fact"><dt>${escape(fact.label)}</dt><dd>${escape(fact.value)}</dd></div>
      `).join('')}</dl>` : ''}

      <div class="actions">
        ${item.map_url ? `<a class="action action--primary" href="${escape(item.map_url)}"
             target="_blank" rel="noopener">Открыть на карте</a>` : ''}
        ${contacts.join('')}
      </div>

      ${sources.length ? `<div class="sources">
        <h3>Источник</h3>
        <ul>${sources.map((source) => `
          <li><a href="${escape(source.url)}" target="_blank" rel="noopener">
            ${escape(source.label || '@' + String(source.channel).replace(/^@/, ''))}
          </a>${source.postedAt ? ` · ${escape(formatFreshness(source.postedAt))}` : ''}</li>
        `).join('')}</ul>
      </div>` : ''}
    </div>`;
}

let lastFocused = null;

async function openDetail(id) {
  lastFocused = document.activeElement;
  el.panelBody.innerHTML = '';
  el.panel.hidden = false;
  document.body.style.overflow = 'hidden';
  const item = await api(`/api/listings/${id}`);
  el.panelBody.innerHTML = detailHtml(item);
  el.panel.querySelector('.panel__close').focus();
}

function closeDetail() {
  el.panel.hidden = true;
  document.body.style.overflow = '';
  lastFocused?.focus();
}

// --- события ----------------------------------------------------------------

el.categories.addEventListener('click', (event) => {
  const tab = event.target.closest('[data-category]');
  if (!tab) return;
  const value = tab.dataset.category || null;
  state.category = state.category === value ? null : value;
  // Город и тип относятся к прежней категории — при смене их надо снять.
  state.city = null;
  state.subcategory = null;
  refresh();
});

const toggleFilter = (key) => (event) => {
  const chip = event.target.closest(`[data-${key}]`);
  if (!chip) return;
  const value = chip.dataset[key];
  state[key === 'subcategory' ? 'subcategory' : 'city'] =
    state[key === 'subcategory' ? 'subcategory' : 'city'] === value ? null : value;
  refresh();
};

el.cities.addEventListener('click', toggleFilter('city'));
el.subcategories.addEventListener('click', toggleFilter('subcategory'));

el.results.addEventListener('click', (event) => {
  const card = event.target.closest('.card');
  if (card) openDetail(card.dataset.id);
});

el.more.addEventListener('click', () => loadListings({ append: true }));

el.reset.addEventListener('click', () => {
  Object.assign(state, { category: null, city: null, subcategory: null, q: '' });
  el.search.value = '';
  el.searchClear.hidden = true;
  refresh();
});

let searchTimer;
el.search.addEventListener('input', () => {
  el.searchClear.hidden = !el.search.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.q = el.search.value.trim();
    writeUrl();
    loadListings();
  }, 250);
});

el.searchClear.addEventListener('click', () => {
  el.search.value = '';
  el.searchClear.hidden = true;
  state.q = '';
  refresh();
});

el.panel.addEventListener('click', (event) => {
  if (event.target.closest('[data-close]')) closeDetail();
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !el.panel.hidden) closeDetail();
  if (event.key === '/' && document.activeElement !== el.search) {
    event.preventDefault();
    el.search.focus();
  }
});

// --- запуск -----------------------------------------------------------------

readUrl();
el.searchClear.hidden = !el.search.value;
refresh();
