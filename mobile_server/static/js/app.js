/**
 * NeonRoute Mobile App Orchestrator
 * State Store, Event Wiring, Navigation, PWA Lifecycle & Media Integration.
 */

import * as api from './api.js';
import * as stream from './stream.js';
import * as ui from './ui.js';

// ─── Application State Store ───
const state = {
  activeSessionId: null,
  activeMode: 'assistant',
  activeForgeModel: 'auto',
  isGenerating: false,
  abortController: null,
  sessions: [],
  characters: [],
  hardwareStats: null,
  sessionEventSource: null,
  activeArtMonitor: null,
};

// ─── DOM Elements Cache ───
const elements = {
  app: document.getElementById('app'),
  chatViewport: document.getElementById('chatViewport'),
  inputTextarea: document.getElementById('inputTextarea'),
  btnSend: document.getElementById('btnSend'),
  btnScrollBottom: document.getElementById('btnScrollBottom'),

  // Header Zone 1 (Left)
  btnHamburger: document.getElementById('btnHamburger'),
  badgeCounter: document.getElementById('badgeCounter'),
  btnHeaderAvatar: document.getElementById('btnHeaderAvatar'),
  headerAvatarImg: document.getElementById('headerAvatarImg'),
  headerCompanionName: document.getElementById('headerCompanionName'),
  headerCompanionStatus: document.getElementById('headerCompanionStatus'),

  // Header Zone 2 (Right)
  btnForgeModel: document.getElementById('btnForgeModel'),
  forgeModelName: document.getElementById('forgeModelName'),
  btnWorldStatus: document.getElementById('btnWorldStatus'),
  btnHeaderGallery: document.getElementById('btnHeaderGallery'),
  hardwarePill: document.getElementById('hardwarePill'),
  dotLlm: document.getElementById('dotLlm'),
  dotForge: document.getElementById('dotForge'),
  dotTts: document.getElementById('dotTts'),
  vramText: document.getElementById('vramText'),

  // Sliding Drawer
  drawerBackdrop: document.getElementById('drawerBackdrop'),
  drawerPanel: document.getElementById('drawerPanel'),
  btnDrawerClose: document.getElementById('btnDrawerClose'),
  tabBtnSessions: document.getElementById('tabBtnSessions'),
  tabBtnCharacters: document.getElementById('tabBtnCharacters'),
  tabContentSessions: document.getElementById('tabContentSessions'),
  tabContentCharacters: document.getElementById('tabContentCharacters'),
  drawerSessionsList: document.getElementById('drawerSessionsList'),
  drawerCharactersGrid: document.getElementById('drawerCharactersGrid'),

  // Modals & Sheets
  forgeModal: document.getElementById('forgeModal'),
  forgeModelsList: document.getElementById('forgeModelsList'),
  btnForgeModalClose: document.getElementById('btnForgeModalClose'),

  worldStatusModal: document.getElementById('worldStatusModal'),
  worldStatusBody: document.getElementById('worldStatusBody'),
  btnWorldModalClose: document.getElementById('btnWorldModalClose'),

  hardwareModal: document.getElementById('hardwareModal'),
  hardwareModalBody: document.getElementById('hardwareModalBody'),
  btnHardwareModalClose: document.getElementById('btnHardwareModalClose'),
  btnUnloadVram: document.getElementById('btnUnloadVram'),

  artGalleryModal: document.getElementById('artGalleryModal'),
  artGalleryGrid: document.getElementById('artGalleryGrid'),
  btnArtGalleryClose: document.getElementById('btnArtGalleryClose'),

  lightboxModal: document.getElementById('lightboxModal'),
  btnLightboxClose: document.getElementById('btnLightboxClose'),
  btnLightboxDownload: document.getElementById('btnLightboxDownload'),
};

// ─── Initialize Application ───
async function initApp() {
  registerServiceWorker();
  initVisualViewportListener();
  initDrawerEvents();
  initDrawerTabs();
  initInputEvents();
  initScrollBottomEvents();
  initForgeEvents();
  initWorldStatusEvents();
  initHardwareEvents();
  initArtGalleryEvents();
  initLightboxEvents();

  // Load initial system status
  await refreshSystemStatus();
  setInterval(refreshSystemStatus, 6000);

  // Load character gallery
  await loadCharactersList();

  // Load sessions
  await loadSessionsList();

  // Switch to most recent session or show empty state
  if (state.sessions && state.sessions.length > 0) {
    await switchSession(state.sessions[0].id);
  } else {
    ui.renderEmptyState(elements.chatViewport, state.activeMode);
  }
}

// ─── Service Worker Registration ───
function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/static/sw.js')
        .then(reg => {
          console.log('[PWA] Service Worker registered:', reg.scope);
          reg.update().catch(() => {});
        })
        .catch(err => console.log('[PWA] Service Worker registration failed:', err));
    });
  }
}

// ─── Mobile Virtual Keyboard Synchronization ───
function initVisualViewportListener() {
  if (window.visualViewport) {
    const handleViewportChange = () => {
      const keyboardHeight = Math.max(0, window.innerHeight - window.visualViewport.height);
      document.documentElement.style.setProperty('--keyboard-offset', `${keyboardHeight}px`);
      if (keyboardHeight > 0) {
        ui.scrollToBottom(elements.chatViewport);
      }
    };
    window.visualViewport.addEventListener('resize', handleViewportChange);
    window.visualViewport.addEventListener('scroll', handleViewportChange);
  }
}

// ─── Floating Scroll-To-Bottom ───
function initScrollBottomEvents() {
  elements.chatViewport.addEventListener('scroll', () => {
    const nearBottom = ui.isNearBottom(elements.chatViewport, 120);
    elements.btnScrollBottom.classList.toggle('visible', !nearBottom);
  });

  elements.btnScrollBottom.addEventListener('click', () => {
    ui.scrollToBottom(elements.chatViewport, true);
  });
}

// ─── System Status & Hardware Telemetry Polling ───
async function refreshSystemStatus() {
  try {
    const status = await api.getSystemStatus();
    state.hardwareStats = status;

    // 1. Update service dots
    if (status.services) {
      status.services.forEach(srv => {
        if (srv.port === 1234) elements.dotLlm.classList.toggle('online', srv.is_online);
        if (srv.port === 7860) elements.dotForge.classList.toggle('online', srv.is_online);
        if (srv.port === 5010) elements.dotTts.classList.toggle('online', srv.is_online);
      });
    }

    // 2. Update VRAM text
    if (status.telemetry) {
      const usedGb = (status.telemetry.vram_used_mb / 1024).toFixed(1);
      const totalGb = (status.telemetry.vram_total_mb / 1024).toFixed(1);
      elements.vramText.textContent = `${usedGb} / ${totalGb} GB`;
    }

    // 3. Update Mode only when no active session is mounted (active session owns its mode)
    if (!state.activeSessionId && status.active_mode && status.active_mode !== state.activeMode) {
      updateActiveMode(status.active_mode);
    }
  } catch (err) {
    console.debug('Failed to refresh system status:', err);
  }
}

const HEADER_GALLERY_ICON_SVG = `
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
    <circle cx="8.5" cy="8.5" r="1.5"></circle>
    <polyline points="21 15 16 10 5 21"></polyline>
  </svg>
`;

const HEADER_WORLD_LORE_ICON_SVG = `
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="10"></circle>
    <line x1="2" y1="12" x2="22" y2="12"></line>
    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
  </svg>
`;

function updateActiveMode(mode) {
  state.activeMode = mode;
  const isRoleplay = (mode === 'roleplay');
  elements.btnWorldStatus.style.display = isRoleplay ? 'flex' : 'none';
  elements.btnHeaderGallery.title = isRoleplay ? 'Правила мира и лор' : 'Галерея артов';
  elements.btnHeaderGallery.innerHTML = isRoleplay ? HEADER_WORLD_LORE_ICON_SVG : HEADER_GALLERY_ICON_SVG;
}

// ─── Drawer & Gestures ───
function initDrawerEvents() {
  elements.btnHamburger.addEventListener('click', openDrawer);
  elements.btnDrawerClose.addEventListener('click', closeDrawer);
  elements.drawerBackdrop.addEventListener('click', closeDrawer);

  // Quick New Session buttons
  document.querySelectorAll('.btn-quick-new').forEach(btn => {
    btn.addEventListener('click', async () => {
      const mode = btn.dataset.mode;
      closeDrawer();
      try {
        await api.setSystemMode(mode);
        updateActiveMode(mode);
        const res = await api.createSession({ mode });
        await loadSessionsList();
        await switchSession(res.session_id);
        ui.showToast(`Создан диалог [${mode.toUpperCase()}]`);
      } catch (err) {
        ui.showToast(`Ошибка создания диалога: ${err.message}`, true);
      }
    });
  });

  // Swipe gesture support
  let touchStartX = 0;
  let touchCurrentX = 0;

  window.addEventListener('touchstart', (e) => {
    touchStartX = e.touches[0].clientX;
    touchCurrentX = touchStartX;
  }, { passive: true });

  window.addEventListener('touchmove', (e) => {
    touchCurrentX = e.touches[0].clientX;
  }, { passive: true });

  window.addEventListener('touchend', () => {
    const deltaX = touchCurrentX - touchStartX;
    if (touchStartX < 30 && deltaX > 70) {
      openDrawer();
    } else if (elements.drawerPanel.classList.contains('open') && deltaX < -70) {
      closeDrawer();
    }
  });
}

function initDrawerTabs() {
  elements.tabBtnSessions.addEventListener('click', () => {
    elements.tabBtnSessions.classList.add('active');
    elements.tabBtnCharacters.classList.remove('active');
    elements.tabContentSessions.classList.add('active');
    elements.tabContentCharacters.classList.remove('active');
  });

  elements.tabBtnCharacters.addEventListener('click', () => {
    elements.tabBtnCharacters.classList.add('active');
    elements.tabBtnSessions.classList.remove('active');
    elements.tabContentCharacters.classList.add('active');
    elements.tabContentSessions.classList.remove('active');
  });
}

function openDrawer() {
  elements.drawerPanel.classList.add('open');
  elements.drawerBackdrop.classList.add('active');
}

function closeDrawer() {
  elements.drawerPanel.classList.remove('open');
  elements.drawerBackdrop.classList.remove('active');
}

// ─── Character Gallery ───
async function selectCompanion(char) {
  closeDrawer();
  try {
    await api.setSystemMode('roleplay');
    updateActiveMode('roleplay');
    const res = await api.createSession({
      mode: 'roleplay',
      characterId: char.id,
      title: char.name
    });
    await loadSessionsList();
    await switchSession(res.session_id);
    ui.showToast(`Начат диалог с ${char.name}`);
  } catch (err) {
    ui.showToast(`Ошибка старта персонажа: ${err.message}`, true);
  }
}

async function loadCharactersList() {
  try {
    const list = await api.getCharacters();
    state.characters = list;
    ui.renderCharactersGallery(elements.drawerCharactersGrid, list, selectCompanion);
  } catch (err) {
    console.debug('Failed to load characters:', err);
  }
}

// ─── Session Management ───
async function loadSessionsList() {
  try {
    const list = await api.getSessions();
    state.sessions = list;
    elements.badgeCounter.textContent = list.length;

    ui.renderSessionsList(
      elements.drawerSessionsList,
      list,
      state.activeSessionId,
      (selectedId) => {
        closeDrawer();
        switchSession(selectedId);
      },
      (deleteId, title) => {
        confirmDeleteSession(deleteId, title);
      }
    );
  } catch (err) {
    ui.showToast(`Ошибка загрузки сессий: ${err.message}`, true);
  }
}

function closeActiveArtMonitor() {
  if (state.activeArtMonitor) {
    state.activeArtMonitor.close();
    state.activeArtMonitor = null;
  }
}

function disconnectSessionEvents() {
  if (state.sessionEventSource) {
    state.sessionEventSource.close();
    state.sessionEventSource = null;
  }
}

function connectSessionEvents(sessionId) {
  disconnectSessionEvents();
  if (!sessionId || typeof EventSource === 'undefined') return;

  const es = new EventSource(`/api/sessions/${encodeURIComponent(sessionId)}/events`);
  state.sessionEventSource = es;

  es.addEventListener('art_completed', async (e) => {
    let data = {};
    try { data = JSON.parse(e.data || '{}'); } catch (err) {}
    closeActiveArtMonitor();

    const targetSid = data.session_id || sessionId;
    if (state.activeSessionId !== targetSid) return;

    let targetBubble = null;
    if (data.message_id) {
      const escapedId = (typeof CSS !== 'undefined' && CSS.escape)
        ? CSS.escape(String(data.message_id))
        : String(data.message_id).replace(/"/g, '\\"');
      targetBubble = elements.chatViewport.querySelector(`.bubble.assistant[data-msg-id="${escapedId}"]`);
    }
    if (!targetBubble) {
      const bubbles = elements.chatViewport.querySelectorAll('.bubble.assistant');
      if (bubbles.length > 0) targetBubble = bubbles[bubbles.length - 1];
    }

    if (targetBubble && data.image_url) {
      ui.appendInlineArt(targetBubble, data.image_url, 'Иллюстрация', data.generation_params || null);
      ui.scrollToBottom(elements.chatViewport);
    } else if (data.image_url) {
      await switchSession(targetSid);
    }

    ui.showToast('Изображение создано!');
    loadSessionsList().catch(() => {});
  });

  es.addEventListener('art_failed', (e) => {
    let data = {};
    try { data = JSON.parse(e.data || '{}'); } catch (err) {}
    closeActiveArtMonitor();
    ui.showToast(`⚠️ ${data.error || 'Ошибка генерации изображения'}`, true);
  });

  es.addEventListener('art_interrupted', () => {
    closeActiveArtMonitor();
    ui.showToast('Генерация прервана');
  });
}

async function switchSession(sessionId) {
  try {
    state.activeSessionId = sessionId;
    closeActiveArtMonitor();
    connectSessionEvents(sessionId);
    const detail = await api.getSessionDetail(sessionId);

    // Update active mode and synchronize backend ModeCoordinator with session mode
    const sessionMode = detail.mode || 'assistant';
    updateActiveMode(sessionMode);
    api.setSystemMode(sessionMode).catch(() => {});

    // Update Active Forge Model if configured in session
    if (detail.active_forge_model) {
      state.activeForgeModel = detail.active_forge_model;
      elements.forgeModelName.textContent = detail.active_forge_model === 'auto' ? 'Авто' : detail.active_forge_model;
    }

    // Update Scene Background Overlay
    if (detail.scene_background_url) {
      elements.chatViewport.style.backgroundImage = `url("${detail.scene_background_url}")`;
      elements.chatViewport.classList.add('has-scene-bg');
    } else {
      elements.chatViewport.style.backgroundImage = '';
      elements.chatViewport.classList.remove('has-scene-bg');
    }

    // Update Header Avatar & Companion Info
    elements.headerAvatarImg.src = `/media/avatar/${sessionId}?t=${Date.now()}`;
    if (detail.title && state.activeMode === 'roleplay') {
      elements.headerCompanionName.textContent = detail.title;
      elements.headerCompanionStatus.textContent = 'В сети';
    } else if (state.activeMode === 'engineer') {
      elements.headerCompanionName.textContent = 'NeonRoute Engineer';
      elements.headerCompanionStatus.textContent = 'Готов к кодингу';
    } else {
      elements.headerCompanionName.textContent = 'Элария';
      elements.headerCompanionStatus.textContent = 'В сети';
    }

    // Clear and render messages
    elements.chatViewport.innerHTML = '';

    if (!detail.messages || detail.messages.length === 0) {
      ui.renderEmptyState(elements.chatViewport, state.activeMode);
    } else {
      detail.messages.forEach(msg => {
        if (msg.role === 'user') {
          ui.renderUserMessage(elements.chatViewport, msg.content);
        } else if (msg.role === 'assistant') {
          const p = ui.createAssistantMessagePlaceholder(elements.chatViewport, state.activeMode, state.activeSessionId);
          if (msg.id) {
            p.bubble.dataset.msgId = msg.id;
          }
          if (msg.thought) {
            ui.updateThoughtChunk(p, msg.thought, elements.chatViewport);
            ui.finalizeThoughtSpoiler(p, elements.chatViewport);
          }
          ui.updateTokenChunk(p, msg.content, elements.chatViewport);
          if (msg.image_url || msg.generation_params) {
            ui.appendInlineArt(p.bubble, msg.image_url, 'Иллюстрация', msg.generation_params);
          }
          ui.finalizeAssistantMessage(p, elements.chatViewport, createActionHandlers(p));
        }
      });
      ui.scrollToBottom(elements.chatViewport);
    }

    // Highlight active in drawer
    ui.renderSessionsList(
      elements.drawerSessionsList,
      state.sessions,
      state.activeSessionId,
      switchSession,
      confirmDeleteSession
    );
  } catch (err) {
    ui.showToast(`Ошибка открытия сессии: ${err.message}`, true);
  }
}

async function confirmDeleteSession(sessionId, title) {
  if (confirm(`Удалить диалог "${title || 'Диалог'}"?`)) {
    try {
      await api.deleteSession(sessionId);
      ui.showToast('Диалог удален');
      await loadSessionsList();
      if (state.activeSessionId === sessionId) {
        state.activeSessionId = null;
        closeActiveArtMonitor();
        disconnectSessionEvents();
        if (state.sessions.length > 0) {
          await switchSession(state.sessions[0].id);
        } else {
          elements.chatViewport.innerHTML = '';
          ui.renderEmptyState(elements.chatViewport, state.activeMode);
        }
      }
    } catch (err) {
      ui.showToast(`Ошибка удаления: ${err.message}`, true);
    }
  }
}

// ─── Assistant Message Action Handlers ───
function createActionHandlers(placeholder) {
  return {
    onArt: async (text, bubble, btn) => {
      let isPolling = true;
      let pollTimer = null;
      btn.classList.add('loading');
      ui.showToast('🎨 Запуск генерации иллюстрации сцены...');

      const vf = ui.createCyberpunkViewfinder(elements.chatViewport, async () => {
        isPolling = false;
        if (pollTimer) clearInterval(pollTimer);
        try {
          await api.interruptArt();
          ui.showToast('Генерация прервана');
        } catch (e) {
          console.debug(e);
        }
        vf.remove();
      });

      pollTimer = setInterval(async () => {
        if (!isPolling) return;
        try {
          const p = await api.getArtProgress();
          const pct = Math.round((p.progress || 0) * 100);
          const status = p.stage_name ? `SD Forge: ${p.stage_name}` : 'Генерация иллюстрации сцены...';
          vf.updateProgress(pct, status);
        } catch (e) {}
      }, 700);

      try {
        const res = await api.generateSceneArt({
          sessionId: state.activeSessionId,
          text: text
        });
        isPolling = false;
        clearInterval(pollTimer);
        vf.remove();

        if (res.image_url) {
          ui.appendInlineArt(bubble, res.image_url, 'Сцена', res.generation_params);
          ui.showToast('Иллюстрация готова!');
        } else {
          ui.showToast('Сервер не вернул ссылку на изображение', true);
        }
      } catch (err) {
        isPolling = false;
        if (pollTimer) clearInterval(pollTimer);
        vf.remove();
        ui.showToast(`Ошибка генерации арта: ${err.message}`, true);
      } finally {
        btn.classList.remove('loading');
      }
    },
    onRetry: async (row) => {
      if (state.isGenerating) return;
      try {
        row.remove();
        await handleRegenerateLast();
      } catch (err) {
        ui.showToast(`Ошибка повтора: ${err.message}`, true);
      }
    },
    onCopy: async (text) => {
      try {
        await navigator.clipboard.writeText(text);
        ui.showToast('Текст скопирован в буфер');
      } catch (err) {
        ui.showToast('Не удалось скопировать', true);
      }
    },
    onChoice: (choiceText) => {
      elements.inputTextarea.value = choiceText;
      handleSendClick();
    }
  };
}

// ─── Input & Direct CLI Commands ───
const DIRECT_CMD_RE = /^!(?:art|арт|img)\s+/i;

function initInputEvents() {
  const textarea = elements.inputTextarea;

  textarea.addEventListener('input', () => {
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 140)}px`;

    const val = textarea.value.trim();
    const isDirectCmd = DIRECT_CMD_RE.test(val);

    elements.btnSend.classList.toggle('btn-direct-command', isDirectCmd);
    elements.btnSend.title = isDirectCmd ? 'Сгенерировать в Forge (!art)' : 'Отправить';
    elements.btnSend.disabled = !val && !state.isGenerating;
  });

  textarea.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendClick();
    }
  });

  elements.btnSend.addEventListener('click', handleSendClick);
}

function setSendButtonState(isStreaming) {
  state.isGenerating = isStreaming;
  elements.btnSend.disabled = false;

  if (isStreaming) {
    elements.btnSend.innerHTML = `
      <svg class="spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle>
        <path d="M12 2a10 10 0 0 1 10 10" stroke-linecap="round"></path>
      </svg>
    `;
    elements.btnSend.title = 'Остановить генерацию';
  } else {
    elements.btnSend.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="22" y1="2" x2="11" y2="13"></line>
        <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
      </svg>
    `;
    const isDirectCmd = DIRECT_CMD_RE.test(elements.inputTextarea.value.trim());
    elements.btnSend.title = isDirectCmd ? 'Сгенерировать в Forge (!art)' : 'Отправить';
    elements.btnSend.classList.toggle('btn-direct-command', isDirectCmd);
    elements.btnSend.disabled = !elements.inputTextarea.value.trim();
  }
}

function createStreamCallbacks(placeholder, errorPrefix = 'Ошибка стриминга') {
  return {
    onThought: (chunk) => {
      ui.updateThoughtChunk(placeholder, chunk, elements.chatViewport);
    },
    onThoughtEnd: () => {
      ui.finalizeThoughtSpoiler(placeholder, elements.chatViewport);
    },
    onToken: (chunk) => {
      ui.updateTokenChunk(placeholder, chunk, elements.chatViewport);
    },
    onArtStarted: (payload) => {
      let parsed = null;
      if (typeof payload === 'string') {
        try { parsed = JSON.parse(payload); } catch (e) {}
      }
      if (parsed && parsed.message_id) {
        placeholder.bubble.dataset.msgId = parsed.message_id;
      }
      ui.showToast('🎨 Арт-директор запускает генерацию в Forge...');
      handleBackgroundGalleryArt(parsed?.session_id || state.activeSessionId);
    },
    onArtError: (payload) => {
      let msg = payload;
      try {
        const parsed = JSON.parse(payload);
        if (parsed && parsed.error) msg = parsed.error;
      } catch (e) {}
      ui.showToast(`⚠️ ${msg || 'SD Forge недоступен'}`, true);
    },
    onDone: (fullPayload) => {
      ui.finalizeThoughtSpoiler(placeholder, elements.chatViewport);
      if (typeof fullPayload === 'string') {
        placeholder.rawText = fullPayload;
        placeholder.textContainer.textContent = fullPayload;
      }
      ui.finalizeAssistantMessage(placeholder, elements.chatViewport, createActionHandlers(placeholder));
      setSendButtonState(false);
      state.abortController = null;
      loadSessionsList();
    },
    onError: (err) => {
      ui.showToast(`${errorPrefix}: ${err}`, true);
      setSendButtonState(false);
      state.abortController = null;
    }
  };
}

async function handleSendClick() {
  if (state.isGenerating) {
    if (state.abortController) {
      state.abortController.abort();
      setSendButtonState(false);
      ui.showToast('Генерация остановлена');
    }
    return;
  }

  const text = elements.inputTextarea.value.trim();
  if (!text) return;

  // Clear input
  elements.inputTextarea.value = '';
  elements.inputTextarea.style.height = 'auto';
  elements.btnSend.classList.remove('btn-direct-command');

  // Render user message bubble
  ui.renderUserMessage(elements.chatViewport, text);

  // If no active session, create one
  if (!state.activeSessionId) {
    try {
      const res = await api.createSession({ mode: state.activeMode });
      state.activeSessionId = res.session_id;
      connectSessionEvents(state.activeSessionId);
      await loadSessionsList();
    } catch (err) {
      ui.showToast(`Ошибка создания сессии: ${err.message}`, true);
      return;
    }
  }

  // Check if Direct CLI Art Command (!art, !арт, !img)
  if (DIRECT_CMD_RE.test(text)) {
    await handleDirectArtCommand(text);
    return;
  }

  const placeholder = ui.createAssistantMessagePlaceholder(elements.chatViewport, state.activeMode, state.activeSessionId);
  setSendButtonState(true);

  state.abortController = new AbortController();

  await stream.sendChatMessageStream({
    sessionId: state.activeSessionId,
    text: text,
    mode: state.activeMode,
    signal: state.abortController.signal,
    ...createStreamCallbacks(placeholder, 'Ошибка стриминга')
  });
}

async function handleDirectArtCommand(commandText) {
  let isPolling = true;
  let pollTimer = null;
  ui.showToast('🎨 Выполнение прямой команды Forge...');

  const vf = ui.createCyberpunkViewfinder(elements.chatViewport, async () => {
    isPolling = false;
    if (pollTimer) clearInterval(pollTimer);
    try {
      await api.interruptArt();
      ui.showToast('Генерация прервана');
    } catch (e) {
      console.debug(e);
    }
    vf.remove();
  });

  pollTimer = setInterval(async () => {
    if (!isPolling) return;
    try {
      const p = await api.getArtProgress();
      const pct = Math.round((p.progress || 0) * 100);
      const status = p.stage_name ? `SD Forge: ${p.stage_name}` : 'Прямая генерация в Forge...';
      vf.updateProgress(pct, status);
    } catch (e) {}
  }, 700);

  try {
    const res = await api.sendDirectArtCommand({
      sessionId: state.activeSessionId,
      command: commandText
    });
    isPolling = false;
    clearInterval(pollTimer);
    vf.remove();

    if (res.image_url) {
      const p = ui.createAssistantMessagePlaceholder(elements.chatViewport, state.activeMode, state.activeSessionId);
      ui.updateTokenChunk(p, `🎨 Прямая команда Forge: \`${res.positive_prompt || commandText}\``, elements.chatViewport);
      ui.appendInlineArt(p.bubble, res.image_url, res.positive_prompt || 'Изображение', res.generation_params);
      ui.finalizeAssistantMessage(p, elements.chatViewport, createActionHandlers(p));
      ui.showToast('Изображение создано!');
      await loadSessionsList();
    } else {
      ui.showToast('Сервер не вернул сгенерированное изображение', true);
    }
  } catch (err) {
    isPolling = false;
    if (pollTimer) clearInterval(pollTimer);
    vf.remove();
    ui.showToast(`Ошибка прямой команды: ${err.message}`, true);
  }
}

function handleBackgroundGalleryArt(targetSessionId = state.activeSessionId) {
  closeActiveArtMonitor();

  let isPolling = true;
  let sawGenerating = false;
  let idleTicks = 0;

  const closeMonitor = () => {
    if (!isPolling) return;
    isPolling = false;
    clearInterval(pollTimer);
    vf.remove();
    if (state.activeArtMonitor && state.activeArtMonitor.close === closeMonitor) {
      state.activeArtMonitor = null;
    }
  };

  const vf = ui.createCyberpunkViewfinder(elements.chatViewport, async () => {
    closeMonitor();
    try { await api.interruptArt(targetSessionId); } catch (e) {}
  });

  state.activeArtMonitor = { close: closeMonitor, sessionId: targetSessionId };

  const pollTimer = setInterval(async () => {
    if (!isPolling) return;
    try {
      const p = await api.getArtProgress();
      const pct = Math.round((p.progress || 0) * 100);
      const status = p.stage_name ? `SD Forge: ${p.stage_name}` : 'Генерация иллюстрации в Forge...';
      vf.updateProgress(pct, status);

      if (p.is_generating || pct > 0) {
        sawGenerating = true;
        idleTicks = 0;
      } else {
        idleTicks += 1;
      }

      // When per-session SSE push channel is connected/connecting, rely on art_completed /
      // art_failed / art_interrupted signals instead of guessing completion during Hires/ADetailer.
      const sseActive = Boolean(
        state.sessionEventSource && state.sessionEventSource.readyState !== EventSource.CLOSED
      );
      if (!sseActive && !p.is_generating && (sawGenerating ? idleTicks >= 12 : idleTicks >= 15)) {
        closeMonitor();
        if (targetSessionId && state.activeSessionId === targetSessionId) {
          await switchSession(targetSessionId);
          ui.showToast('Изображение создано!');
        }
      }
    } catch (e) {}
  }, 800);
}

async function handleRegenerateLast() {
  const placeholder = ui.createAssistantMessagePlaceholder(elements.chatViewport, state.activeMode, state.activeSessionId);
  setSendButtonState(true);

  state.abortController = new AbortController();

  await stream.sendChatRegenerateStream({
    sessionId: state.activeSessionId,
    mode: state.activeMode,
    signal: state.abortController.signal,
    ...createStreamCallbacks(placeholder, 'Ошибка повторной генерации')
  });
}

// ─── Forge Models Bottom Sheet ───
function initForgeEvents() {
  elements.btnForgeModel.addEventListener('click', async () => {
    try {
      const data = await api.getForgeModels(state.activeSessionId);
      const models = data.models || [];
      const activeId = data.active_model_id || state.activeForgeModel || 'auto';

      ui.renderForgeModelsList(elements.forgeModelsList, models, activeId, async (selectedModel) => {
        try {
          await api.setActiveForgeModel(selectedModel.id, state.activeSessionId);
          state.activeForgeModel = selectedModel.id;
          elements.forgeModelName.textContent = selectedModel.id === 'auto' ? 'Авто' : selectedModel.name;
          elements.forgeModal.classList.remove('active');
          ui.showToast(`Выбрана модель: ${selectedModel.name}`);
        } catch (e) {
          ui.showToast(`Ошибка смены модели: ${e.message}`, true);
        }
      });

      elements.forgeModal.classList.add('active');
    } catch (err) {
      ui.showToast(`Не удалось загрузить модели: ${err.message}`, true);
    }
  });

  elements.btnForgeModalClose.addEventListener('click', () => {
    elements.forgeModal.classList.remove('active');
  });
  elements.forgeModal.addEventListener('click', (e) => {
    if (e.target === elements.forgeModal) elements.forgeModal.classList.remove('active');
  });
}

// ─── World Status & Character Gallery Bottom Sheet ───
function setWorldModalTitle(title) {
  const titleEl = elements.worldStatusModal.querySelector('.modal-title');
  if (titleEl) titleEl.textContent = title;
}

async function openWorldStatusModal() {
  if (!state.activeSessionId) return;
  try {
    const castData = await api.getSessionCast(state.activeSessionId);
    setWorldModalTitle('📜 Правила мира и лор');
    elements.worldStatusBody.classList.remove('characters-gallery-detailed');
    ui.renderWorldStatus(elements.worldStatusBody, castData);
    elements.worldStatusModal.classList.add('active');
  } catch (err) {
    ui.showToast(`Ошибка загрузки статуса мира: ${err.message}`, true);
  }
}

async function openCharactersGalleryModal() {
  try {
    if (!state.characters || state.characters.length === 0) {
      await loadCharactersList();
    }
    setWorldModalTitle('🎭 Галерея персонажей');
    ui.renderCharactersGallery(
      elements.worldStatusBody,
      state.characters,
      async (char) => {
        elements.worldStatusModal.classList.remove('active');
        await selectCompanion(char);
      },
      { detailed: true }
    );
    elements.worldStatusModal.classList.add('active');
  } catch (err) {
    ui.showToast(`Ошибка загрузки персонажей: ${err.message}`, true);
  }
}

function initWorldStatusEvents() {
  elements.btnWorldStatus.addEventListener('click', openCharactersGalleryModal);

  elements.worldStatusBody.addEventListener('click', async (e) => {
    const target = e.target.closest('button');
    if (!target) return;

    if (target.id === 'btnGenerateSceneBg') {
      target.disabled = true;
      target.textContent = '🎨 Генерация фона...';
      try {
        ui.showToast('Генерация фона локации без персонажей...');
        const res = await api.generateSceneBackground({ sessionId: state.activeSessionId });
        if (res.image_url) {
          elements.chatViewport.style.backgroundImage = `url("${res.image_url}")`;
          elements.chatViewport.classList.add('has-scene-bg');
          ui.showToast('Фон сцены успешно установлен!');
          const castData = await api.getSessionCast(state.activeSessionId);
          ui.renderWorldStatus(elements.worldStatusBody, castData);
        }
      } catch (err) {
        ui.showToast(`Ошибка создания фона: ${err.message}`, true);
      } finally {
        target.disabled = false;
        target.textContent = '🎨 Сгенерировать фон';
      }
    } else if (target.id === 'btnResetSceneBg') {
      elements.chatViewport.style.backgroundImage = '';
      elements.chatViewport.classList.remove('has-scene-bg');
      ui.showToast('Фон сцены сброшен');
    }
  });

  elements.btnWorldModalClose.addEventListener('click', () => {
    elements.worldStatusModal.classList.remove('active');
  });
  elements.worldStatusModal.addEventListener('click', (e) => {
    if (e.target === elements.worldStatusModal) elements.worldStatusModal.classList.remove('active');
  });
}

// ─── Hardware & VRAM Purge Modal ───
function initHardwareEvents() {
  elements.hardwarePill.addEventListener('click', () => {
    ui.renderHardwareDetails(elements.hardwareModalBody, state.hardwareStats);
    elements.hardwareModal.classList.add('active');
  });

  elements.btnHardwareModalClose.addEventListener('click', () => {
    elements.hardwareModal.classList.remove('active');
  });
  elements.hardwareModal.addEventListener('click', (e) => {
    if (e.target === elements.hardwareModal) elements.hardwareModal.classList.remove('active');
  });

  elements.btnUnloadVram.addEventListener('click', async () => {
    try {
      elements.btnUnloadVram.disabled = true;
      elements.btnUnloadVram.textContent = 'Освобождение VRAM...';
      await api.unloadVram();
      ui.showToast('VRAM успешно выгружена');
      await refreshSystemStatus();
      ui.renderHardwareDetails(elements.hardwareModalBody, state.hardwareStats);
    } catch (err) {
      ui.showToast(`Ошибка выгрузки VRAM: ${err.message}`, true);
    } finally {
      elements.btnUnloadVram.disabled = false;
      elements.btnUnloadVram.textContent = '🧹 Освободить VRAM';
    }
  });
}

// ─── Art Gallery Modal (or World Lore in Roleplay) ───
function initArtGalleryEvents() {
  elements.btnHeaderGallery.addEventListener('click', async () => {
    if (state.activeMode === 'roleplay') {
      await openWorldStatusModal();
      return;
    }
    try {
      const data = await api.getArtGallery(state.activeSessionId);
      ui.renderArtGalleryGrid(elements.artGalleryGrid, data.items || [], (item) => {
        ui.openLightbox(item.url, item.filename, item.url);
      });
      elements.artGalleryModal.classList.add('active');
    } catch (err) {
      ui.showToast(`Ошибка загрузки галереи: ${err.message}`, true);
    }
  });

  elements.btnArtGalleryClose.addEventListener('click', () => {
    elements.artGalleryModal.classList.remove('active');
  });
  elements.artGalleryModal.addEventListener('click', (e) => {
    if (e.target === elements.artGalleryModal) elements.artGalleryModal.classList.remove('active');
  });
}

// ─── Lightbox Modal ───
function initLightboxEvents() {
  elements.btnHeaderAvatar.addEventListener('click', () => {
    const src = elements.headerAvatarImg.src;
    const name = elements.headerCompanionName.textContent;
    ui.openLightbox(src, name);
  });

  elements.btnLightboxClose.addEventListener('click', () => {
    ui.closeLightbox();
  });
  elements.lightboxModal.addEventListener('click', (e) => {
    if (e.target === elements.lightboxModal) ui.closeLightbox();
  });
}

// Start app on DOMContentLoaded
window.addEventListener('DOMContentLoaded', initApp);
