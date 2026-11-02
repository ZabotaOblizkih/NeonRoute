/**
 * NeonRoute Mobile SSE Stream Parser
 * Streams tokens via fetch & ReadableStream with custom SSE event dispatch.
 */

function _processSseMessage(messageText, { onThought, onThoughtEnd, onToken, onDone, onError, onArtStarted }) {
  if (!messageText || !messageText.trim()) return;

  let eventType = 'message';
  const dataLines = [];

  const lines = messageText.split(/\r?\n/);
  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventType = line.substring(6).trim();
    } else if (line.startsWith('data:')) {
      // W3C SSE: remove strictly one leading space after 'data:' if present
      const dataValue = line.startsWith('data: ') ? line.slice(6) : line.slice(5);
      dataLines.push(dataValue);
    }
  }

  const dataContent = dataLines.join('\n');

  switch (eventType) {
    case 'thought':
      if (onThought) onThought(dataContent);
      break;
    case 'thought_end':
      if (onThoughtEnd) onThoughtEnd();
      break;
    case 'token':
      if (onToken) onToken(dataContent);
      break;
    case 'done':
      if (onDone) onDone(dataContent);
      break;
    case 'art_started':
      if (onArtStarted) onArtStarted(dataContent);
      break;
    case 'error':
      if (onError) onError(dataContent);
      break;
    default:
      if (onToken && dataContent) onToken(dataContent);
      break;
  }
}

async function _consumeStreamReader(response, { onThought, onThoughtEnd, onToken, onDone, onError, onArtStarted }) {
  if (!response.body) {
    throw new Error('ReadableStream is not supported or response body is empty');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Split SSE messages by double newline (supporting both CRLF \r\n\r\n and LF \n\n)
    const parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop(); // keep last incomplete chunk

    for (const part of parts) {
      _processSseMessage(part, { onThought, onThoughtEnd, onToken, onDone, onError, onArtStarted });
    }
  }

  // Residual buffer
  if (buffer && buffer.trim()) {
    const parts = buffer.split(/\r?\n\r?\n/);
    for (const part of parts) {
      _processSseMessage(part, { onThought, onThoughtEnd, onToken, onDone, onError, onArtStarted });
    }
  }
}

export async function sendChatMessageStream({
  sessionId,
  text,
  mode,
  onThought,
  onThoughtEnd,
  onToken,
  onDone,
  onError,
  onArtStarted,
  signal
}) {
  try {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, text, mode }),
      signal
    });

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errText || 'Chat stream failed'}`);
    }

    await _consumeStreamReader(response, { onThought, onThoughtEnd, onToken, onDone, onError, onArtStarted });
  } catch (err) {
    if (err.name === 'AbortError') {
      console.log('Chat stream aborted by user');
      return;
    }
    if (onError) onError(err.message || String(err));
  }
}

export async function sendChatRegenerateStream({
  sessionId,
  mode,
  onThought,
  onThoughtEnd,
  onToken,
  onDone,
  onError,
  signal
}) {
  try {
    const response = await fetch('/api/chat/regenerate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, mode }),
      signal
    });

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errText || 'Chat regenerate failed'}`);
    }

    await _consumeStreamReader(response, { onThought, onThoughtEnd, onToken, onDone, onError });
  } catch (err) {
    if (err.name === 'AbortError') {
      console.log('Chat regenerate aborted by user');
      return;
    }
    if (onError) onError(err.message || String(err));
  }
}
