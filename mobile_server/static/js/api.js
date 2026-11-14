/**
 * NeonRoute Mobile REST API Client
 */

export async function getSystemStatus() {
  const res = await fetch('/api/system/status');
  if (!res.ok) throw new Error(`Status HTTP ${res.status}`);
  return await res.json();
}

export async function setSystemMode(mode) {
  const res = await fetch('/api/system/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode })
  });
  if (!res.ok) throw new Error(`Mode switch failed: ${res.status}`);
  return await res.json();
}

export async function unloadVram() {
  const res = await fetch('/api/system/unload-vram', {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`VRAM unload failed: ${res.status}`);
  return await res.json();
}

export async function getSessions() {
  const res = await fetch('/api/sessions');
  if (!res.ok) throw new Error(`Sessions list failed: ${res.status}`);
  return await res.json();
}

export async function getSessionDetail(sessionId) {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!res.ok) throw new Error(`Session detail failed: ${res.status}`);
  return await res.json();
}

export async function createSession({ mode = "assistant", title = null, characterId = null } = {}) {
  const res = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, title, character_id: characterId })
  });
  if (!res.ok) throw new Error(`Session create failed: ${res.status}`);
  return await res.json();
}

export async function deleteSession(sessionId) {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error(`Session delete failed: ${res.status}`);
  return await res.json();
}

export async function getSessionCast(sessionId) {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/cast`);
  if (!res.ok) throw new Error(`Cast fetch failed: ${res.status}`);
  return await res.json();
}

export async function getCharacters() {
  const res = await fetch('/api/characters');
  if (!res.ok) throw new Error(`Characters list failed: ${res.status}`);
  return await res.json();
}

export async function getForgeModels(sessionId = null) {
  const url = sessionId
    ? `/api/art/models?session_id=${encodeURIComponent(sessionId)}`
    : '/api/art/models';
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Forge models failed: ${res.status}`);
  return await res.json();
}

export async function setActiveForgeModel(modelId, sessionId = null) {
  const res = await fetch('/api/art/active-model', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_id: modelId, session_id: sessionId })
  });
  if (!res.ok) throw new Error(`Set Forge model failed: ${res.status}`);
  return await res.json();
}

export async function generateSceneArt({
  sessionId,
  prompt = null,
  negativePrompt = null,
  aspectRatio = "portrait",
  modelId = null,
  characterId = null,
  overrideParams = null,
  text = null,
  promptOverride = null
} = {}) {
  const res = await fetch('/api/art/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      prompt: prompt || text || promptOverride || "",
      negative_prompt: negativePrompt || "",
      aspect_ratio: aspectRatio,
      model_id: modelId,
      character_id: characterId,
      override_params: overrideParams,
      text,
      prompt_override: promptOverride
    })
  });
  if (!res.ok) throw new Error(`Art generation failed: ${res.status}`);
  return await res.json();
}

export async function sendDirectArtCommand({ sessionId, command }) {
  const res = await fetch('/api/art/direct-command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, command })
  });
  if (!res.ok) throw new Error(`Direct art command failed: ${res.status}`);
  return await res.json();
}

export async function getArtProgress() {
  const res = await fetch('/api/art/progress');
  if (!res.ok) throw new Error(`Progress failed: ${res.status}`);
  return await res.json();
}

export async function interruptArt(sessionId = null) {
  const url = sessionId
    ? `/api/art/interrupt?session_id=${encodeURIComponent(sessionId)}`
    : '/api/art/interrupt';
  const res = await fetch(url, {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`Interrupt failed: ${res.status}`);
  return await res.json();
}

export async function generateSceneBackground({ sessionId, prompt = null }) {
  const res = await fetch('/api/art/scene-background', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, prompt })
  });
  if (!res.ok) throw new Error(`Scene background failed: ${res.status}`);
  return await res.json();
}

export async function getArtGallery(sessionId = null) {
  const url = sessionId
    ? `/api/art/gallery?session_id=${encodeURIComponent(sessionId)}`
    : '/api/art/gallery';
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Gallery failed: ${res.status}`);
  return await res.json();
}

