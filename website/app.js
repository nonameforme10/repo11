/* ==========================================================
   Navbatchi Dashboard — app logic (vanilla JS, no dependencies)
   Sections: Config, Mock API, Helpers, State, Render, Actions, Init
   ========================================================== */
'use strict';

/* ---------- Config ---------- */
const CONFIG = {
  TIMEZONE: 'Asia/Tashkent',
  UPCOMING_DAYS: 13,          // rows shown in the schedule
  ANCHOR_DATE: '2026-10-05',  // the day members[0] was on duty
};

/* ---------- Mock data (replace with your REST API) ---------- */
const MOCK_MEMBERS = [
  { id: 1,  name: 'Baxtiyorov Abdulloh', username: '@developer_7779' },
  { id: 2,  name: 'Karimova Madina',     username: '@madina_k' },
  { id: 3,  name: 'Yusupov Jasur',       username: '@jasur_dev' },
  { id: 4,  name: 'Rahimova Zilola',     username: '@zilola_r' },
  { id: 5,  name: 'Tursunov Sardor',     username: '@sardor_t' },
  { id: 6,  name: 'Ergasheva Nilufar',   username: '@nilufar_e' },
  { id: 7,  name: 'Aliyev Bobur',        username: '@bobur_ali' },
  { id: 8,  name: 'Saidova Malika',      username: '@malika_s' },
  { id: 9,  name: 'Nazarov Otabek',      username: '@otabek_nz' },
  { id: 10, name: 'Hamidova Dilnoza',    username: '@dilnoza_h' },
  { id: 11, name: 'Qodirov Sherzod',     username: '@sherzod_q' },
  { id: 12, name: 'Ismoilova Gulnora',   username: '@gulnora_i' },
  { id: 13, name: 'Mirzayev Timur',      username: '@timur_mrz' },
  { id: 14, name: 'Orifjonova Sevara',   username: '@sevara_o' },
];

/* Single place to swap mock data for real HTTP calls later. */
const Api = {
  async getMembers() {
    // const res = await fetch('/api/members'); return res.json();
    return structuredClone(MOCK_MEMBERS);
  },
  async markDone() {
    // await fetch('/api/duty/done', { method: 'POST' });
    return { ok: true };
  },
};

/* ---------- Helpers ---------- */
const MONTHS = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr'];
const WEEKDAYS = ['Yakshanba', 'Dushanba', 'Seshanba', 'Chorshanba', 'Payshanba', 'Juma', 'Shanba'];
const DAY_MS = 86400000;

const $ = (sel) => document.querySelector(sel);

/** Create an element; text is set via textContent, so API data can't inject HTML. */
const h = (tag, cls, text) => {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
};

/** Today's calendar date in Tashkent, as a UTC-midnight Date (safe for day math). */
const tashkentToday = () => {
  const [y, m, d] = new Intl.DateTimeFormat('en-CA', { timeZone: CONFIG.TIMEZONE })
    .format(new Date()).split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
};
const addDays = (date, n) => new Date(date.getTime() + n * DAY_MS);
const fmtDay = (d) => `${d.getUTCDate()}-${MONTHS[d.getUTCMonth()]}`;
const fmtWeekday = (d) => WEEKDAYS[d.getUTCDay()];
const initials = (name) => name.split(' ').map((w) => w[0]).slice(0, 2).join('').toUpperCase();

/** Round avatar with initials; colour variant rotates through 3 theme gradients. */
const avatar = (member, i) => h('span', `avatar av-${i % 3}`, initials(member.name));

/* ---------- State ---------- */
const state = {
  members: [],
  offset: 0,    // index of today's person in members[]
  done: false,  // has today's duty been marked as done?
};

/** Person on duty `n` days from today (0 = today, 1 = tomorrow...). */
const memberAt = (n) => state.members[(state.offset + n) % state.members.length];

/* ---------- Render ---------- */
function renderClock() {
  $('#clock').textContent = new Intl.DateTimeFormat('en-GB', {
    timeZone: CONFIG.TIMEZONE, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date());
}

function renderHero() {
  const today = tashkentToday();
  const m = memberAt(0);
  $('#hero-name').textContent = m.name;
  $('#hero-user').textContent = m.username;
  $('#hero-date').textContent = `${fmtWeekday(today)}, ${fmtDay(today)}`;

  $('#hero').classList.toggle('is-done', state.done);
  const btn = $('#done-btn');
  btn.disabled = state.done;
  btn.textContent = state.done ? 'Bajarildi ✓' : 'Bajarildi deb belgilash';
}

function renderSchedule() {
  const list = $('#schedule');
  list.replaceChildren();
  const today = tashkentToday();

  for (let i = 1; i <= CONFIG.UPCOMING_DAYS; i++) {
    const date = addDays(today, i);
    const m = memberAt(i);
    const row = h('li', i === 1 ? 'row row--tomorrow' : 'row');

    const when = h('div', 'row__date');
    when.append(h('b', null, fmtDay(date)), h('small', null, fmtWeekday(date)));

    const who = h('div', 'row__who');
    who.append(h('div', 'row__name', m.name), h('div', 'row__user', m.username));

    row.append(when, avatar(m, (state.offset + i) % state.members.length), who);
    if (i === 1) row.append(h('span', 'row__tag', 'Ertaga'));
    list.append(row);
  }
}

function renderRoster() {
  const grid = $('#roster');
  grid.replaceChildren();
  $('#member-count').textContent = `(${state.members.length})`;

  state.members.forEach((m, i) => {
    const card = h('div', i === state.offset ? 'member member--active' : 'member');
    card.append(avatar(m, i), h('div', 'member__name', m.name), h('div', 'member__user', m.username));
    grid.append(card);
  });
}

const renderAll = () => { renderHero(); renderSchedule(); renderRoster(); };

/* ---------- Toast ---------- */
let toastTimer;
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
}

/* ---------- Actions ---------- */
async function markDone() {
  if (state.done) return;
  await Api.markDone();
  state.done = true;
  renderHero();
  toast('Navbatchilik bajarildi');
}

function shuffleSchedule() {
  // Fisher-Yates shuffle
  for (let i = state.members.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [state.members[i], state.members[j]] = [state.members[j], state.members[i]];
  }
  state.offset = 0;
  state.done = false;
  renderAll();
  toast('Jadval aralashtirildi');
}

function addMember() {
  const name = (prompt('Ism familiya:') || '').trim();
  if (!name) return;
  const username = (prompt('Telegram username:', '@') || '').trim() || '@username';
  state.members.push({ id: Date.now(), name, username });
  renderAll();
  toast(`${name} qo‘shildi`);
}

function skipTurn() {
  const skipped = memberAt(0).name;
  state.offset = (state.offset + 1) % state.members.length;
  state.done = false;
  renderAll();
  toast(`${skipped} navbati o‘tkazib yuborildi`);
}

/* ---------- Init ---------- */
async function init() {
  state.members = await Api.getMembers();

  // Figure out who is on duty today from the anchor date
  const days = Math.round((tashkentToday() - new Date(CONFIG.ANCHOR_DATE)) / DAY_MS);
  const n = state.members.length;
  state.offset = ((days % n) + n) % n;

  renderAll();
  renderClock();
  setInterval(renderClock, 1000);

  $('#done-btn').addEventListener('click', markDone);
  const actions = { shuffle: shuffleSchedule, add: addMember, skip: skipTurn };
  document.querySelector('.admin').addEventListener('click', (e) => {
    const action = e.target.closest('[data-action]')?.dataset.action;
    if (action) actions[action]();
  });
}

init();
