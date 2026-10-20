const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;

const state = {
  ui: 'ru',
  source: 'ru',
  target: 'es',
  recognizer: null,
  filter: 'all',
  autoSpeak: false,
};

const el = {
  uiLangs: document.getElementById('ui-langs'),
  inputLangs: document.getElementById('input-langs'),
  outputLangs: document.getElementById('output-langs'),
  swapBtn: document.getElementById('swap-btn'),
  mic: document.getElementById('mic'),
  micLabel: document.getElementById('mic-label'),
  sourceText: document.getElementById('source-text'),
  translateBtn: document.getElementById('translate-btn'),
  result: document.getElementById('result'),
  resultSource: document.getElementById('result-source'),
  resultTarget: document.getElementById('result-target'),
  playBtn: document.getElementById('play-btn'),
  shareNative: document.getElementById('share-native'),
  shareCopy: document.getElementById('share-copy'),
  recordBtn: document.getElementById('record-btn'),
  recAudio: document.getElementById('rec-audio'),
  shareAudio: document.getElementById('share-audio'),
  recDownload: document.getElementById('rec-download'),
  modeChips: document.getElementById('mode-chips'),
  status: document.getElementById('status'),
  filters: document.getElementById('filters'),
  phraseList: document.getElementById('phrase-list'),
};

function t(key) {
  const dict = UI[state.ui] || UI.ru;
  return dict[key] !== undefined ? dict[key] : UI.ru[key];
}

function loadPrefs() {
  try {
    const saved = JSON.parse(localStorage.getItem('phrasebook-prefs') || '{}');
    if (UI[saved.ui]) state.ui = saved.ui;
    if (LANGS[saved.source]) state.source = saved.source;
    if (LANGS[saved.target]) state.target = saved.target;
    if (typeof saved.auto === 'boolean') state.autoSpeak = saved.auto;
  } catch (error) {
    console.warn(error);
  }
  if (state.source === state.target) {
    state.target = CODES.find((code) => code !== state.source);
  }
}

function savePrefs() {
  try {
    localStorage.setItem('phrasebook-prefs', JSON.stringify({
      ui: state.ui,
      source: state.source,
      target: state.target,
      auto: state.autoSpeak,
    }));
  } catch (error) {
    console.warn(error);
  }
}

function setStatus(message) {
  el.status.textContent = message;
}

function chipButton(code, active, onClick) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'chip' + (active ? ' is-active' : '');
  btn.setAttribute('role', 'radio');
  btn.setAttribute('aria-checked', String(active));
  btn.textContent = `${LANGS[code].flag} ${LANGS[code].name}`;
  btn.addEventListener('click', onClick);
  return btn;
}

function renderUiLangs() {
  el.uiLangs.innerHTML = '';
  CODES.forEach((code) => {
    el.uiLangs.appendChild(chipButton(code, state.ui === code, () => {
      state.ui = code;
      savePrefs();
      applyUI();
    }));
  });
}

function renderLangChips() {
  el.inputLangs.innerHTML = '';
  el.outputLangs.innerHTML = '';
  CODES.forEach((code) => {
    el.inputLangs.appendChild(chipButton(code, state.source === code, () => setSource(code)));
    el.outputLangs.appendChild(chipButton(code, state.target === code, () => setTarget(code)));
  });
}

function categoryName(id) {
  return id === 'all' ? t('all') : UI[state.ui].categories[id];
}

function renderFilters() {
  el.filters.innerHTML = '';
  ['all', ...CATEGORIES].forEach((id) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip' + (state.filter === id ? ' is-active' : '');
    btn.textContent = categoryName(id);
    btn.addEventListener('click', () => {
      state.filter = id;
      renderFilters();
      renderPhrases();
    });
    el.filters.appendChild(btn);
  });
}

function renderPhrases() {
  const visible = PHRASES.filter((p) => state.filter === 'all' || p.category === state.filter);
  el.phraseList.innerHTML = '';

  visible.forEach((phrase) => {
    const item = document.createElement('li');
    item.className = 'phrase';

    const pick = document.createElement('button');
    pick.type = 'button';
    pick.className = 'phrase-pick';
    pick.setAttribute('aria-label', `${t('showTranslation')}: ${phrase[state.source]}`);

    const source = document.createElement('span');
    source.lang = state.source;
    source.dir = LANGS[state.source].dir;
    source.textContent = phrase[state.source];

    const target = document.createElement('span');
    target.className = 'phrase-target';
    target.lang = state.target;
    target.dir = LANGS[state.target].dir;
    target.textContent = phrase[state.target];

    pick.append(source, target);
    pick.addEventListener('click', () => {
      el.sourceText.value = phrase[state.source];
      showResult(phrase[state.source], phrase[state.target]);
    });

    const play = document.createElement('button');
    play.type = 'button';
    play.className = 'icon-btn';
    play.setAttribute('aria-label', `${t('playAria')}: ${phrase[state.target]}`);
    play.textContent = '▶';
    play.addEventListener('click', () => speak(phrase[state.target], state.target));

    item.append(pick, play);
    el.phraseList.appendChild(item);
  });
}

// Режим озвучки: вручную (только по кнопке) или авто (сразу после перевода).
function renderModeChips() {
  el.modeChips.innerHTML = '';
  [[false, 'modeManual'], [true, 'modeAuto']].forEach(([auto, key]) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'chip' + (state.autoSpeak === auto ? ' is-active' : '');
    btn.setAttribute('role', 'radio');
    btn.setAttribute('aria-checked', String(state.autoSpeak === auto));
    btn.textContent = t(key);
    btn.addEventListener('click', () => {
      state.autoSpeak = auto;
      savePrefs();
      renderModeChips();
    });
    el.modeChips.appendChild(btn);
  });
}

function updateMicLabel() {
  if (!SpeechRecognitionAPI) {
    el.micLabel.textContent = t('micUnsupported');
  } else {
    el.micLabel.textContent = state.recognizer ? t('micListening') : t('micIdle');
  }
  el.mic.classList.toggle('is-listening', Boolean(state.recognizer));
  el.mic.disabled = !SpeechRecognitionAPI;
}

function applyUI() {
  document.documentElement.lang = state.ui;
  document.documentElement.dir = LANGS[state.ui].dir;
  document.title = t('appTitle');
  document.querySelectorAll('[data-i18n]').forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((node) => {
    node.placeholder = t(node.dataset.i18nPlaceholder);
  });
  document.querySelectorAll('[data-i18n-aria]').forEach((node) => {
    node.setAttribute('aria-label', t(node.dataset.i18nAria));
  });
  renderUiLangs();
  renderLangChips();
  renderFilters();
  renderPhrases();
  renderModeChips();
  updateMicLabel();
  updateRecordButton();
}

// Меняет языки. Если сменился язык ввода, старый текст очищается.
function setLanguages(source, target) {
  if (source === target) return;
  const sourceChanged = source !== state.source;
  stopMic();
  state.source = source;
  state.target = target;
  if (sourceChanged) el.sourceText.value = '';
  el.sourceText.lang = source;
  el.sourceText.dir = LANGS[source].dir;
  el.result.hidden = true;
  setStatus('');
  savePrefs();
  renderLangChips();
  renderPhrases();
}

function setSource(code) {
  if (code === state.target) {
    setLanguages(code, state.source);
  } else {
    setLanguages(code, state.target);
  }
}

function setTarget(code) {
  if (code === state.source) {
    setLanguages(state.target, code);
  } else {
    setLanguages(state.source, code);
  }
}

function swapLanguages() {
  const translated = el.result.hidden ? '' : el.resultTarget.textContent;
  setLanguages(state.target, state.source);
  if (translated) el.sourceText.value = translated;
}

function normalize(text) {
  return text
    .toLowerCase()
    .replace(/[.,!?¿¡;:'«»—…-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function findPhrase(text) {
  const query = normalize(text);
  if (!query) return null;
  return PHRASES.find((phrase) => normalize(phrase[state.source]) === query) || null;
}

function showResult(sourceText, targetText) {
  el.resultSource.textContent = sourceText;
  el.resultSource.lang = state.source;
  el.resultSource.dir = LANGS[state.source].dir;
  el.resultTarget.textContent = targetText;
  el.resultTarget.lang = state.target;
  el.resultTarget.dir = LANGS[state.target].dir;
  el.result.hidden = false;
  setStatus('');
  if (state.autoSpeak) speak(targetText, state.target);
}

// Текст для отправки: исходная фраза, перевод и подпись приложения.
function shareText() {
  return `${el.resultSource.textContent}\n${el.resultTarget.textContent}\n\nMinkin`;
}

async function shareNative() {
  try {
    await navigator.share({ title: 'Minkin', text: shareText() });
  } catch (error) {
    if (error.name !== 'AbortError') setStatus(error.message);
  }
}

async function copyText() {
  try {
    await navigator.clipboard.writeText(shareText());
    setStatus(t('copied'));
  } catch (error) {
    setStatus(t('copyError'));
  }
}

// Запись речи в аудиофайл средствами браузера, без облачных сервисов.
const rec = {
  recorder: null,
  chunks: [],
  blob: null,
  url: null,
  ext: 'webm',
};

function recordingFile() {
  return new File([rec.blob], `minkin-${Date.now()}.${rec.ext}`, { type: rec.blob.type });
}

function canShareRecording() {
  try {
    return Boolean(rec.blob) && Boolean(navigator.canShare) && navigator.canShare({ files: [recordingFile()] });
  } catch (error) {
    return false;
  }
}

function updateRecordButton() {
  el.recordBtn.textContent = rec.recorder ? t('stopRecord') : t('record');
  el.recordBtn.classList.toggle('is-recording', Boolean(rec.recorder));
}

function showRecording() {
  el.recAudio.src = rec.url;
  el.recAudio.hidden = false;
  el.recDownload.href = rec.url;
  el.recDownload.download = `minkin-${Date.now()}.${rec.ext}`;
  el.recDownload.hidden = false;
  el.shareAudio.hidden = !canShareRecording();
}

async function shareRecording() {
  try {
    await navigator.share({ title: 'Minkin', files: [recordingFile()] });
  } catch (error) {
    if (error.name !== 'AbortError') setStatus(error.message);
  }
}

async function toggleRecord() {
  if (rec.recorder) {
    rec.recorder.stop();
    return;
  }
  if (!window.MediaRecorder || !navigator.mediaDevices) {
    setStatus(t('recordUnsupported'));
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    setStatus(t('micNotAllowed'));
    return;
  }

  const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
    .find((type) => MediaRecorder.isTypeSupported(type)) || '';
  const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
  const type = (recorder.mimeType || mime || 'audio/webm').split(';')[0];

  rec.chunks = [];
  recorder.ondataavailable = (event) => {
    if (event.data.size) rec.chunks.push(event.data);
  };
  recorder.onstop = () => {
    stream.getTracks().forEach((track) => track.stop());
    if (rec.url) URL.revokeObjectURL(rec.url);
    rec.blob = new Blob(rec.chunks, { type });
    rec.url = URL.createObjectURL(rec.blob);
    rec.ext = type.includes('mp4') ? 'm4a' : 'webm';
    rec.recorder = null;
    updateRecordButton();
    showRecording();
  };

  rec.recorder = recorder;
  recorder.start();
  updateRecordButton();
}

function speak(text, lang) {
  if (!('speechSynthesis' in window)) {
    setStatus(t('ttsUnsupported'));
    return;
  }
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = LANGS[lang].speech;
  const voice = speechSynthesis.getVoices().find((v) => v.lang.toLowerCase().startsWith(LANGS[lang].api));
  if (voice) utterance.voice = voice;
  utterance.rate = 0.9;
  speechSynthesis.speak(utterance);
}

async function translate() {
  const text = el.sourceText.value.trim();
  if (!text) {
    setStatus(t('enterText'));
    return;
  }

  const phrase = findPhrase(text);
  if (phrase) {
    el.sourceText.value = phrase[state.source];
    showResult(phrase[state.source], phrase[state.target]);
    return;
  }

  setStatus(t('translating'));
  el.translateBtn.disabled = true;
  try {
    const params = new URLSearchParams({
      q: text,
      langpair: `${LANGS[state.source].api}|${LANGS[state.target].api}`,
    });
    const response = await fetch(`${TRANSLATE_URL}?${params}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data.responseStatus && data.responseStatus !== 200) {
      throw new Error(data.responseDetails || `статус ${data.responseStatus}`);
    }
    const translated = data.responseData && data.responseData.translatedText;
    if (!translated) throw new Error('пустой ответ');
    showResult(text, translated);
  } catch (error) {
    console.error(error);
    el.result.hidden = true;
    setStatus(`${t('translateError')} (${error.message}). ${t('checkInternet')}`);
  } finally {
    el.translateBtn.disabled = false;
  }
}

function startListening() {
  if (!SpeechRecognitionAPI) {
    setStatus(t('speechUnsupported'));
    return;
  }

  const recognizer = new SpeechRecognitionAPI();
  recognizer.lang = LANGS[state.source].speech;
  recognizer.interimResults = true;
  recognizer.continuous = false;
  recognizer.maxAlternatives = 1;

  let finalText = '';

  recognizer.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) {
        finalText += result[0].transcript;
      } else {
        interim += result[0].transcript;
      }
    }
    el.sourceText.value = (finalText + interim).trim();
  };

  recognizer.onerror = (event) => {
    if (event.error === 'aborted') return;
    const key = MIC_ERRORS[event.error];
    setStatus(key ? t(key) : event.error);
  };

  recognizer.onend = () => {
    if (state.recognizer !== recognizer) return;
    state.recognizer = null;
    updateMicLabel();
    if (el.sourceText.value.trim()) translate();
  };

  try {
    state.recognizer = recognizer;
    setStatus('');
    recognizer.start();
  } catch (error) {
    state.recognizer = null;
    setStatus(error.message);
  }
  updateMicLabel();
}

// Остановка без перевода: используется при смене языка.
function stopMic() {
  const recognizer = state.recognizer;
  if (!recognizer) return;
  state.recognizer = null;
  recognizer.abort();
  updateMicLabel();
}

function toggleMic() {
  if (state.recognizer) {
    state.recognizer.stop();
  } else {
    startListening();
  }
}

el.mic.addEventListener('click', toggleMic);
el.translateBtn.addEventListener('click', translate);
el.playBtn.addEventListener('click', () => speak(el.resultTarget.textContent, state.target));
el.shareNative.addEventListener('click', shareNative);
el.shareCopy.addEventListener('click', copyText);
if (!navigator.share) el.shareNative.hidden = true;
el.recordBtn.addEventListener('click', toggleRecord);
el.shareAudio.addEventListener('click', shareRecording);
el.swapBtn.addEventListener('click', swapLanguages);
el.sourceText.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    translate();
  }
});

loadPrefs();
el.sourceText.lang = state.source;
el.sourceText.dir = LANGS[state.source].dir;
applyUI();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js');
}
