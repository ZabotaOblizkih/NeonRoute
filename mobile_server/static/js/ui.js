/**
 * NeonRoute Mobile UI Renderer
 * Handles DOM rendering for messages, thought spoilers, choices, stats, drawer, modals, and toasts.
 */

import { renderMarkdown } from './markdown.js';

// ─── Auto-scroll Lock ───
export function isNearBottom(container, threshold = 80) {
  if (!container) return true;
  return (container.scrollHeight - container.scrollTop - container.clientHeight) <= threshold;
}

export function scrollToBottom(container, smooth = false) {
  if (!container) return;
  container.scrollTo({
    top: container.scrollHeight,
    behavior: smooth ? 'smooth' : 'auto'
  });
}

// ─── Chat Viewport Rendering ───
export function renderEmptyState(container, mode = 'assistant') {
  container.innerHTML = `
    <div class="chat-empty-state">
      <svg class="chat-empty-icon" viewBox="0 0 24 24" fill="none" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
      </svg>
      <div class="chat-empty-title">NeonRoute</div>
      <div class="chat-empty-desc">
        Готов к работе в режиме <b>${mode.toUpperCase()}</b>. Задайте вопрос или выберите ролевого персонажа.
      </div>
    </div>
  `;
}

export function renderUserMessage(container, text) {
  const emptyState = container.querySelector('.chat-empty-state');
  if (emptyState) emptyState.remove();

  const row = document.createElement('div');
  row.className = 'chat-row user';

  const bubble = document.createElement('div');
  bubble.className = 'bubble user';
  bubble.textContent = text;

  row.appendChild(bubble);
  container.appendChild(row);
  scrollToBottom(container, true);
}

export function createAssistantMessagePlaceholder(container, mode = 'assistant', sessionId = null) {
  const emptyState = container.querySelector('.chat-empty-state');
  if (emptyState) emptyState.remove();

  const row = document.createElement('div');
  row.className = 'chat-row assistant';

  // Assistant Mini-Avatar (32x32)
  const avatarBox = document.createElement('div');
  avatarBox.className = 'bubble-avatar';
  const avatarImg = document.createElement('img');
  avatarImg.src = sessionId ? `/media/avatar/${sessionId}` : '/media/avatar/current';
  avatarImg.alt = 'Avatar';
  avatarImg.onerror = () => { avatarImg.src = '/media/avatar/current'; };
  avatarBox.appendChild(avatarImg);
  avatarBox.addEventListener('click', () => {
    openLightbox(avatarImg.src, 'Аватар ассистента');
  });

  const bubble = document.createElement('div');
  bubble.className = `bubble assistant bubble-${mode}`;

  // Thought spoiler container
  const thoughtBox = document.createElement('div');
  thoughtBox.className = 'thought-box open';
  thoughtBox.style.display = 'none';

  const thoughtHeader = document.createElement('div');
  thoughtHeader.className = 'thought-header';
  thoughtHeader.innerHTML = `
    <div class="thought-title-group">
      <div class="thought-pulse-dot"></div>
      <span class="thought-title-text">Ход мыслей...</span>
    </div>
    <svg class="thought-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="6 9 12 15 18 9"></polyline>
    </svg>
  `;

  const thoughtContent = document.createElement('div');
  thoughtContent.className = 'thought-content';

  thoughtHeader.addEventListener('click', () => {
    thoughtBox.classList.toggle('open');
  });

  thoughtBox.appendChild(thoughtHeader);
  thoughtBox.appendChild(thoughtContent);

  // Main text container
  const textContainer = document.createElement('div');
  textContainer.className = 'assistant-text';
  textContainer.addEventListener('click', (e) => {
    const copyBtn = e.target.closest('.md-code-copy-btn');
    if (!copyBtn) return;
    const codeEl = copyBtn.closest('.md-code-block')?.querySelector('pre code');
    if (!codeEl) return;
    const codeText = codeEl.textContent || '';
    const onCopied = () => {
      copyBtn.textContent = '✓ Скопировано';
      setTimeout(() => { copyBtn.textContent = '📋 Копировать'; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(codeText).then(onCopied).catch(() => {});
    }
  });

  bubble.appendChild(thoughtBox);
  bubble.appendChild(textContainer);

  row.appendChild(avatarBox);
  row.appendChild(bubble);
  container.appendChild(row);

  return {
    row,
    bubble,
    mode,
    avatarImg,
    thoughtBox,
    thoughtHeader,
    thoughtContent,
    textContainer,
    rawText: ''
  };
}

export function updateThoughtChunk(placeholder, chunk, container) {
  const nearBottom = isNearBottom(container);
  placeholder.thoughtBox.style.display = 'block';
  placeholder.thoughtContent.textContent += chunk;
  if (nearBottom) scrollToBottom(container);
}

export function finalizeThoughtSpoiler(placeholder, container) {
  const thoughtText = placeholder.thoughtContent.textContent.trim();
  if (!thoughtText) {
    placeholder.thoughtBox.style.display = 'none';
    return;
  }

  const charCount = thoughtText.length;
  const tokenEst = Math.max(1, Math.round(charCount / 4));

  const titleText = placeholder.thoughtHeader.querySelector('.thought-title-text');
  if (titleText) {
    titleText.textContent = `Ход мыслей (${tokenEst} токенов)`;
  }

  const pulseDot = placeholder.thoughtHeader.querySelector('.thought-pulse-dot');
  if (pulseDot) pulseDot.style.display = 'none';

  placeholder.thoughtBox.classList.remove('open');
}

export function updateTokenChunk(placeholder, chunk, container) {
  const nearBottom = isNearBottom(container);
  placeholder.rawText = (placeholder.rawText || '') + chunk;
  placeholder.textContainer.innerHTML = renderMarkdown(placeholder.rawText);
  if (nearBottom) scrollToBottom(container);
}

/**
 * Parses tags like <choices>...</choices> and <stats>...</stats> / <meters>...</meters>
 */
export function parseSpecialTags(rawText) {
  let text = rawText || '';
  const choices = [];
  const stats = [];

  // 1. Extract <choices>...</choices>
  const choicesMatch = text.match(/<choices>([\s\S]*?)<\/choices>/i);
  if (choicesMatch) {
    const block = choicesMatch[1];
    text = text.replace(choicesMatch[0], '').trim();
    const lines = block.split('\n').map(l => l.trim()).filter(Boolean);
    for (const l of lines) {
      const cleanLine = l.replace(/^(\d+[\.\)]\s*|[-*•]\s*)/, '').trim();
      if (cleanLine) choices.push(cleanLine);
    }
  }

  // 2. Extract <stats>...</stats> or <meters>...</meters>
  const statsMatch = text.match(/<(?:stats|meters)>([\s\S]*?)<\/(?:stats|meters)>/i);
  if (statsMatch) {
    const block = statsMatch[1];
    text = text.replace(statsMatch[0], '').trim();
    const lines = block.split('\n').map(l => l.trim()).filter(Boolean);
    for (const l of lines) {
      const parts = l.split(':');
      if (parts.length >= 2) {
        stats.push({
          label: parts[0].trim(),
          value: parts.slice(1).join(':').trim()
        });
      } else {
        stats.push({ label: '', value: l });
      }
    }
  }

  return { cleanText: text, choices, stats };
}

/**
 * Finalizes assistant message: parses special tags, renders choices, stats, and action footer.
 */
export function finalizeAssistantMessage(placeholder, container, handlers = {}) {
  const { onArt, onRetry, onCopy, onChoice } = handlers;
  const fullText = placeholder.rawText || placeholder.textContainer.textContent || '';
  const { cleanText, choices, stats } = parseSpecialTags(fullText);

  // Set narrative text as formatted Markdown
  placeholder.textContainer.innerHTML = renderMarkdown(cleanText);

  // Render Stats Accordion if present
  if (stats.length > 0 && !placeholder.bubble.querySelector('.stats-accordion')) {
    const acc = document.createElement('div');
    acc.className = 'stats-accordion';

    const header = document.createElement('div');
    header.className = 'stats-accordion-header';
    header.innerHTML = `
      <span>📊 Характеристики и параметры</span>
      <span class="chevron">▾</span>
    `;

    const body = document.createElement('div');
    body.className = 'stats-accordion-body';

    stats.forEach(s => {
      const row = document.createElement('div');
      row.className = 'stat-item-row';
      if (s.label) {
        row.innerHTML = `<span>${s.label}:</span><b>${s.value}</b>`;
      } else {
        row.innerHTML = `<span>${s.value}</span>`;
      }
      body.appendChild(row);
    });

    header.addEventListener('click', () => {
      const isHidden = body.style.display === 'none';
      body.style.display = isHidden ? 'flex' : 'none';
      header.querySelector('.chevron').textContent = isHidden ? '▾' : '▸';
    });

    acc.appendChild(header);
    acc.appendChild(body);
    placeholder.bubble.appendChild(acc);
  }

  // Render Choices Action Chips if present
  if (choices.length > 0 && !placeholder.bubble.querySelector('.choices-container')) {
    const choicesBox = document.createElement('div');
    choicesBox.className = 'choices-container';

    choices.forEach(ch => {
      const btn = document.createElement('button');
      btn.className = 'choice-chip';
      btn.textContent = ch;
      btn.addEventListener('click', () => {
        if (onChoice) onChoice(ch);
      });
      choicesBox.appendChild(btn);
    });

    placeholder.bubble.appendChild(choicesBox);
  }

  // Render Message Action Footer ([ 🎨 Арт ], [ 🔄 Повтор ], [ 📋 ]) only in Roleplay mode
  if (placeholder.mode === 'roleplay' && !placeholder.bubble.querySelector('.message-action-footer')) {
    const footer = document.createElement('div');
    footer.className = 'message-action-footer';

    // 1. Art button
    const btnArt = document.createElement('button');
    btnArt.className = 'btn-action-chip btn-art';
    btnArt.title = 'Сгенерировать иллюстрацию сцены через Forge и Арт-директор';
    btnArt.innerHTML = '🎨 Арт сцены';
    btnArt.addEventListener('click', () => {
      if (onArt) onArt(cleanText, placeholder.bubble, btnArt);
    });

    // 2. Retry / Regenerate button
    const btnRetry = document.createElement('button');
    btnRetry.className = 'btn-action-chip btn-retry';
    btnRetry.title = 'Сгенерировать ответ заново';
    btnRetry.innerHTML = '🔄 Повтор';
    btnRetry.addEventListener('click', () => {
      if (onRetry) onRetry(placeholder.row);
    });

    // 3. Copy button
    const btnCopy = document.createElement('button');
    btnCopy.className = 'btn-action-chip btn-copy';
    btnCopy.title = 'Скопировать текст сообщения';
    btnCopy.innerHTML = '📋 Копировать';
    btnCopy.addEventListener('click', () => {
      if (onCopy) onCopy(cleanText);
    });

    footer.appendChild(btnArt);
    footer.appendChild(btnRetry);
    footer.appendChild(btnCopy);
    placeholder.bubble.appendChild(footer);
  }

  if (container) scrollToBottom(container);
}

/**
 * Appends generated artwork inside assistant message bubble with Lightbox and Info Accordion
 */
export function appendInlineArt(bubble, imageUrl, caption = 'Сгенерированная сцена', generationParams = null) {
  let artContainer = bubble.querySelector('.inline-scene-art');
  if (!artContainer) {
    artContainer = document.createElement('div');
    artContainer.className = 'inline-scene-art';
    const footer = bubble.querySelector('.message-action-footer');
    if (footer) {
      bubble.insertBefore(artContainer, footer);
    } else {
      bubble.appendChild(artContainer);
    }
  }

  artContainer.innerHTML = `<img src="${imageUrl}" alt="${caption}" loading="lazy">`;
  artContainer.onclick = () => {
    openLightbox(imageUrl, caption, imageUrl);
  };

  // Render Info Accordion if generationParams present
  if (generationParams) {
    let existingAccordion = bubble.querySelector('.art-info-accordion');
    if (existingAccordion) existingAccordion.remove();

    const acc = document.createElement('div');
    acc.className = 'art-info-accordion';

    const header = document.createElement('div');
    header.className = 'art-info-header';
    header.innerHTML = `
      <span>ℹ️ Инфо генерации</span>
      <span class="chevron">▾</span>
    `;

    const body = document.createElement('div');
    body.className = 'art-info-body';

    const paramGrid = document.createElement('div');
    paramGrid.className = 'art-param-grid';
    paramGrid.innerHTML = `
      <div>Модель: <b>${generationParams.model_name || 'SD Forge'}</b></div>
      <div>Сид: <b>${generationParams.seed || -1}</b></div>
      <div>Сэмплер: <b>${generationParams.sampler || 'Euler a'}</b></div>
      <div>Шаги: <b>${generationParams.steps || 28}</b></div>
      <div>CFG: <b>${generationParams.cfg_scale || 6.0}</b></div>
      <div>Hires Denoise: <b>${generationParams.denoising_strength || 0.35}</b></div>
    `;
    body.appendChild(paramGrid);

    // Positive prompt block
    if (generationParams.prompt) {
      const posBlock = document.createElement('div');
      posBlock.className = 'art-prompt-block';
      posBlock.innerHTML = `
        <div class="art-prompt-title-row">
          <span>Положительный промпт</span>
          <button class="btn-copy-chip">📋 Копировать</button>
        </div>
        <div class="art-prompt-text">${generationParams.prompt}</div>
      `;
      posBlock.querySelector('.btn-copy-chip').addEventListener('click', (e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(generationParams.prompt);
        showToast('Положительный промпт скопирован');
      });
      body.appendChild(posBlock);
    }

    // Negative prompt block
    if (generationParams.negative_prompt) {
      const negBlock = document.createElement('div');
      negBlock.className = 'art-prompt-block';
      negBlock.innerHTML = `
        <div class="art-prompt-title-row">
          <span>Негативный промпт</span>
          <button class="btn-copy-chip">📋 Копировать</button>
        </div>
        <div class="art-prompt-text">${generationParams.negative_prompt}</div>
      `;
      negBlock.querySelector('.btn-copy-chip').addEventListener('click', (e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(generationParams.negative_prompt);
        showToast('Негативный промпт скопирован');
      });
      body.appendChild(negBlock);
    }

    header.addEventListener('click', () => {
      const isHidden = body.style.display === 'none';
      body.style.display = isHidden ? 'flex' : 'none';
      header.querySelector('.chevron').textContent = isHidden ? '▾' : '▸';
    });

    acc.appendChild(header);
    acc.appendChild(body);

    const footer = bubble.querySelector('.message-action-footer');
    if (footer) {
      bubble.insertBefore(acc, footer);
    } else {
      bubble.appendChild(acc);
    }
  }
}

/**
 * Creates Cyberpunk Viewfinder (CyberpunkArtPlaceholder) with scanning line and progress
 */
export function createCyberpunkViewfinder(container, onAbort = null) {
  const vf = document.createElement('div');
  vf.className = 'cyberpunk-viewfinder';
  vf.innerHTML = `
    <div class="viewfinder-scanline"></div>
    <div class="viewfinder-header">
      <div class="viewfinder-title">
        <div class="viewfinder-pulse"></div>
        <span>Cyberpunk Viewfinder</span>
      </div>
      <button class="btn-viewfinder-abort">⏹ Прервать</button>
    </div>
    <div class="viewfinder-status">Инициализация VRAM / Выгрузка LLM...</div>
    <div class="viewfinder-progress-track">
      <div class="viewfinder-progress-fill"></div>
    </div>
  `;

  const statusEl = vf.querySelector('.viewfinder-status');
  const fillEl = vf.querySelector('.viewfinder-progress-fill');
  const abortBtn = vf.querySelector('.btn-viewfinder-abort');

  if (onAbort) {
    abortBtn.addEventListener('click', onAbort);
  } else {
    abortBtn.style.display = 'none';
  }

  container.appendChild(vf);
  scrollToBottom(container, true);

  return {
    element: vf,
    updateProgress: (pct, statusText) => {
      if (statusText) statusEl.textContent = statusText;
      if (pct !== undefined && pct !== null) {
        fillEl.style.width = `${Math.min(100, Math.max(0, pct))}%`;
      }
    },
    remove: () => {
      vf.style.opacity = '0';
      vf.style.transition = 'opacity 0.25s ease';
      setTimeout(() => vf.remove(), 250);
    }
  };
}

// ─── Sessions Drawer Rendering ───
export function renderSessionsList(container, sessions, activeSessionId, onSelect, onDelete) {
  container.innerHTML = '';

  if (!sessions || sessions.length === 0) {
    container.innerHTML = `
      <div style="padding: 24px 8px; text-align: center; color: var(--text-muted); font-size: 13px;">
        Нет сохраненных диалогов
      </div>
    `;
    return;
  }

  sessions.forEach(sess => {
    const item = document.createElement('div');
    item.className = `session-item ${sess.id === activeSessionId ? 'active' : ''}`;

    const info = document.createElement('div');
    info.className = 'session-info';

    const title = document.createElement('div');
    title.className = 'session-title';
    title.textContent = sess.title || 'Диалог';

    const meta = document.createElement('div');
    meta.className = 'session-meta';

    const modeTag = (sess.mode || 'assistant').toUpperCase();
    const countTag = `${sess.message_count || 0} сообщ.`;
    meta.textContent = `${modeTag} • ${countTag}`;

    info.appendChild(title);
    info.appendChild(meta);

    info.addEventListener('click', () => {
      if (onSelect) onSelect(sess.id);
    });

    const delBtn = document.createElement('button');
    delBtn.className = 'session-delete-btn';
    delBtn.title = 'Удалить диалог';
    delBtn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="3 6 5 6 21 6"></polyline>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
      </svg>
    `;

    delBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (onDelete) onDelete(sess.id, sess.title);
    });

    item.appendChild(info);
    item.appendChild(delBtn);
    container.appendChild(item);
  });
}

// ─── Character Gallery Rendering (2-column grid or detailed modal list) ───
export function renderCharactersGallery(container, characters, onSelect, options = {}) {
  const { detailed = false } = options;
  container.innerHTML = '';
  container.classList.toggle('characters-gallery-detailed', detailed);

  if (!characters || characters.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1 / -1; padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px;">
        Персонажи не найдены
      </div>
    `;
    return;
  }

  const appendDetailBlock = (parent, label, value) => {
    if (!value) return;
    const block = document.createElement('div');
    block.className = 'character-detail-block';
    const lbl = document.createElement('div');
    lbl.className = 'character-detail-label';
    lbl.textContent = label;
    const val = document.createElement('div');
    val.className = 'character-detail-value';
    val.textContent = value;
    block.appendChild(lbl);
    block.appendChild(val);
    parent.appendChild(block);
  };

  characters.forEach(char => {
    const card = document.createElement('div');
    card.className = detailed ? 'character-card-item detailed' : 'character-card-item';
    card.dataset.id = char.id;

    const poster = document.createElement('img');
    poster.className = 'character-card-poster';
    poster.src = char.avatar_url || `/media/character-avatar/${char.id}`;
    poster.alt = char.name;
    poster.loading = 'lazy';
    poster.onerror = () => { poster.src = '/media/avatar/current'; };

    const body = document.createElement('div');
    body.className = 'character-card-body';

    const name = document.createElement('div');
    name.className = 'character-card-name';
    name.textContent = char.name;

    const archetype = document.createElement('div');
    archetype.className = 'character-card-archetype';
    archetype.textContent = char.archetype || 'Персонаж';

    body.appendChild(name);
    body.appendChild(archetype);

    if (detailed) {
      if (char.author) {
        const author = document.createElement('div');
        author.className = 'character-card-author';
        author.textContent = char.author;
        body.appendChild(author);
      }

      if (char.tags && char.tags.length > 0) {
        const tagsCloud = document.createElement('div');
        tagsCloud.className = 'tags-cloud';
        char.tags.forEach(tag => {
          const badge = document.createElement('span');
          badge.className = 'tag-badge';
          badge.textContent = tag;
          tagsCloud.appendChild(badge);
        });
        body.appendChild(tagsCloud);
      }

      appendDetailBlock(body, 'Описание', char.description);
      appendDetailBlock(body, 'Характер', char.personality);
      appendDetailBlock(body, 'Сценарий', char.scenario);
      appendDetailBlock(body, 'Приветствие', char.first_mes);
    } else {
      const desc = document.createElement('div');
      desc.className = 'character-card-desc';
      desc.textContent = char.description || char.author || '';
      body.appendChild(desc);
    }

    const startBtn = document.createElement('button');
    startBtn.className = 'btn-card-start';
    startBtn.textContent = detailed ? '🎴 Выбрать компаньона' : 'Начать диалог';

    body.appendChild(startBtn);

    if (detailed) {
      const headerRow = document.createElement('div');
      headerRow.className = 'character-card-detailed-head';
      headerRow.appendChild(poster);
      const headMeta = document.createElement('div');
      headMeta.className = 'character-card-detailed-meta';
      headMeta.appendChild(name);
      headMeta.appendChild(archetype);
      if (body.querySelector('.character-card-author')) {
        headMeta.appendChild(body.querySelector('.character-card-author'));
      }
      headerRow.appendChild(headMeta);
      card.appendChild(headerRow);
      card.appendChild(body);
      startBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (onSelect) onSelect(char);
      });
    } else {
      card.appendChild(poster);
      card.appendChild(body);
      card.addEventListener('click', () => {
        if (onSelect) onSelect(char);
      });
    }

    container.appendChild(card);
  });
}

// ─── Forge Models List Rendering ───
export function renderForgeModelsList(container, models, activeId, onSelect) {
  container.innerHTML = '';

  if (!models || models.length === 0) {
    container.innerHTML = `
      <div style="padding: 16px; text-align: center; color: var(--text-muted); font-size: 13px;">
        Список моделей пуст или Forge недоступен
      </div>
    `;
    return;
  }

  models.forEach(model => {
    const isSel = (model.id === activeId || model.name === activeId);
    const row = document.createElement('div');
    row.className = `forge-model-row ${isSel ? 'selected' : ''}`;
    row.dataset.id = model.id;

    row.innerHTML = `
      <div class="forge-model-info">
        <div class="forge-model-name">${model.name}</div>
        <div class="forge-model-desc">${model.description || (model.id === 'auto' ? 'Автоматический выбор стиля арт-директором' : '')}</div>
      </div>
      ${isSel ? '<span class="forge-model-check">✓</span>' : ''}
    `;

    row.addEventListener('click', () => {
      if (onSelect) onSelect(model);
    });

    container.appendChild(row);
  });
}

// ─── World Status & Campaign Sheet Rendering ───
export function renderWorldStatus(container, castData) {
  container.innerHTML = '';

  if (!castData) {
    container.innerHTML = `
      <div style="padding: 20px; text-align: center; color: var(--text-muted); font-size: 13px;">
        Нет данных о состоянии мира для этой сессии
      </div>
    `;
    return;
  }

  // Location
  const locSec = document.createElement('div');
  locSec.className = 'world-card-section';
  locSec.innerHTML = `
    <div class="world-section-label">Текущая локация</div>
    <div class="world-section-value">${castData.location || 'Локация не указана'}</div>
  `;
  container.appendChild(locSec);

  // Scene Background Section
  const bgSec = document.createElement('div');
  bgSec.className = 'world-card-section';
  bgSec.innerHTML = `
    <div class="world-section-label">Фон сцены чата</div>
    <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 6px;">
      ${castData.scene_background_url ? 'Фон активен' : 'Фон не установлен'}
    </div>
    <div style="display: flex; gap: 8px;">
      <button id="btnGenerateSceneBg" class="btn-action-chip" style="flex: 1; justify-content: center;">
        🎨 Сгенерировать фон
      </button>
      ${castData.scene_background_url ? '<button id="btnResetSceneBg" class="btn-action-chip" style="color: var(--neon-red);">❌ Сбросить</button>' : ''}
    </div>
  `;
  container.appendChild(bgSec);

  // Active Quest
  if (castData.quest) {
    const questSec = document.createElement('div');
    questSec.className = 'world-card-section';
    questSec.innerHTML = `
      <div class="world-section-label">Активное задание / Цель</div>
      <div class="world-section-value">${castData.quest}</div>
    `;
    container.appendChild(questSec);
  }

  // Focus Companion
  if (castData.companion) {
    const compSec = document.createElement('div');
    compSec.className = 'world-card-section';
    compSec.innerHTML = `
      <div class="world-section-label">Компаньон в фокусе</div>
      <div class="world-section-value">
        ${castData.companion}
        ${castData.relationship_state ? `<span style="color: var(--neon-cyan); font-size: 12px; margin-left: 6px;">(${castData.relationship_state})</span>` : ''}
      </div>
    `;
    container.appendChild(compSec);
  }

  // Inventory
  if (castData.inventory && castData.inventory.length > 0) {
    const invSec = document.createElement('div');
    invSec.className = 'world-card-section';
    const tags = castData.inventory.map(item => `<span class="tag-badge">🎒 ${item}</span>`).join('');
    invSec.innerHTML = `
      <div class="world-section-label">Инвентарь</div>
      <div class="tags-cloud">${tags}</div>
    `;
    container.appendChild(invSec);
  }

  // Stats / Meters
  if (castData.stats && Object.keys(castData.stats).length > 0) {
    const statsSec = document.createElement('div');
    statsSec.className = 'world-card-section';
    let rows = '';
    for (const [k, v] of Object.entries(castData.stats)) {
      rows += `<div class="stat-item-row"><span>${k}:</span><b>${v}</b></div>`;
    }
    statsSec.innerHTML = `
      <div class="world-section-label">Параметры и счетчики</div>
      <div style="display: flex; flex-direction: column; gap: 4px; margin-top: 4px;">${rows}</div>
    `;
    container.appendChild(statsSec);
  }

  // World State Events
  if (castData.world_state && castData.world_state.length > 0) {
    const wsSec = document.createElement('div');
    wsSec.className = 'world-card-section';
    const tags = castData.world_state.map(item => `<span class="tag-badge">🌐 ${item}</span>`).join('');
    wsSec.innerHTML = `
      <div class="world-section-label">События мира</div>
      <div class="tags-cloud">${tags}</div>
    `;
    container.appendChild(wsSec);
  }
}

// ─── Hardware & VRAM Details Rendering ───
export function renderHardwareDetails(container, status) {
  container.innerHTML = '';
  if (!status) {
    container.innerHTML = '<div style="color: var(--text-muted); font-size: 13px;">Данные телеметрии недоступны</div>';
    return;
  }

  const tel = status.telemetry || {};
  const vramUsed = (tel.vram_used_mb / 1024).toFixed(1);
  const vramTotal = (tel.vram_total_mb / 1024).toFixed(1);
  const vramPct = tel.vram_total_mb > 0 ? Math.round((tel.vram_used_mb / tel.vram_total_mb) * 100) : 0;

  const ramUsed = (tel.ram_used_mb / 1024).toFixed(1);
  const ramTotal = (tel.ram_total_mb / 1024).toFixed(1);

  let servicesHtml = '';
  if (status.services) {
    status.services.forEach(srv => {
      const isUp = srv.is_online;
      const dotColor = isUp ? 'var(--neon-green)' : 'var(--neon-red)';
      servicesHtml += `
        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px; margin-bottom: 4px;">
          <span>${srv.name} (порт ${srv.port})</span>
          <span style="color: ${dotColor}; font-weight: 600;">${isUp ? 'Онлайн' : 'Офлайн'}</span>
        </div>
      `;
    });
  }

  container.innerHTML = `
    <div style="display: flex; flex-direction: column; gap: 12px;">
      <div class="world-card-section">
        <div class="world-section-label">Видеопамять (VRAM)</div>
        <div style="display: flex; justify-content: space-between; font-size: 14px; font-weight: 600;">
          <span>${vramUsed} / ${vramTotal} GB</span>
          <span style="color: var(--neon-cyan);">${vramPct}%</span>
        </div>
        <div style="width: 100%; height: 6px; background: rgba(255,255,255,0.08); border-radius: 3px; overflow: hidden; margin-top: 4px;">
          <div style="width: ${vramPct}%; height: 100%; background: var(--neon-cyan);"></div>
        </div>
      </div>

      <div class="world-card-section">
        <div class="world-section-label">Оперативная память (RAM)</div>
        <div style="font-size: 13px;">${ramUsed} / ${ramTotal} GB</div>
      </div>

      <div class="world-card-section">
        <div class="world-section-label">Службы инференса</div>
        <div style="margin-top: 6px;">${servicesHtml}</div>
      </div>
    </div>
  `;
}

// ─── Art Gallery Grid Rendering ───
export function renderArtGalleryGrid(container, items, onSelect) {
  container.innerHTML = '';
  if (!items || items.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1 / -1; padding: 30px; text-align: center; color: var(--text-muted); font-size: 13px;">
        В галерее пока нет сгенерированных изображений
      </div>
    `;
    return;
  }

  items.forEach(item => {
    const card = document.createElement('div');
    card.className = 'art-gallery-card';

    const img = document.createElement('img');
    img.className = 'art-gallery-thumb';
    img.src = item.url;
    img.alt = item.filename;
    img.loading = 'lazy';

    const meta = document.createElement('div');
    meta.className = 'art-gallery-meta';
    meta.textContent = item.filename;

    card.appendChild(img);
    card.appendChild(meta);

    card.addEventListener('click', () => {
      if (onSelect) onSelect(item);
    });

    container.appendChild(card);
  });
}

// ─── Fullscreen Lightbox ───
export function openLightbox(imgSrc, title = '', downloadUrl = null) {
  const modal = document.getElementById('lightboxModal');
  const img = document.getElementById('lightboxImg');
  const titleEl = document.getElementById('lightboxTitle');
  const downloadLink = document.getElementById('btnLightboxDownload');
  if (!modal || !img) return;

  img.src = imgSrc;
  if (titleEl) titleEl.textContent = title;
  if (downloadLink) {
    downloadLink.href = downloadUrl || imgSrc;
    downloadLink.download = `${title || 'art'}.png`.replace(/\s+/g, '_');
  }
  modal.classList.add('active');
}

export function closeLightbox() {
  const modal = document.getElementById('lightboxModal');
  if (modal) modal.classList.remove('active');
}

// ─── Toasts ───
export function showToast(message, isError = false) {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `toast ${isError ? 'error' : ''}`;
  toast.textContent = message;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-10px)';
    toast.style.transition = 'all 0.25s ease-out';
    setTimeout(() => toast.remove(), 250);
  }, 3200);
}
