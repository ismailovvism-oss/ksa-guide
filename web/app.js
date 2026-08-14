/* Справочник по Саудии — фронтенд.

   Без сборки и зависимостей: один файл, который можно прочитать целиком.

   Навигация устроена в три уровня, от широкого к узкому:

     город    — контекст поиска, живёт в шапке и переживает переходы;
     раздел   — вкладки: объявления по категориям, локации отдельно;
     фасеты   — тип и район, свои у каждого раздела.

   Город намеренно не в одном ряду с остальными фильтрами: человек ищет
   жильё в своём городе, а не сравнивает Джидду с Мединой. Он выбирается
   один раз, запоминается и дальше сужает всё остальное.

   Состояние живёт в адресной строке, поэтому любую выдачу можно отправить
   ссылкой. */

const PAGE_SIZE = 48;
const LOCATION = 'location';      // раздел мест — отдельный от объявлений
const CITY_KEY = 'ksa.city';      // выбранный город переживает перезагрузку
const CHIPS_VISIBLE = 12;         // сколько чипов показывать до «ещё»

const state = {
  city: null,
  category: null,   // null — все объявления; LOCATION — раздел мест
  subcategory: null,
  district: null,
  q: '',
  offset: 0,
  chipsExpanded: false,
};

const el = {
  categories: document.getElementById('categories'),
  filters: document.getElementById('filters'),
  subcategories: document.getElementById('subcategories'),
  subcategoriesGroup: document.getElementById('subcategories-group'),
  subcategoriesLabel: document.getElementById('subcategories-label'),
  districts: document.getElementById('districts'),
  districtsGroup: document.getElementById('districts-group'),
  cityButton: document.getElementById('city-button'),
  cityCurrent: document.getElementById('city-current'),
  cityMenu: document.getElementById('city-menu'),
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

// --- доступ к данным --------------------------------------------------------

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

let staticBundle = null;
const loadStatic = async () => {
  if (!staticBundle) staticBundle = await fetchJson('data/listings.json');
  return staticBundle;
};

const inCategory = (item, category) =>
  category ? item.category === category : item.category !== LOCATION;

const matches = (item, params) =>
  inCategory(item, params.category) &&
  (!params.city || item.city === params.city) &&
  (!params.subcategory || item.subcategory === params.subcategory) &&
  (!params.district || item.district === params.district) &&
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
  const bundle = await loadStatic();
  const items = bundle.items;

  if (path === '/api/facets') {
    // Повторяет расчёт бэкенда: разделы и города — в своих рамках,
    // тип и район — в самых узких (город + раздел).
    const inCity = (list) =>
      params.city ? list.filter((item) => item.city === params.city) : list;
    const cityScoped = inCity(items);
    const narrow = cityScoped.filter((item) => inCategory(item, params.category));
    const counts = new Map();
    cityScoped.forEach((item) =>
      counts.set(item.category, (counts.get(item.category) || 0) + 1));

    return {
      categories: bundle.ads
        .filter((slug) => counts.get(slug))
        .map((slug) => ({
          slug,
          title: items.find((item) => item.category === slug).categoryTitle,
          count: counts.get(slug),
        })),
      // Города — все, какие есть в справочнике; счётчик — в рамках раздела,
      // чтобы на пустом разделе город всё равно можно было выбрать.
      cities: [...new Set(items.map((item) => item.city).filter(Boolean))]
        .map((city) => ({
          key: city,
          count: items.filter(
            (item) => item.city === city && inCategory(item, params.category)).length,
        }))
        .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key)),
      subcategories: countBy(narrow, 'subcategory'),
      districts: countBy(narrow, 'district'),
      adsTotal: [...counts].reduce((sum, [slug, n]) => sum + (slug === LOCATION ? 0 : n), 0),
      locationsTotal: counts.get(LOCATION) || 0,
    };
  }

  if (path === '/api/listings') {
    const found = items.filter((item) => matches(item, params)).sort(byRank);
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

// --- вспомогательное --------------------------------------------------------

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
  item.category === LOCATION ? '' : formatFreshness(item.last_seen_at);

const isLocations = () => state.category === LOCATION;
const hasFilters = () => Boolean(state.subcategory || state.district || state.q);

// --- адресная строка --------------------------------------------------------

const readUrl = () => {
  const params = new URLSearchParams(location.search);
  state.category = params.get('category');
  state.subcategory = params.get('type');
  state.district = params.get('district');
  state.q = params.get('q') || '';
  // Город берём из ссылки, а если её открыли без него — из прошлого выбора.
  state.city = params.get('city') || localStorage.getItem(CITY_KEY) || null;
  el.search.value = state.q;
};

const writeUrl = () => {
  const params = new URLSearchParams();
  if (state.city) params.set('city', state.city);
  if (state.category) params.set('category', state.category);
  if (state.subcategory) params.set('type', state.subcategory);
  if (state.district) params.set('district', state.district);
  if (state.q) params.set('q', state.q);
  const query = params.toString();
  history.replaceState(null, '', query ? `?${query}` : location.pathname);
};

// --- город ------------------------------------------------------------------

function renderCities(cities, sectionTotal) {
  el.cityCurrent.textContent = state.city || 'Все города';
  el.cityButton.classList.toggle('city__button--chosen', Boolean(state.city));

  // Итог раздела, а не сумма городов: у части карточек город не определился,
  // и по сумме получилось бы меньше, чем есть на самом деле.
  const options = [{ key: '', label: 'Все города', count: sectionTotal }, ...cities];
  el.cityMenu.innerHTML = options
    .map((option) => {
      const value = option.key || '';
      const label = option.label || option.key;
      const active = (state.city || '') === value;
      return `
        <button type="button" class="city__option" role="option"
                aria-selected="${active}" data-city="${escape(value)}">
          <span>${escape(label)}</span>
          <span class="city__count">${option.count}</span>
        </button>`;
    })
    .join('');
}

const closeCityMenu = () => {
  el.cityMenu.hidden = true;
  el.cityButton.setAttribute('aria-expanded', 'false');
};

// --- разделы ----------------------------------------------------------------

function renderCategories(facets) {
  const tab = (slug, title, count, extra = '') => `
    <button type="button" class="tab ${extra}" role="tab"
            aria-selected="${(state.category || '') === (slug || '')}"
            data-category="${escape(slug || '')}">
      ${escape(title)}
      <span class="tab__count">${count}</span>
    </button>`;

  const ads = [tab(null, 'Все объявления', facets.adsTotal)]
    .concat(facets.categories.map((item) => tab(item.slug, item.title, item.count)));

  // Локации отделены чертой: это справочник мест, а не лента объявлений,
  // и правила у них другие — ни цены, ни срока жизни.
  const locations = facets.locationsTotal
    ? `<span class="rail__divider" aria-hidden="true"></span>` +
      tab(LOCATION, 'Локации', facets.locationsTotal, 'tab--aside')
    : '';

  el.categories.innerHTML = ads.join('') + locations;
}

// --- фасеты -----------------------------------------------------------------

function renderChips(container, group, items, active, attribute) {
  if (!items.length) {
    group.hidden = true;
    return;
  }
  group.hidden = false;

  // Длинный список типов сворачиваем: два десятка чипов занимают пол-экрана
  // и мешают увидеть сами карточки.
  const collapsed = !state.chipsExpanded && items.length > CHIPS_VISIBLE;
  const shown = collapsed ? items.slice(0, CHIPS_VISIBLE) : items;

  container.innerHTML =
    shown
      .map(
        (item) => `
        <button type="button" class="chip" aria-pressed="${item.key === active}"
                data-${attribute}="${escape(item.key)}">
          ${escape(item.key)}
          <span class="chip__count">${item.count}</span>
        </button>`
      )
      .join('') +
    (collapsed
      ? `<button type="button" class="chip chip--more" data-expand>
           ещё ${items.length - CHIPS_VISIBLE}
         </button>`
      : '');
}

async function loadFacets() {
  const facets = await api('/api/facets', {
    city: state.city,
    category: state.category,
  });
  const sectionTotal = isLocations()
    ? facets.locationsTotal
    : state.category
      ? facets.categories.find((item) => item.slug === state.category)?.count || 0
      : facets.adsTotal;
  renderCities(facets.cities, sectionTotal);
  renderCategories(facets);

  // Подпись зависит от раздела: у мест это вид заведения, у объявлений — вид
  // товара или услуги.
  el.subcategoriesLabel.textContent = isLocations() ? 'Что это' : 'Тип';
  renderChips(el.subcategories, el.subcategoriesGroup, facets.subcategories,
              state.subcategory, 'subcategory');
  renderChips(el.districts, el.districtsGroup, facets.districts,
              state.district, 'district');
  el.filters.hidden = el.subcategoriesGroup.hidden && el.districtsGroup.hidden;
}

// --- выдача -----------------------------------------------------------------

function cardHtml(item) {
  // Когда город выбран, повторять его на каждой карточке незачем — весь экран
  // и так про него. Место освобождается под район, который различает карточки.
  const place = [item.subcategory, state.city ? item.district : item.city].filter(Boolean);
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

// Пустой экран объясняет, почему пусто, и даёт следующий шаг.
function emptyHtml() {
  const block = (title, text, button = '') =>
    `<strong>${title}</strong><p>${text}</p>${button}`;
  const button = (action, label) =>
    `<button type="button" class="button" data-action="${action}">${label}</button>`;

  if (hasFilters()) {
    return block(
      'Ничего не нашлось',
      'Попробуйте другой запрос или снимите часть фильтров.',
      button('reset', 'Сбросить фильтры'),
    );
  }
  if (isLocations()) {
    return block(
      'Здесь пока пусто',
      state.city
        ? `В городе ${escape(state.city)} мест ещё нет.`
        : 'Мест в этом разрезе ещё нет.',
      state.city ? button('all-cities', 'Посмотреть все города') : '',
    );
  }
  return block(
    'Объявлений пока нет',
    'Идёт подключение телеграм-каналов — аренда, работа и услуги появятся ' +
      'здесь по мере сбора. А пока посмотрите места: их уже больше двухсот.',
    button('locations', 'Открыть локации'),
  );
}

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
    district: state.district,
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
  if (!data.total) el.empty.innerHTML = emptyHtml();

  el.count.textContent = data.total
    ? `${data.total} ${plural(data.total, 'карточка', 'карточки', 'карточек')}`
    : '';
  el.reset.hidden = !hasFilters();
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

el.cityButton.addEventListener('click', () => {
  const open = el.cityMenu.hidden;
  el.cityMenu.hidden = !open;
  el.cityButton.setAttribute('aria-expanded', String(open));
});

el.cityMenu.addEventListener('click', (event) => {
  const option = event.target.closest('[data-city]');
  if (!option) return;
  state.city = option.dataset.city || null;
  // Город — долгоживущий выбор, а не разовый фильтр.
  if (state.city) localStorage.setItem(CITY_KEY, state.city);
  else localStorage.removeItem(CITY_KEY);
  // Район привязан к городу и при смене теряет смысл.
  state.district = null;
  closeCityMenu();
  refresh();
});

document.addEventListener('click', (event) => {
  if (!el.cityMenu.hidden && !event.target.closest('.city')) closeCityMenu();
});

el.categories.addEventListener('click', (event) => {
  const tab = event.target.closest('[data-category]');
  if (!tab) return;
  state.category = tab.dataset.category || null;
  // Тип и район принадлежат прежнему разделу — при переходе они не переносятся.
  state.subcategory = null;
  state.district = null;
  state.chipsExpanded = false;
  refresh();
});

const toggleChip = (key, attribute) => (event) => {
  if (event.target.closest('[data-expand]')) {
    state.chipsExpanded = true;
    loadFacets();
    return;
  }
  const chip = event.target.closest(`[data-${attribute}]`);
  if (!chip) return;
  const value = chip.dataset[key];
  state[key] = state[key] === value ? null : value;
  refresh();
};

el.subcategories.addEventListener('click', toggleChip('subcategory', 'subcategory'));
el.districts.addEventListener('click', toggleChip('district', 'district'));

el.results.addEventListener('click', (event) => {
  const card = event.target.closest('.card');
  if (card) openDetail(card.dataset.id);
});

el.more.addEventListener('click', () => loadListings({ append: true }));

// Сброс не трогает город: это контекст, а не фильтр.
function resetFilters() {
  state.subcategory = null;
  state.district = null;
  state.q = '';
  state.chipsExpanded = false;
  el.search.value = '';
  el.searchClear.hidden = true;
  refresh();
}

el.reset.addEventListener('click', resetFilters);

el.empty.addEventListener('click', (event) => {
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (!action) return;
  if (action === 'reset') resetFilters();
  if (action === 'locations') {
    state.category = LOCATION;
    state.subcategory = null;
    state.district = null;
    refresh();
  }
  if (action === 'all-cities') {
    state.city = null;
    localStorage.removeItem(CITY_KEY);
    refresh();
  }
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
  if (event.key === 'Escape') {
    if (!el.panel.hidden) closeDetail();
    else if (!el.cityMenu.hidden) closeCityMenu();
  }
  if (event.key === '/' && document.activeElement !== el.search) {
    event.preventDefault();
    el.search.focus();
  }
});

// --- запуск -----------------------------------------------------------------

readUrl();
el.searchClear.hidden = !el.search.value;
refresh();
