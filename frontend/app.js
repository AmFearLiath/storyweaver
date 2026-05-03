'use strict';

/* ══════════════════════════════════════════════════════════════════════════
   ADVENTURE — app.js · Vollständige Spiellogik
   ══════════════════════════════════════════════════════════════════════════ */

// ── State ────────────────────────────────────────────────────────────────────
const state = {
  token: null,
  user: null,
  currentStoryId: null,
  currentStory: null,
  isProcessing: false,
  ollamaOk: false,
  models: [],
  stories: [],
  characters: [],
  presetRules: [],
  gameStarted: false,
  worldItems: [],
  currentAvatarFile: null,
  lastActiveCharName: null,
};

// ══════════════════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════════════════
async function init() {
  state.token = localStorage.getItem('token');
  if (!state.token) { window.location.href = '/'; return; }

  // Verify token
  try {
    const me = await api('/api/auth/me');
    if (!me) return;
    state.user = me;
    setVal('userName', me.username);
  } catch {
    window.location.href = '/';
    return;
  }

  // Load preset rules
  try {
    const pr = await api('/api/presets/rules');
    state.presetRules = pr || [];
  } catch {}

  // Load stories
  await loadStories();

  if (state.stories.length === 0) {
    openStoryModal();
  } else {
    const savedId = localStorage.getItem('currentStoryId');
    if (savedId && state.stories.find(s => s.id == savedId)) {
      await selectStory(parseInt(savedId));
    } else {
      openStoryModal();
    }
  }

  // Ollama status
  await checkOllama();
  setInterval(checkOllama, 30000);

  // Enter key for free action
  const fi = document.getElementById('freeInput');
  if (fi) fi.addEventListener('keydown', e => { if (e.key === 'Enter') submitFreeAction(); });

  // Keyboard shortcuts [1]–[6] for options
  document.addEventListener('keydown', e => {
    if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;
    const n = parseInt(e.key);
    if (n >= 1 && n <= 6 && !state.isProcessing) {
      const btn = document.querySelector(`.option-btn[data-opt-index="${n - 1}"]`);
      if (btn && !btn.disabled) btn.click();
    }
  });

  // Close user dropdown on outside click
  document.addEventListener('click', e => {
    const dd = document.getElementById('userDropdown');
    const btn = document.getElementById('userMenuBtn');
    if (dd && btn && !dd.contains(e.target) && !btn.contains(e.target)) {
      dd.classList.add('hidden');
    }
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// AUTH
// ══════════════════════════════════════════════════════════════════════════════
async function logout() {
  try { await api('/api/auth/logout', 'POST'); } catch {}
  localStorage.removeItem('token');
  localStorage.removeItem('currentStoryId');
  window.location.href = '/';
}

function toggleUserMenu() {
  document.getElementById('userDropdown').classList.toggle('hidden');
}

// ══════════════════════════════════════════════════════════════════════════════
// STORIES
// ══════════════════════════════════════════════════════════════════════════════
async function loadStories() {
  try {
    state.stories = await api('/api/stories') || [];
  } catch { state.stories = []; }
}

function openStoryModal() {
  renderStoryList();
  document.getElementById('storyModal').classList.remove('hidden');
}

function closeStoryModal() {
  document.getElementById('storyModal').classList.add('hidden');
}

function renderStoryList() {
  const el = document.getElementById('storyList');
  if (!state.stories.length) {
    el.innerHTML = '<p class="story-list-empty">Keine Geschichten vorhanden — erstelle deine erste!</p>';
    return;
  }
  el.innerHTML = state.stories.map(s => {
    const active = s.id === state.currentStoryId ? 'active' : '';
    return `
      <div class="story-card ${active}">
        <div class="story-card-info">
          <div class="story-card-name">${esc(s.name)}</div>
          <div class="story-card-meta">
            ${esc(s.story_genre || s.genre || '')} · Szene ${s.scene_counter || 0}
            ${s.description ? ` · ${esc(s.description.slice(0,60))}${s.description.length > 60 ? '…' : ''}` : ''}
          </div>
        </div>
        <div class="story-card-actions">
          <button class="btn btn-primary btn-sm" onclick="selectStory(${s.id});closeStoryModal()">▶ Spielen</button>
          <button class="btn btn-danger-ghost btn-sm" onclick="deleteStory(${s.id})">🗑️</button>
        </div>
      </div>`;
  }).join('');
}

async function selectStory(id) {
  state.currentStoryId = id;
  localStorage.setItem('currentStoryId', id);
  state.currentStory = state.stories.find(s => s.id === id) || null;

  // Update header
  const nameEl = document.getElementById('storyName');
  if (nameEl && state.currentStory) nameEl.textContent = state.currentStory.name;

  // Reset UI to neutral while loading the new story
  state.gameStarted = false;
  document.getElementById('storyContent').innerHTML = '';
  document.getElementById('startScreen').classList.remove('hidden');
  document.getElementById('storyWindow').classList.add('hidden');
  document.getElementById('optionsPanel').classList.add('hidden');
  renderWorldItems([]);

  // Load story config
  try {
    const cfg = await api(`/api/stories/${id}/config`);
    if (cfg) loadStoryConfig(cfg.config || {});
  } catch {}

  // Load characters
  await loadCharacters();

  // Load game state
  await loadGameState();
}

async function createNewStory() {
  const name = getVal('newStoryName').trim();
  if (!name) { showToast('Bitte einen Namen eingeben.'); return; }
  try {
    const s = await api('/api/stories', 'POST', {
      name,
      description: getVal('newStoryDesc'),
      genre: getVal('newStoryGenre'),
    });
    state.stories.unshift(s);
    setVal('newStoryName', '');
    setVal('newStoryDesc', '');
    renderStoryList();
    showToast(`Geschichte "${s.name}" erstellt!`);
    await selectStory(s.id);
    closeStoryModal();
  } catch (e) { showToast('Fehler: ' + e.message, 'error'); }
}

async function deleteStory(id) {
  if (!confirm('Geschichte wirklich löschen? Alle Daten gehen verloren.')) return;
  try {
    await api(`/api/stories/${id}`, 'DELETE');
    state.stories = state.stories.filter(s => s.id !== id);
    if (state.currentStoryId === id) {
      state.currentStoryId = null;
      state.currentStory = null;
      localStorage.removeItem('currentStoryId');
      document.getElementById('storyName').textContent = 'Geschichte wählen...';
    }
    renderStoryList();
    showToast('Geschichte gelöscht.');
  } catch (e) { showToast('Fehler: ' + e.message, 'error'); }
}

// ══════════════════════════════════════════════════════════════════════════════
// STORY CONFIG (World + Style modals)
// ══════════════════════════════════════════════════════════════════════════════
function loadStoryConfig(cfg) {
  // World tab
  setVal('worldName',      cfg.world_name        || '');
  setVal('worldEra',       cfg.world_era          || '');
  setVal('worldAtmosphere',cfg.world_atmosphere   || '');
  setVal('worldDesc',      cfg.world_description  || '');
  setVal('scenarioDesc',   cfg.scenario           || '');

  // Genre tab
  const genre = cfg.story_genre || 'Fantasy';
  const sel = document.getElementById('storyGenre');
  if (sel) {
    // Check if option exists, else add it
    let opt = [...sel.options].find(o => o.value === genre);
    if (!opt) {
      const newOpt = document.createElement('option');
      newOpt.value = genre; newOpt.textContent = genre;
      sel.appendChild(newOpt);
    }
    sel.value = genre;
  }
  setVal('storyFrame', cfg.story_frame || '');

  // Rules tab
  renderPresetRules(cfg.preset_rules || []);
  setVal('customRules', cfg.custom_rules || '');

  // Style tab
  setSelectVal('languageStyle', cfg.language_style || 'neutral');
  setSelectVal('detailLevel',   cfg.detail_level   || 'mittel');

  // Style examples
  renderStyleExamples(cfg.style_examples || []);

  // Forbidden phrases
  const phrases = Array.isArray(cfg.forbidden_phrases) ? cfg.forbidden_phrases.join('\n') : (cfg.forbidden_phrases || '');
  setVal('forbiddenPhrases', phrases);

  // Word alts
  renderWordAlts(cfg.forbidden_words_alts || []);
}

function collectConfig() {
  const sel = document.getElementById('storyGenre');
  const genre = sel ? sel.value : 'Fantasy';

  // Collect preset rules
  const presetRules = [];
  document.querySelectorAll('.preset-rule-item.checked').forEach(el => {
    presetRules.push(el.dataset.rule);
  });

  // Collect style examples
  const examples = [];
  document.querySelectorAll('.style-example-pair').forEach(pair => {
    const good = pair.querySelector('.ex-good')?.value || '';
    const bad  = pair.querySelector('.ex-bad')?.value  || '';
    if (good || bad) examples.push({ good, bad });
  });

  // Collect forbidden phrases
  const rawPhrases = getVal('forbiddenPhrases');
  const forbidden = rawPhrases.split('\n').map(s => s.trim()).filter(Boolean);

  // Collect word alts
  const wordAlts = [];
  document.querySelectorAll('.word-alt-row').forEach(row => {
    const word = row.querySelector('.word-alt-word')?.value?.trim() || '';
    const alts = row.querySelector('.word-alt-alts')?.value?.trim() || '';
    if (word) wordAlts.push({ word, alts });
  });

  return {
    world_name:          getVal('worldName'),
    world_era:           getVal('worldEra'),
    world_atmosphere:    getVal('worldAtmosphere'),
    world_description:   getVal('worldDesc'),
    scenario:            getVal('scenarioDesc'),
    story_genre:         genre,
    story_genre_custom:  '', // handled by addCustomGenre
    story_frame:         getVal('storyFrame'),
    preset_rules:        presetRules,
    custom_rules:        getVal('customRules'),
    language_style:      getVal('languageStyle'),
    detail_level:        getVal('detailLevel'),
    style_examples:      examples,
    forbidden_phrases:   forbidden,
    forbidden_words_alts: wordAlts,
  };
}

async function saveWorldConfig() {
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.'); return; }
  try {
    await api(`/api/stories/${state.currentStoryId}/config`, 'POST', { config: collectConfig() });
    showToast('Welteinstellungen gespeichert!');
    closeWorldModal();
  } catch (e) { showToast('Fehler: ' + e.message, 'error'); }
}

async function saveStyleConfig() {
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.'); return; }
  try {
    await api(`/api/stories/${state.currentStoryId}/config`, 'POST', { config: collectConfig() });
    showToast('Stileinstellungen gespeichert!');
    closeStyleModal();
  } catch (e) { showToast('Fehler: ' + e.message, 'error'); }
}

// ── World Modal ──────────────────────────────────────────────────────────────
function openWorldModal() {
  if (!state.currentStoryId) { showToast('Zuerst eine Geschichte auswählen.'); return; }
  // Reset tabs
  switchTabById('worldModal', 'wtab-world');
  document.getElementById('worldModal').classList.remove('hidden');
}
function closeWorldModal() { document.getElementById('worldModal').classList.add('hidden'); }

// ── Style Modal ──────────────────────────────────────────────────────────────
function openStyleModal() {
  if (!state.currentStoryId) { showToast('Zuerst eine Geschichte auswählen.'); return; }
  switchTabById('styleModal', 'stab-style');
  document.getElementById('styleModal').classList.remove('hidden');
}
function closeStyleModal() { document.getElementById('styleModal').classList.add('hidden'); }

// ── Custom Genre ─────────────────────────────────────────────────────────────
function addCustomGenre() {
  const inp = document.getElementById('customGenreInput');
  const val = inp ? inp.value.trim() : '';
  if (!val) { showToast('Bitte ein Genre eingeben.'); return; }
  const sel = document.getElementById('storyGenre');
  const exists = [...sel.options].find(o => o.value.toLowerCase() === val.toLowerCase());
  if (!exists) {
    const opt = document.createElement('option');
    opt.value = val; opt.textContent = val;
    sel.appendChild(opt);
  }
  sel.value = val;
  inp.value = '';
  showToast(`Genre "${val}" hinzugefügt und ausgewählt.`);
}

// ── Preset Rules ─────────────────────────────────────────────────────────────
function renderPresetRules(active) {
  const el = document.getElementById('presetRulesList');
  if (!el) return;
  el.innerHTML = '';
  state.presetRules.forEach(rule => {
    const checked = active.includes(rule);
    const item = document.createElement('div');
    item.className = `preset-rule-item${checked ? ' checked' : ''}`;
    item.dataset.rule = rule;
    item.innerHTML = `<input type="checkbox" class="preset-rule-cb" ${checked ? 'checked' : ''} />
      <span class="preset-rule-text">${esc(rule)}</span>`;
    const cb = item.querySelector('input');
    cb.addEventListener('change', () => item.classList.toggle('checked', cb.checked));
    item.addEventListener('click', e => {
      if (e.target === cb) return;
      cb.checked = !cb.checked;
      cb.dispatchEvent(new Event('change'));
    });
    el.appendChild(item);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// CHARACTERS
// ══════════════════════════════════════════════════════════════════════════════
async function loadCharacters() {
  if (!state.currentStoryId) return;
  try {
    state.characters = await api(`/api/characters/${state.currentStoryId}`) || [];
    renderCharacters(state.characters);
  } catch {}
}

function renderCharacters(chars) {
  const el = document.getElementById('characterList');
  if (!el) return;
  if (!chars.length) { el.innerHTML = '<p style="color:var(--text-dim);font-size:12px;padding:4px">Noch keine Charaktere.</p>'; return; }
  el.innerHTML = chars.map(c => {
    const st = c.status || 'alive';
    const prot = c.is_protagonist ? '<span class="char-prot-star" title="Hauptprotagonist">⭐</span>' : '';
    const inv = Array.isArray(c.inventory) ? c.inventory : [];
    const exp = Array.isArray(c.experiences) ? c.experiences : [];
    const cc  = (c.current_clothing || '').trim();
    let stateLine = '';
    if (cc) stateLine = cc.length > 32 ? cc.slice(0, 30) + '…' : cc;
    else if (inv.length) stateLine = `🎒 ${inv.length} Gegenstand${inv.length !== 1 ? 'stände' : ''}`;
    const expBadge = exp.length ? `<span title="${exp.length} Erfahrung(en)" style="font-size:10px;color:var(--accent-dim)">✦${exp.length}</span>` : '';
    const avatarHtml = c.avatar_path
      ? `<img src="${esc(c.avatar_path)}" class="char-avatar-thumb" alt="${esc(c.name)}" />`
      : `<span class="char-avatar-thumb char-avatar-placeholder">👤</span>`;
    const rowCls = `char-row${c.is_protagonist ? ' is-protagonist' : ''}${state.lastActiveCharName === c.name ? ' is-active' : ''}`;
    return `
      <div class="${rowCls}" onclick="openCharModalEdit(${c.id})">
        ${avatarHtml}
        <span class="char-status-dot ${st}" title="${st}"></span>
        ${prot}
        <div class="char-row-info">
          <div class="char-row-name">${esc(c.name)} ${expBadge}</div>
          <div class="char-row-role">${esc(c.role || '')}</div>
          ${stateLine ? `<div class="char-row-state">${esc(stateLine)}</div>` : ''}
        </div>
        <span class="char-row-edit">✏️</span>
      </div>`;
  }).join('');
}

function openCharModal(id = null) {
  document.getElementById('charModalTitle').textContent = id ? 'Charakter bearbeiten' : 'Neuer Charakter';
  document.getElementById('editCharId').value = id || '';
  document.getElementById('deleteCharBtn').classList.toggle('hidden', !id);

  // Reset form
  ['editCharName','editCharRole','editCharAge','editCharPhysical','editCharClothing',
   'editCharDesc','editCharPowers','editCharLikes','editCharDislikes','editCharWeapon',
   'editCharCurrentClothing','inventoryInput','experienceInput','aiCharHint'].forEach(f => setVal(f, ''));
  setSelectVal('editCharStatus', 'alive');
  document.getElementById('editIsProtagonist').checked = false;
  document.getElementById('relationsList').innerHTML = '';
  document.getElementById('inventoryList').innerHTML = '';
  document.getElementById('experiencesList').innerHTML = '';

  // Reset avatar tab
  _resetAvatarTab();

  // Reset tabs
  switchTabById('charModal', 'ctab-basics');
  document.getElementById('charModal').classList.remove('hidden');
}

function _resetAvatarTab() {
  const preview = document.getElementById('charAvatarPreview');
  if (preview) { preview.innerHTML = '<span class="avatar-placeholder">👤</span>'; preview.style.backgroundImage = ''; }
  const fi = document.getElementById('avatarFileInput');
  if (fi) fi.value = '';
  const fn = document.getElementById('avatarFileName');
  if (fn) fn.textContent = 'Keine Datei ausgewählt';
  const btn = document.getElementById('avatarUploadBtn');
  if (btn) btn.disabled = true;
  state.currentAvatarFile = null;
}

function openCharModalEdit(id) {
  const c = state.characters.find(ch => ch.id === id);
  if (!c) return;
  openCharModal(id);

  setVal('editCharName',     c.name         || '');
  setVal('editCharRole',     c.role         || '');
  setVal('editCharAge',      c.age          || '');
  setVal('editCharPhysical', c.physical_traits || '');
  setVal('editCharClothing', c.default_clothing || '');
  setVal('editCharDesc',     c.description  || '');
  setVal('editCharPowers',   c.superpowers  || '');
  setVal('editCharLikes',    c.likes        || '');
  setVal('editCharDislikes', c.dislikes     || '');
  setVal('editCharWeapon',   c.favorite_weapon || '');
  setSelectVal('editCharStatus', c.status   || 'alive');
  document.getElementById('editIsProtagonist').checked = !!c.is_protagonist;

  const rels = (typeof c.relationships === 'string') ? JSON.parse(c.relationships || '[]') : (c.relationships || []);
  renderRelations(rels);

  // Zustand-Tab
  setVal('editCharCurrentClothing', c.current_clothing || '');
  const inv = Array.isArray(c.inventory) ? c.inventory : [];
  renderInventory(inv);
  const exp = Array.isArray(c.experiences) ? c.experiences : [];
  renderExperiences(exp);

  // Avatar-Tab
  if (c.avatar_path) {
    const preview = document.getElementById('charAvatarPreview');
    if (preview) {
      preview.innerHTML = `<img src="${esc(c.avatar_path)}" class="char-avatar-preview-img" alt="Avatar" />`;
    }
  }
}

function closeCharModal() { document.getElementById('charModal').classList.add('hidden'); }

async function aiGenerateCharacter() {
  const name = getVal('editCharName').trim();
  if (!name) { showToast('Bitte zuerst einen Namen eingeben.'); return; }
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.'); return; }
  const hint = getVal('aiCharHint').trim();

  const btn = document.getElementById('aiGenCharBtn');
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Generiere…';

  // Converts any LLM value (object, array, JSON-string, plain string) to a readable string
  function flattenToString(v) {
    if (v === null || v === undefined) return '';
    // If it's already a plain string, try to parse it as JSON first
    if (typeof v === 'string') {
      try { v = JSON.parse(v); } catch { return v.trim(); }
    }
    // Array → join items; each item may itself be an object
    if (Array.isArray(v)) {
      return v.map(item => {
        if (typeof item === 'object' && item !== null) {
          return Object.entries(item).map(([k, val]) => `${k}: ${val}`).join(', ');
        }
        return String(item);
      }).filter(Boolean).join(', ');
    }
    // Plain object → "Key: Value" pairs
    if (typeof v === 'object') {
      return Object.entries(v).map(([k, val]) => `${k}: ${val}`).join(', ');
    }
    return String(v).trim();
  }

  try {
    const result = await api('/api/ai/generate-character', 'POST', {
      story_id: state.currentStoryId,
      name,
      description: hint,
    });
    if (result._error) { showToast(result._error); return; }

    // Fill form fields — only overwrite if the field is currently empty or AI returned something
    const fill = (id, val) => {
      const s = flattenToString(val);
      if (s) setVal(id, s);
    };
    fill('editCharRole',     result.role);
    fill('editCharAge',      result.age);
    fill('editCharPhysical', result.physical_traits);
    fill('editCharClothing', result.default_clothing);
    fill('editCharDesc',     result.description);
    fill('editCharPowers',   result.superpowers);
    fill('editCharLikes',    result.likes);
    fill('editCharDislikes', result.dislikes);
    fill('editCharWeapon',   result.favorite_weapon);

    showToast('✨ Profil generiert! Andere Tabs prüfen und ggf. anpassen.');
  } catch (e) {
    showToast('KI-Fehler: ' + (e.message || 'Unbekannt'), 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

// ── Inventory (Zustand-Tab) ───────────────────────────────────────────────────
function renderInventory(items) {
  const el = document.getElementById('inventoryList');
  if (!el) return;
  el.innerHTML = items.map((item, i) => `
    <div class="tag-item">
      <span>${esc(item)}</span>
      <button onclick="removeInventoryItem(${i})" title="Entfernen">✕</button>
    </div>`).join('');
}
function addInventoryItem() {
  const inp = document.getElementById('inventoryInput');
  const val = (inp?.value || '').trim();
  if (!val) return;
  const items = collectInventory();
  if (!items.includes(val)) { items.push(val); renderInventory(items); }
  if (inp) inp.value = '';
}
function removeInventoryItem(idx) {
  const items = collectInventory();
  items.splice(idx, 1);
  renderInventory(items);
}
function collectInventory() {
  return [...document.querySelectorAll('#inventoryList .tag-item span')].map(s => s.textContent.trim()).filter(Boolean);
}

// ── Experiences (Zustand-Tab) ─────────────────────────────────────────────────
function renderExperiences(items) {
  const el = document.getElementById('experiencesList');
  if (!el) return;
  el.innerHTML = items.map((item, i) => `
    <div class="exp-item">
      <span>${esc(item)}</span>
      <button onclick="removeExperienceItem(${i})" title="Entfernen">✕</button>
    </div>`).join('');
  el.scrollTop = el.scrollHeight;
}
function addExperienceItem() {
  const inp = document.getElementById('experienceInput');
  const val = (inp?.value || '').trim();
  if (!val) return;
  const items = collectExperiences();
  items.push(val);
  renderExperiences(items);
  if (inp) inp.value = '';
}
function removeExperienceItem(idx) {
  const items = collectExperiences();
  items.splice(idx, 1);
  renderExperiences(items);
}
function collectExperiences() {
  return [...document.querySelectorAll('#experiencesList .exp-item span')].map(s => s.textContent.trim()).filter(Boolean);
}

async function saveCharacter() {
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.'); return; }
  const name = getVal('editCharName').trim();
  if (!name) { showToast('Name ist erforderlich.'); return; }

  const rels = collectRelations();
  const body = {
    story_id:        state.currentStoryId,
    name,
    role:            getVal('editCharRole'),
    is_protagonist:  document.getElementById('editIsProtagonist').checked ? 1 : 0,
    status:          getVal('editCharStatus'),
    age:             getVal('editCharAge'),
    physical_traits: getVal('editCharPhysical'),
    default_clothing:getVal('editCharClothing'),
    description:     getVal('editCharDesc'),
    superpowers:     getVal('editCharPowers'),
    likes:           getVal('editCharLikes'),
    dislikes:        getVal('editCharDislikes'),
    favorite_weapon: getVal('editCharWeapon'),
    relationships:   rels,
    current_clothing:getVal('editCharCurrentClothing'),
    inventory:       collectInventory(),
    experiences:     collectExperiences(),
    avatar_path:     (state.characters.find(ch => ch.id === parseInt(document.getElementById('editCharId').value || '0'))?.avatar_path) || '',
  };

  const editId = document.getElementById('editCharId').value;
  if (editId) body.id = parseInt(editId);

  try {
    await api('/api/characters', 'POST', body);
    showToast(editId ? 'Charakter aktualisiert.' : 'Charakter erstellt.');
    closeCharModal();
    await loadCharacters();
  } catch (e) { showToast('Fehler: ' + e.message, 'error'); }
}

async function deleteCurrentChar() {
  const id = document.getElementById('editCharId').value;
  if (!id) return;
  if (!confirm('Charakter wirklich löschen?')) return;
  try {
    await api(`/api/characters/${state.currentStoryId}/${id}`, 'DELETE');
    showToast('Charakter gelöscht.');
    closeCharModal();
    await loadCharacters();
  } catch (e) { showToast('Fehler: ' + e.message, 'error'); }
}

// ── Relationships ─────────────────────────────────────────────────────────────
const REL_TYPES = ['Neutral','Freundschaft','Liebe','Familie','Rivalität','Misstrauen','Feindschaft','Mentor','Schüler','Verbündete','Komplex','Unbekannt'];

function renderRelations(rels) {
  const el = document.getElementById('relationsList');
  el.innerHTML = '';
  (rels || []).forEach(r => addRelationRow(r));
}

function addRelation() {
  addRelationRow({ target: '', type: 'Neutral', description: '' });
}

function addRelationRow(rel) {
  const el = document.getElementById('relationsList');
  const row = document.createElement('div');
  row.className = 'relation-row';
  const opts = REL_TYPES.map(t => `<option value="${t}" ${t === (rel.type || 'Neutral') ? 'selected' : ''}>${t}</option>`).join('');
  row.innerHTML = `
    <input class="input-field rel-target" placeholder="Charakter" value="${esc(rel.target || '')}" />
    <select class="select-field rel-type">${opts}</select>
    <input class="input-field rel-desc" placeholder="Kurzbeschreibung..." value="${esc(rel.description || '')}" />
    <button class="relation-del" onclick="this.closest('.relation-row').remove()" title="Entfernen">✕</button>`;
  el.appendChild(row);
}

function collectRelations() {
  const rows = document.querySelectorAll('.relation-row');
  const result = [];
  rows.forEach(row => {
    const target = row.querySelector('.rel-target')?.value?.trim();
    const type   = row.querySelector('.rel-type')?.value || 'Neutral';
    const desc   = row.querySelector('.rel-desc')?.value?.trim() || '';
    if (target) result.push({ target, type, description: desc });
  });
  return result;
}

// ══════════════════════════════════════════════════════════════════════════════
// STYLE EXAMPLES
// ══════════════════════════════════════════════════════════════════════════════
function renderStyleExamples(examples) {
  const el = document.getElementById('styleExamplesList');
  el.innerHTML = '';
  (examples || []).forEach(ex => addStyleExampleRow(ex));
}

function addStyleExample() { addStyleExampleRow({ good: '', bad: '' }); }

function addStyleExampleRow(ex) {
  const el = document.getElementById('styleExamplesList');
  const pair = document.createElement('div');
  pair.className = 'style-example-pair';
  pair.innerHTML = `
    <div class="style-example-pair-header">
      <div style="display:flex;gap:16px">
        <span class="style-example-label-good">✅ Gut</span>
        <span class="style-example-label-bad">❌ Schlecht</span>
      </div>
      <button class="btn btn-danger-ghost btn-sm" onclick="this.closest('.style-example-pair').remove()">✕ Entfernen</button>
    </div>
    <textarea class="textarea-field ex-good" rows="3" placeholder="Gutes Stilbeispiel...">${esc(ex.good || '')}</textarea>
    <textarea class="textarea-field ex-bad"  rows="3" placeholder="Schlechtes Stilbeispiel...">${esc(ex.bad  || '')}</textarea>`;
  el.appendChild(pair);
}

// ══════════════════════════════════════════════════════════════════════════════
// WORD ALTS
// ══════════════════════════════════════════════════════════════════════════════
function renderWordAlts(alts) {
  const el = document.getElementById('wordAltsList');
  el.innerHTML = '';
  (alts || []).forEach(a => addWordAltRow(a));
}

function addWordAlt() { addWordAltRow({ word: '', alts: '' }); }

function addWordAltRow(a) {
  const el = document.getElementById('wordAltsList');
  const row = document.createElement('div');
  row.className = 'word-alt-row';
  const alts = Array.isArray(a.alts) ? a.alts.join(', ') : (a.alts || '');
  row.innerHTML = `
    <input class="input-field word-alt-word" placeholder="Verbotenes Wort" value="${esc(a.word || '')}" />
    <span class="word-alt-arrow">→</span>
    <input class="input-field word-alt-alts" placeholder="Alt1, Alt2, Alt3..." value="${esc(alts)}" />
    <button class="word-alt-del" onclick="this.closest('.word-alt-row').remove()" title="Entfernen">✕</button>`;
  el.appendChild(row);
}

// ══════════════════════════════════════════════════════════════════════════════
// LLM SETTINGS
// ══════════════════════════════════════════════════════════════════════════════
function openLLMModal() {
  // Populate model list from cached state
  if (state.models.length) {
    _populateModelSelect(state.models, null);
  }
  document.getElementById('llmModal').classList.remove('hidden');
  loadLLMConfig();
}

function _populateModelSelect(models, preferredModel) {
  const sel = document.getElementById('ollamaModel');
  if (!sel) return;
  if (!models || models.length === 0) {
    sel.innerHTML = '<option value="">⚠️ Kein Modell installiert</option>';
    return;
  }
  const prev = sel.value;
  sel.innerHTML = models.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
  const target = prev && models.includes(prev) ? prev
    : preferredModel && models.includes(preferredModel) ? preferredModel
    : models[0];
  sel.value = target;
}

function closeLLMModal() { document.getElementById('llmModal').classList.add('hidden'); }

async function loadLLMConfig() {
  try {
    const cfg = await api('/api/llm/config');
    if (!cfg) return;
    setSlider('temperature', 'tempValue',  cfg.temperature    || 0.7);
    setSlider('top_p',       'topPValue',  cfg.top_p          || 0.9);
    setSlider('repeat_penalty','repPenValue', cfg.repeat_penalty || 1.1);
    setSliderInt('num_predict',   'numPredictValue',  cfg.num_predict   || 1600);
    setSliderInt('memory_depth',  'memoryDepthValue', cfg.memory_depth  || 3);
    setSelectVal('numCtx',         cfg.num_ctx         || '4096');
    setSelectVal('outputLanguage', cfg.output_language || 'Deutsch');
    const modelSel = document.getElementById('ollamaModel');
    if (modelSel && cfg.ollama_model) {
      // Only select if the model actually exists in Ollama's list
      const exists = [...modelSel.options].find(o => o.value === cfg.ollama_model);
      if (exists) modelSel.value = cfg.ollama_model;
      // else: leave checkOllama()'s selection intact
    }
  } catch {}
}

async function saveLLMSettings() {
  const config = {
    temperature:     parseFloat(document.getElementById('temperature')?.value || 0.7),
    top_p:           parseFloat(document.getElementById('top_p')?.value || 0.9),
    repeat_penalty:  parseFloat(document.getElementById('repeat_penalty')?.value || 1.1),
    ollama_model:    getVal('ollamaModel'),
    num_predict:     parseInt(document.getElementById('num_predict')?.value || 1600),
    memory_depth:    parseInt(document.getElementById('memory_depth')?.value || 3),
    num_ctx:         getVal('numCtx') || '4096',
    output_language: getVal('outputLanguage') || 'Deutsch',
  };
  try {
    await api('/api/llm/config', 'POST', { config });
    showToast('LLM-Einstellungen gespeichert.');
    closeLLMModal();
  } catch (e) { showToast('Fehler: ' + e.message, 'error'); }
}

// ══════════════════════════════════════════════════════════════════════════════
// OLLAMA STATUS
// ══════════════════════════════════════════════════════════════════════════════
async function checkOllama() {
  const dot  = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  const modelSel = document.getElementById('ollamaModel');
  try {
    const data = await api('/api/ollama/status');
    if (!data) return;
    if (data.connected) {
      state.ollamaOk = true;
      if (dot)  { dot.className = 'status-dot ok'; }
      const models = data.models || [];
      state.models = models;
      if (models.length > 0) {
        if (text) text.textContent = data.model || models[0];
        _populateModelSelect(models, data.model);
        const mi = document.getElementById('startModelInfo');
        if (mi) mi.textContent = `Modell: ${data.model || models[0]} · Ollama bereit`;
      } else {
        if (text) text.textContent = 'Ollama — kein Modell';
        _populateModelSelect([], null);
        const mi = document.getElementById('startModelInfo');
        if (mi) mi.innerHTML = 'Kein Modell installiert.<br/><code style="font-size:12px">ollama pull mistral</code>';
      }
    } else {
      throw new Error('disconnected');
    }
  } catch {
    state.ollamaOk = false;
    if (dot)  { dot.className = 'status-dot error'; }
    if (text) text.textContent = 'Ollama offline';
    const mi = document.getElementById('startModelInfo');
    if (mi) mi.textContent = 'Ollama nicht erreichbar. Bitte starten: ollama serve';
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// GAME
// ══════════════════════════════════════════════════════════════════════════════
async function loadGameState() {
  if (!state.currentStoryId) return;
  try {
    const gs = await api(`/api/game/state/${state.currentStoryId}`);
    if (!gs) return;

    const sceneEl = document.getElementById('sceneBadge');
    if (sceneEl) sceneEl.textContent = `Szene ${gs.scene_number ?? gs.scene_counter ?? 0}`;

    if (gs.events && gs.events.length > 0) {
      // Game already started — show story window
      state.gameStarted = true;
      document.getElementById('startScreen').classList.add('hidden');
      document.getElementById('storyWindow').classList.remove('hidden');
      document.getElementById('optionsPanel').classList.remove('hidden');
      renderStoryEntries(gs.events);
      renderWorldItems(gs.world_items || []);
      scrollStoryToBottom();
      // Last options
      const lastEvent = gs.events[gs.events.length - 1];
      if (lastEvent && lastEvent.options_json) {
        try {
          const opts = typeof lastEvent.options_json === 'string' ? JSON.parse(lastEvent.options_json) : lastEvent.options_json;
          setOptions(opts);
        } catch {}
      }
    } else {
      // No events — show start screen
      state.gameStarted = false;
      document.getElementById('startScreen').classList.remove('hidden');
      document.getElementById('storyWindow').classList.add('hidden');
      document.getElementById('optionsPanel').classList.add('hidden');
    }
  } catch (e) {
    console.error('loadGameState Fehler:', e);
    showToast('Spielstand konnte nicht geladen werden: ' + (e.message || e), 'error');
  }
}

async function startGame() {
  if (!state.currentStoryId) { showToast('Bitte zuerst eine Geschichte auswählen.'); return; }
  if (!state.ollamaOk) { showToast('Ollama nicht erreichbar.'); return; }
  if (state.characters.length === 0) {
    showToast('Bitte zuerst mindestens einen Charakter erstellen.');
    openCharModal();
    return;
  }
  const hint = document.getElementById('startHint');
  if (hint) hint.textContent = 'Das Abenteuer beginnt...';
  try {
    setProcessing(true);
    const result = await api('/api/game/start', 'POST', { story_id: state.currentStoryId });
    if (!result) return;

    state.gameStarted = true;
    document.getElementById('startScreen').classList.add('hidden');
    document.getElementById('storyWindow').classList.remove('hidden');
    document.getElementById('optionsPanel').classList.remove('hidden');
    document.getElementById('storyContent').innerHTML = '';

    renderScene(result, null);
    scrollStoryToBottom();
  } catch (e) {
    showToast('Fehler beim Start: ' + e.message, 'error');
    if (hint) hint.textContent = '';
  } finally { setProcessing(false); }
}

async function chooseOption(text) {
  await processAction(text, false);
}

async function submitFreeAction() {
  const val = getVal('freeInput').trim();
  if (!val) return;
  setVal('freeInput', '');
  await processAction(val, true);
}

async function processAction(action, isCustom) {
  if (!state.currentStoryId || state.isProcessing) return;
  // Detect which character acted (first char-name match in the action text)
  if (action && state.characters.length) {
    const lower = action.toLowerCase();
    const match = state.characters.find(c => c.name && lower.includes(c.name.toLowerCase()));
    if (match) state.lastActiveCharName = match.name;
  }
  try {
    setProcessing(true);
    document.getElementById('loadingOverlay').classList.remove('hidden');
    const result = await api('/api/game/action', 'POST', {
      story_id: state.currentStoryId,
      action,
      is_custom: isCustom,
    });
    if (!result) return;
    renderScene(result, action);
    scrollStoryToBottom();
  } catch (e) {
    showToast('Fehler: ' + e.message, 'error');
  } finally {
    setProcessing(false);
    document.getElementById('loadingOverlay').classList.add('hidden');
  }
}

// ── World Items ───────────────────────────────────────────────────────────────
function renderWorldItems(items) {
  state.worldItems = items || [];
  const emptyEl       = document.getElementById('sidebarRightEmpty');
  const itemItems     = state.worldItems.filter(i => (i.type || 'item') === 'item');
  const codeItems     = state.worldItems.filter(i => i.type === 'code');
  const obstacleItems = state.worldItems.filter(i => i.type === 'obstacle');
  _renderWorldPanel('rightItemsPanel',     'rightItemsList',     itemItems);
  _renderWorldPanel('rightCodesPanel',     'rightCodesList',     codeItems);
  _renderWorldPanel('rightObstaclesPanel', 'rightObstaclesList', obstacleItems);
  if (emptyEl) emptyEl.style.display = state.worldItems.length ? 'none' : '';
}

function _buildItemHtml(item) {
  const status = item.status || 'available';
  const itype  = item.type   || 'item';
  let icon, cls, meta;
  if (itype === 'code') {
    if (status === 'used') {
      icon = '✅'; cls = 'wi-used';
      meta = `<span class="wi-meta">Code verwendet</span>`;
    } else if (status === 'found') {
      icon = '🔑'; cls = 'wi-code-found';
      const copyBtn = `<span class="wi-code-value" title="In Aktion einfügen" onclick="insertCode('${esc(item.code_value || '')}')">${esc(item.code_value || '???')}</span>`;
      meta = `${copyBtn}${item.required_for ? `<span class="wi-meta">für: ${esc(item.required_for)}</span>` : ''}`;
    } else {
      icon = '🔒'; cls = 'wi-code-unknown';
      meta = item.location
        ? `<span class="wi-meta wi-loc">suche: ${esc(item.location)}</span>`
        : `<span class="wi-meta">noch nicht gefunden</span>`;
    }
  } else if (itype === 'obstacle') {
    const DANGER_ICON = { low: '⚠️', medium: '🔶', high: '🔴', lethal: '☠️' };
    const danger = item.danger_level || 'medium';
    icon = DANGER_ICON[danger] || '⚠️';
    if (status === 'overcome') {
      cls = 'wi-obstacle-overcome';
      meta = `<span class="wi-meta">✅ überwunden</span>`;
    } else if (status === 'avoided') {
      cls = 'wi-obstacle-avoided';
      meta = `<span class="wi-meta">↩️ umgangen</span>`;
    } else if (status === 'triggered') {
      cls = 'wi-obstacle-triggered';
      meta = `<span class="wi-meta wi-loc">💥 ausgelöst${item.location ? ' @ ' + esc(item.location) : ''}</span>`
           + (item.required_for ? `<span class="wi-meta">↳ ${esc(item.required_for)}</span>` : '');
    } else {
      cls = 'wi-obstacle-active';
      meta = (item.location ? `<span class="wi-meta wi-loc">${esc(item.location)}</span>` : '')
           + (item.required_for ? `<span class="wi-meta">↳ ${esc(item.required_for)}</span>` : '');
    }
  } else {
    if (status === 'used') {
      icon = '✅'; cls = 'wi-used';
      meta = '<span class="wi-meta">verbraucht</span>';
    } else if (status === 'held') {
      icon = '✋'; cls = 'wi-held';
      meta = `<span class="wi-meta">bei ${esc(item.held_by || '?')}</span>`;
    } else {
      icon = '📦'; cls = 'wi-available';
      meta = item.location ? `<span class="wi-meta wi-loc">${esc(item.location)}</span>` : '';
    }
  }
  const tip = [item.description, item.required_for ? 'Benötigt für: ' + item.required_for : ''].filter(Boolean).join(' | ');
  return `<div class="world-item ${cls}" title="${esc(tip)}">
    <span class="wi-icon">${icon}</span>
    <div class="wi-body"><span class="wi-name">${esc(item.name)}</span>${meta}</div>
  </div>`;
}

function _renderWorldPanel(panelId, listId, items) {
  const panel = document.getElementById(panelId);
  const list  = document.getElementById(listId);
  if (!panel || !list) return;
  if (!items.length) { panel.style.display = 'none'; list.innerHTML = ''; return; }
  panel.style.display = '';
  const order = { triggered: 0, active: 1, available: 2, held: 3, unknown: 2, found: 2, used: 4, overcome: 5, avoided: 5 };
  const sorted = [...items].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
  list.innerHTML = sorted.map(_buildItemHtml).join('');
}

// Inserts a found code into the player action input field for easy use
function insertCode(codeValue) {
  const input = document.getElementById('freeInput');
  if (!input) return;
  const current = input.value.trim();
  input.value = current ? `${current} ${codeValue}` : codeValue;
  input.focus();
  showToast(`Code "${codeValue}" in Eingabe eingefügt`, 'info');
}

// ── Avatar ────────────────────────────────────────────────────────────────────
function onAvatarFileSelected() {
  const fi = document.getElementById('avatarFileInput');
  const fn = document.getElementById('avatarFileName');
  const btn = document.getElementById('avatarUploadBtn');
  const preview = document.getElementById('charAvatarPreview');
  if (!fi || !fi.files || !fi.files[0]) return;
  const file = fi.files[0];
  state.currentAvatarFile = file;
  if (fn) fn.textContent = file.name;
  if (btn) btn.disabled = false;
  // Local preview
  if (preview) {
    const url = URL.createObjectURL(file);
    preview.innerHTML = `<img src="${url}" class="char-avatar-preview-img" alt="Vorschau" />`;
  }
}

async function uploadAvatar() {
  const charId = document.getElementById('editCharId').value;
  if (!charId) { showToast('Bitte erst Charakter speichern.', 'error'); return; }
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.', 'error'); return; }
  if (!state.currentAvatarFile) { showToast('Keine Datei ausgewählt.', 'error'); return; }

  const btn = document.getElementById('avatarUploadBtn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Hochladen…'; }

  try {
    const formData = new FormData();
    formData.append('file', state.currentAvatarFile);
    const resp = await fetch(`/api/characters/${state.currentStoryId}/${charId}/avatar`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${state.token}` },
      body: formData,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || 'Upload fehlgeschlagen');
    }
    const result = await resp.json();
    // Update state + preview
    const c = state.characters.find(ch => ch.id === parseInt(charId));
    if (c) c.avatar_path = result.avatar_path;
    showToast('Avatar hochgeladen!', 'success');
    state.currentAvatarFile = null;
    const fi = document.getElementById('avatarFileInput');
    if (fi) fi.value = '';
    const fn = document.getElementById('avatarFileName');
    if (fn) fn.textContent = 'Keine Datei ausgewählt';
    await loadCharacters();
  } catch (e) {
    showToast('Fehler: ' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⬆️ Hochladen'; }
  }
}

async function exportCharacterJSON() {
  const charId = document.getElementById('editCharId').value;
  if (!charId) { showToast('Bitte erst Charakter speichern.', 'error'); return; }
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.', 'error'); return; }
  try {
    const resp = await fetch(`/api/characters/${state.currentStoryId}/${charId}/export-json`, {
      headers: { 'Authorization': `Bearer ${state.token}` },
    });
    if (!resp.ok) throw new Error('Export fehlgeschlagen');
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const c = state.characters.find(ch => ch.id === parseInt(charId));
    a.download = `character_${(c?.name || charId).replace(/[^a-z0-9]/gi, '_')}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast('JSON exportiert!', 'success');
  } catch (e) {
    showToast('Fehler: ' + e.message, 'error');
  }
}

function renderScene(result, playerAction) {
  const sceneNum = result.scene_number ?? result.scene_counter ?? 0;
  const sceneEl = document.getElementById('sceneBadge');
  if (sceneEl) sceneEl.textContent = `Szene ${sceneNum}`;

  const content = document.getElementById('storyContent');
  const entry = document.createElement('div');
  entry.className = 'story-entry';

  let html = `<div class="story-scene-label">Szene ${sceneNum}</div>`;
  if (playerAction) {
    html += `<div class="story-player-action">▶ ${esc(playerAction)}</div>`;
  }
  html += `<div class="story-text">${formatStoryText(result.story_text || '')}</div>`;

  const wc = result.world_changes || '';
  const es = result.events_summary || '';
  if (es || (wc && wc.toLowerCase() !== 'keine')) {
    html += '<div class="story-meta">';
    if (es)                               html += `<div>📌 ${esc(es)}</div>`;
    if (wc && wc.toLowerCase() !== 'keine') html += `<div>🌍 ${esc(wc)}</div>`;
    html += '</div>';
  }

  entry.innerHTML = html;
  content.appendChild(entry);
  setOptions(result.options || []);
  if (result.world_items !== undefined) {
    renderWorldItems(result.world_items);
  }
}

function renderStoryEntries(events) {
  const content = document.getElementById('storyContent');
  content.innerHTML = '';
  events.forEach(ev => {
    const entry = document.createElement('div');
    entry.className = 'story-entry';
    let html = `<div class="story-scene-label">Szene ${ev.scene_number}</div>`;
    if (ev.player_action) {
      html += `<div class="story-player-action">▶ ${esc(ev.player_action)}</div>`;
    }
    html += `<div class="story-text">${formatStoryText(ev.story_text || '')}</div>`;
    const ewc = ev.world_changes || '';
    const ees = ev.events_summary || '';
    if (ees || (ewc && ewc.toLowerCase() !== 'keine')) {
      html += '<div class="story-meta">';
      if (ees)                                html += `<div>📌 ${esc(ees)}</div>`;
      if (ewc && ewc.toLowerCase() !== 'keine') html += `<div>🌍 ${esc(ewc)}</div>`;
      html += '</div>';
    }
    entry.innerHTML = html;
    content.appendChild(entry);
  });
}

function setOptions(options) {
  const btnContainer = document.getElementById('optionButtons');
  btnContainer.innerHTML = '';
  if (!Array.isArray(options)) return;
  const keys = ['1','2','3','4','5','6'];
  options.slice(0, 6).forEach((opt, i) => {
    const btn = document.createElement('button');
    btn.className = 'option-btn';
    btn.dataset.optIndex = i;
    btn.innerHTML = `<div class="option-btn-content"><span class="option-number">Option ${i + 1}</span>${esc(opt)}</div><span class="option-key">${keys[i]}</span>`;
    btn.onclick = () => chooseOption(opt);
    btnContainer.appendChild(btn);
  });
}

async function confirmReset() {
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.'); return; }
  if (!confirm('Das Spiel wirklich neu starten? Der gesamte Spielverlauf dieser Geschichte wird gelöscht.')) return;
  try {
    await api('/api/game/reset', 'POST', { story_id: state.currentStoryId });
    state.gameStarted = false;
    document.getElementById('storyContent').innerHTML = '';
    document.getElementById('optionButtons').innerHTML = '';
    document.getElementById('startScreen').classList.remove('hidden');
    document.getElementById('storyWindow').classList.add('hidden');
    document.getElementById('optionsPanel').classList.add('hidden');
    document.getElementById('sceneBadge').textContent = 'Szene 0';
    const hint = document.getElementById('startHint');
    if (hint) hint.textContent = '';
    showToast('Spiel zurückgesetzt.');
  } catch (e) { showToast('Fehler: ' + e.message, 'error'); }
}

function scrollStoryToBottom() {
  const sw = document.getElementById('storyWindow');
  if (sw) setTimeout(() => { sw.scrollTop = sw.scrollHeight; }, 60);
}

// ══════════════════════════════════════════════════════════════════════════════
// HISTORY
// ══════════════════════════════════════════════════════════════════════════════
async function openHistoryModal() {
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.'); return; }
  const el = document.getElementById('historyContent');
  el.innerHTML = '<p style="color:var(--text-dim)">Lade Verlauf...</p>';
  document.getElementById('historyModal').classList.remove('hidden');
  try {
    const events = await api(`/api/history/${state.currentStoryId}`);
    if (!events || !events.length) {
      el.innerHTML = '<p style="color:var(--text-dim)">Noch keine Ereignisse.</p>';
      return;
    }
    el.innerHTML = events.map(ev => `
      <div class="history-entry">
        <div class="history-entry-header">
          <span class="history-scene">Szene ${ev.scene_number}</span>
          ${ev.player_action ? `<span class="history-action">"${esc(ev.player_action)}"</span>` : ''}
        </div>
        <div class="history-text">${formatStoryText(ev.story_text || '')}</div>
      </div>`).join('');
  } catch (e) { el.innerHTML = `<p style="color:var(--danger)">Fehler: ${esc(e.message)}</p>`; }
}

function closeHistoryModal() { document.getElementById('historyModal').classList.add('hidden'); }

// ══════════════════════════════════════════════════════════════════════════════
// TABS
// ══════════════════════════════════════════════════════════════════════════════
function switchTab(tabId, btnEl) {
  const modal = btnEl.closest('.modal-content');
  if (!modal) return;
  modal.querySelectorAll('.tab-content').forEach(tc => tc.classList.add('hidden'));
  modal.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const target = document.getElementById(tabId);
  if (target) target.classList.remove('hidden');
  btnEl.classList.add('active');
}

function switchTabById(modalId, tabId) {
  const modal = document.getElementById(modalId);
  if (!modal) return;
  const contents = modal.querySelectorAll('.tab-content');
  const btns     = modal.querySelectorAll('.tab-btn');
  contents.forEach(tc => tc.classList.add('hidden'));
  btns.forEach(b => b.classList.remove('active'));
  const target = document.getElementById(tabId);
  if (target) target.classList.remove('hidden');
  // Find button that activates this tab
  btns.forEach(b => {
    if (b.getAttribute('onclick')?.includes(`'${tabId}'`)) b.classList.add('active');
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// API HELPER
// ══════════════════════════════════════════════════════════════════════════════
async function api(url, method = 'GET', body = null) {
  const headers = { 'Content-Type': 'application/json' };
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  const opts = { method, headers };
  if (body !== null) opts.body = JSON.stringify(body);
  const resp = await fetch(url, opts);
  if (resp.status === 401) { logout(); return null; }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || resp.statusText);
  }
  return resp.json();
}

// ══════════════════════════════════════════════════════════════════════════════
// HELPERS
// ══════════════════════════════════════════════════════════════════════════════
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function getVal(id) {
  const el = document.getElementById(id);
  return el ? (el.value ?? '') : '';
}
function setVal(id, v) {
  const el = document.getElementById(id);
  if (el) el.value = v;
}
function setSelectVal(id, v) {
  const el = document.getElementById(id);
  if (el) el.value = v;
}
function setSlider(sliderId, valueId, value) {
  const el = document.getElementById(sliderId);
  if (el) el.value = value;
  const vEl = document.getElementById(valueId);
  if (vEl) vEl.textContent = parseFloat(value).toFixed(2);
}
function setSliderInt(sliderId, valueId, value) {
  const el = document.getElementById(sliderId);
  if (el) el.value = value;
  const vEl = document.getElementById(valueId);
  if (vEl) vEl.textContent = parseInt(value);
}
function updateSlider(sliderId, valueId) {
  const el = document.getElementById(sliderId);
  const vEl = document.getElementById(valueId);
  if (el && vEl) vEl.textContent = parseFloat(el.value).toFixed(2);
}
function updateSliderInt(sliderId, valueId) {
  const el = document.getElementById(sliderId);
  const vEl = document.getElementById(valueId);
  if (el && vEl) vEl.textContent = parseInt(el.value);
}
function setProcessing(val) {
  state.isProcessing = val;
  const freeBtn = document.getElementById('freeActionBtn');
  const startBtn = document.getElementById('startBtn');
  if (freeBtn)  freeBtn.disabled = val;
  if (startBtn) startBtn.disabled = val;
  document.querySelectorAll('.option-btn').forEach(b => b.disabled = val);
  setGmStatus(val ? 'thinking' : 'done');
}

function setGmStatus(status) {
  // status: 'idle' | 'thinking' | 'done'
  const bar   = document.getElementById('gmStatusBar');
  const text  = document.getElementById('gmStatusText');
  const pulse = document.getElementById('gmPulse');
  if (bar)   bar.dataset.status = status;
  if (pulse) {
    pulse.classList.remove('active', 'done');
    if (status === 'thinking') pulse.classList.add('active');
    if (status === 'done')     pulse.classList.add('done');
  }
  if (text) {
    if (status === 'thinking') text.textContent = 'Game Master schreibt…';
    else if (status === 'done') text.textContent = 'Szene bereit';
    else text.textContent = 'Bereit';
  }
  if (status === 'done') {
    clearTimeout(setGmStatus._timer);
    setGmStatus._timer = setTimeout(() => setGmStatus('idle'), 2200);
  }
}
function showToast(msg, type = 'info') {
  const t = document.getElementById('toast');
  if (!t) return;
  // Build content: message + copy button for errors
  t.innerHTML = '';
  const span = document.createElement('span');
  span.textContent = msg;
  t.appendChild(span);
  if (type === 'error') {
    const btn = document.createElement('button');
    btn.textContent = '📋';
    btn.title = 'Fehlermeldung kopieren';
    btn.style.cssText = 'margin-left:8px;background:none;border:none;cursor:pointer;font-size:14px;color:inherit;opacity:.8;';
    btn.onclick = (e) => { e.stopPropagation(); navigator.clipboard?.writeText(msg); btn.textContent = '✓'; };
    t.appendChild(btn);
  }
  t.className = 'toast-' + type;
  t.classList.remove('hidden');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add('hidden'), type === 'error' ? 10000 : 3500);
  t.onclick = () => { clearTimeout(t._timer); t.classList.add('hidden'); };
}
function formatStoryText(text) {
  if (!text) return '';
  return esc(text).replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br/>');
}

// ══════════════════════════════════════════════════════════════════════════════
// NPC ÜBERNEHMEN
// ══════════════════════════════════════════════════════════════════════════════
function openNPCModal() {
  if (!state.currentStoryId) { showToast('Bitte zuerst eine Geschichte auswählen.'); return; }
  setVal('npcName', '');
  setVal('npcDescription', '');
  document.getElementById('npcGenerating').classList.add('hidden');
  document.getElementById('npcGenBtn').disabled = false;
  document.getElementById('npcModal').classList.remove('hidden');
}

function closeNPCModal() { document.getElementById('npcModal').classList.add('hidden'); }

function openNPCManual() {
  const name = getVal('npcName').trim();
  closeNPCModal();
  openCharModal();
  if (name) setVal('editCharName', name);
}

async function generateNPCProfile() {
  const name = getVal('npcName').trim();
  if (!name) { showToast('Bitte einen Namen eingeben.'); return; }

  const genBtn = document.getElementById('npcGenBtn');
  const genSpinner = document.getElementById('npcGenerating');
  genBtn.disabled = true;
  genSpinner.classList.remove('hidden');

  try {
    const result = await api('/api/ai/generate-character', 'POST', {
      story_id: state.currentStoryId,
      name,
      description: getVal('npcDescription').trim(),
    });
    if (!result) return;

    // Handle LLM error gracefully
    if (result._error) {
      showToast('⚠️ ' + result._error + ' — NPC manuell ausfüllen.');
      closeNPCModal();
      openNPCManual();
      return;
    }

    // Open char modal pre-filled with AI result
    closeNPCModal();
    openCharModal();
    setVal('editCharName',     result.name            || name);
    setVal('editCharRole',     result.role            || '');
    setVal('editCharAge',      result.age             || '');
    setVal('editCharPhysical', result.physical_traits || '');
    setVal('editCharClothing', result.default_clothing|| '');
    setVal('editCharDesc',     result.description     || '');
    setVal('editCharPowers',   result.superpowers     || '');
    setVal('editCharLikes',    result.likes           || '');
    setVal('editCharDislikes', result.dislikes        || '');
    setVal('editCharWeapon',   result.favorite_weapon || '');
    showToast('Profil generiert — bitte prüfen und speichern.');
  } catch (e) {
    showToast('KI-Fehler: ' + e.message, 'error');
  } finally {
    genBtn.disabled = false;
    genSpinner.classList.add('hidden');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// KI-WELTGENERATOR
// ══════════════════════════════════════════════════════════════════════════════
async function generateWorld() {
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.'); return; }
  const prompt = getVal('worldGenPrompt').trim();
  if (!prompt) { showToast('Bitte eine Beschreibung eingeben.'); return; }

  const btn = document.getElementById('worldGenBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Generiert...';

  try {
    const result = await api('/api/ai/generate-world', 'POST', {
      story_id: state.currentStoryId,
      description: prompt,
    });
    if (!result) return;

    // Check for soft error (LLM failed but no 500)
    if (result._error) {
      showToast('⚠️ ' + result._error + ' — Felder konnten nicht gefüllt werden.');
      btn.disabled = false;
      btn.textContent = '✨ Generieren';
      return;
    }

    // Fill world fields
    if (result.world_name)        setVal('worldName',       result.world_name);
    if (result.world_era)         setVal('worldEra',        result.world_era);
    if (result.world_atmosphere)  setVal('worldAtmosphere', result.world_atmosphere);
    if (result.world_description) setVal('worldDesc',       result.world_description);
    if (result.scenario)          setVal('scenarioDesc',    result.scenario);
    if (result.story_frame)       setVal('storyFrame',      result.story_frame);

    // Genre: try to select in dropdown, else use custom
    if (result.story_genre) {
      const sel = document.getElementById('storyGenre');
      const exists = [...sel.options].find(o => o.value === result.story_genre);
      if (exists) {
        sel.value = result.story_genre;
      } else {
        // Add as custom option
        const opt = new Option(result.story_genre, result.story_genre, true, true);
        sel.add(opt);
      }
    }

    showToast('Welt generiert — Felder wurden ausgefüllt. Bitte prüfen und speichern.');
    setVal('worldGenPrompt', '');
  } catch (e) {
    showToast('KI-Fehler: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '✨ Generieren';
  }
}

// ── Export / Import ───────────────────────────────────────────────────────────
async function exportConfig() {
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.'); return; }
  try {
    const data = await api(`/api/stories/${state.currentStoryId}/export`);
    if (!data) return;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    const name = (data.story_title || 'adventure').replace(/[^a-z0-9äöü_\- ]/gi, '_');
    a.href     = url;
    a.download = `${name}_export.json`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('Export heruntergeladen.');
  } catch (e) {
    showToast('Export fehlgeschlagen: ' + e.message);
  }
}

async function importConfig(input) {
  if (!state.currentStoryId) { showToast('Keine Geschichte ausgewählt.'); return; }
  const file = input.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    if (data.export_version !== 1) {
      showToast('Ungültiges Format (export_version fehlt oder falsch).');
      input.value = '';
      return;
    }
    if (!confirm(`Konfiguration aus "${file.name}" importieren?\nAlle bestehenden Charaktere werden ersetzt.`)) {
      input.value = '';
      return;
    }
    const result = await api('/api/stories/import', 'POST', {
      story_id: state.currentStoryId,
      data,
    });
    if (!result) return;
    if (result.errors && result.errors.length) {
      showToast(`Import abgeschlossen (${result.errors.length} Fehler). Seite wird neu geladen.`);
    } else {
      showToast('Import erfolgreich! Seite wird neu geladen.');
    }
    // Reload story data
    await selectStory(state.currentStoryId);
    input.value = '';
  } catch (e) {
    showToast('Import fehlgeschlagen: ' + e.message);
    input.value = '';
  }
}

// ── Start ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);
